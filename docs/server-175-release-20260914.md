# 2026-09-14 服务器发布记录（历史）

> 此版本已被 `20260914-workbench` 替代，GitHub 连接功能已撤除。当前维护命令见 [撤除记录](github-connection-retired-20260914.md)。以下描述发布当时的状态。

用户在本地版本更新后明确要求更新远程服务器。本轮已完成发布，时间为北京时间 2026-09-14 01:20。

## 版本与访问

- 入口：<http://175.24.166.236:8082>。保留原有账号和权限；Multi 和 GitHub 仍需要相应工具权限，普通账号不会自动升级。
- 发布目录：`/home/ubuntu/mini-claude-releases/20260914-github-mvp`；快捷路径 `/home/ubuntu/mini-claude-current`。
- 原目录 `/home/ubuntu/mini-claude-code` 保留用于旧版回退和共享离线模型缓存，不能删除。
- 源码来自 `feature/github-shell-mvp` 未提交工作区，基础提交 `0648e72`，最终发布包包含 248 个文件。
- 最终包 SHA-256：`631829b6a8c1daeeb0e40d3d76d145ef73bb1c3885b9d12df7cabbdd8e47ca86`。
- API、前端、reaper、Git worker、普通执行镜像使用各自 `mini-claude-*` 仓库的 `20260914-github-mvp` 标签，Git 版本为 2.39.5。
- MySQL 已升级到 `20260914_0006`，`alembic check` 无结构差异。GitHub 控制库 SQLite v1 已初始化，目前没有任何 PAT 连接。
- 本轮没有提交或推送 Git，没有修改同服务器上另一个项目。此次远程迁移不代表本机运行数据库也已从此前的 `0005` 升级到 `0006`。

## 已实施的变更

部署新版 Shell 沙箱、只读子 Agent 的 Multi 流程、GitHub 设置与绑定入口，以及受控 Git worker；Single 保持默认模式。
服务器原有业务凭据和端口保留，显式打开 `ENABLE_MULTI_AGENT` 和 `GITHUB_ENABLED`。
GitHub 独立主密钥在服务器生成，保存在发布目录的 `.env`（0600），没有复制本机主密钥、PAT、用户工作区或数据库。
服务器 GitHub 状态根 `/srv/mini-claude/github-state` 为 10001:10001、0700，API 内映射 `/github-state`。
普通 Shell 仍禁网，不持有 GitHub 凭据或 Git 元数据；受控 worker 仅处理已绑定仓库。

新增 `20260914_0006` 迁移处理服务器历史 `users.email` 和 `users.username` 重复索引。
与 `0005` 的 Skill 重复索引清理一样，只有确认相同列的规范唯一索引存在时才删除重复项。
所有待删除用户索引在第一次 MySQL DDL 前统一校验，避免发现第二个异常时已经部分修改。
已有账号和业务行没有因这些迁移被删除或重建。

## 备份和切换过程

备份目录为 `/home/ubuntu/mini-claude-backup-20260914-github-mvp`（0700），包含：

- `source-and-config.tar.gz`：旧源码及环境配置。
- `database.sql`：停服且无活动任务时生成的 MySQL 一致性逻辑备份。
- `redis.rdb`：按 Redis 实际 `CONFIG GET dir/dbfilename` 生成并取出的快照。
- `workspaces-and-memory.tar.gz`：workspace、Chroma 和共享技能数据卷。
- `deployment-20260914.env`：新部署环境及 GitHub 主密钥的独立私有备份。
- `images.json`、测试报告及失败尝试的备份，便于审计和回退。

含数据或密钥的文件均留在服务器私有备份目录，不进入 Git。大小和摘要见发布证据 JSON。
Redis 的实际目录是 `/var/lib/redis-stack`，不能假设快照一定在 Compose 示例的 `/data`。
本轮没有重建或迁移 MySQL/Redis 容器，仅替换 API、前端和 reaper。

切换前验证完整结构差异、活动 session lease 和执行容器；关闭入口后再次核对无活动任务，再停止 API/reaper、完成备份和迁移。
首次尝试因 Redis 快照路径不符恢复旧应用；第二次因完整 schema 校验发现服务器特有用户索引，退回 `0004` 并恢复旧应用。
修正后先在不停服状态下确认所有结构差异仅为三个已覆盖的重复索引，最终切换成功。
这两次错误已解决；失败过程中没有通过忽略校验、覆盖数据库或放宽唯一约束继续发布。

## 验证证据

