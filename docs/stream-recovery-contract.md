# Stream 与故障恢复契约

2026-09-14。当前执行仍绑定请求生命周期；用户没有要求关闭页面后继续长任务，因此持久后台 runner 扩展条件未触发。图的本地单事件缓冲只用于心跳/取消，不是任务队列，不跨连接续跑。

| 故障/操作 | 权威行为 | 文件与事件 | 验收状态 |
|---|---|---|---|
| 短断网或页面离开 | 心跳检测传输中断，关闭图并在受保护清理中记 interrupted/failed；若正常完成先发生则保留原终态 | 不重放工具；重新加载持久历史、任务收据与事件索引 | 538003真实浏览器通过；旧失败保留 |
| Stop | 精确 trace 的 Redis tombstone；取消当前执行/只读子任务/后台Shell；持久终态确认后解锁输入 | 已发生的副作用留收据；未决收据明确待核对 | 538003真实Stop→新任务通过 |
| 审批等待 | Graph checkpoint 保留；服务恢复原审批超时；审批resume仍需归属与唯一resume lock | 刷新不构造新审批操作；拒绝/超时不得执行 | 538003真实审批/拒绝通过；最终09f808真实旧scope失效验证通过 |
| API崩溃 | 新stream活动租约30秒，1秒心跳续租；失去owner后原子占用fence并终结failed，禁止重放Graph | 可能未完成的文件发布、收据、恢复journal标记待核对；容器仍受deadline/reaper管理 | 538003真实SIGKILL+start通过，写一次且终态failed |
| Redis故障 | 无法验证owner就停止执行；状态请求报告不可用，不凭进程缓存宣称成功 | 恢复Redis后核对lease/checkpoint/历史；禁止猜测新任务可以重复执行 | 538003真实Redis停5秒通过；首次错过注入窗口的失败实验保留 |
| 文件已发布、终态未落盘 | durable receipt是文件事实；Graph恢复不重跑写；丢失finished receipt则保留running未决记录 | 用户查看实际文件和receipt；Diff/恢复不能绕过未决记录 | 部分发布故障单元通过；09f808真实容器发布后kill API通过，已发布文件保留一次、后续执行收据未决 |
| 多文件恢复中断 | requested/applied/attempting路径与版本持久化；拒绝盲目重复恢复 | 文件逐个替换，不声称树级原子事务或恢复外部副作用 | 单元及真实Docker恢复通过 |
| 旧版本遗留租约 | 保留原期限兼容；旧实例没有新心跳时，不把长模型调用误判为失联 | 租约到期后走同一owner-fenced核对，不能因升级直接重放 | 旧隔离断网trace原长租约到期后已正常收敛failed，未删除租约 |

## 事件与正文

- SSE数据含trace_id、stream_fence、单调seq，并发生产使用文件锁协调。`id`为trace和seq组合，前端丢弃重复/旧序号。
- `/tasks/{trace}/events?after=`只返回当前用户的最近512条事件索引；索引缺口以gap标明。
- 索引记录类型、工具ID及状态，不保存模型片段正文或工具参数。片段脱敏不能可靠识别跨chunk秘密，因此正文从已有受权MySQL聊天历史补取。
- 前端对断线任务有界轮询权威状态；终态替换持久时间线，不叠加重复文本、不调用resume重放副作用。恢复卡的内容来自执行收据，不能由模型自述伪造。
- 索引/快照/收据位于用户workspace外的服务控制目录。TraceStore也已迁到服务控制目录；旧用户可写Trace只做legacy_unverified兼容读取，不进入可信指标或恢复授权；有保留期与容量上限，不是永久完整审计。

最终证据：[发布后重启](release-evidence/browser-publish-fault-20260914/restart-evidence.json)、[旧长租约](release-evidence/goal-g-legacy-lease-20260914.json)、[早期重启收据回读](release-evidence/goal-g-published-restart-readback-20260914.json)。
