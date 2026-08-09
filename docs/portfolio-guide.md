# 作品集与面试指南

## 改造前后

| 维度 | 早期状态 | 当前状态 |
|---|---|---|
| 执行 | 响应式模型/工具循环 | 显式解析、规划、执行、检查点、验证、总结 |
| 状态 | 从消息推断完成 | 七态任务状态机、合法迁移和可恢复 `paused` checkpoint |
| 工具 | 字符串结果、粗粒度敏感列表 | Contract、权限、参数风险、HITL、规范化结果 |
| 安全 | 路径保护和简单命令匹配 | 用户隔离、复合命令解析、敏感路径、环境净化、原子写入、可恢复删除 |
| 恢复 | Redis checkpoint 但策略隐式 | HITL 确认、协作式 Pause/Continue、拒绝、超时、取消、失败收敛和验证门 |
| 观测 | 日志和 SSE 分散 | Trace ID 串联节点、模型、工具、审批、token 和终态 |
| 评测 | 只有模块测试 | Platform、Memory、Agent 三层版本化 benchmark |
| 部署 | 后端服务为主 | Vue/Nginx、API、MySQL、Redis 四服务 Compose |

## 当前量化结果

- 495 项后端测试、36 项前端测试、Ruff 0 findings。
- Platform benchmark 10/10；这是确定性平台分，不是模型智能分。
- Memory benchmark 6/6；这是小型检索/过滤分，不证明模型正确使用。
- `deepseek-chat` single-Agent 8/10，基础设施错误 0。
- Multi-Agent 三用例对照尚未运行，不宣称收益。

## 简历可用描述

项目描述：

> 基于 LangGraph 构建可自主理解代码库、拆解任务、调用工具、修改代码、运行验证并从失败中恢复的有状态 Coding Agent，并以 FastAPI + Vue 3 实现面向企业内网的权限、审批、隔离、恢复与审计控制面。

核心工作：

- 基于 `LangGraph StateGraph` 构建“代码检索 → Todo 规划 → 文件/Shell 工具执行 → 结果观察 → 失败诊断 → 修改后验证”的多轮 Agent 闭环；将非零退出、策略拦截和超时结构化反馈给模型，并通过 verification gate 阻止未验证代码被标记为成功。
- 使用 Redis checkpoint 持久化完整 Agent 状态，实现精确到用户/会话/Trace 的协作式 Pause/Continue、后台命令、轮次/工具/token 预算、工具输出 microcompact、完整 transcript 和 LLM 摘要续跑，并结合受准入控制的 Chroma 长期记忆与按需 Skill 加载支持长任务和工程经验复用。
- 设计 Contract-driven 工具运行时，统一文件、Shell、任务、记忆和子 Agent 的权限、风险、超时、幂等、副作用与结果协议，实现参数级 HITL、多租户 Workspace 隔离、凭据净化、原子写入和可恢复删除。
- 建立覆盖模型、节点、工具、暂停/审批、token、错误和终态的 Trace 与版本化 Agent benchmark；真实 `deepseek-chat` single-Agent 在代码理解、文件操作、测试、失败修复、安全拒绝和中断恢复等 10 个任务中完成 8/10，并保持 495 项后端和 36 项前端回归。

## 20 个常见追问与回答要点

1. **为什么使用 LangGraph？**
   节点和边让暂停、恢复、路由、checkpoint 和逐步 Trace 可测试；代价是状态 schema 和图迁移更复杂。

2. **它为什么是 Coding Agent 而不是聊天机器人？**
   成功标准包含仓库理解、计划、工具修改、可执行验证和证据汇报，而不只是生成文本。

3. **任务成功如何定义？**
   由合法终态和确定性断言定义；修改代码后没有成功测试、构建、Lint 或编译记录就不能成功。

4. **为什么需要七种状态？**
   `pending/running/paused/waiting_confirmation/succeeded/failed/cancelled` 分离排队、执行、用户暂停、人工审批和三种终态；`paused` 可恢复但 `cancelled` 不可恢复，避免 UI、Trace 与 checkpoint 互相矛盾。

5. **工具重试怎么防止重复副作用？**
   只有契约声明为幂等的只读工具可有限重试；文件写入、Shell 和协作副作用不会盲目重放。

6. **HITL 如何恢复？**
   LangGraph interrupt 保存 checkpoint；会话所有者批准或拒绝后，以 `Command(resume=...)` 从中断点继续。

   用户 Pause 使用另一种 typed `user_pause` interrupt，只能由同一用户、会话和 Trace 的 Continue 恢复，不会被 HITL 审批接口误消费。

7. **用户一直不确认怎么办？**
   超时任务用确定性拒绝恢复图并记录 Trace。多副本生产环境需要把调度迁移到持久队列。

8. **Approve All 为什么不能放行所有命令？**
   它只批准当前批次的 review 操作；dangerous 操作仍由执行器硬拦截，避免审批成为安全绕过。

9. **多租户隔离在哪里实现？**
   JWT 确定用户，MySQL 校验会话归属，所有文件路径解析到 `user_<id>`，API、工具、记忆和 Trace 都带用户边界。

10. **Shell 能完全限制在 Workspace 吗？**
    不能。当前会阻止绝对/穿越/敏感路径、嵌套 Shell、替换、inline code 和危险 Git，但它不是内核隔离；生产应使用临时 rootless 容器和 egress policy。

11. **为什么净化子进程环境？**
    防止模型 Key、JWT、数据库密码通过 `env`、脚本错误或工具输出泄漏回 Workspace 和模型上下文。

12. **为什么文件写入要原子化？**
    临时文件、`fsync` 和 `os.replace` 避免超时或崩溃留下半个源文件，并尽量保留原权限。

13. **Trace 记录什么？**
    请求摘要、阶段、节点、模型、工具、风险、耗时、token、重试、审批、预算、错误和最终结果。

14. **Trace 如何避免泄密？**
    保存有长度限制的摘要，递归脱敏敏感键和值，并按用户 Workspace 和 API 身份授权读取。

15. **为什么不用 LangSmith 作为唯一 Trace？**
    本地 Trace 可离线、可复现、供应商无关；当前 JSON 是适配器基线，生产再接集中 SQL、ClickHouse 或 OpenTelemetry。

16. **为什么任务 10/10 但工具成功率只有 80%？**
    recovery 用例故意先运行失败测试，安全用例故意拒绝工具；这些预期失败降低工具成功率，但最终任务断言仍通过。

17. **真实 Agent 结果是多少？**
    保留运行是 DeepSeek single-Agent 8/10。两个失败分别暴露自动审批续跑和安全断言口径问题，不是基础设施错误。

18. **为什么默认关闭 Multi-Agent？**
    委派增加 token、时延、协调失败和权限面。只有在适合的信息并行任务上通过对照实验，才值得开启。

19. **如何控制成本？**
    每任务/会话 token 预算、轮次和工具上限、工具输出截断、microcompact/full compact，以及 Trace 中的模型/工具用量指标。

20. **下一步最值得做什么？**
    修复并复测两个 single 失败，再完成三用例 single/multi 对照和 3～5 分钟演示；生产化才继续做任务容器、集中 Trace、SSO 和备份恢复。

## 表达红线

- Platform 10/10 必须带“确定性平台 backend”。
- Memory 6/6 必须带“小型合成检索集”。
- Shell 必须说“用户态策略防护”，不能说“安全沙箱”。
- Multi-Agent 必须说“实验性、待对照”，不能说“提升效率”。
- 真实失败要解释成改进证据，不能删除或包装成成功。
