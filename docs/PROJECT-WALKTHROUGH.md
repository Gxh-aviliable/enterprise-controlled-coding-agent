# Mini Claude Code 项目理解指南

本文按“一次用户请求如何完成代码任务”的顺序解释当前仓库。它面向第一次接手项目的开发者，内容以 `feature/repository-cleanup` 上的现行代码为准，不再讲解已经删除的早期单文件原型。

## 1. 先建立正确定位

项目不是一个让模型直接控制宿主机的聊天 Demo，而是一个面向企业内网的受控 Coding Agent 平台：

```text
Vue 工作台
  → FastAPI 认证与会话边界
  → LangGraph 有状态 Agent
  → Tool Contract、权限、风险和 HITL
  → 用户独立 Workspace
  → MySQL / Redis / Chroma / Trace
```

模型负责理解任务、规划和选择工具；平台负责决定它能否执行、在哪里执行、何时需要确认，以及如何记录和恢复。

## 2. 启动入口

后端入口是 `enterprise_agent/api/main.py`：

- `pyproject.toml` 将 `serve` 命令映射到 `enterprise_agent.api.main:run`；
- FastAPI lifespan 启动 MySQL、Chroma、Redis checkpointer 和记忆清理任务；
- 路由统一注册到认证、对话、Workspace、记忆、Trace 和管理员模块；
- `/health` 检查 MySQL 与 Redis，而不是只返回静态成功。

前端入口是 `frontend/src/main.js` 和 `frontend/src/App.vue`：

- `App.vue` 控制 Chat、Files、Trace、Memory、Admin 五个主视图；
- `Sidebar.vue` 管理会话、文件树和视图切换；
- `ChatPanel.vue` 负责消息持久化、SSE、工具卡片、HITL 和任务取消；
- `frontend/src/api/client.js` 集中处理令牌和 API 请求。

Docker 部署时，Nginx 托管 Vue 静态资源并把 `/api` 转发给 FastAPI。完整拓扑见 [ARCHITECTURE](../ARCHITECTURE.md)。

## 3. 一个请求如何进入系统

以 Chat 中提交“定位并修复测试失败”为例：

1. 前端带 JWT、`session_id` 和执行模式调用 `/chat/stream`。
2. `api/middleware/auth.py` 验证用户，路由再次验证会话归属。
3. `admin/quotas.py` 在任务开始前申请额度和并发租约。
4. 路由创建 `trace_id`，持久化用户消息和助手占位消息。
5. LangGraph 以 `session_id` 作为 Redis `thread_id` 开始或恢复执行。
6. 图事件被转换为 SSE，前端逐步渲染文本、工具、审批和终态。
7. 最终助手正文写入 MySQL；Redis 继续保存可恢复的执行 checkpoint。

MySQL 是用户可见会话正文的权威来源。Redis checkpoint 过期不会删除 MySQL 中的会话和消息。

## 4. LangGraph 执行闭环

图定义位于 `enterprise_agent/core/agent/graph.py`，核心路径是：

```text
task_parse
  → init_context
  → check_background
  → check_inbox
  → plan_task
  → pre_microcompact
  → llm_call
      ├─ 需要工具 → prepare_tool_execution → tool_confirm → tool_executor
      │               → checkpoint_task → save_memory
      │               ├─ 继续执行 → pre_microcompact
      │               ├─ 验证代码 → verification_gate → pre_microcompact
      │               └─ 完成 → finalize_task → persist_memory → END
      ├─ 需要压缩 → compress_context → llm_call
      └─ 文本完成 → save_memory → finalize_task → persist_memory → END
```

对应工程闭环为：

```text
任务解析 → 规划 → 执行 → 检查点 → 验证 → 总结
```

`nodes.py` 包含节点实现，`state.py` 定义跨节点状态。阅读时先看图和状态字段，再进入单个节点，避免直接从大型节点文件顺序阅读。

## 5. 任务状态与执行阶段

`core/execution/state_machine.py` 把任务状态和对话会话状态分开。

任务状态：

```text
pending → running ⇄ waiting_confirmation → succeeded
                 ├────────────────────────→ failed
                 └────────────────────────→ cancelled
```

执行阶段：

```text
parsing → planning → executing → checkpointing → validating → summarizing
```

状态机拒绝非法跳转，但允许同状态重放。这是 LangGraph interrupt、确认恢复和 checkpoint 重放时保持幂等的基础。

## 6. 工具为什么不是普通 Python 函数

工具在 `core/agent/tools/` 中实现，在 `tools/__init__.py` 注册。每个工具还必须在 `contracts.py` 中拥有唯一契约：

- 输入 schema；
- `safe`、`review` 或 `dangerous` 风险等级；
- 权限要求；
- 超时；
- 是否幂等、是否允许重试；
- 是否需要确认；
- 副作用类型；
- 规范化结果状态。

一次工具调用需要依次通过：

```text
是否注册
  → 当前数据库角色是否有权限
  → 参数级风险判断
  → 是否需要 HITL
  → 执行器自身安全策略
  → 结果规范化与 Trace
```

即使用户点击 Approve，执行器仍会拦截 `dangerous` 命令；Approve 只批准可审查操作，不会绕过路径、Shell 或敏感信息规则。

常用工具组：

| 目录 | 职责 |
|---|---|
| `file_ops.py` | 读取、原子写入、编辑、可恢复删除 |
| `shell.py` | 复合命令解析、风险分级、环境净化、超时和输出截断 |
| `task.py` | Todo 与持久任务板 |
| `background.py` | 后台命令和进程组回收 |
| `skills.py` | 共享/个人 Skill 加载与刷新 |
| `subagent.py`、`team.py` | 实验性委派与团队协作 |
| `memory.py` | 主动长期记忆搜索 |
| `context_tools.py` | 上下文压缩和 transcript 查询 |

