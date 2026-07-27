# 5 分钟作品集演示脚本

## 演示前准备

1. 使用固定 commit 和准备好的演示用户启动 Compose。
2. 在用户 Workspace 放入一个只有一处小 Bug、带失败测试的仓库。
3. 预先确认模型 endpoint 可用，并保留一次成功录屏。
4. 打开 Chat 和 Trace 页面。
5. 准备 Platform 10/10 与 DeepSeek 8/10 报告作为网络故障时的证据。

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
3. 文件修改进入 review 风险并触发 HITL。
4. Approve Current Batch 后从 checkpoint 恢复。
5. Agent 修改文件并运行窄测试。
6. verification gate 要求代码修改后存在成功验证。
7. 最终回答包含文件、命令、退出状态和限制。

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

1. 在哪一步失败或暂停？
2. 为什么失败或需要确认？
3. 花了多少时间和 token？

展示：

- 同一个 Trace ID 串联节点、模型、工具、HITL 和终态；
- 模型与工具耗时；
- token、工具次数和预算；
- 风险、退出码、错误类型和重试；
- 记忆候选、过滤、注入回执；
- 任务成功率、工具成功率、平均耗时/token、人工介入率和安全拦截。

## 4:20–5:00：工程证据与取舍

展示以下真实结果：

- 后端 381 passed，前端 23 passed，Ruff 通过；
- Platform 10/10；
- Memory 6/6；
- DeepSeek single-Agent 8/10；
- 前端最大 chunk 76.99 kB；
- Docker API 非 root、CPU-only PyTorch。

最后用一句话收尾：

> 我先建立可靠的 single-Agent 控制面和可复现评测，再判断 Multi-Agent 是否值得付出额外 token、延迟和权限面；目前三用例对照仍待测，所以没有宣称 Multi 更好。
