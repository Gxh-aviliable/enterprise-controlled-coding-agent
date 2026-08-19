# Pause/Continue 下线与 Cancel-and-Replan 滚动迁移

本文说明如何把已经存在 `Pause/Continue` 协议的多实例环境安全迁移到 `Stop/Cancel + Replan`。当前代码是下文的 **Phase B** 形态：主动 Pause 的 Graph 节点、API 和前端入口已删除，旧 paused Trace 只能终态化和只读展示。

## 新协议不变量

- `Stop` 只取消当前 `trace_id`，是终态操作，不回滚已经发生的文件或外部副作用。
- 服务端确认旧 Trace 已到达 `cancelled` 并释放 Redis active-trace lease 后，同 Session 才能接受下一条消息。
- 同 Session 的下一条消息创建全新 `trace_id`；新一轮 LLM 依据聊天历史、workspace 真实状态和 continuation receipt 重新规划，不对旧 Graph 执行 `Command(resume)`。
- `Command(resume)` 仅保留给敏感工具确认，批准、拒绝和超时都必须保持原 `trace_id`。
- RedisSaver checkpoint、MySQL durable chat history、workspace 隔离和历史 Trace 审计保留。历史 `paused` 事件可展示，但不能再恢复执行。

## Phase A：桥接版先覆盖全集群

不要把旧 Pause 版本与当前 Phase B 版本直接混部署。先发布一个桥接版，并逐个替换所有旧实例。桥接版必须：

1. 暂时保留旧 pause 节点和路由，因而能读取并终态化已存在的 paused checkpoint。
2. 提前引入按 `user_id/session_id/trace_id` 隔离的 Redis active-trace lease、`cancel_requested` tombstone、runner fencing 和通用 resume lock。
3. 识别集群级 `agent:protocol:user-pause:retired` 标记；标记生效后拒绝新 Pause/Continue，但仍能将旧任务安全转成 `cancelled`。
4. 包含幂等迁移：把 `paused` / `pause_requested` / `resuming` 的 checkpoint 和 Trace 终态化为 `cancelled`，原因为 `user_pause_feature_retired`，写入 continuation receipt，并清理对应 `agent:pause:*` key。

桥接版全部就绪前不得设置 retirement marker，否则仍在运行的旧实例可能继续写入新 pause key。所有实例已是桥接版后，按以下顺序操作：

1. 暂停新建任务或先从负载均衡器排空正在更新的实例。
2. 设置 retirement marker，运行迁移，再重复运行一次验证幂等性。
3. 确认 `agent:pause:*` 为 0，没有可执行的 `paused` / `pause_requested` / `resuming` checkpoint，相关 Trace 均为 `cancelled` 且原因正确。
4. 确认 waiting-confirmation 任务仍可用原 Trace 的 resume lock 完成批准、拒绝或超时。

## Phase B：再发布删除版

只有 Phase A 验证通过后，才能滚动发布当前代码：

1. 先对 MySQL、Redis 和 workspace 快照或备份，记录发布前的版本号。
2. 每次只排空并替换一个实例；就绪检查必须等待启动迁移完成。启动逻辑会再次幂等终态化遗漏 paused 任务，不允许带遗留任务的实例宣告 ready。
3. 观察 active lease、cancel convergence、checkpoint 终态写入、MySQL continuation receipt 和工具确认 resume lock 指标，再继续下一个实例。
4. 全部替换后，验收 Pause/Continue 路由不存在、新任务只显示 Stop，以及 Stop 后下一条消息产生新 `trace_id`。

当前兼容窗口内仍保留 `TaskStatus.PAUSED` 的枚举值，仅用于解析和终态化旧数据；状态机不再允许进入或恢复该状态。等待完整的 Trace/checkpoint 保留周期结束、确认不再有旧序列化值需要读取后，才可在后续独立发布删除枚举。历史 Trace 的 `paused` 显示兼容可长期保留。

## 回滚限制

- retirement marker 生效后，不得回滚到一个不识别该标记、仍会创建 Pause 请求的镜像。需要回滚时，目标至少必须是 Phase A 桥接版。
- 不要通过恢复旧 checkpoint 来“撤销取消”。Stop 可能已产生文件或外部副作用，正确处理是保留审计记录并用新 Trace 重新规划。
- 不得手工删除 active-trace lease 来解锁 Session。必须等 runner 停止、checkpoint 达到 `cancelled`、receipt 持久化，再由持有正确 lease/runner token 的一方释放。

## 发布后核查

- 同一 Session 在 running、前台 Shell、托管 background process 和 waiting-confirmation 中分别执行 Stop。
- Cancel 失败或仍为 `cancelling` 时，前端保持输入锁定，不会创建重叠任务。
- 取消完成后发送下一条消息，确认 `trace_id` 变更且 continuation receipt 仅被新轮模型上下文消费一次。
- 通过页面重载验证 waiting-confirmation 仍指向原 Trace，并验证旧 Trace 的晚到 SSE/checkpoint 无法改写新 Trace 时间线。
- 等待 Redis checkpoint TTL 过期后，确认 MySQL durable history 经裁剪去重后仍进入新轮模型上下文。
