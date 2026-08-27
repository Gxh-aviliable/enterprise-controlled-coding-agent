# 项目文档导航

本目录只保留当前可运行版本仍然需要的说明、评测证据和开发记录。已经完成的旧实施计划、失效的代码阅读指南、原始调试转储和重复报告均已从当前分支移除；如需追溯，可通过 Git 历史查看。

## 从这里开始

新开发者建议按以下顺序阅读：

1. [项目 README](../README.md)：产品定位、真实指标、快速启动和已知边界。
2. [后端 Agent 从零精读指南](PROJECT-WALKTHROUGH.md)：以“定位 Bug → 修改文件 → 执行测试 → 汇报结果”为主线，通过连续代码块逐层读懂 FastAPI、AgentState、LangGraph、模型/工具循环、HITL、验证门、记忆、Trace 与评测。
3. [系统架构](../ARCHITECTURE.md)：组件边界、状态机、数据流、安全和部署设计。
4. [能力矩阵](capability-matrix.md)：哪些能力已验证、部分完成或仍待测。
5. [Benchmark 说明](../benchmarks/README.md)：评测数据、复现方法和解释边界。
6. [求职展示版 v1.0 证据清单](release-evidence/portfolio-v1.0.md)：候选源码 commit、验证命令、正式评测产物与发布边界。

这六份文档构成当前项目事实的主要入口。如果它们与历史开发日志冲突，以代码、自动化测试和最新 benchmark 原始产物为准。

## 按目标阅读

| 目标 | 文档 |
|---|---|
| 10 分钟启动项目 | [README 快速开始](../README.md#快速开始) |
| 从零精读后端 Agent 主链路 | [后端 Agent 从零精读指南](PROJECT-WALKTHROUGH.md) |
| 理解任务状态与工具治理 | [系统架构](../ARCHITECTURE.md) |
| 查看真实测试和评测结果 | [能力矩阵](capability-matrix.md)、[Benchmark](../benchmarks/README.md) |
| 核对候选 commit 与发布证据 | [求职展示版 v1.0 证据清单](release-evidence/portfolio-v1.0.md) |
| 部署到 Linux 服务器 | [远程服务器部署](remote-server-deployment.md) |
| 滚动下线 Pause/Continue | [Cancel-and-Replan 两阶段迁移](cancel-and-replan-rolling-migration.md) |
| 理解长期记忆准入与召回 | [长期记忆治理](memory-governance.md) |
| 理解管理员权限与安全边界 | [管理员控制台](admin-console.md) |
| 准备 3～5 分钟面试演示 | [演示脚本](demo-script.md) |
| 准备简历和面试追问 | [作品集与面试指南](portfolio-guide.md) |
| 查看当前秋招优先级 | [秋招冲刺清单](../00-秋招冲刺-必读.md) |
| 查看数据库迁移方式 | [Migration 说明](../migrations/README.md) |
| 查看测试结构和命令 | [测试说明](../tests/README.md) |

## 当前可复现证据

当前自动化验证更新于 2026-08-28；下列评测产物于 2026-08-27（UTC）在候选源码 commit `1d637c5753e93c72989c3fdae2ab5edf50e078eb` 上生成：

| 证据 | 结果 | 解释边界 |
|---|---:|---|
| 后端 pytest | 731 passed | 单元、API、任务状态机、Stop 闭环、工具、Workspace 安全写入、上下文、记忆和 Trace 回归 |
| 前端 Vitest | 97 passed | 关键交互、SSE 控制流、Stop/HITL、Preview/Edit、冲突和未保存导航防护回归 |
| Ruff | 0 findings | 当前配置覆盖的 Python 静态检查 |
| Platform benchmark | 30/30 | 工具成功率 89.39%；确定性工具、状态、策略和评测器，不是模型智能分 |
| Memory benchmark | 6/6 | 小型合成集的检索与过滤，不证明模型正确采用记忆 |
| DeepSeek single-Agent v2 | 23/30 | `deepseek-v4-flash`：easy 7/10、medium 10/10、hard 6/10；工具成功率 77.53%，平均 16.840 s / 53,128.93 token，HITL 80.0%，安全拦截 41，基础设施/系统错误均为 0 |
| Multi-Agent 对照 | 待测 | 不宣称多 Agent 优于单 Agent |

对应原始产物：

- [Platform Markdown](../benchmarks/results/20260827T182126Z-platform-single.md) /
  [Platform JSON](../benchmarks/results/20260827T182126Z-platform-single.json)
- [Memory Markdown](../benchmarks/results/20260827T182146Z-memory-recall.md) /
  [Memory JSON](../benchmarks/results/20260827T182146Z-memory-recall.json)
- [Agent v2 Markdown](../benchmarks/results/20260827T181517Z-agent-single.md) /
  [Agent v2 JSON](../benchmarks/results/20260827T181517Z-agent-single.json)
- [求职展示版 v1.0 证据清单](release-evidence/portfolio-v1.0.md)

当前正式 Agent 分数是完整 30 题、干净工作树上的 23/30，共保留 7 项失败。随后针对部分失败执行的两次 5 题诊断复跑使用 dirty worktree，且 `official.valid=false`，只能分析随机性和失败形态，不能替代或重算正式分数。旧 25/30、旧 Platform 10/10 和 v1 8/10 仅作为历史记录，不再代表当前候选版本；其中 v1 与 v2 的题集和 evaluator 也不严格同口径。Stop/Cancel 协议本身由确定性 API、状态机、Redis 与进程终止回归证明；v2 中的 cancel-and-replan 用例只补充模型行为证据，不替代协议测试。

## 历史记录的使用方式

`docs/dev-logs/` 是按日期追加的开发快照，保留当时的判断、测试结果和未完成事项，用于审计“项目如何演进”。旧日志不会因为后续实现变化而重写，因此：

- 可以用于追踪决策和问题修复过程；
- 不应拿旧测试数量或旧待办判断当前能力；
- 不应从旧日志复制已经被替换的命令、接口或架构描述；
- 当前结论必须回到代码、README、ARCHITECTURE 和最新评测产物核验。

## 文档维护规则

- README 负责对外展示，ARCHITECTURE 负责当前技术事实，CHANGELOG 负责版本变化。
- 同一指标只引用原始 benchmark 或自动化测试结果，不人工编造。
- 未真实执行的能力统一写为“待测”，不能从代码存在推导为效果已证明。
- 已实施完毕的临时计划不继续保留在当前文档树中，Git 历史承担恢复职责。
- 修改公开行为、架构、验证结果或部署方式时，同步更新相关文档和当天开发日志。
