# 2026-09-23 Skills 服务器发布

> 最新版本已由 [Skill 归属修复发布](server-175-skill-ownership-20260923.md) 替代；下文保留前两次发布的历史证据，当前维护请使用新文档。

用户在本地交付后明确要求“同步到服务器上面我看看效果吧”，本轮按此授权完成服务部署及必要迁移。入口为 <http://175.24.166.236:8082>，刷新页面后点击左侧 **Skills**；聊天输入区展开 **Skills** 可选择当前账号可见的条目。

## 发布内容

- 当前目录：`/home/ubuntu/mini-claude-releases/20260923-skills`；`/home/ubuntu/mini-claude-current` 指向此处。
- API 和前端：`mini-claude-api:20260923-skills`、`mini-claude-frontend:20260923-skills`。
- 执行器/回收器：保留 `20260916-workbench-r2`。MySQL、Redis、回收器容器没有重建；未改其他服务器项目。
- 数据库：`20260914_0006` → `20260923_0007`，`alembic upgrade head` 和 `alembic check` 通过。
- 最终源码摘要：`e1f33eb48dd008ff1517723f002abf8fc638432dc1cddb2021eba8a56858859c`。285 个源码/构建/测试文件逐一核验，运行镜像中 118 个后端/迁移/内置 Skill 文件核验一致。Git 分支 `feature/enterprise-skills`，未提交、未推送的工作区快照；包括原有未跟踪实现及今天的工具历史修复。
- 沿用服务器 `.env`、模型缓存、端口与数据卷；没有同步本地凭据、workspace 或数据库。API 安装 Git 2.47.3，确认支持 DNS 固定配置；Nginx ZIP 上限由 8 MiB 对齐到 16 MiB。

## 数据与实际验证

切换前关闭公网入口并确认没有活动任务，停止 API 后备份 MySQL、Redis、workspace、Chroma、shared-skills 和配置。备份保存在服务器 `/home/ubuntu/mini-claude-backup-20260923-skills`，权限受限，不下载业务备份。

迁移前后 5 个用户、13 个会话、62 条聊天消息数量一致，117 个 workspace 文件的汇总内容摘要一致。备份及之前的工具历史版本镜像保留。

线上真实接口验证通过：未认证拒绝、ZIP 完整三文件预览/安装、禁用/启用、旧版本冲突 409、另一个用户猜 ID 读写 404、目录隔离及卸载。两个临时普通账号（11、12）、导入预览和 workspace 已删除，未创建管理员或操作原有用户任务。

Chrome 直接访问公网，使用真实认证和 Skill API，无请求 mock：Skills 文件树、ZIP 发现预览、聊天指定稳定 ID 均通过，无 JS 或 HTTP 错误。截图保存在 `docs/release-evidence/skills-20260923/server-*.png`。

服务器 GitHub 子目录导入实测 HTTP 200（约 3.23 秒）：`anthropics/skills` 的 `skills/algorithmic-art`，commit `34040c9c568585f6929bedeaad110ad08f079624`，发现 4 文件完整包。本次网络成功不代表公网长期稳定，离线场景仍可使用 ZIP。

真实 Docker 验证成功执行已安装 Skill 的 `scripts/check.py`，返回 `skill-demo-ok`，读取参考文件；写入资源返回只读拒绝，执行容器清理，资源没有发布回用户 workspace。没有调用真实模型；这里验证的是部署、认证、管理与运行资源链路。

验收发现旧 shared-skills 卷根目录为 `root:root 0755`，首次任务快照写缓存失败。已仅修正卷根目录为 `10001:10001`，复验通过；两个 API Dockerfile 均预建正确归属的目录，修复已固化到最终镜像。失败证据保留。冒烟脚本最初误把安装成功状态 201 当作 200，修正测试预期后继续验证，没有修改正确的 API 状态码。

## 同日 Skills 界面优化补丁

按用户截图反馈，前端单独升级为 `mini-claude-frontend:20260923-skills-ui`，源码目录 `/home/ubuntu/mini-claude-releases/20260923-skills-ui`。完整 API 版本和 `mini-claude-current` 保持原 Skills 发布，未重建 API、MySQL、Redis、回收器。

- 使用 frontend-design：技能卡片铺满可用宽度，分类/搜索，点击查看详情侧栏，添加使用独立原生模态弹窗。默认不展示稳定 ID、哈希、原始来源 JSON、文件树或原始 YAML。操作/文件/技术信息按需展开，支持明暗主题和键盘返回焦点。
- 聊天移除常驻 Skills 配置面板与额外目录请求，保留后台自动使用和文字调用；移除模式栏的内部工程说明。主题按钮阻止冒泡，避免切换主题时触发新会话。
- 前端源码摘要 `6a66d782b40a2bc13303ce30c543c67d1b5016cf50897e47fdf29506de27555c`（41 个文件）；前端 build_id `2026-09-22T19:33:00.048Z`。API 的 `/health` 继续报告后端原摘要，两者代表不同发布。
- `npm --prefix frontend test`：116 passed；`npm --prefix frontend run build` 通过。Chrome 使用真实服务器认证/Skill API，完成模板预览/添加/启停、分类、详情焦点/Escape、干净聊天、明暗主题和 1024 布局验证；无 API mock 或模型调用。390 宽度仅测试 Skills 组件（测试中隐藏原有桌面侧栏），不宣称整个工作台已适配手机。
- 前端补丁证据：`docs/release-evidence/skills-ui-20260923/`。临时普通账号及其数据验收后清理；本次不改业务账号和用户任务。

只回退本次界面时，在 `/home/ubuntu/mini-claude-current` 执行三层 Compose、不加新的前端 overlay，`up -d --no-build --no-deps --wait frontend` 即可恢复 `20260923-skills` 前端，不重启 API 或迁移数据库。

## 维护和回退

当前维护使用三层 Compose 加新前端覆盖：

```bash
cd /home/ubuntu/mini-claude-current
docker compose --env-file .env -p mini-claude \
  -f docker/docker-compose.yml -f docker/server.yml -f docker/workbench-server.yml \
  -f /home/ubuntu/mini-claude-releases/20260923-skills-ui/docker/frontend-hotfix.yml ps
curl --fail http://127.0.0.1:8082/api/health
```

需要回退应用时，先确认无活动任务，再使用旧目录及已准备且通过 Compose 校验的回退配置：

```bash
cd /home/ubuntu/mini-claude-releases/20260923-tool-history
docker compose --env-file .env -p mini-claude \
  -f docker/docker-compose.yml -f docker/server.yml -f docker/workbench-server.yml \
  -f /home/ubuntu/mini-claude-backup-20260923-skills/rollback.yml \
  up -d --no-build --no-deps --wait --wait-timeout 180 api frontend
curl --fail http://127.0.0.1:8082/api/health
```

回退配置直接启动旧 Uvicorn，跳过旧镜像无法识别 `0007` 的启动迁移，保留新增数据库列、安装和预览数据。确认健康后再调整 `mini-claude-current`。不要直接执行旧镜像默认启动命令、自动 downgrade、恢复整个旧数据库或 `down -v`。应用回退命令仅校验配置，未实际执行。

证据目录：`docs/release-evidence/skills-20260923/`；`server-source.json` 为源码清单，`server-deployment.json` 为首轮迁移/数据保全记录，`server-final.json` 为最终镜像与健康核对，另有真实 API、Git、浏览器、沙箱和清理记录。
