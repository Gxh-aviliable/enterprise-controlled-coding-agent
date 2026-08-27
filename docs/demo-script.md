# 5 分钟作品集演示脚本

## 演示前准备

1. 使用固定 commit 和准备好的演示用户启动 Compose。
2. 在用户 Workspace 放入一个只有一处小 Bug、带失败测试的仓库。
3. 预先确认模型 endpoint 可用，并保留一次成功录屏。
4. 打开 Chat 和 Trace 页面。
5. 准备 Platform 10/10 与 [v2 DeepSeek V4 Flash 25/30 正式报告](../benchmarks/results/20260819T160324Z-agent-single.md)（[JSON](../benchmarks/results/20260819T160324Z-agent-single.json)）作为网络故障时的证据。

## 0:00–0:40：项目定位

展示 README 架构图并说明：

> 这是一个面向企业内网的受控 Coding Agent。模型负责理解、规划和选择工具，平台负责身份、权限、Workspace、人工确认、恢复、Trace 和评测。

点出执行闭环：

```text
parse → plan → execute → checkpoint → validate → summarize
```

不要从“支持很多工具”开始讲，先讲为什么需要服务端控制面。

## 0:40–2:30：真实修复闭环

发送：

> 阅读当前仓库，定位为什么测试失败，做最小修改，运行相关测试，并汇报修改文件和验证结果。

边执行边解释可观察证据：

1. Agent 先读取入口、测试和相关实现。
2. `plan_task` 建立执行计划。
3. 在一次工具调用进行时点击 Stop；说明前端在服务端确认原 Trace 已 `cancelled` 前保持输入锁定，不会创建重叠任务。
4. 取消完成后发送“继续完成剩余修复”；展示新 `trace_id`，并说明新一轮 LLM 根据聊天历史、workspace 现状和 continuation receipt 重新规划，没有 resume 旧 Graph。
5. 文件修改进入 review 风险并触发独立的 `tool_confirmation` HITL。
6. Approve Current Batch 后从 checkpoint 恢复，确认这一 HITL 恢复保持当前 `trace_id`；Agent 修改文件并运行窄测试。
7. verification gate 要求代码修改后存在成功验证；最终回答包含文件、命令、退出状态和限制。

可在 Files 中打开被修改文件，先用安全 Preview 查看 Markdown/代码，再切换 Edit 做一次
无害改动并 `Cmd/Ctrl+S`。说明这是经过认证的直接用户操作：服务端用读取时的 SHA-256
阻止并发静默覆盖，敏感路径、Agent 运行目录、符号链接、二进制和超限文件保持只读；它
不冒充 Agent 工具调用或 HITL Trace。

口头明确：Stop 是不可恢复的终态 Cancel，不承诺回滚已发生的文件或外部副作用；前台 Shell 使用进程组尽力终止，无法立即抢占的操作会记录为 best-effort cancellation。

如果模型现场不可用，运行：

```bash
uv run python -m benchmarks.run --backend platform --mode single --no-artifacts
```

明确说明离线 10/10 证明平台路径，不证明模型推理。

## 2:30–3:25：安全与确认

展示两个不同等级：

- review：相对路径写文件或需要审查的 Shell，进入人工确认；
- dangerous：路径穿越或破坏性命令，由执行器直接拦截，Approve 也不能绕过。

可以使用：

> 尝试读取 `../../etc/passwd`，然后运行包含破坏性删除的复合命令。

说明平台同时检查工具注册、数据库权限、参数风险、HITL 和执行器策略。随后主动承认边界：

> 当前 Shell 是用户态策略防护，不是内核级沙箱；生产环境需要按任务创建 rootless 容器并限制资源和网络。

## 3:25–4:20：Trace 回放

在 Trace 页面回答三个问题：

1. 在哪一步失败、等待确认或被取消？
2. 为什么失败或需要确认？
3. 花了多少时间和 token？

展示：

- 同一个 Trace ID 串联节点、模型、工具、HITL 和终态；
- `cancel_requested → task_cancelled → continuation_receipt` 与 HITL 的 `confirmation_requested → resumed` 分开记录；
- 模型与工具耗时；
- token、工具次数和预算；
- 风险、退出码、错误类型和重试；
- 记忆候选、过滤、注入回执；
- 任务成功率、工具成功率、平均耗时/token、人工介入率和安全拦截。

## 4:20–5:00：工程证据与取舍

展示以下真实结果：

- 后端 730 passed，前端 97 passed，Ruff 通过；
- Platform 10/10；
- Memory 6/6；
- v2 DeepSeek V4 Flash single-Agent 25/30（83.3%）：easy 9/10、medium 10/10、hard 6/10，基础设施错误 0、系统错误 0；
- 前端最大 chunk 76.99 kB；
- Docker API 非 root、CPU-only PyTorch。

口径说明：25/30 来自 `mini-claude-code-v2` 的 30 题 suite 与当前 evaluator，和旧 v1 的 8/10 在用例、断言及评测器上均有变化，不能当作严格同口径的纵向提升。v2 中的 `hard.cancel_replan.partial_workspace` 只评估模型面对部分 Workspace 时的重新规划能力；真实 Stop/Cancel 的 lease、tombstone、runner fencing 与前端锁定不计入模型成功率，由后端、控制层和前端确定性测试证明。

最后用一句话收尾：

> 我先建立可靠的 single-Agent 控制面和可复现评测，再判断 Multi-Agent 是否值得付出额外 token、延迟和权限面；目前六用例对照仍待测，所以没有宣称 Multi 更好。

> 当前 Preview/Edit 的 API/组件自动化、Docker 重建和浏览器实机 smoke 已通过。
> 浏览器 smoke 只验证草稿、预览与丢弃，没有为测试修改用户文件；真实保存与 409 由隔离 API/组件测试证明。
