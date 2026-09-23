# 2026-09-16 本地工作区服务器发布

> 2026-09-23：当前 API 与 `mini-claude-current` 已更新，见 [断线后工具历史修复与维护说明](server-tool-history-recovery-20260923.md)。本文保留 09-16 发布历史。

本次由用户明确要求上传当前本地内容并部署，覆盖此前 Goal 仅本地部署的范围限制。沿用 `mini-claude` Compose 项目、公网端口和服务器 `.env`。不提交或推送 Git，不覆盖用户工作区，不改其他服务器项目。

## 发布内容与版本

- 入口：<http://175.24.166.236:8082>。
- 最终目录：`/home/ubuntu/mini-claude-releases/20260916-workbench-r2`；快捷路径 `/home/ubuntu/mini-claude-current`。
- 四个镜像 `mini-claude-api`、`mini-claude-frontend`、`mini-claude-executor`、`mini-claude-sandbox-reaper` 使用 `20260916-workbench-r2` 标签。
- 最终源码摘要：`17a7741ddcf204a69d91fb73f58785c356d9aa652d21135d45f2e130cf579126`。
- 发布包 SHA-256：`8cb44f9872b6e5296e5b8a38b1463e2f4dc413cc0f48dabc4c5c1522bf337e3d`。
- Git 基础提交 `0648e72`，分支 `feature/verified-agent-delivery`，包含未提交文件；Git HEAD 不能独立代表本次源码。
- 保留聊天内文件变更、可展开 Diff、验证与版本信息、恢复冲突检查、Single/Multi。GitHub 连接仍关闭。没有宣称新增完整统一的“未完成事项”面板。
- 默认开启执行器联网，Python 3.12.13、Node 22.23.2、npm 10.9.8；pip/npm 安装的工作区依赖可跨命令复用。

初版 `20260916-workbench` 部署后，真实 API 冒烟暴露 Linux 上 Docker 自动创建根目录 `node_modules` 挂载点的所有权问题：执行成功但快照清理遇到 PermissionError。R2 在启动 Docker 前由服务用户创建挂载目标，并为全部真实 Docker 用例增加执行快照无残留断言。最初两份自建测试快照已在确认对应容器不存在后删除。保留初版证据，不将其冒烟冒充无问题通过。

## 数据保留与回退

MySQL revision 保持 `20260914_0006`；切换前后 schema 检查通过。不替换 MySQL/Redis 容器和 workspace/chroma/shared-skills 数据卷。原业务数据基线：5 个用户、10 个会话、42 条消息、0 个托管技能。只在自建临时账号上执行功能验证，结束删除临时账号和其工作区。

首次完整备份：`/home/ubuntu/mini-claude-backup-20260916-workbench`；R2 切换前再次备份：`/home/ubuntu/mini-claude-backup-20260916-workbench-r2`。包含私有服务器配置、容器检查信息、MySQL dump、Redis 实际 RDB 路径的快照、workspace/chroma/shared-skills 压缩包。目录权限 0700，敏感文件 0600；不下载凭据和业务备份到本地。

旧版本 `/home/ubuntu/mini-claude-releases/20260914-workbench` 及旧镜像保留。需要应用回退时，从该目录使用三层 Compose `up -d --no-build --no-deps --wait api frontend sandbox-reaper`，验证后再切换快捷路径。数据库版本相同，不执行 downgrade，不自动恢复整个数据库。不要执行历史 `scripts/server_175_apply_release.py` 的旧迁移回退流程。

维护命令（服务器执行）：

```bash
cd /home/ubuntu/mini-claude-current
docker compose --env-file .env -p mini-claude \
  -f docker/docker-compose.yml -f docker/server.yml -f docker/workbench-server.yml \
  -f /home/ubuntu/mini-claude-releases/20260916-diff-colors/docker/frontend-hotfix.yml ps
curl --fail http://127.0.0.1:8082/api/health
```

## 验证证据

