# 本地演示运行与恢复（2026-09-14 Goal交付）

> 2026-09-15：按秋招演示需求，执行容器现默认直接联网，支持pip/npm安装并跨命令复用依赖。本文后续9月14日的禁网说明属于旧版行为，当前用法见[联网与依赖说明](runtime-dependencies.md)。

原本地应用已在14:35更新到主目录 `my_mini_claude_code` 的 `09f808c3…` 诊断快照。唯一构建入口 `scripts/local_demo.sh` 默认使用主目录；原MySQL/Redis容器及命名卷保持不变。没有更新远程服务器。完整范围见[交付报告](agent-improvement-delivery-20260914.md)及[发布manifest](release-evidence/goal-delivery-manifest-20260914.json)。

访问[工作台](http://localhost:3000)，使用原账号登录。API[健康页](http://localhost:8000/health)显示构建摘要。Single默认，Multi只读子任务保持原权限；配对测试未显示收益。GitHub设置/仓库选择/Push审批入口保持撤除。

## 当前维护命令

在主工作区执行：

```bash
bash scripts/local_demo.sh config --quiet
bash scripts/local_demo.sh build api frontend
bash scripts/local_demo.sh up -d --no-deps api frontend
bash scripts/local_demo.sh exec -T api /app/.venv/bin/alembic current
bash scripts/local_demo.sh exec -T api /app/.venv/bin/alembic check
```

更新前检查没有活动任务并备份。当前DB revision `20260914_0006`，结构核对通过。不要使用 `down -v`。Python/Node管理员镜像默认 `enterprise-agent-sandbox-python-node:1`；具体不可变ID见发布manifest，普通执行继续禁网、非root、只读根及资源限制。模型不能指定镜像或自行安装依赖。

烟测只创建并删除自身合成账号/工作区：

```bash
docker --context desktop-linux exec -i docker-api-1 /app/.venv/bin/python - < scripts/local_demo_verify.py
```

真实模型合成演示已在隔离端口13000/18000完成，脚本、截图、Trace和断言见 `docs/release-evidence/browser-delivery-20260914/`。复验需将这些脚本复制到0700私有临时目录，在该目录安装Playwright 1.63.0（或使用已有安装），运行 `seed.py` 后 `e2e.cjs`、`cross-user.cjs`。seed只在 `goal-agent-20260914` 创建合成账号。fixture.json含临时凭据，仅留0600临时目录，不能复制回证据目录。Chrome脚本使用macOS安装路径；Linux复验须显式替换可用浏览器路径，不改变业务断言。故障驱动仅允许精确隔离项目，不能用于原应用。

## 本次备份和应用回退

完整私有目录：`logs/goal-backups/20260914-142539/`，目录0700、文件0600，包含业务数据与旧环境配置，不能公开。SHA和归档可读性已验证；第一次142225目录是不完整失败备份，不得用作恢复源。原命名卷保留，模型下载缓存未归档。

回退前等待所有任务终结；以下仅切换原应用旧镜像，不恢复或覆盖数据：

```bash
docker --context desktop-linux compose --project-name docker \
  -f logs/goal-backups/20260914-142539/rollback.private.compose.json \
  up -d --no-deps --no-build api frontend
```

本Goal前后DB均为0006，不降级到0004。整库/整卷恢复会覆盖备份后数据，属于独立灾难恢复操作；本轮仅做控制存储备份到新暂存目录的恢复演练，没有覆盖原业务卷。旧镜像保留为 `enterprise-agent-api:before-goal-20260914-142539` 及对应frontend标签。

文件级任务恢复仅限UI明确支持的文本及版本，用户后续改动导致409整批拒绝；不撤销外部数据库/网络效果。见[存储与恢复说明](agent-storage-and-recovery.md)。

## 本Goal之前的历史记录

以下保留旧部署与迁移过程，仅用于追溯，旧“当前”指其记录时刻。其远程更新、独立工作区来源、0004降级命令不适用于本Goal交付。

<details>
<summary>展开历史部署记录</summary>

已将本机前后端更新到独立工作区 `../my_mini_claude_code-github-mvp` 的当前代码。
该工作区包含原工作区的沙箱和 Multi 改动；部署前比对原始基线，未发现这些代码在分叉后又发生修改。
原工作区未被合并、覆盖、提交或推送。随后已按用户授权更新远程服务器。
当前版本已撤除 GitHub 连接功能，详情见 [撤除记录](github-connection-retired-20260914.md)。

## 打开与演示

访问 <http://localhost:3000>，强制刷新后使用原有账号登录。MySQL、Redis、工作区、Chroma 等原有数据卷继续使用。
Single 是默认模式；Multi 要求对应工具权限，普通免费账号不会自动升级。

建议先演示已有文件的读取、编辑和测试，按页面提示审批 Shell，随后查看任务 Trace 和聊天历史。
GitHub 连接、仓库选择及 Push 审批页面和 API 已撤除，不再提供 PAT 配置入口。
外部 VS Code 打开功能不在本次验证范围内；文件展示优先使用工作台自带查看器。

## 数据库与备份

- MySQL 已执行 `alembic upgrade head`，版本为 `20260914_0006`；本机无需删除额外的用户重复索引。
- 新迁移移除 `managed_shared_skills.name` 上旧的重复唯一索引 `name`，保留 `ix_managed_shared_skills_name` 的唯一约束，不修改业务行。
- `alembic check` 已通过。迁移前检查曾发现上述重复索引，不能仅以版本号位于 head 判断结构完全一致。
- 迁移前备份：`logs/db-backups/enterprise_agent-20260914-004907.sql`，597511 字节。
- SHA-256：`527fba639e4927ce18e6c1ff356107f146c713a415c7c11cf1aa3c5990bf7895`。
- 备份目录权限 0700、文件权限 0600，位于 Git 忽略目录；包含业务数据，不能提交或公开。
- 历史 GitHub 状态保留于 `logs/local-demo/github-state/`，运行 API 已不再挂载。
- 历史私有配置保留于 `logs/local-demo/runtime.github-retired.env`（0600）；当前 `runtime.env` 已删除 GitHub 密钥，集成关闭。

## 本机维护命令

从原项目根目录执行，脚本固定使用本机 Docker Desktop context，配置叠加在 GitHub MVP 工作区 Compose 上：

```bash
bash scripts/local_demo.sh config --quiet
bash scripts/local_demo.sh up -d --no-deps api frontend sandbox-reaper
bash scripts/local_demo.sh exec -T api /app/.venv/bin/alembic current
bash scripts/local_demo.sh exec -T api /app/.venv/bin/alembic check
```

以后修改该部署工作区代码，需要先运行 `bash scripts/local_demo.sh build api frontend sandbox-reaper` 再更新服务。
不要直接在原项目执行未经叠加配置的 Compose 更新；它会切回原分支构建及默认配置。
不要使用 `down -v`，避免删除数据卷。主机入口仅绑定 `127.0.0.1:3000` 和 `127.0.0.1:8000`。

重复执行集成验证（创建并清理独立临时账号和工作区，不读取其他用户内容）：

```bash
docker --context desktop-linux exec -i docker-api-1 /app/.venv/bin/python - \
  < scripts/local_demo_verify.py
# 可选：使用已配置的模型 API，各进行一次 Single/Multi 简短调用，会产生模型用量。
docker --context desktop-linux exec -i docker-api-1 /app/.venv/bin/python - --live-model \
  < scripts/local_demo_verify.py
```

临时账号退出时删除，其会话数据库记录按外键级联删除；Redis 中相关任务缓存按原有 TTL 回收。

## 已验证与边界

当前撤除验证见 `docs/release-evidence/github-retired-20260914.json`。以下保留首次升级时的验证范围，旧证据为 `docs/release-evidence/local-demo-20260914.json`。

- API、前端、MySQL、Redis 健康；前端根页面、版本文件、`/api/health` 返回 HTTP 200。
- 新 API 的 100 个 Python 源文件与部署工作区逐文件摘要一致。
- 临时账号注册、登录、管理员能力查询通过；GitHub 接口现在对普通用户和管理员均返回 404。
- 文件编辑成功，旧摘要再次提交返回 409。
- Docker Shell 非 root、禁网、未继承 GitHub 主密钥；文件回写、下一次执行读取及容器清理通过。
- Single 与 Multi 的 HTTP 真实模型请求均为 200，返回非空内容；历史和 Trace 查询通过。
  此项只验证两个模式的模型连通性，没有触发子 Agent 审查或 GitHub 远程写入。
- 前端 Vite 构建通过；索引迁移 3 项测试和相关 Ruff 检查通过。
- 未验证：交互式浏览器完整点击流程、真实 GitHub Clone/Push、外部编辑器、全量重新回归。

## 回退到本次更新前的应用

旧镜像已保存为 `enterprise-agent-api:before-local-demo-20260914`、
`enterprise-agent-frontend:before-local-demo-20260914`。
原容器环境和数据卷配置保存在私有 `logs/local-demo/rollback.compose.json`，其中包含原环境凭据，不可公开。
回退前确认没有任务正在运行。先用新镜像退回迁移版本，恢复重复索引，避免旧镜像无法识别新 revision：

```bash
bash scripts/local_demo.sh exec -T api /app/.venv/bin/alembic downgrade 20260819_0004
bash scripts/local_demo.sh stop sandbox-reaper
docker --context desktop-linux compose --project-name docker \
  -f logs/local-demo/rollback.compose.json up -d --no-deps api frontend
```

这只回退应用和本轮索引迁移，不清空业务数据。SQL 备份应作为灾难恢复资料；恢复整个备份会覆盖后续数据，不能当作日常切换步骤。
回退配置已保存，但本轮未实际执行应用回退或备份还原演练。

</details>
