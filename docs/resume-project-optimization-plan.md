# 秋招简历项目优化路线：Enterprise Controlled Engineering Agent

> 面向对象：港中深研一学生，准备 2026 秋招  
> 项目定位：面向企业内网部署的受控工程 Agent 平台  
> 目标：把项目从“能跑的 Agent 原型”优化成“简历上讲得清、面试中扛得住、工程上有亮点”的后端/AI 工程项目。

---

## 1. 架构师视角的项目判断

这个项目不要按“我做了一个聊天 Agent”来包装。它真正有价值的地方不是 Agent prompt 多复杂，而是尝试解决一个更工程化的问题：

> 如何让 AI Coding Agent 在企业服务器侧受控运行，并具备用户隔离、工具审批、文件权限、会话状态、项目记忆和部署治理能力。

从架构上看，当前项目可以拆成四层：

| 层级 | 当前模块 | 简历价值 |
|---|---|---|
| Agent 编排层 | `core/agent/graph.py`, `nodes.py`, `state.py`, `llm_factory.py` | LangGraph 状态机、多模型接入、工具调用 |
| 工具执行层 | `tools/shell.py`, `file_ops.py`, `workspace.py`, `task.py`, `skills.py` | 文件操作、安全执行、workspace 隔离 |
| 平台服务层 | `api/routes/*`, `auth/*`, `models/*`, `db/*` | FastAPI、JWT、多用户、SSE 流式响应 |
| 企业治理层 | 工具确认、记忆、审计、部署 | 与普通 Agent demo 拉开差距的关键 |

因此，项目优化不应该继续堆“更像聊天机器人”的功能，而应该强化：

- 受控执行
- 多用户隔离
- 工具权限治理
- 审计与可观测性
- 工程任务闭环
- 服务器部署能力

这些方向更适合秋招面试，因为它们能体现后端架构、工程复杂度和系统设计能力。

---

## 2. 简历目标版本

建议最终在简历中把项目描述成：

> 设计并实现一个面向企业内网部署的受控工程 Agent 平台，支持多用户登录、独立 workspace、LangGraph 有状态 Agent、SSE 流式对话、文件读写、Shell 工具执行、敏感操作确认、项目记忆和本地/服务器部署配置。系统通过 workspace 路径隔离、工具权限治理和操作日志，为企业代码助手场景提供可控、安全、可审计的 AI 编程工作台。

面试时重点讲三件事：

1. **为什么不是普通 Agent demo**
   - 普通 demo 关注模型回复。
   - 本项目关注 Agent 在企业环境里如何安全执行任务。

2. **你解决了哪些工程问题**
   - 多用户隔离
   - 工具执行安全
   - 状态持久化
   - 流式响应
   - 文件系统边界
   - 部署环境差异

3. **你如何验证系统可靠性**
   - 单元测试
   - workspace 路径逃逸测试
   - shell 黑名单测试
   - API 集成测试
   - 前端构建验证
   - Docker/服务器部署验证

---

## 3. 当前项目短板

### 3.1 Agent 内核有雏形，但治理能力还不够硬

当前已经有 LangGraph、tools、memory、subagent、background task 等能力，但生产级差距主要在：

- Shell 安全仍偏黑名单，不够强。
- 工具确认缺少完整审计链路。
- 任务执行后缺少统一 diff/test/report 闭环。
- 子 Agent 和 team 能力还偏原型，面试中不宜过度包装。

### 3.2 平台业务较完整，但“可展示闭环”不足

已有 auth、chat、workspace、memory API，也有 Vue 前端。但从简历角度，需要一个清晰 demo 闭环：

1. 用户登录。
2. 创建/进入 workspace。
3. 让 Agent 阅读代码。
4. Agent 修改文件。
5. 用户确认敏感工具。
6. 系统展示 diff / 文件变化 / 测试结果。
7. 记录审计日志和任务摘要。

当前前 4 步较明显，后 3 步需要加强。

### 3.3 部署叙事还需要更扎实

项目定位是企业内网部署，因此必须能讲清楚：

- 本地 macOS 开发怎么跑。
- Linux 服务器怎么部署。
- MySQL / Redis / Chroma 数据如何持久化。
- workspace 挂载在哪里。
- Web VSCode / code-server 怎么打开服务器文件。
- API key 和 JWT secret 如何管理。

---

## 4. 优化优先级

