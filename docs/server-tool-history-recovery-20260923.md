# 2026-09-23：断线后继续聊天返回工具协议 400

## 原因与处理

服务器原会话 `15ed7b75-3b0e-48d6-b852-01900f584ea0` 在审批后执行工具时断线。北京时间 09-23 02:11:24，Nginx 同秒记录页面重新加载、状态请求 499，Trace 将任务收敛为 failed。现有 API 容器持续运行六天，无本次容器重启证据；日志符合刷新页面中断请求的情况，无法单凭服务端日志确定用户的具体浏览器操作。

真正使“继续”永久失败的是消息收尾遗漏：FAILED 仅写任务状态，没有为 AI 已发出的两条工具调用记录对应结果。后续两次“继续”追加 HumanMessage 后，历史变成 `AI(tool_calls) → Human → Human`，模型要求的 `tool_use → tool_result` 配对不成立，因此返回 400。这不是用户文件错误，也不是缺少安装包。

修复在两处复用同一函数：

1. `_safe_mark_task_terminal` 的 FAILED 路径补齐历史，并从 `persist_memory` 节点终结 checkpoint，清空 pending calls。
2. `llm_call_node` 在发送模型前修复旧历史，兼容本次发布前已受影响的会话。

只补缺失的结果，插在下一条普通消息之前；已有真实结果保持不变。补充消息标记 error，并说明执行结果未知、重试前核对工作区及外部状态。函数不会执行工具，不把未知结果写成成功，重复调用不会重复添加。

服务器原会话在无活跃任务、API 暂停期间完成备份与定点修复，只增加两条未知结果，保留其他消息和 failed 状态；没有替用户继续任务或运行旧命令。页面中的旧报错是历史记录，用户可以直接在原会话发送新消息。

## 发布与回退

- 当前目录：`/home/ubuntu/mini-claude-releases/20260923-tool-history`，`/home/ubuntu/mini-claude-current` 已指向此处。
- API 镜像：`mini-claude-api:20260923-tool-history`。
- 镜像 ID：`sha256:830cbd70e8ba9905b4e739a486ce43c183ea8bbd099bb371ed3c53f1cca7ba62`。
- 源码摘要：`37f4369dad96f386d96bf80e7693dc133ab851111c0d44b8a803a98b14bc6275`，未提交诊断快照。
- 前端保持 `20260916-diff-colors`；executor/reaper 保持 `20260916-workbench-r2`，联网配置保留。MySQL schema 不变，无迁移新增。
- 私有备份：`/home/ubuntu/mini-claude-backup-20260923-tool-history`，含服务器配置、容器信息、MySQL dump、Redis RDB、workspace/chroma/shared-skills。目录 0700、文件 0600，不下载业务备份或凭据。

当前目录维护只需三层 Compose：

```bash
cd /home/ubuntu/mini-claude-current
docker compose --env-file .env -p mini-claude \
  -f docker/docker-compose.yml -f docker/server.yml -f docker/workbench-server.yml ps
curl --fail http://127.0.0.1:8082/api/health
```

应用回退使用旧目录，只替换 API；保留已经修复的合法消息，无需恢复整个数据库：

```bash
cd /home/ubuntu/mini-claude-releases/20260916-workbench-r2
docker compose --env-file .env -p mini-claude \
  -f docker/docker-compose.yml -f docker/server.yml -f docker/workbench-server.yml \
  -f /home/ubuntu/mini-claude-releases/20260916-diff-colors/docker/frontend-hotfix.yml \
  up -d --no-build --no-deps --wait api
docker exec mini-claude-frontend-1 nginx -s reload
curl --fail http://127.0.0.1:8082/api/health
```

确认回退健康后再把 `mini-claude-current` 指回旧目录。Nginx reload 用于刷新 Docker API 地址，不重建前端容器。旧版重新断线仍可能重现该 bug，回退仅供发布异常时使用。

## 验证与边界

后端回归 853 passed、25 deselected；新增节点输入恢复用例另跑 1 passed。覆盖部分结果、完整结果、多个中断轮次、历史 human 保留、重复修复和 FAILED 状态更新。真实模型合成协议请求通过；临时账号的真实流式聊天从残缺历史正常完成，无工具执行，测试后账号/checkpoint 删除、业务行数恢复。没有把真实用户聊天传给模型做测试。

公网健康检查与 112 个运行文件摘要核对通过。前端、MySQL、Redis、reaper 容器 ID 未变化。未改前端或容器执行逻辑，未重复跑前端和真实 Docker 执行全套测试。

备份与发布后的 79 个原用户文件内容逐一相同。收尾时本地另有并发技能包开发，导致本地整仓摘要变化；这些 admin/skills/新迁移/依赖声明改动不在本次包中，已保留未部署。本任务七个源码、测试和配置文件仍匹配发布快照；服务器数据库保持 `20260914_0006`。

既有 Ruff 三项告警（两处 imports 排序、一处旧行长度）仍在，已对旧版源码核实；新增 helper 和测试文件检查通过。

证据：`docs/release-evidence/tool-history-20260923-source.json`、`tool-history-20260923-deployment.json`、`tool-history-20260923-verification.json` 及两份测试 XML。修复不改变“断线停止任务”的既有行为，也不能保证中断前的外部操作没有副作用。
