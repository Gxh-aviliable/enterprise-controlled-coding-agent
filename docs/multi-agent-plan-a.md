# 方案 A：主 Agent 修改，子 Agent 并行只读探索

## 实施计划（2026-09-11）

1. 保留现有未提交依赖，从 develop 创建 `feature/multi-agent-plan-a`。基线备份：系统临时目录 `mini-agent-plan-a-baseline-or6cdmbc`，含源码归档、补丁和 SHA-256 清单。
2. 统一 `delegate_task` 的分析/探索入口和结构化终态，隐藏旧 team 工具；保留 `task` 作为显式兼容适配，不重新暴露给模型。
3. 实现父 trace 下的子执行记录、幂等结果、预算、取消与恢复。在线使用 Redis 并校验父 runner fence；无在线 runner 的离线基准使用显式本地记录适配，不在在线 Redis 故障时降级。
4. 探索工具采用服务端过滤的不可变文件快照，只开放读取、列目录和文字搜索。方案 A **不提供子 Shell**，因此无需把 SAFE Shell 冒充只读；测试与 Shell 仍由主 Agent 经现有权限、HITL 和容器执行。
5. 仅并行经过审批的连续独立委派，默认最多 2 个；写操作是串行边界。父图统一合并结果、Token 和 ToolMessage，保留既有验证/记忆流程。
6. 增加真实子事件、父子标识、状态、用量和历史回放，屏蔽子模型原始流混入主回复。
7. 补充失败/无结果/预算/只读/跨会话/重复/重启/取消/乱序回放测试；执行后端和前端回归、构建、Docker 回归及配置校验；如模型或 Docker 不可用，明确记录，不虚报验收。

## 决策和范围

- 使用现有 FastAPI、LangGraph、Redis、Trace 和 Vue，不另建多 Agent 平台。
- 分析只接收给定材料；探索能力取父文件权限与服务端只读工具的交集，模型角色名不授予权限。
- 文件快照属于可信 API 内的受限读取边界，未宣称 Python 工具在容器内。子 Agent 无进程入口、无依赖安装、无网络工具、无工作区发布能力。
- 在线子任务幂等键由用户、会话、trace、调用 ID 派生，旧任务结果不能跨 trace 注入。重启不恢复内存协程：过期/旧 runner 的执行记录明确 interrupted，主任务显式重试。
- 本轮仅本地开发，不修改服务器，不提交或推送既有改动。

## 验收及实际结果

实施完成后在此补充结果；详细原始问题见 `multi-agent-code-audit.md`。

## 已实现的运行机制

- `core/execution/children.py`：统一请求/结果、有界调度、取消监测、父权限交集、子 Token/工具次数分配及结果收齐。子消息保持 System/Human/AI/Tool 配对，角色和任务使用 JSON 数据，模型思考块不作为可见结果。
- `child_workspace.py`：从当前用户授权目录构建过滤后的不可变内存快照，排除平台 operational 文件、敏感路径、符号链接/硬链接/特殊文件。一个并行批次共享同一快照 ID；子工具仅有 `read_file/list_files/search_files`，返回分页/截断标记，不开放 Shell、写文件或网络工具。
- `child_store.py`：在线 Redis 收据以用户/session/trace/tool_call_id 派生子 ID，Lua 原子认领并校验父 lease/runner，终态不可覆盖。Stop 与成功落盘竞争时以取消为准。在线 Redis 故障明确失败；离线 graph/benchmark 使用独立文件收据，无生产静默降级。
- `nodes.py`：只有审批通过且在工具预算内的连续 `delegate_task` 批次会并行。批次结果由父执行器按调用顺序合并；每个调用都有 ToolMessage。写入必须发生在委派结果返回主模型后的后续批次，不能同轮预先猜测子结果并修改。真实结构化子结果取代“非错误字符串即成功”。
- Token 记入父任务/会话累计成本，内层工具计入父任务工具次数；上下文窗口估算保持独立。子任务最多分得剩余工具次数的一半，以保留主 Agent 修改和验证空间；每名子任务上限 8 次，仍受总任务预算约束。
- `api/main.py`：启动时 CAS 回收过期/旧 runner 的未完成记录，不影响其他活跃 worker；退出时取消并等待子协程。只读快照和工具线程工作在取消后也会等待完成，不将线程取消误当成进程终止。
- SSE 通过 LangGraph custom stream 提供真实 queued/running/terminal 事件，过滤带 child_id 的子模型文本；子卡片带父调用 ID、单调事件序号，实时和持久化历史均拒绝旧事件覆盖新状态。Trace 展示父子 ID、快照版本、模型/工具事件和用量。
- 旧 `task/spawn_teammate/...` 不再绑定给模型；旧 checkpoint 中的调用明确拒绝，不偷偷转成新语义。直接调用无治理上下文的委派包装器返回 `child_scope_required`。旧队友管理器另外修复同名引用覆盖及用户内跨会话缓存；`claim_task` 增加互斥和 owner/status/dependency 检查，但仍不是执行队列。