### P0：先保证项目能稳定跑通

目标：让你在 Mac 上可以稳定开发，并能给面试官展示基础闭环。

#### 任务 1：本地开发环境标准化

要做：

- 提供 `.env.example` 和 `.env.local.example`。
- 明确 macOS 本地使用 `WORKSPACE_BASE=./workspaces`。
- 明确服务器使用 `WORKSPACE_BASE=/workspaces`。
- README 增加“一键启动顺序”。
- 补充依赖安装说明：`python3 -m uv sync`、`npm install`。

验收标准：

```bash
python3 -m uv run pytest tests/core/tools/test_shell.py tests/api/test_workspace_open_url.py
cd frontend && npm run build
```

能通过。

简历价值：

- 体现跨平台开发与部署意识。
- 体现工程可维护性。

#### 任务 2：Docker Compose 可运行

要做：

- 修复 Dockerfile。
- 补全 api、mysql、redis、chroma 或本地 Chroma 持久化配置。
- 增加 volumes：
  - MySQL 数据
  - Redis 数据
  - Chroma 数据
  - workspace 数据
- 增加健康检查。

验收标准：

```bash
docker compose -f docker/docker-compose.yml config
docker compose -f docker/docker-compose.yml up -d
curl http://localhost:8000/health
```

`/health` 至少能显示 MySQL、Redis 状态。

简历价值：

- 能说“支持 Docker Compose 一键部署”。
- 后端岗位很看重部署闭环。

---

### P1：做出简历最有辨识度的安全治理能力

目标：让项目区别于普通 LangGraph demo。

#### 任务 3：工具调用审计日志

要做：

新增 `ToolAuditLog` 表，记录：

- user_id
- session_id
- tool_name
- args_summary
- result_summary
- status
- requires_confirmation
- approved_by_user
- started_at
- finished_at
- duration_ms

建议文件：

- `enterprise_agent/models/tool_audit.py`
- `enterprise_agent/core/agent/tools/audit.py`
- `enterprise_agent/api/routes/audit.py`

验收标准：

- 每次 shell、write_file、edit_file、delete、subagent 调用都能落库。
- 前端或 API 可查询最近 N 条工具调用。

简历表达：

> 设计工具调用审计链路，记录 Agent 执行命令、文件写入和敏感操作确认结果，支持按用户和会话追踪，增强企业场景下的可观测性与合规性。

#### 任务 4：敏感工具确认闭环

要做：

- 明确哪些工具需要确认：
  - shell
  - write_file
  - edit_file
  - delete
  - move
  - spawn_teammate
- 确认请求中展示：
  - 工具名称
  - 参数摘要
  - 风险等级
  - 预期影响
- 用户批准后继续执行。
- 用户拒绝后 Agent 需要换方案或解释。

验收标准：

- 前端能展示确认卡片。
- 后端能 resume 流式会话。
- 审计表能记录 approve/reject。

简历表达：

> 实现 Agent 敏感工具的人机协同确认机制，避免模型直接执行高风险命令，提升企业内网代码助手的安全边界。

#### 任务 5：Shell 执行安全升级

当前黑名单不够。建议演进为“策略 + 沙箱”：

第一阶段：

- 按命令类型分级：
  - safe：`ls`, `pwd`, `cat`, `python -m pytest`
  - review：`pip install`, `npm install`, `git`
  - dangerous：`rm`, `chmod`, `sudo`, `curl | sh`
- 对 review/dangerous 必须确认。

第二阶段：

- shell 命令只能在 workspace 内执行。
- 超时、输出截断、退出码结构化。
- 禁止访问系统敏感目录。

第三阶段：

- 引入容器级执行隔离。

简历表达：

> 将 Agent shell 工具从简单黑名单升级为分级策略控制，结合 workspace 限制、超时控制和人工确认，降低模型执行高危命令的风险。

---

### P2：补齐工程任务闭环

目标：让 Agent 不只是“能聊天”，而是能完成工程任务。

#### 任务 6：Git diff 展示与任务报告

要做：

- Agent 修改文件后，自动生成变更摘要。
- 提供 API 获取当前 workspace 的 git diff。
- 前端展示：
  - 修改文件列表
  - 每个文件修改摘要
  - 测试结果
  - 风险提示

验收标准：

```bash
git diff --stat
git diff -- path/to/file
```

能通过 API 或工具返回。

简历表达：