- 初版完整本地回归：后端 848 passed、25 deselected；前端 102 passed；前端构建通过。R2 仅增加挂载目录创建及清理断言，沙箱定向回归 15 passed、19 deselected。
- Linux 真实 Docker、联网安装、持久化、用户隔离及版本失效验证分别留存初版和 R2 XML；R2 额外断言快照清理完成。
- 运行镜像 111 个后端/迁移/技能文件逐项 SHA-256 校验；现有 server Dockerfile 仅将 `uv.lock` 包下载地址换成镜像源，反向替换后原锁文件摘要一致。
- 浏览器使用已安装 Chrome 检查公网登录界面、HTTP 200 和页面脚本错误；默认 Playwright 浏览器缓存不存在，未安装额外浏览器。
- `docs/release-evidence/server-20260916-*` 保存各次验证结果；最终部署证据以文件名包含 `r2` 的版本为准。

最终 R2 验收：19 项真实 Docker 测试通过；公网健康返回 R2 摘要，Chrome 登录页无脚本错误；通过实际 API 配置完成 pip cowsay 6.1 和 npm semver 7.7.2 安装、下一次执行复用及快照清理；Single/Multi 真实模型请求均 HTTP 200 并返回内容。原用户、会话、消息和技能数量切换前后不变。最终证据见 [R2 部署清单](release-evidence/server-20260916-r2-deployment.json) 与 [最终应用冒烟](release-evidence/server-20260916-r2-smoke.json)。

## 前端补丁：审批滚动与上传目录

前端镜像单独更新为 `mini-claude-frontend:20260916-ui-fixes`；源码快照位于 `/home/ubuntu/mini-claude-releases/20260916-ui-fixes`，摘要 `7d8525df5ddb5273cb073621894d8ea01eab79f26c8d14936f470fda13865759`（39 个前端/构建文件）。API 的 `/health` 仍报告后端 R2 摘要，前端以 `/version.json` 和 `ui-fixes-20260916-deployment.json` 记录版本，两者不是同一个发布快照。

- 审批弹窗限制为视口高度，正文独立滚动，标题和操作按钮保持可见，窄屏按钮换行。
- 文件面板提供 `Upload to workspace/` 路径输入框；点击目录或文件分别选中该目录或父目录，按钮上传和拖拽共用同一路径，根目录按钮可清空路径。后端已有子目录上传接口，无需改动。
- 上传使用完整目标路径执行未保存编辑检查；部分上传失败仍刷新已完成的文件。新建文件夹使用相同目录，避免将选中文件误作父目录。
- 106 项前端测试及构建通过。Chrome 在 1280×720、390×620、740×360 三种视口验证审批正文滚动和四个按钮可见、可命中、位置不随正文滚动；审批使用模拟 API 数据，没有代替用户批准任务。
- 切换仅执行四层 Compose 的 `up -d --no-build --no-deps --wait frontend`。API、MySQL、Redis、回收器容器 ID 均未变化。前端回退时省略补丁覆盖，运行原三层配置的同一命令即可，旧前端镜像保留。

证据：`docs/release-evidence/ui-fixes-20260916-source.json`、`ui-fixes-20260916-browser.json`、`ui-fixes-20260916-deployment.json`；实际公网上传检查另存 `ui-fixes-20260916-public.json`。

公网实际上传验收通过：独立临时账号从新版按钮上传至 `GXH/nested/upload-probe.txt`，HTTP 200，真实 API 读回一致；根目录切换和页面脚本检查通过。临时账号与测试工作区已清理，未修改 Gxh 的文件或审批。

## Diff 颜色补丁

当前前端镜像更新至 `mini-claude-frontend:20260916-diff-colors`，保留先前审批滚动与目录上传修复。维护时第四层覆盖改用 `/home/ubuntu/mini-claude-releases/20260916-diff-colors/docker/frontend-hotfix.yml`。回退到上一版则改回 `20260916-ui-fixes` 目录的覆盖文件，仅更新 frontend 服务。

`TaskChangesCard` 将 Diff 分行显示：删除为红色、新增为绿色，配淡色背景；文件头保留中性色、hunk 标记使用强调色。浅色与深色主题均验证，原文本、换行及 HTML 转义保持一致。前端源码摘要 `4ed991a6265192d4d706654f3332691ea23f7398feac824f1d9957fa004c32d5`，107 项前端测试及构建通过；证据见 `docs/release-evidence/diff-colors-20260916-*`。
