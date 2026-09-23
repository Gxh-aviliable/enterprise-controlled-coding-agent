# Shell 策略与 Docker 对齐：本地交付

2026-09-23，分支 `feature/enterprise-skills`。保留已有未提交实现，在当前基线上增量修改；没有切换、stash、reset、提交、推送或发布服务，也没有启动旧 Goal。

## 实际修改

- `enterprise_agent/core/agent/tools/shell.py`：服务端 `AGENT_EXECUTOR` 决定策略，前台、后台、风险分类共用入口。Docker 采用有限防误操作检查；local 保持旧校验。模型参数或 Shell 环境赋值不能选择宿主执行器。
- `enterprise_agent/core/agent/tools/contracts.py`：Docker 简单只读命令仍 SAFE；内联代码、复合语法、变量/替换、网络、写操作、嵌套 Shell、`find` 执行/删除/写文件选项、`rg` 外部预处理和未知命令进入 REVIEW。整根删除、格式化、关机等命中有限防误操作检查时拒绝。真实执行器的 stderr 内容不能伪装为 `policy_blocked` 或 timeout。
- `enterprise_agent/core/agent/nodes.py`：提示词按 Docker/local 分别描述路径、重定向、删除、联网和验证证据。默认优先专用删除工具，用户明确要求的工具仍必须遵守。
- `enterprise_agent/core/execution/evidence.py`：仅新增识别尾部精确 `2>&1` 和 Docker 内 `cd /workspace &&`；管道、任意输出重定向、多行、`|| true` 等可执行但不发验证成功收据。真实非零退出仍失败。
- `enterprise_agent/sandbox/storage.py`：Docker Desktop 移除容器后嵌套 bind 目录曾短暂拒绝 rmdir，针对自有快照在 PermissionError 后最多重试两次（50/100 ms）；持续错误继续抛出并记录，不扩展清理范围。
- 定向测试：`tests/core/tools/test_shell.py`、`test_contracts.py`、`tests/core/test_nodes.py`、`tests/core/execution/test_evidence.py`、`test_approvals.py`、`tests/sandbox/test_workspace.py`、`test_docker.py`。
- `scripts/shell_policy_demo.py`：独立合成真实模型任务，复用现有图和审批流程，固定 8 轮/12 次工具/40,000 token/180 秒上限；完整工具工件经 SHA-256 校验后取证，不修改 benchmark 原题。

没有新增依赖、Shell 解释器、权限页面、策略服务或第二套审批。执行器非 root、只读根、资源限制、最小挂载、凭据过滤、用户隔离、链接拒绝、写回冲突和依赖卷均保留。

## 命令差异

“允许”表示通过策略，实际执行仍依赖镜像工具、网络配置、审批和真实退出码。

| 命令 | 原统一策略 / 现在 local | 现在 Docker |
| --- | --- | --- |
| `ls -la` | 允许、SAFE | 允许、SAFE |
| `find . -not -path './.git/*'` | 敏感路径误拦 | 允许、SAFE；`.git` 仍不进入快照 |
| `python -c "print(1 + 1)"` / `node -e ...` | 拒绝内联代码 | 允许、REVIEW |
| `pytest -q 2>&1` | 拒绝 FD 重定向 | REVIEW；保留退出码及测试验证收据 |
| `ls missing 2>/dev/null` | 拒绝 | REVIEW；真实非零退出，不误报成功 |
| `echo "$HOME"` / `echo $(printf ok)` | 拒绝 | REVIEW |
| `curl -I ...` / `wget ...` | 按名称拒绝 | REVIEW；本机基础镜像未装 curl/wget，curl 实际返回 127 |
| `cd /workspace && python -m pytest -q` | 拒绝绝对路径 | REVIEW；`/workspace` 只指容器路径 |
| heredoc / 多行脚本 | 拒绝 | REVIEW；不发复杂脚本的测试通过收据 |
| `rm -- generated.tmp` | 拒绝，提示专用工具 | REVIEW；删除回写并有变更收据 |
| `find . -delete` / `find . -exec ...` | 沿用 local 旧限制 | REVIEW，不能因开头 find 判 SAFE |
| `rm -rf /` / `mkfs.ext4 /dev/sda` / `shutdown now` | 拒绝 | 明确拒绝 |
| `python script.py` / `python -m pip install requests` | 原已允许 | 继续允许、REVIEW，不是本轮新增能力 |
| `pytest -q \| cat` / `pytest -q \|\| true` | 原部分语法可用 | REVIEW；退出 0 也不等于测试通过 |

上述防误操作检查是有限文本匹配，不声称识别所有脚本等价变体。Docker 失效、镜像缺失或资源限制不满足不会回退宿主。网络和依赖卷副作用无法用文件恢复撤销。`.env`、`.git`、私钥和 Agent 内部目录仍被快照过滤；不恢复 GitHub 集成，不承诺一般 Git 仓库操作可用。

## 验证与复现

所有报告在 `docs/release-evidence/shell-policy-20260923-*`。最终源码及锁文件摘要见 `source.json`，最终报告关联和交付包摘要见 `verification.json`。

