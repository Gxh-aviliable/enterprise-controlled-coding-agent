# 2026-09-23 Skill 归属修复与发布

按用户要求，Skill 只分为管理员发布的公共内容和用户管理的内容；项目是用户内容的作用范围。原有四个包迁入公共库，管理员可以查看正文、保存草稿、发布、下架、回滚。新安装不再预装 Skill，旧公共目录不参与运行时发现。聊天保持简洁。

## 当前服务器版本

- 入口：<http://175.24.166.236:8082>。刷新后进入用户菜单 → Admin Control Room → 公共 Skills；原有 python 等条目显示“已发布”，正文可以编辑。
- 当前目录及软链目标：`/home/ubuntu/mini-claude-releases/20260923-skill-ownership`。
- 镜像：`mini-claude-api:20260923-skill-ownership`、`mini-claude-frontend:20260923-skill-ownership`。
- 数据库：`20260923_0008`，升级与 `alembic check` 通过。
- 源码摘要：`6e791f36c9459a50e6135bed9f79a75951d646827b4d4232d1244a11c451305c`，共 284 个交付文件。
- 分支：`feature/enterprise-skills`，未提交、未推送的工作区快照，包含此前已存在的实现。

当前统一使用三层 Compose，不再叠加旧 Skills UI 补丁：

```bash
cd /home/ubuntu/mini-claude-current
docker compose --env-file .env -p mini-claude \
  -f docker/docker-compose.yml -f docker/server.yml -f docker/workbench-server.yml ps
curl --fail http://127.0.0.1:8082/api/health
```

## 数据保全及验证

确认没有活动任务后停止入口/API，备份 MySQL、Redis、workspace、Chroma、包缓存和服务器配置。成功切换对应备份：`/home/ubuntu/mini-claude-backup-20260923-skill-ownership-r2`；第一次尝试的独立备份也保留，未下载业务备份。

迁移前后原有 5 个用户、13 个会话、62 条消息数量一致，117 个 workspace 文件的汇总内容摘要一致。只新增四条公共 Skill 与对应 v1 完整包、迁移审计。迁移之前核验服务器四个旧包的哈希与冻结数据完全一致。MySQL、Redis、回收器及其他服务器项目没有重建。

- 后端回归：888 passed，28 项 opt-in 集成/沙箱测试未纳入此次全量回归。
- 前端：117 passed，生产 build 通过；Ruff 与 Compose config 通过。
- 独立真实 MySQL 8：空库升级不预装；已有用户的 0007→0008 升级保留完整包；Alembic 无漂移。
- Chrome 管理组件搭配真实 MySQL、JWT/RBAC 和 API：旧正文编辑、保存不发布、发布对普通用户可见、下架不复活、回滚恢复、普通用户管理员接口 403 均通过。组件单独挂载，无 API mock；未在生产修改公共正文做测试。
- 线上核对和截图见 `docs/release-evidence/skill-ownership-20260923/`，没有调用真实模型。

线上 Chrome 实测两类筛选、干净聊天、详情焦点/Escape、明暗主题与 1024 布局通过，无 JS/HTTP 错误。临时普通账号 14 已清理，清理后原有行数和 workspace 哈希再次一致；该账号实测读取四个公共详情成功、管理员接口 403。镜像内 117 个后端/迁移文件逐项哈希核验通过，前端 build_id 为 `2026-09-23T05:04:44.882Z`。

第一次切换因临时打包器 `umask 077` 使新建源码子目录成为 0700，镜像内 UID 10001 无法导入配置，在迁移开始前失败。自动恢复旧服务且数据库仍为 0007。随后仅将交付源码目录调整为 0755、源码文件补齐读取权限，服务器 `.env` 继续 0600；重建后先验证非 root 导入和迁移文件读取，再完成切换。以后打包必须保留源码目录遍历权限，或在打包器中规范化权限；不要递归放宽整个发布目录及凭据权限。

## 回退注意

0008 只转换公共内容归属，downgrade 不删除管理员内容。上一版默认启动命令无法识别 0008；备份中的 `rollback.yml` 已配置直接启动旧 Uvicorn，并恢复旧前端镜像。第一次迁移前失败时该恢复流程实测成功。

迁移后回退旧应用会重新显示旧内置目录，可能与已迁移公共项重复，因此不应作为日常切换方案。修复应优先向前发布。不要自动 downgrade、恢复整个旧数据库或 `down -v`；新版本发布后可能已有管理员编辑，旧备份不能覆盖这些数据。
