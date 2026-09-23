# 秋招演示：受控 Agent 容器执行

## 讲解顺序（约 5 分钟）

1. 问题：仅用字符串黑名单拦截 Shell，无法控制被执行 Python 项目及其子进程；API 本身部署到 Docker 也不等于隔离每次 Agent 执行。
2. 展示原工具调度链：用户工具权限 → Tool Contracts/HITL → 命令策略 → 统一 Executor。沙箱保持现有审批机制，前后台共享执行器。
3. 每条命令独立容器，只挂载授权用户的过滤快照，Linux 非 root、只读 rootfs、cap-drop、默认断网及 cgroup 限额。说明 API/回收服务 socket 权限仍是可信管理员边界。
4. 执行下面的确定性演示：读取错误函数、第一次 pytest 失败、容器内修复函数、回写后 Python 文件工具看到修复、第二次 pytest 通过。展示 envelope 的容器 ID、退出码、发布路径及清理确认。
5. 展示真实隔离测试和 Trace：其他用户/平台凭据/socket 不可见；OOM、PID、tmpfs、输出限制；取消 detached 子进程、独立任务不受影响、janitor 回收；最后说清快照发布、无硬磁盘配额和共享内核限制。

## 可复现命令

在项目根目录，使用已经安装开发依赖的 Python 环境（本项目 `.venv/bin/python`；也可 `python3 -m uv run ...`）。构建阶段需要访问已批准的镜像/依赖源；执行阶段始终断网：

```bash
docker build -f docker/sandbox.Dockerfile -t enterprise-agent-sandbox:1 .
docker build -f docker/sandbox-reaper.Dockerfile -t enterprise-agent-sandbox-reaper:1 .
# macOS Docker Desktop；Linux 改成 /var/run/docker.sock
export SANDBOX_DOCKER_SOCKET="$HOME/.docker/run/docker.sock"
.venv/bin/python scripts/sandbox_demo.py --output /tmp/sandbox-demo.json
RUN_DOCKER_SANDBOX_TESTS=1 .venv/bin/python -m pytest tests/sandbox/test_docker.py -v
.venv/bin/python -m pytest -q -m 'not integration and not docker_sandbox'
.venv/bin/python -m ruff check enterprise_agent tests scripts/sandbox_demo.py scripts/smoke_test.py
docker compose -f docker/docker-compose.yml config --quiet
```

脚本使用临时独立用户工作区，生成 toy 项目，不读取生产数据，不修改当前项目源码。它真实调用现有 Python 文件工具和真实 Docker executor，并保存 Trace；这是**操作员驱动的确定性后端演示**，不是本轮已验证的付费 LLM/浏览器全链路。命令策略在执行前校验；操作员选定的示例步骤代表已授权命令，不额外造出新审批渠道供 Agent 使用。

真实 Docker 测试有显式标记。未设置 `RUN_DOCKER_SANDBOX_TESTS=1` 时跳过；设置后 daemon 或镜像缺失将失败，不能把跳过算作隔离通过。Linux 下测试必须以能访问 Docker socket 的非 root 服务用户运行，因为执行器拒绝 root UID/GID。测试只删除自身随机部署标签/独立名称的容器，不运行全局 prune，不碰生产卷。

## 工作台现场演示（需要部署环境及 LLM 服务）

先按 [agent-sandbox.md](agent-sandbox.md) 部署，在专用演示账号工作区准备同样的 `app.py` 和 `test_app.py`，发送：

> 请先读取项目和测试，定位 add 函数问题，修改后运行 pytest 验证，并说明测试结果。所有命令在当前工作区执行。

按平台现有 HITL 提示审阅具体写入和 Shell 命令再批准。打开任务 Trace，展示工具事件与 `agent_executor` 容器 ID/exit_code/cleanup_confirmed。随后用专用 `long.py` 启动后台任务并 Stop，展示 exact trace 清理和其他任务继续执行。脚本内容由管理员提前准备，不能为了演示放宽 Shell 黑名单或关闭审批。

本轮不依赖真实付费模型、账号数据库或浏览器跑通这一步；后端真实容器行为和现有 HITL/权限/取消回归分别有证据。现场演示前在目标 Linux 环境跑同一真实隔离套件。

## 已保存证据

- [自动演示 JSON 与 Trace](release-evidence/sandbox-demo.json)
- [Docker 隔离验收 JUnit](release-evidence/sandbox-docker-tests.xml)
- [项目回归 JUnit](release-evidence/sandbox-regression-tests.xml)
- [UID 10001 API 容器控制与回写](release-evidence/sandbox-controller.json)
- [环境、结果与剩余边界](release-evidence/sandbox-mvp.md)

## 容器内控制器部署探测

`tests/sandbox/container_controller_probe.py` 在 API 镜像内运行，以 UID/GID 10001 创建独立沙箱、检查 daemon bind 路径映射并确认回写文件归属。可用正式构建好的 API 镜像设置 `SANDBOX_API_TEST_IMAGE`：

```bash
SANDBOX_API_TEST_IMAGE=your-reviewed-api-image \
  bash scripts/sandbox_controller_smoke.sh /tmp/sandbox-controller.json
```

脚本仅对新建临时 staging 目录使用一次受限的 root chown 辅助容器；实际 API 和执行容器都是非 root。Linux 设置正确的 `DOCKER_SOCKET_GID`。探测代码随仓库只读挂载，API 实现直接使用指定镜像里的代码，不挂载生产 `.env` 或工作区卷。

本轮全量 API 构建因远端 `python:3.11-slim` metadata 超时未完成。已有可信 `docker-api:latest` 中依赖未变化，使用以下离线覆盖方式完成当前 API 源码打包并运行上述探测；这不是洁净重建成功的证明：

```bash
docker build -f - -t enterprise-agent-sandbox-api:verified-overlay . <<'DOCKERFILE'
FROM docker-api:latest
COPY --chown=10001:10001 enterprise_agent /app/enterprise_agent
DOCKERFILE
bash scripts/sandbox_controller_smoke.sh /tmp/sandbox-controller.json
```

镜像源恢复后仍需执行：`docker build -f docker/Dockerfile -t enterprise-agent-sandbox-api:dev .`。
