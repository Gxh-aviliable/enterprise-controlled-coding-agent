# 175.24.166.236 部署记录

## 当前版本：2026-09-23 Skills 管理与运行系统

按用户要求发布当前工作区，保留今天的工具历史恢复修复。API 镜像为 `20260923-skills`，前端经本轮界面优化更新为 `20260923-skills-ui`，数据库升级到 `20260923_0007`；执行器、回收器及原有用户数据保留。入口仍为 <http://175.24.166.236:8082>，维护入口 `/home/ubuntu/mini-claude-current` 指向新发布目录。维护使用当前目录内三层 Compose，再追加 `/home/ubuntu/mini-claude-releases/20260923-skills-ui/docker/frontend-hotfix.yml`，不要追加 9 月 16 日旧前端覆盖。详见 [Skills 发布、验证和回退](server-175-release-20260923-skills.md)。

## 历史版本：2026-09-16 工作台与联网执行

已按用户要求部署当前本地未提交工作区；包含聊天内结果展示、Diff、验证版本、恢复冲突检查及 pip/npm 联网安装。发布目录 `/home/ubuntu/mini-claude-releases/20260916-workbench-r2`，API、执行器和回收器镜像标签 `20260916-workbench-r2`，前端已单独更新至 `20260916-diff-colors`；数据库保持 `20260914_0006`。原账号、数据卷、端口与服务器配置保留。公网入口 <http://175.24.166.236:8082>，维护入口 `/home/ubuntu/mini-claude-current`。详见 [本次发布、验证与回退记录](server-175-release-20260916.md)。