## 7. Workspace 隔离

`core/agent/tools/workspace.py` 根据当前用户解析 `user_<id>` 目录。文件工具和 Workspace API 共享路径边界：

- 拒绝 `..` 和解析后逃逸；
- 拒绝 `.env`、`.git`、SSH、云凭据和私钥类路径；
- Shell 以用户 Workspace 为工作目录；
- 模型 API Key、JWT 和数据库凭据不会继承到 Shell 子进程；
- 删除工具只接受精确相对路径，并移动到恢复区生成 manifest。

当前 Shell 属于用户态策略防护，不是内核级沙箱。生产环境仍需要 rootless 容器、seccomp/AppArmor、资源限制和出站网络策略。

## 8. 四类持久化分别保存什么

| 存储 | 当前用途 | 不应承担的职责 |
|---|---|---|
| MySQL | 用户、会话、聊天正文、额度、审计、管理员授权、Shared Skill 版本 | LangGraph 中间状态 |
| Redis | LangGraph checkpoint、短期恢复、确认和密码重置临时数据 | 永久聊天正文 |
| Chroma | 通过准入策略的长期任务记忆和用户偏好 | 保存每轮原始对话 |
| 用户 Workspace | 代码、任务板、Trace JSON、恢复删除区 | 跨用户共享数据 |

Alembic 是部署环境 schema 演进的权威来源，`create_all()` 只保留为本地兼容路径。

## 9. Trace 如何回答“发生了什么”

`observability/trace_store.py` 为每个任务记录：

- 节点开始、结束、耗时和阶段；
- 模型输入/输出摘要、token 和错误；
- 工具名称、风险、结果、退出码、耗时和重试；
- HITL 请求、批准、拒绝或超时；
- 上下文压缩、记忆候选、过滤和注入回执；
- 最终状态、结果摘要和失败原因。

`/tasks` 路由提供列表、详情、完整时间线和聚合指标。前端 `TraceViewer.vue` 负责回放。Trace 落盘前进行递归脱敏，但当前后端仍是单进程 JSON 基线，多副本部署需要集中式 Trace 存储。

## 10. 长期记忆为何不会保存所有对话

长期记忆主流程位于 `memory/policy.py` 和 `memory/long_term.py`：

1. 任务完成时形成结构化候选；
2. 准入策略检查任务是否成功、是否存在工程证据或明确长期意图；
3. 普通聊天、一次性创作、失败任务和无证据结论默认拒绝；
4. 合格记录写入用户隔离的 Chroma collection；
5. 下次任务只召回通过 schema、质量和相关性门槛的 Active 记录；
6. 候选、过滤原因和最终注入记录进入 Trace。

Memory 页面中的“recalled”代表记录被选中并注入上下文，不代表模型一定正确使用。详细规则见[长期记忆治理](memory-governance.md)。

## 11. Single 与 Multi-Agent 的边界

`single_agent` 是默认且已经测量的基线。`multi_agent`：

- 必须由服务端显式启用；
- 需要数据库中的高级工具权限；
- 通过独立 specialist 上下文完成真实委派；
- 没有成功委派时不能伪装成 Multi 任务成功；
- 目前没有完成 3 个 delegation-suitable 用例的真实 single/multi 对照。

因此项目只陈述“具备实验性委派能力”，不声称多 Agent 带来质量收益。

## 12. 管理员控制面

`api/routes/admin.py` 和 `frontend/src/components/admin/AdminConsole.vue` 提供：

- 用户启停和会话撤销；
- 日/月 token、任务数、并发和 Workspace 额度；
- 默认只读元数据的跨用户任务与文件检查；
- 有理由、有时限、有审计的临时正文访问；
- Shared Skill 草稿、校验、发布、回滚和退役；
- 管理操作审计和系统健康。

管理员不是第二套 Agent。它是围绕现有多租户执行面的治理入口，详细边界见[管理员控制台](admin-console.md)。

## 13. 如何验证你理解的不是旧版本

先运行不调用外部模型的回归：

```bash
uv run pytest -q
uv run ruff check enterprise_agent migrations tests benchmarks scripts
npm test --prefix frontend -- --run
npm run build --prefix frontend
uv run python scripts/smoke_test.py
uv run python -m benchmarks.run --backend platform --mode single --no-artifacts
```

然后检查三个代表性报告：

- Platform 10/10：平台确定性路径；
- Memory 6/6：小型召回评测；
- DeepSeek single-Agent 8/10：真实模型执行。

真实模型 benchmark 会发送合成 workspace、提示和工具上下文并产生 API 费用，不属于默认本地验证命令。

## 14. 推荐代码阅读顺序

1. `enterprise_agent/config/settings.py`
2. `enterprise_agent/api/main.py`
3. `enterprise_agent/api/routes/chat.py`
4. `enterprise_agent/core/agent/state.py`
5. `enterprise_agent/core/execution/state_machine.py`
6. `enterprise_agent/core/agent/graph.py`
7. `enterprise_agent/core/agent/tools/contracts.py`
8. `enterprise_agent/core/agent/tools/workspace.py`
9. `enterprise_agent/core/agent/tools/file_ops.py` 与 `shell.py`
10. `enterprise_agent/observability/trace_store.py`
11. `enterprise_agent/memory/policy.py` 与 `long_term.py`
12. `frontend/src/App.vue`、`ChatPanel.vue`、`TraceViewer.vue`
13. 对应的 `tests/` 和 `benchmarks/v1/cases.json`

每读一个模块，都回到测试确认其真实行为。不要把注释、README 或模型回答当成唯一证据。