```bash
.venv/bin/python -m pytest -q \
  tests/core/tools/test_shell.py tests/core/tools/test_background.py \
  tests/core/tools/test_contracts.py tests/core/execution/test_lifecycle_nodes.py \
  tests/core/execution/test_evidence.py tests/core/execution/test_approvals.py \
  tests/sandbox/test_contract.py tests/sandbox/test_workspace.py tests/sandbox/test_preflight.py \
  tests/core/test_nodes.py
.venv/bin/python -m pytest -q -m 'not integration and not docker_sandbox'
RUN_DOCKER_SANDBOX_TESTS=1 RUN_SANDBOX_NETWORK_TESTS=1 \
  SANDBOX_DOCKER_SOCKET=/Users/gxh/.docker/run/docker.sock \
  SANDBOX_REAPER_HOST_SOCKET=/var/run/docker.sock SANDBOX_REAPER_SOCKET_GID=0 \
  .venv/bin/python -m pytest -q tests/sandbox/test_docker.py
PYTHONPATH=. SANDBOX_DOCKER_SOCKET=/Users/gxh/.docker/run/docker.sock \
  .venv/bin/python scripts/shell_policy_demo.py
```

这里的 socket、UID/GID、staging 使用本机实值，不能直接复制为 Linux 服务器配置。测试使用 UID 501/GID 20、独立用户工作区/临时 staging 和测试容器标签；回收器补充组 0 仅用于访问 daemon socket，执行容器仍非 root、没有 socket 挂载。

- 定向回归：355 passed。
- 后端非外部服务回归：1001 passed，31 deselected（显式排除 integration/docker_sandbox）。
- 真实 Docker：22 passed，无跳过。新语法分别从 `bash` 和 `background_run` 入口执行；覆盖输出、退出码、验证/删除收据、文件回写和容器清理。
- 隔离检查：只读根、非 root、capabilities、CPU/内存/PID、受限 tmpfs、平台凭据/其他用户/宿主/socket 不可见、链接拒绝、禁网、冲突拒绝、前后台取消和超时清理、独立回收器均通过；原网络 opt-in 测试真实安装并跨容器复用 pip/npm 依赖。
- HTTP 使用独立带测试标签的 Python HTTP 容器。Bash TCP 发 HEAD 验证联网成功/禁网失败；curl 策略放行后实际因未安装返回 127，没有回退宿主或假称 curl 请求成功。
- 真实 Agent：`deepseek-v4-flash`，独立合成算术文件；先运行内联 Python 输出 2，再修复函数，通过 `cd /workspace && python -m pytest -q 2>&1` 验证，测试文件不变。脚本通过现有 HITL 协议对合成任务自动提供审批决定，未绕过审批校验。这是一次诊断任务，不是全套 benchmark 新成绩，也不是浏览器人工审批验收。
- Ruff（本轮全部 Python 文件）、`git diff --check`、基础 Compose 与三层服务器 Compose `config --quiet` 均通过。前端未改，未重跑前端。

首次 Docker 套件 21 passed、1 failed、1 teardown error：新增测试用了 Docker 29 已移除的顶层 IP 字段；另一次 Docker Desktop 短暂拒绝清理嵌套挂载目录。修正 Networks.bridge 取值并加入有限重试后最终套件通过；初次 XML 保留。真实 Agent 首次已完成任务，但诊断脚本误期望离线 runner 提供 API 入口的 execution 事件，检查失败；改为校验完整工件后通过，初始 JSON 也保留。新增审批过期测试曾设置错已清空的 UI deadline，现测试实际绑定在调用上的 `_approval_deadline`；拒绝与过期均阻止前后台执行，未放松生产审批逻辑。

## 交付与未完成事项

源码归档和 manifest 保留既有 Skill 归属、聊天恢复及 UI 实现，排除本地凭据、工作区、模型缓存、依赖目录、Git 元数据。归档源码目录 0755，文件至少所有者/组/其他可读，避免历史 umask 077 导致非 root 无法导入。解包后应先按 manifest 核验摘要。

本轮仅本地交付：没有构建或启动新的 API 生产镜像，没有访问或部署服务器，没有改数据库、前端运行实例或本地既有业务服务。所观察的本地 `docker-api-1` 仍是旧运行镜像，build manifest 为 `a7bbb693f7c3b0940c3ba5e12b51cde8ec5be32f742243c64abdd6c7364b1ea3`；不代表本轮源码已上线。现有基础沙箱镜像未安装 curl/wget，因此 curl 成功请求尚未验收；正常 HTTP 联网已由可控端点验证。

后续发布必须重新读取服务器实际版本、确认无活动任务并备份，再以唯一 API 镜像标签发布；保留服务器 `.env`、前端及 Skill 数据。本轮不新增迁移，已有 0008 迁移属于既有基线。不要直接运行旧 `server_175_apply_release.py` 或沿用本地旧 Compose 标签替换生产；以发布前实际镜像准备回退，不降级数据库或恢复整库。非外部服务回归未包含 Redis/MySQL opt-in 集成测试，本次未重跑这些独立集成套件。
