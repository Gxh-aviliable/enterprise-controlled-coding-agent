# 容器执行沙箱 MVP 验证证据

日期：2026-09-09（Asia/Shanghai）。分支：`feature/container-agent-sandbox`，从 `develop` 创建，基线 `0648e72dad083e21a70eb8811e1e0dc890bd5714`；本次工作和原用户改动均未提交、未推送。

## 已实际运行的验证

| 验证 | 结果 | 证据/说明 |
| --- | --- | --- |
| 项目非外部服务回归 | **755 passed** | [JUnit](sandbox-regression-tests.xml)，权限/HITL/取消/Trace/文件工具等真实业务回归；1 条既有 tokenizer fallback warning |
| 真实 Docker 隔离 | **14 passed** | [JUnit](sandbox-docker-tests.xml)，不是 Mock 隔离测试 |
| 读取项目 → 修复代码 → pytest | **通过** | [JSON + Trace](sandbox-demo.json)：首次退出 1，修复退出 0，复测 1 passed；执行容器均确认清理 |
| 非 root API 容器控制另一执行容器 | **通过** | [JSON](sandbox-controller.json)：API UID 10001，发布文件 UID 10001，daemon 路径映射有效 |
| 独立 reaper 服务镜像 | **通过** | Docker 测试实际启动 reaper 容器，删除过期孤儿，不删除其他部署的运行任务 |
| Shell 策略/错误协议回归 | **通过** | 不放宽黑名单/HITL；显式沙箱错误码不再被误报为 timeout；提前退出 137 与真正 timeout 区分 |
| Ruff | **通过** | `ruff check enterprise_agent tests scripts/sandbox_demo.py scripts/smoke_test.py` |
| 部署配置 | **通过** | `docker compose -f docker/docker-compose.yml config --quiet` |
| Shell 脚本语法和差异空白检查 | **通过** | `bash -n scripts/sandbox_controller_smoke.sh`；`git diff --check` |
| 沙箱镜像 / reaper 镜像构建 | **通过** | 两个 Dockerfile 真实构建 |
| API 当前源码离线覆盖镜像 | **通过** | 基于已有可信 `docker-api:latest`，运行 UID 10001 容器探测；无源码目录覆盖挂载 |
| 原 Dockerfile 的 API 洁净构建 | **未完成** | 拉取 `python:3.11-slim` metadata 时 `context deadline exceeded`；未用替代结果冒充洁净构建 |
| 验证后容器残留 | **无** | `docker ps -a --filter label=enterprise.sandbox` 无输出；探测辅助容器均使用 `--rm` |

## 真实隔离覆盖

`tests/sandbox/test_docker.py` 使用本机 Docker Desktop 4.79.0 / Engine 29.5.3（Linux arm64、cgroup v2），Python 测试控制端 3.12.13/macOS arm64。执行镜像为 Debian bookworm/Python 3.12.13。隔离断言由真实执行容器中的脚本完成：

- 非 root、CapEff 为零、NoNewPrivs=1，不能写 `/etc`，只能写快照和有限 `/tmp`。
- 创建宿主秘密文件、其他用户目录、工作区 `.env`/`.agent` 和平台环境秘密，容器不可见；无法访问 Docker socket 或 API 根目录。
- 无默认路由，TCP 连接失败。Docker Desktop 内核存在未启用隧道接口，因此不以“接口名只有 lo”作为判据。
- cgroup CPU/memory/PID 配置可读且匹配；600 MiB 分配实际触发 256 MiB OOM；实际创建子进程直到 PID 限制；写满 64 MiB tmpfs 出现 ENOSPC。
- stdout 5,000,000 + stderr 2,000,000 字节输出准确计数，各自仅保留 1,024 字节，`source_truncated=true`；无执行容器磁盘日志。
- 前台/后台取消删除整个容器，包含 fork + setsid 子进程；同部署其他任务继续完成，其他部署不被 reaper 删除。
- 异步取消 Event 到同步真实执行器，并在 exact Trace 中记录容器 ID、错误码和清理确认。
- Python 文件工具与 Shell 使用同一逻辑授权工作区，结束后可读到回写；运行期间用户修改同一文件则 conflict，不覆盖用户内容。
- 容器产生的软链接不发布；timeout 不回写部分修改；异常快照目录权限可安全恢复清理而不跟随外部链接。
- 镜像不存在、socket 不可用、授权目录不匹配时失败，宿主标记文件没有出现。

业务权限/HITL/Redis 协议使用项目已有回归测试；Docker suite 不依赖生产 Redis/MySQL 或付费 LLM。这里没有声称对数据库/模型服务做了完整新部署 E2E。

## 镜像标识

- `enterprise-agent-sandbox:1`：`sha256:b6610b2e4b52bedf733e7300d446f83b64a90fff4456eb3442daf2abea9a26be`
- `enterprise-agent-sandbox-reaper:1`：`sha256:8230f2631be7dd766cf238d211b95b24217410066e7e9d20d6f48f9a312b18dd`
- `enterprise-agent-sandbox-api:verified-overlay`：`sha256:7b0377de86cf417e012640fd64f00e932f198127cb35b629af1b579d2057b511`

上述为本机 image IDs。没有 push 镜像或修改远端部署。离线 API 覆盖构建复用现有依赖，依赖清单本轮未变；其复现步骤见 [演示文档](../sandbox-demo.md)。

## 未验证与剩余边界

1. 未在目标 Linux 服务器重新验收；本轮真实 Docker 的内核由 Docker Desktop VM 提供。目标服务器需运行相同真实套件。
2. API 洁净构建受镜像源超时阻碍；重试命令：`docker build -f docker/Dockerfile -t enterprise-agent-sandbox-api:dev .`。
3. 未新启动完整 MySQL/Redis/LLM 工作台做付费模型/浏览器任务；已运行确定性后端完整修复演示、现有业务回归与非 root 容器控制探测。
4. API、Python 文件工具与 reaper 是可信控制面，socket 权限等价主机管理；容器共享内核，不能承诺绝对安全。
5. 快照仅同步普通文件，运行期修改在正常结束后发布；无 staging 磁盘硬配额、无多文件原子提交、无崩溃后输出恢复。IDE 等不合作写入仍需避免并发。
6. reaper/daemon 同时失效时不能承诺按时清理，失败显式报告；后台注册表重启后不恢复。服务级告警、集中审计与配额磁盘为后续加固项。

完整边界及部署排障：[agent-sandbox.md](../agent-sandbox.md)。
