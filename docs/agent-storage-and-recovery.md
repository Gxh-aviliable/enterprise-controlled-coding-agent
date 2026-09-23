# Agent 证据、恢复与存储维护

当前仅支持一个 API 写进程。Trace、执行收据、事件序号、文本快照、恢复日志放在 `WORKSPACE_BASE/.workspace-locks/`；用户工作区及执行容器仅能访问 `user_<id>` 子目录，不能挂载控制记录。原 `.agent/traces` 继续只读兼容，标记 `legacy_unverified`，不用于批准恢复，不删除或自动升级其可信度。

## 保留和容量

- Trace：每任务最多5,000事件、8 MiB；截断明确标注，累计计数保留。终态30天后清理。未终结任务、未决执行收据和中断恢复日志保留，告警要求人工核对。
- 文本快照：每文件256 KiB、每任务100文件/4 MiB、7天；每小时后台清理并在访问时再检查。大文件、二进制、链接、敏感路径和可检测的秘密不提供恢复。
- 同任务的stream索引（512条）、收据、恢复日志随过期终态一起清理。协调锁文件保留，避免删除锁inode导致竞态；运维还需监控inode使用量。
- 控制JSON总量达到512 MiB记录容量告警。该阈值不是硬磁盘配额；部署目录应有宿主存储配额和磁盘/inode监控。用户项目文件不在本清理范围。
- `maintenance_loop`每小时运行，单写进程内与Trace写锁协调，收据/快照使用已有文件锁，与恢复共用workspace写锁。不得直接增加多个Uvicorn workers。

合成测量：100/500/2,000次事件写入，优化前约0.23/1.15/4.61 MB，p95约1.29/4.10/15.11ms；可信目录及紧凑JSON后约0.22/1.11/4.46 MB，p95约0.89/2.70/11.81ms。1,000任务的磁盘量是推算，不是线上流量。原始报告见 `release-evidence/goal-k-capacity-{before,after}-20260914.json`，脚本 `python -m benchmarks.trace_capacity --output <report.json>`。

当前未触发增量存储迁移。若新增多写进程，或真实任务规模下写入p95达到50ms，应先迁入现有MySQL的带唯一键增量事件；此性能阈值为本次运维决策阈值，不是原计划的硬指标。

## 备份和恢复

使用API相同的受信服务账号及WORKSPACE_BASE配置执行：

```bash
python -m enterprise_agent.observability.storage_governance backup /private/new-records-backup
python -m enterprise_agent.observability.storage_governance verify /private/new-records-backup
python -m enterprise_agent.observability.storage_governance restore /private/new-records-backup /private/new-restore-staging
python -m enterprise_agent.observability.storage_governance cleanup
```

备份目录必须新建、权限0700，文件0600。还原只允许新的暂存目录，校验路径及SHA后再复制并复验，不覆盖在线数据。示例路径按部署系统更换。备份可能含用户文本，不入Git或对外公开。恢复在线服务需要另行维护窗口和完整MySQL、Redis、workspace卷备份；服务记录备份不是全应用事务快照。

真实标准库文件备份→新目录还原→逐文件SHA与脱敏核对通过；校验和破坏、已有目标覆盖拒绝由针对性测试验证。证据 `release-evidence/goal-k-backup-drill-20260914.json`。

任务恢复只恢复所选文件内容，不能撤销网络/数据库效果。当前内容必须仍与任务最终版本一致；所有所选文件预检后逐文件替换。多文件中断时检查`requested_paths`、`applied_paths`及`attempting_path`，不盲目重放。API崩溃后的running收据是未决副作用，应核对当前文件；不能将其解释为“未执行”。