- `docs/release-evidence/server-175-release-20260914.json`：镜像、101 个运行 Python 文件校验、数据库和接口状态、备份摘要。
- `docs/release-evidence/server-175-github-docker-tests-20260914.xml`：24 passed，2 个未注册 pytest marker 提示，无失败。
- Git/普通 Shell 测试在服务器独立目录、无生产配置/数据的验证容器执行，覆盖持久提交、跨进程恢复、用户隔离、禁网、取消、资源限制和回收。
- 首次测试为 23 passed / 1 failed，原因是测试写死回收器镜像和 socket GID。改为 `SANDBOX_REAPER_TEST_IMAGE` 与实际 socket 文件 GID 后完整重跑 24 passed；生产配置始终使用实际 GID 111。
- 两份索引迁移测试共 9 passed，验证既有数据、唯一约束、幂等、回退和异常时拒绝修改。
- API、前端、MySQL、Redis 健康，reaper running；内部和公网 `/api/health` 正常，前端版本文件可访问。
- 用户、会话、聊天记录、共享技能表行数与更新前一致；未创建、提升或修改任何生产账号。
- 服务器新 API 中通过既有模型配置进行一次直接模型调用，返回非空响应。此项不是登录后的 HTTP/SSE 完整任务验收。
- GitHub 匿名请求返回 401，控制库为 v1，无配置的连接。验证结束无本部署执行容器残留，专用测试目录已清理。

尚未验证：真实用户登录后的完整浏览器任务演练、真实 GitHub Clone/Commit/审批/Push、HTTPS、从备份完整还原。
迁移后最终版本尚未实测回退；已验证的是前两次失败切换自动恢复旧服务的路径。

## 当前版本维护命令

```bash
cd /home/ubuntu/mini-claude-current
docker compose --env-file .env -p mini-claude \
  -f docker/docker-compose.yml -f docker/server.yml -f docker/github-server.yml config --quiet
docker compose --env-file .env -p mini-claude \
  -f docker/docker-compose.yml -f docker/server.yml -f docker/github-server.yml ps
docker exec mini-claude-api-1 /app/.venv/bin/alembic current
docker exec mini-claude-api-1 /app/.venv/bin/alembic check
curl -f http://127.0.0.1:8082/api/health
```

后续发布应换新标签、新发布目录并重复备份/验证；不要覆盖当前回退镜像，不要执行 `down -v`。
`scripts/server_175_apply_release.py` 是本次固定版本的一次性切换脚本，有备份存在检查，不能不检查状态就重复运行。

## GitHub 凭据安全入口

当前公网是 HTTP。不要通过公网明文页面填写 PAT。尚未配置 HTTPS 时，可在可信本机终端运行：

```bash
ssh -N -L 127.0.0.1:18083:127.0.0.1:8082 mini-claude-server
```

保持终端连接，然后打开 <http://localhost:18083> 并登录服务器账号，在 GitHub 设置页填写 PAT、明确的提交作者信息和授权测试仓库。
这是访问服务器的 SSH 加密隧道，区别于本机应用的 `localhost:3000`。
fine-grained PAT 限定测试仓库；读取需 Contents read，推送需 Contents read/write，不授予 Workflows 写权限。
真实 Push 仍需用户审核具体仓库、分支、提交及预期远端状态，可能触发目标仓库已有 CI/CD。
本轮没有创建 SSH 隧道或配置 HTTPS，也没有获取任何用户 PAT。

## 回退说明

先停止接收请求并确认没有活动任务/执行容器，备份回退时刻的数据与 GitHub 状态。
在新发布目录使用新镜像将索引迁移退回旧镜像认识的版本，再从旧目录启动旧镜像：

```bash
cd /home/ubuntu/mini-claude-current
docker compose --env-file .env -p mini-claude \
  -f docker/docker-compose.yml -f docker/server.yml -f docker/github-server.yml stop frontend api sandbox-reaper
docker compose --env-file .env -p mini-claude \
  -f docker/docker-compose.yml -f docker/server.yml -f docker/github-server.yml \
  run --rm --no-deps --entrypoint /app/.venv/bin/alembic api downgrade 20260819_0004
cd /home/ubuntu/mini-claude-code
docker compose --env-file .env -p mini-claude \
  -f docker/docker-compose.yml -f docker/server.yml \
  up -d --no-build --no-deps --wait api frontend sandbox-reaper
```

回退会撤回本轮 Multi/GitHub 功能。不要为回退应用而恢复整个旧 SQL 备份，避免覆盖后续用户数据。
保留新环境文件、GitHub control.sqlite3 和仓库状态，以便将来恢复新版；原环境备份不包含后来生成的 GitHub 密钥。
`mini-claude-current` 是运维快捷路径，不决定 Docker 已运行的镜像；回退后应按实际版本更新或移除该快捷路径，避免运维误用。