> 实现 Agent 任务完成后的 diff 汇总与验证报告，帮助用户理解模型修改范围和潜在风险。

#### 任务 7：测试命令模板

要做：

- 每个 workspace 支持配置测试命令。
- 例如：
  - Python: `python3 -m pytest`
  - Frontend: `npm run build`
  - Lint: `ruff check`
- Agent 完成修改后可主动运行相关测试。

建议配置文件：

```text
.agent/project.json
```

示例：

```json
{
  "test_commands": [
    "python3 -m uv run pytest",
    "python3 -m uv run ruff check enterprise_agent tests",
    "cd frontend && npm run build"
  ]
}
```

简历表达：

> 支持项目级验证命令模板，使 Agent 在完成代码修改后自动运行测试和构建，形成“修改-验证-报告”的工程闭环。

---

### P3：提升记忆系统质量

目标：把 memory 从“聊天摘要”变成“工程知识库”。

#### 任务 8：记忆分类与写入质量门

记忆类型建议：

- `project_fact`：项目事实，如启动方式、目录结构、部署路径
- `task_summary`：任务结果，如改了什么、验证了什么
- `decision`：架构决策，如为什么使用 RedisSaver
- `user_preference`：用户偏好，如喜欢先计划再实施

写入规则：

- 不保存无意义闲聊。
- 不保存完整工具输出。
- 不保存敏感信息。
- 不保存 API key。
- 每条记忆必须有类型、来源、时间、置信度。

验收标准：

- Memory API 可按类型查询。
- Agent 能检索过去项目事实。
- 记忆结果不会污染 prompt。

简历表达：

> 将长期记忆从普通对话摘要升级为工程知识结构化存储，按项目事实、任务结果和架构决策分类，提高 Agent 在长期项目中的上下文连续性。

---

### P4：前端展示与面试 Demo

目标：面试时能展示一个完整工作台，而不是只看后端接口。

#### 任务 9：工作台首页优化

前端应突出四块：

- Chat：与 Agent 对话
- Workspace：文件树和文件预览
- Tool Calls：工具执行记录
- Memory：项目记忆

建议增加：

- 当前 session 状态
- 当前 workspace 路径
- 最近工具调用
- 最近测试结果
- 风险确认弹窗

简历表达：

> 实现 Vue 3 工程工作台，集成 Agent 对话、workspace 文件管理、工具调用状态和项目记忆展示，提升 AI 编程任务的可观测性。

---

## 5. 推荐 6 周执行计划

### 第 1 周：Mac 本地开发与部署底座

目标：

- Mac 本地完整跑通。
- Docker Compose 基础可用。
- README 写清楚启动流程。

交付物：

- `.env.example`
- `docker-compose.yml`
- `/health`
- 本地启动文档

### 第 2 周：工具审计与确认机制

目标：

- 工具调用落库。
- shell/write/edit 等敏感工具有确认记录。

交付物：

- `ToolAuditLog`
- audit API
- 工具调用测试

### 第 3 周：工程任务闭环

目标：

- Agent 修改后能展示 diff。
- 能运行测试命令并记录结果。

交付物：

- diff API
- test command config
- task report

### 第 4 周：记忆系统重构

目标：

- 长期记忆分类。
- 降低记忆污染。

交付物：

- memory schema
- 类型过滤
- 检索测试

### 第 5 周：前端可展示 Demo

目标：

- 登录后能演示完整 Agent 工作流。
- 展示工具调用、文件变化、测试结果。

交付物：

- ToolCall 面板
- Memory 面板
- Workspace 文件管理优化

### 第 6 周：简历包装与面试准备

目标：

- 整理项目 README。
- 补架构图。
- 准备面试讲稿。
- 准备常见追问答案。

交付物：

- 简历项目描述
- 架构图
- Demo 脚本
- 面试 Q&A

---

## 6. 简历 bullet 建议

可以从以下 bullet 中挑 3-4 条放进简历：