## 使用方式

本地 `.env` 当前已开启 `ENABLE_MULTI_AGENT=true`。新环境需显式开启并具备 `tools:advanced`（探索还需要文件权限）；默认配置仍保留 Single 基线。

```dotenv
ENABLE_MULTI_AGENT=true
CHILD_MAX_CONCURRENCY=2
CHILD_TIMEOUT_SECONDS=120
CHILD_TOKEN_BUDGET=64000
AGENT_EXECUTOR=docker
```

重启本地后端并刷新前端，在输入区选 **Multi**。示例：

> 请让两个子 Agent 分别检查实现和测试，汇总发现后由主 Agent 修复问题并运行测试。

模型入口为 `delegate_task(role, prompt, profile="analysis" | "explore")`。analysis 分析已给材料；explore 读取授权工作区快照。模型应给子任务明确目标和已知路径，在足够证据后结束，避免重复扫描浪费预算。

若上次任务是在旧版本暂停审批，建议取消后新建请求，避免继续执行已退休的 team/task 调用。这里没有迁移或删除旧聊天历史。

## 验证结果（2026-09-11）

| 验证 | 实际结果 |
| --- | --- |
| 后端常规回归 | 762 passed；6 个真实 Redis 用例在常规运行中跳过，14 个 Docker 用例单独执行 |
| 真实 Redis | 6 passed；使用独立临时 Redis 容器，验证原子幂等、晚到写入 fence、Stop 竞争、跨会话不干扰、恢复不影响活跃 worker、故障不降级 |
| 真实 Docker 沙箱 | 14 passed；验证既有主 Shell 的隔离、禁网、资源/输出限制、取消、回收、文件共享边界 |
| 最终事件序号改动的针对性回归 | 98 passed；包含子 SSE、历史、并行、权限、结果和取消 |
| Vue 测试 | 99 passed |
| 前端构建 / Ruff / git diff --check / Compose config | 通过 |
| 真实模型与主容器执行演示 | `deepseek-v4-flash`，17.009 秒，任务及全部断言 passed；两个探索子任务成功且时间重叠，共享快照；主编辑在两个子任务完成之后，容器 pytest 成功 |

真实模型演示复用已有 benchmark 的 `easy.edit.fix_subtract` 合成夹具和检查器，增加明确的双委派提示。主图实际经过审批 interrupt/resume（演示驱动自动批准该合成任务），不是把假工具结果当作模型执行。隐藏测试保护原测试文件并检查修改范围。记录中总 Token 为 43,578（提供者与框架统计），这仅是一次诊断演示，不是效率收益结论。

其中一次调试运行将演示总工具预算设为 15，两个子任务探索加主读取用尽预算，编辑被拒，任务正确失败。该失败保留在证据文件中；最终演示回到项目默认总预算 25，指定已知路径减少重复探索，并保留父流程预算，没有放宽 Shell/文件安全策略。另一次早期演示遇到 macOS `/var` 别名路径被发布检查拒绝，演示脚本改用 canonical 临时目录；未修改沙箱路径安全规则。

