# GitHub 连接功能撤除（2026-09-14）

按用户要求，本地和服务器已发布工作台版本，撤除 GitHub 连接、仓库选择、Push 审批页面、客户端请求入口、后端路由及启动恢复。历史内部模块保留但不再对外提供功能，运行配置关闭集成，不持有 GitHub 主密钥或挂载 GitHub 状态目录。

## 运行版本与数据

- 本地入口：http://localhost:3000，API/前端镜像标签为 workbench。实际源码仍在独立工作区 ../my_mini_claude_code-github-mvp。
- 服务器入口：http://175.24.166.236:8082，源码目录 /home/ubuntu/mini-claude-releases/20260914-workbench；mini-claude-current 已切换到此目录，API/前端镜像标签为 20260914-workbench。
- Shell、沙箱、Single/Multi、账号和数据卷保留；MySQL/Redis 没有重建。两端 MySQL revision 均为 20260914_0006，本次没有新增迁移或清空数据。
- 历史 GitHub 状态保留于本地 logs/local-demo/github-state/ 和服务器 /srv/mini-claude/github-state。旧主密钥仅保留在私有旧配置/备份中；本地旧配置为 logs/local-demo/runtime.github-retired.env（0600）。
- 服务器旧发布目录与备份保留；原 mini-claude-code/model-cache 仍承载共享模型缓存，不能删除。此次没有向 GitHub 发起 Token 吊销请求。

## 维护

本地从原工作区执行：

```bash
bash scripts/local_demo.sh config --quiet
bash scripts/local_demo.sh build api frontend
bash scripts/local_demo.sh up -d --no-deps --wait api frontend
```

服务器使用当前目录及新配置层，不再使用历史 github-server.yml 发布：

```bash
cd /home/ubuntu/mini-claude-current
docker compose --env-file .env -p mini-claude -f docker/docker-compose.yml -f docker/server.yml -f docker/workbench-server.yml config --quiet
docker compose --env-file .env -p mini-claude -f docker/docker-compose.yml -f docker/server.yml -f docker/workbench-server.yml build api frontend
docker compose --env-file .env -p mini-claude -f docker/docker-compose.yml -f docker/server.yml -f docker/workbench-server.yml up -d --no-build --no-deps --wait --wait-timeout 180 api frontend
```

更新前确认没有活动任务。本次服务器切换先停止前端接收新请求，再次核对活动任务后替换 API/前端；失败恢复旧版的分支没有触发。不要执行 down -v 或用旧数据库备份覆盖当前数据。

## 验证与边界

- 前端 npm test -- --run：10 个测试文件、99 项通过；两端 Vite/Docker 构建及 Compose 配置校验通过，构建产物无 GitHubSettings。
- 后端 pytest tests/github/test_approvals.py -p no:cacheprovider -q：9 项通过，含路由/启动恢复撤除断言。
- 两端前后端健康，页面/健康接口 200，GitHub 接口 404；公网检查通过。
- 本地 alembic check：No new upgrade operations detected。
- 本地演示探针：登录、普通/管理员 GitHub 接口 404、模式能力、文件编辑、旧摘要写入 409、真实 Docker Shell 禁网、跨调用持久性和容器清理通过。测试账号和工作区已清理。
- ruff check scripts/local_demo_verify.py 与 git diff --check 通过。
- 本次没有重新执行完整浏览器点击、真实模型调用、全量后端回归或备份恢复；此前同日模型和 Docker 测试见历史发布记录。

修改涉及运行工作区的 App.vue、api/client.js、api/main.py、基础 Compose、GitHub 生命周期测试，删除 GitHubSettings.vue 和组件测试；原工作区更新本地/服务器部署覆盖配置、演示探针和文档。分支保留 feature/multi-agent-plan-a / feature/github-shell-mvp，未提交或推送。

证据：[脱敏验证 JSON](release-evidence/github-retired-20260914.json)。