- 设计并实现基于 FastAPI + LangGraph 的企业内网工程 Agent 平台，支持 SSE 流式对话、多模型接入和 Redis 会话状态持久化。
- 实现多用户 workspace 隔离机制，统一约束文件读写、上传下载和 shell 执行路径，防止路径穿越和跨用户访问。
- 构建 Agent 工具治理体系，对 shell、文件写入、子 Agent 等敏感工具增加确认流和审计记录，提升企业场景下的可控性。
- 接入 ChromaDB 长期记忆，将任务摘要、项目事实和用户偏好结构化存储，支持 Agent 在长期项目中检索历史上下文。
- 完成 Vue 3 工程工作台，集成对话、文件树、工具调用状态和项目记忆展示，形成浏览器端 AI 编程工作流。
- 支持 Docker Compose 私有化部署，集成 MySQL、Redis、Chroma 和可配置 LLM endpoint，适配企业内网部署场景。

---

## 7. 面试讲述结构

建议按 STAR 结构讲：

### S：背景

现有 Claude Code、Cursor 等工具多运行在个人电脑或外部 SaaS 上，企业内部代码存在安全和权限治理需求。

### T：目标

做一个部署在企业内网服务器上的工程 Agent 工作台，让开发者通过浏览器使用 Agent，同时平台统一管理 workspace、工具权限、模型和审计。

### A：行动

重点讲 4 个模块：

1. LangGraph Agent 状态机
2. workspace 路径隔离
3. 工具确认和审计
4. 记忆与任务总结

### R：结果

用具体结果说话：

- 支持多用户登录和 session 管理。
- 支持文件树、文件读取、上传下载。
- 支持 Agent 调用 shell、文件读写、后台任务。
- 支持流式响应。
- 支持本地 Mac 开发和 Linux 服务器部署配置。
- 测试覆盖核心 workspace 和工具逻辑。

---

## 8. 高频面试追问准备

### Q1：为什么不用现成 Claude Code / Cursor？

答：

Claude Code 和 Cursor 更偏个人开发者工具，而这个项目关注企业内网部署和权限治理。核心差异是代码不出内网、Agent 在服务器 workspace 中受控运行、敏感工具可确认和审计。

### Q2：Agent 会不会乱执行危险命令？

答：

当前通过 workspace 限制、命令校验、敏感工具确认和超时控制降低风险。后续会把黑名单升级为策略引擎，并将高风险命令放入隔离容器执行。

### Q3：多用户如何隔离？

答：

每个用户对应独立 workspace，例如 `/workspaces/user_<id>`。所有文件 API 和 Agent 工具都通过统一的 `resolve_path()` 做路径解析，确保不能逃逸到其他用户目录。

### Q4：为什么用 Redis？

答：

Redis 用于短期状态和 LangGraph checkpointer，适合保存会话状态、流式中断恢复和临时上下文。长期工程记忆则放在 ChromaDB。

### Q5：记忆系统如何避免污染？

答：

不应保存所有对话，而是按类型保存高价值信息，例如项目事实、任务结果、架构决策和用户偏好，并设置重要性评分和写入质量门。

### Q6：这个项目你最大的技术难点是什么？

建议回答：

不是 LangGraph 调起来，而是如何让 Agent 的工具执行在多用户 Web 平台中保持安全边界。比如路径隔离、敏感工具确认、状态持久化、审计和部署环境差异，这些才是工程难点。

---

## 9. 不建议投入太多时间的方向

秋招前时间有限，以下方向先不要重投入：

- 从零训练模型。
- 自研向量数据库。
- 做非常复杂的多 Agent 团队协作。
- 做花哨但无工程深度的前端动画。
- 接太多模型 provider，却没有稳定主链路。

更应该把一个主链路做深：

```text
登录 -> 进入 workspace -> Agent 读代码 -> 申请执行工具 -> 用户确认 -> 修改文件 -> 运行测试 -> 生成报告 -> 审计落库
```

这条链路跑通，比堆十个松散功能更适合秋招。

---

## 10. 最终建议

这个项目适合包装成 **后端 + AI Agent 平台工程** 项目，而不是单纯 AI 应用项目。

你作为研一学生，简历上最需要证明的是：

- 能把 LLM 应用落到真实工程系统里。
- 理解后端服务、数据库、缓存、权限和部署。
- 知道 Agent 工具执行的风险和治理方式。
- 能把一个原型打磨成可演示、可部署、可维护的项目。

下一步最推荐做：

1. Docker Compose 完整跑通。
2. 工具调用审计表。
3. 敏感工具确认闭环。
4. diff/test/report 工程任务闭环。
5. README + 架构图 + Demo 脚本。

完成这 5 件事，这个项目就会从“我做了一个 Agent”升级成“我做了一个企业可控工程 Agent 平台”，简历含金量会高很多。