### 可复现命令

```bash
# 常规回归（真实服务测试另跑）
.venv/bin/python -m pytest -q -m 'not docker_sandbox'
# 前端（在 frontend 目录）
npm test
npm run build
# 配置和静态检查（仓库根目录）
.venv/bin/ruff check enterprise_agent tests scripts/plan_a_demo.py
docker compose -f docker/docker-compose.yml config --quiet
# 真实 Docker，需要已有执行镜像及 Docker daemon
RUN_DOCKER_SANDBOX_TESTS=1 .venv/bin/python -m pytest -q tests/sandbox/test_docker.py
# 真实模型演示：使用配置的模型与 Docker；只发送合成夹具
PYTHONPATH=. .venv/bin/python scripts/plan_a_demo.py
```

Redis 测试需启动**专用可丢弃实例**，如：

```bash
docker run -d --rm --name mini-plan-a-redis-test -p 127.0.0.1:16379:6379 redis/redis-stack:latest
CHILD_TEST_REDIS_URL=redis://127.0.0.1:16379/0 .venv/bin/python -m pytest -q tests/core/execution/test_children_redis.py
docker rm -f mini-plan-a-redis-test
```

若端口被占用，选择空闲本机端口。不要将 `CHILD_TEST_REDIS_URL` 指向业务实例，测试会创建专用租约及子记录。

### 证据

- `release-evidence/plan-a-regression-tests.xml`
- `release-evidence/plan-a-redis-tests.xml`
- `release-evidence/plan-a-docker-tests.xml`
- `release-evidence/plan-a-real-model-demo.json`（含可重算的时间重叠、共享快照和写入先后检查）
- `release-evidence/plan-a-real-model-demo-budget-failure.json`（保留预算失败）

## 限制与排障

- 子 Agent 是独立模型上下文和只读能力边界，**不是每个子 Agent 一台独立容器**。只有主 Agent 的 Shell 经现有容器执行；Python 文件读取仍在可信 API 内。模型提供者访问由平台控制，不能把“没有子网络工具”说成平台不调用外部模型。
- 快照可保证同批子 Agent 看同一版本；用户后续编辑不会改变已读证据。主 Agent 应在修改前复核当前文件。宿主管理员、非协作外部编辑器与底层文件系统/内核仍在现有信任边界内，容器限制亦不代表绝对安全。
- 子协程不支持服务重启后原地续跑。收据保留到 checkpoint TTL；旧 running 被标记 interrupted，主模型以新调用 ID 显式重试。相同 ID 的已完成结果复用，已失败结果不会隐式再花模型费用。
- 模型 Token 无 usage 时使用标明的字符估算；即使有 usage，预算也是请求前估算与响应后计量，不能承诺提供者账单零超额。取消模型请求也不能证明提供者没有发生费用。
- `child_file_permission_denied`：父用户缺少文件能力；`child_scope_required`：从治理执行器外调用；`child_persistence_failed`：Redis/父 fence/收据终态竞争，任务不能视为成功；`child_already_running/child_interrupted`：重复调用或旧执行已失效。
- `child_token_budget/child_tool_budget/child_round_budget`：缩小探索目标，让主 Agent 使用已获得的证据；不要盲目放大预算。`child_empty_result/child_incomplete_response` 表示没有完整可见结论，不能通过写入门槛。
- 当前真实演示是内存 checkpointer 的实际主图 + 实际模型 + Docker；HTTP/SSE 路由和 Vue 由自动化测试验证，未进行真实浏览器登录演示或杀掉整个 API 后的端到端恢复演示。真实 Redis 的重启恢复判定已单独验证。
- 六个困难用例的正式 Single/Multi 效果对照尚未执行，不宣称方案 A 提高成功率或更省 Token。旧官方 Single 基线和历史审查证据保持不变。

收尾兼容补验：旧工具调用使用 `tool_retired` 错误码，区别于真实权限不足。子执行/工具契约/API 三组测试补验 **86 passed**；不需要将用户升级为更高权限来调用已退休的工具。
