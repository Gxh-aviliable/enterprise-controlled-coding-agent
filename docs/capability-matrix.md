# 当前能力矩阵

当前架构更新日期：2026-08-19；下方原有量化基线和真实模型 benchmark 仍以各自保留产物的日期为准。`已验证` 表示存在当前自动化或真实运行证据；`部分完成` 表示实现存在，但生产边界或效果证据仍不完整。

| 领域 | 能力 | 状态 | 当前证据 | 主要边界 |
|---|---|---:|---|---|
| Agent | LangGraph 有状态工具循环 | 已验证 | 显式阶段、图/节点测试、Agent 8/10 | 真实仓库任务集仍较小 |
| Agent | 失败重试与恢复 | 已验证 | 模型重试、幂等工具策略、确认恢复和 benchmark recovery | 未做跨进程 crash chaos |
| Agent | 修改后验证门 | 已验证 | lifecycle、节点、benchmark 用例 | 自动确认续跑仍有一个真实失败 |
| Agent | token/轮次/工具预算 | 已验证 | 配置、节点、Trace 和测试 | 100 万 token 是上限，不代表模型上下文一定支持 |
| Agent | 工具输出裁剪与受限证据恢复 | 已验证 | artifact-first、SHA-256 receipt、分页读取、microcompact、transcript、reducer 回归 | artifact 是脱敏/限长的 Workspace 调试证据，非防篡改集中审计库 |
| HITL | 敏感工具中断、批准、拒绝、超时恢复 | 已验证 | typed `tool_confirmation` interrupt、Redis checkpoint、token-owned resume lock、API、前端和超时测试 | 这是唯一可 `Command(resume)` 的中断，并保持原 Trace |
| Task Control | Stop/Cancel 与新 Trace 重新规划 | 已验证 | Redis active lease、cancel tombstone、runner fence、continuation receipt、API/前端取消失败回归 | Stop 是终态且不回滚副作用；只有服务端确认 cancelled 才解锁新 Trace |
| 工具 | 文件读写编辑和可恢复删除 | 已验证 | 原子写入、敏感路径、穿越、恢复 manifest 测试 | 不是文件系统级沙箱 |
| 工具 | Shell 与后台任务 | 部分完成 | 复合解析、风险分级、环境净化、超时、前台进程组取消和托管后台 Trace 终止测试 | 仍是用户态策略防护；无法抢占的操作为 best-effort |
| 工具 | Todo、任务板和上下文工具 | 已验证 | 工具级与生命周期回归 | 文件任务板不适合多副本 |
| 工具 | Shared / personal Skill | 已验证 | loader、版本治理、管理员 API 与测试 | 未实现签名和供应链证明 |
| Multi-Agent | specialist 委派 | 部分完成 | 工具和权限测试；旧子循环只读并拒绝 review/dangerous 工具 | single/multi 三用例对照待测，写入仍回主 Agent |
| Memory | Redis checkpoint | 已验证 | 图配置、HITL 恢复/归属 API 测试 | Redis 不是永久聊天正文，取消后的新任务不 resume 旧图 |
| Memory | MySQL 持久聊天 | 已验证 | history persistence、continuation receipt、迁移、去重裁剪与刷新回归 | 生产备份恢复演练待补 |
| Memory | Chroma 长期记忆治理 | 已验证 | 准入、Legacy、召回、级联删除和 6/6 评测 | 模型是否正确应用记忆未归因 |
| Auth | JWT、刷新、撤销和实时角色 | 已验证 | 认证、角色降级、禁用和会话归属测试 | 尚无企业 SSO/项目 RBAC |
| Isolation | 用户 Workspace 与路径边界 | 已验证 | 工具/API 穿越和敏感路径测试 | Shell 仍需 rootless 容器 |
| Workspace UI | 安全 Preview/Edit 与并发保护 | 已验证 | read SHA-256、原子 write、409 冲突、前端 dirty/快捷键/导航防护测试，以及 Markdown Preview/Edit/Discard 浏览器 smoke | 仅既有、至多 1 MiB 的普通 UTF-8 文件；敏感/Agent operational/symlink 拒绝；实机 smoke 未保存测试草稿 |
| Observability | 统一 Trace 与回放 | 已验证 | 模型/节点/工具/HITL/Cancel/预算事件和 UI 测试；历史 paused 事件只读兼容 | JSON 后端是单进程基线 |
| Observability | 六类核心指标 | 已验证 | `/tasks/metrics` 与 Agent/Platform 报告 | 尚无集中监控告警 |
| Evaluation | 后端/前端自动化 | 已验证 | 561 后端、77 前端、Ruff 0 findings | 浏览器 E2E 仍主要为手工 smoke；本轮 Preview/Edit 已完成不落盘草稿验证 |
| Evaluation | Platform benchmark | 已验证 | 10/10、80.0% 工具成功率 | 不代表模型智能 |
| Evaluation | Agent single benchmark | 已验证 | DeepSeek 8/10，基础设施错误 0 | 仅 10 个合成用例和一次保留运行 |
| Evaluation | Multi-Agent 对照 | 待测 | 三个用例已标记适合委派 | 不宣称多 Agent 收益 |
| Deployment | 四服务 Docker Compose | 已验证 | 健康检查、持久卷、Nginx、非 root API | 生产 TLS、备份、密钥管理需运维落地 |
| Admin | 用户、额度、授权、Skill、审计 | 已验证 MVP | API、模型、迁移、Vue 和安全测试 | 完整 RBAC/双人审批待补 |

## 当前允许使用的量化结论

- 后端：561 passed。
- 前端：77 passed。
- Platform：10/10，平均 84.8 ms，工具成功率 80.0%，人工介入率 20.0%。
- Memory：6/6，Recall@3、Precision@3、MRR 在该小型数据集上均为 100%。
- DeepSeek single-Agent：8/10，工具成功率 82.9%，平均 5.285 s，平均 19,339.9 token，人工介入率 50.0%，安全拦截 6，基础设施错误 0。

## 不能使用的夸大表述

- 不能把 Platform 10/10 写成 Agent 任务成功率。
- 不能把 Memory 6/6 写成模型正确使用记忆。
- 不能把用户态 Shell 策略写成“安全沙箱”。
- 不能声称 Multi-Agent 优于 single-Agent。
- 不能用 10 个合成任务推导生产通用能力。