当前前端维护需在三层 Compose 后追加 `-f /home/ubuntu/mini-claude-releases/20260916-diff-colors/docker/frontend-hotfix.yml`；该覆盖只更新前端镜像。Diff 红绿着色、审批内容滚动和子目录上传的修复、验证及回退说明见 [9 月 16 日发布记录](server-175-release-20260916.md#前端补丁审批滚动与上传目录)。

## 历史版本：2026-09-14 工作台（已撤除 GitHub 连接）

按用户最新要求，GitHub 连接、仓库选择、Push 审批页面及公开 API 已撤除；运行 API 关闭集成，不再持有 GitHub 主密钥或挂载其状态目录。保留 Shell、沙箱、Single/Multi、原账号和数据卷。

当前源码为 `/home/ubuntu/mini-claude-releases/20260914-workbench`，`/home/ubuntu/mini-claude-current` 已切换到此目录。API 与前端镜像标签为 `20260914-workbench`。MySQL revision 为 `20260914_0006`。

公网入口为 <http://175.24.166.236:8082>。维护使用 `docker/docker-compose.yml`、`docker/server.yml`、`docker/workbench-server.yml` 三层配置，详见 [撤除及验证记录](github-connection-retired-20260914.md)。下文的 GitHub 发布与维护命令仅为历史，不能用于当前版本更新。

## 历史版本：2026-09-14 GitHub Shell MVP 与 Multi

用户明确授权后，已将本地验证的 GitHub Shell、Multi 和沙箱实现部署到服务器，保留原账号、权限、凭据、端口和数据卷。
公网入口仍为 <http://175.24.166.236:8082>。MySQL 当前 revision 为 `20260914_0006`，GitHub 控制库为 SQLite v1。

当前源码位于 `/home/ubuntu/mini-claude-releases/20260914-github-mvp`，快捷路径为 `/home/ubuntu/mini-claude-current`。
原 `/home/ubuntu/mini-claude-code` 作为旧版和模型缓存位置保留，不是当前运行源码，不能删除。
后续维护请使用新目录及三层 Compose 配置，详见 [本次发布与回退说明](server-175-release-20260914.md)。

24 项服务器真实 Docker/Git 测试、9 项迁移测试、完整 schema 检查、公网健康检查、模型直连检查和 101 个运行源码文件校验通过；业务表行数与更新前一致。
GitHub Token 尚未配置，真实 Clone/Push 未验收。服务器仍使用 HTTP，填写 PAT 请通过 HTTPS 或文档中的 SSH 隧道。

## 历史版本：2026-09-11 容器执行沙箱

2026-09-11 已将本地 `feature/container-agent-sandbox` 的当前工作区发布到服务器，包含未提交功能与修复；未替用户提交或推送 Git。基础提交仍为 `0648e72`，因此不能只用 Git HEAD 判断线上是否最新。发布包 SHA-256：`fe8894a6ee089a7422b102a2e2cfd3d0247b8f63b053e8d22bde35565bdda333`；服务器根目录 `release-manifest.json` 记录 287 个快照文件，`release-deployed.json` 记录镜像标识，运行镜像内 96 个后端/迁移/共享技能文件校验一致。

当前镜像：

- API：`mini-claude-api:20260911-sandbox`
- 前端：`mini-claude-frontend:20260911-sandbox`
- Shell 执行镜像：`mini-claude-executor:20260911`
- 回收服务：`mini-claude-sandbox-reaper:20260911`

本次使用 `docker/server.yml`、`docker/Dockerfile.server` 和 `docker/sandbox.Dockerfile.server`；服务器依赖镜像源设置与离线模型缓存保留，API 经过实际完整构建，没有用旧 API 镜像覆盖源码代替构建。服务器专用文件在本轮核对后保存回仓库，原发布清单代表发布时的源代码快照，不包含之后追加的发布记录。

执行器配置为 `docker`，部署标签 `mini-claude-175`。API 和执行容器 UID/GID 为 `10001:10001`，宿主 staging 为 `/srv/mini-claude/sandbox-staging`（0700），API 内映射为 `/sandbox-staging`。Docker socket GID 为 111，只有可信 API 控制侧和回收服务挂载 socket；命令执行容器不挂载 socket。执行容器默认禁网、只读根文件系统、去掉 capabilities，限制 CPU/内存/PID/时间/输出；执行失败不会回退为宿主 Shell。

保留了服务器 `.env` 中的业务凭据、端口、现有账号（包括用户 ID 5 的管理员权限）、MySQL/Redis/workspace/Chroma 卷和离线模型。没有同步本机 `.env`、本机用户文件或数据库。此前撤回的项目导入/选择/项目环境配置没有重新引入；本次也没有修改工具卡片展示或放宽敏感路径策略。

### 验证结果

- 本地 `.venv/bin/python -m pytest -q -m 'not docker_sandbox'`：757 passed，14 deselected。
- 本地 `npm test -- --run`：97 passed；`npm run build` 和 Ruff 检查通过。
- 服务器隔离测试：验证镜像基于本次 API 镜像，非 root 控制侧、独立临时工作区，执行 `python -m pytest -c /dev/null /tests/sandbox/test_docker.py --basetemp=/srv/mini-claude-validation-20260911/pytest -q --junitxml=/srv/mini-claude-validation-20260911/docker-tests.xml`，14 passed（20.85 秒）。两项警告为临时配置未注册 marker、只读位置不能写 pytest cache，不影响测试断言。
- 真实测试覆盖其他用户/宿主文件/凭据/socket 隔离、默认禁网、只读根目录与权限、CPU/内存/PID/tmpfs/输出限制、超时/OOM、前后台取消与子进程清理、任务互不干扰、独立回收服务、文件工具并发冲突和故障禁止回退。
- 已上线 API 容器内验证：在临时用户工作区读取错误实现 → 容器内 pytest 失败 → 容器内修复 → Python 文件工具读回 → 容器内 pytest 通过；三次执行均确认容器清理，文件归属 10001，Trace 保存执行器及容器信息。
- Compose 配置校验、API/前端/MySQL/Redis 健康状态通过；回收服务 running。公网 `/healthz` 和 `/api/health` 通过，Alembic 为 `20260819_0004 (head)`。验证结束时本部署没有残留执行容器。
- 本轮未完成新增测试账号的真实模型 HTTP 对话验收：自动审批拒绝在生产库创建临时管理员，理由是部署同步授权未明确覆盖新增管理员账号；操作未执行。不能将确定性沙箱演示称为完整模型对话验收。

证据：

- [版本与镜像校验](release-evidence/server-175-release-20260911.json)
- [服务器真实 Docker 测试](release-evidence/server-175-sandbox-tests-20260911.xml)
- [线上 API 控制侧与项目修复演示](release-evidence/server-175-sandbox-controller-20260911.json)

### 更新与回滚

后续修改仍需显式发布，不是本地文件自动同步。先核对和备份服务器配置/数据，发布经过验证的源码版本，构建执行镜像，然后构建 API/前端/回收服务。保持 Compose project `mini-claude` 和现有卷名；不要使用 `down -v`。确认没有活动任务后再切换：

```bash
cd /home/ubuntu/mini-claude-code
docker build -f docker/sandbox.Dockerfile.server -t mini-claude-executor:20260911 .
docker compose --env-file .env -p mini-claude -f docker/docker-compose.yml -f docker/server.yml config --quiet
docker compose --env-file .env -p mini-claude -f docker/docker-compose.yml -f docker/server.yml build api frontend sandbox-reaper
docker compose --env-file .env -p mini-claude -f docker/docker-compose.yml -f docker/server.yml up -d --no-build --no-deps --wait --wait-timeout 180 api frontend sandbox-reaper
curl -f http://127.0.0.1:8082/api/health
```

以上使用当前版本标签；下一次发布应使用新的版本标签并更新 `SANDBOX_IMAGE`，避免覆盖回滚镜像。本次备份位于服务器 `/home/ubuntu/mini-claude-backup-20260911-sandbox/`（仅服务器用户可访问），包含 `source-and-config.tar.gz`、`database.sql`、`workspaces-and-memory.tar.gz`、旧 `server.yml`/`Dockerfile.server` 和构建/测试日志。旧 `20260907` API/前端镜像保留。需要回滚时先停止接收任务并确认执行容器已清理，再恢复备份中的旧 Compose 与服务器环境配置、切回旧镜像；本次无新增 schema migration，不应为了回滚代码直接覆盖升级后产生的业务数据。回滚旧版也会撤回本次 Shell 容器隔离能力。

仍有的边界：Python 文件工具运行于可信 API；API/socket 控制侧拥有宿主机管理员级能力；普通 staging bind 尚无独立硬磁盘配额。项目环境仍是预构建 Python 镜像，不支持任意在线安装依赖或独立项目环境配置。详见 [沙箱架构与边界](agent-sandbox.md)。

## 初始部署记录（2026-09-07，以下为历史）

部署账户：`ubuntu`。项目目录：`/home/ubuntu/mini-claude-code`。
现有项目使用 8081，宿主机 80 已占用；本项目使用 8082。

本次使用当前工作区源码快照，基础提交 `0648e72`，包含未提交功能改动。
上传源码包 SHA-256：`6a7b632bd1d63817c1cf4c882b8f4d5dd6eafd08a5d88dc81af33709e6c68c6c`。
不包含本机 workspace、数据库或虚拟环境。模型配置经用户单独授权传输，JWT、数据库密码独立生成，保存在服务器 `.env`，权限 0600。

服务器为 Ubuntu 24.04 amd64，内存 3.6 GiB、已有 swap 1.9 GiB。
Docker Compose project 固定为 `mini-claude`，避免与原服务共享网络或数据卷。

## 运维命令（在服务器执行）

```bash
cd /home/ubuntu/mini-claude-code
docker compose --env-file .env -p mini-claude -f docker/docker-compose.yml -f docker/server.yml ps
docker compose --env-file .env -p mini-claude -f docker/docker-compose.yml -f docker/server.yml logs --tail=100 api
curl -f http://127.0.0.1:8082/healthz
curl -f http://127.0.0.1:8082/api/health
```

后端通过 `docker/Dockerfile.server` 构建，使用国内 Debian 镜像及 PyPI 镜像安装 uv；锁文件下载域名替换为清华镜像，版本和哈希不变。离线公开 embedding 与 GPT-2 tokenizer 缓存位于 `model-cache/`，挂载至 `/data/huggingface`，并设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`。
覆盖配置 `docker/server.yml` 指定镜像和内存限额：API 1500 MiB、MySQL 600 MiB、Redis 384 MiB、前端 128 MiB。
API 仅回环绑定 18082，MySQL 13382，Redis 16382。公网仅需为本项目配置 TCP 8082 的访问规则。

数据库和运行数据存于 `mini-claude_*` 卷，升级时不得使用 `down -v`。
修改 `.env` 中 MySQL 密码不会自动修改已初始化数据库的密码。

## 验收状态

17:29 更新：公网 8082 已可达。移除阻塞渲染的外部 Google Fonts 样式后，隔离 Chrome 实测登录页面正常显示，即使模拟字体站点不可用也不再白屏。

2026-09-07：API/前端镜像构建、四个容器健康检查、Alembic 迁移、离线模型加载、注册/登录/用户信息接口通过。服务器本机 `/healthz` 和 `/api/health` 通过。公网 8082 连接超时，已请用户在轻量服务器控制台放行 TCP 8082；主机 UFW 未启用。
首次打包携带的 156 个 AppleDouble 元数据文件已移到服务器 `/home/ubuntu/mini-claude-macos-metadata-20260907`，可以恢复。`.dockerignore` 已排除 `._*` 和 `.DS_Store`，API 已重建并验证迁移成功。
没有部署 Web VSCode，文件查看可使用项目内置编辑器。HTTPS 尚未配置。

真实模型调用、`read_file`、流式响应和 MySQL 对话持久化已通过：最新只读任务为 `completed`，回复含测试文件的正确标记；测试账户已停用。成功的 SSE 结束标记为 `[DONE]`，并非 `task_finished: succeeded`。

发现但未在部署中修改的现有问题：任务分类器将“不要修改文件”中的“修改”也识别成执行要求；只读工具不算副作用证据，可能导致只读任务被误判失败。使用明确只读措辞的任务已成功。前端构建还报告 5 项 npm 依赖漏洞，应另行评估。
