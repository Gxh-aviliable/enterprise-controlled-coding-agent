# Agent 容器执行沙箱 MVP

> 2026-09-23：Shell 策略已按服务端 `AGENT_EXECUTOR` 区分。Docker 支持内联脚本、变量/替换、管道、重定向、heredoc 和容器绝对路径；普通只读命令自动执行，复杂语法、网络和写操作复用 REVIEW。local 保留旧严格规则。见 [本轮交付与验收](shell-policy-docker-delivery-20260923.md)。

> 2026-09-15：按秋招演示需求，执行容器现默认直接联网，支持pip/npm安装并跨命令复用依赖。本文后续9月14日的禁网说明属于旧版行为，当前用法见[联网与依赖说明](runtime-dependencies.md)。

## 方案与威胁模型（实现前记录）

把模型生成的命令、项目代码、测试和全部子进程视为不可信。保留工具权限、HITL 和命令策略，策略不是操作系统隔离。防止这些进程读取其他租户、API 凭据、Docker socket 或宿主机文件，并限制资源消耗。

可信计算基包括 API/Python 文件工具、容器控制模块、独立回收服务、Docker daemon、管理员配置和预构建镜像。MVP 控制模块在 API 内，通过 Unix socket 调用 Docker Engine；API 和回收服务拥有等同宿主机管理权限。这不是对 API 漏洞的隔离方案，不将 socket 暴露给模型工具或执行容器。容器共享内核，不声称绝对安全。

每次执行创建独立容器，前后台共用 Executor.run(request, cancelled) 接口，返回原有 JSON envelope 加执行器/容器/退出/取消/超时/截断/回写元数据。禁止自动 fallback；local 仅显式配置使用，属于未隔离开发模式。

容器仅挂载当前授权用户工作区的独立过滤快照到 /workspace（可写），不挂载工作区父目录。快照排除敏感文件、Agent 内部数据、软链接、硬链接、特殊文件；镜像和根文件系统只读，/tmp 是有限 tmpfs。非 root UID/GID 与 API 文件所有者一致，cap-drop ALL、no-new-privileges、独立 PID/IPC/network namespaces、禁网、CPU/内存/PID/墙钟时间和捕获输出上限；镜像必须提前构建，不在运行时拉取或安装联网依赖。

Python 文件工具仍在可信 API 内执行，不属于容器。两者共享逻辑授权工作区；快照与回写使用现有 workspace_write_lock，运行期间 Python 可以继续写入；回写对比基线，只写变化文件，冲突报错且不覆盖用户改动。取消/超时/沙箱异常不回写。运行中的后台文件不会立即出现在原工作区；正常退出后发布（非零退出也保留可回溯的修改）。IDE/外部宿主进程不参与锁，须避免同时写；不将这个模式宣传为实时共享目录。

生命周期：snapshot → create（标签标识部署、任务、绝对截止时间）→ start → poll cancellation/timeout → collect → force remove → validate/publish → 删除快照。容器 PID 1 使用镜像内 timeout 防普通孤儿进程；可信独立 reaper 定期删除本部署过期或已退出容器，API 启动也执行回收。只按本部署标签和唯一执行 ID 操作，绝不全局 prune。回收失败明确报告，恢复 daemon 后重试。服务重启不恢复命令，过期回收；旧后台任务内存结果不恢复。

MVP 限制：工作区 bind 的宿主存储无硬磁盘配额（快照输入/回写有大小和文件数上限，执行期磁盘仍需部署专用限额分区）；容器不能防内核逃逸。工作区自己保存的未被规则识别的秘密仍可能被代码读取，禁止将平台秘密放入工作区。快照不支持符号链接、Git 元数据、特殊文件和跨文件原子事务。

技术依据：[Docker 安全边界](https://docs.docker.com/engine/security/)、[资源限制](https://docs.docker.com/engine/containers/resource_constraints/)、[Engine API v1.45](https://docs.docker.com/reference/api/engine/version/v1.45/)。

## 实现入口和执行协议

- `enterprise_agent/sandbox/executor.py`：`Executor` Protocol、不可变 `ExecutionRequest`、Docker/显式 Local 实现；不使用宿主 Shell 调用 Docker。
- `enterprise_agent/sandbox/engine.py`：标准库 Unix socket Engine API v1.45 客户端；在 start **之前**建立 attach，按 Docker multiplex frame 流式读取，stdout/stderr 各限制 `TOOL_SOURCE_CAPTURE_MAX_BYTES` 字节，超出部分持续排空且精确计数；执行容器关闭磁盘日志，不会先写入无限临时文件再截断。返回 UTF-8 解码文本，替换非法字节，去除首尾空白。
- `enterprise_agent/sandbox/workspace.py`：拒绝特殊文件/文件软硬链接，忽略受保护目录及目录软链接，限制快照输入/回写数量和大小。只同步普通文件及其必要父目录，不保留空目录、目录删除、扩展属性或 Git 状态。权限去除 setuid/setgid，发布文件至少对所有者可读写。
- `enterprise_agent/core/agent/tools/shell.py`、`background.py`：前后台及风险分类共用按执行模式选择的 `validate_command`，复用原工具名与审批入口，前后台均调用 `execute`。Docker 文本检查只防明显整根删除、格式化、关机等误操作，不证明脚本无副作用。后台捕获启动时的用户、workspace 和 exact trace，避免线程丢失 ContextVar。
- `core/agent/tools/contracts.py`：保留显式错误码，避免把 `sandbox_unavailable`、取消等错误误归类为 timeout。
- `core/agent/nodes.py`、`sandbox/control.py`：异步工具调用因超时或协程取消结束时向同步执行线程传递 Event；任务 Stop 继续读取现有 exact-trace Redis tombstone。后台也支持同进程 Event、跨进程 tombstone；`cancel()` 先返回请求状态，只有执行器清理结束才写终态。
- `api/main.py`：启动回收过期运行，关闭时通知所有执行线程清理。启动时 daemon 不可用会记录错误，文件/API 功能仍可用，Shell 调用逐次失败，不回退。
- Trace 增加 `execution / agent_executor` 事件，记录执行器、容器 ID、exact trace、退出码、取消/超时、清理、输出计数和发布路径，不重复记录原始 stdout/凭据。后台 `check_background` 也返回执行元数据，保留原工件回执和完成通知。

重要结果字段：`executor`、`execution_id`、`container_id`、`exit_code`、`error_code`、`stdout_original_bytes`、`stderr_original_bytes`、`source_truncated`、`cleanup_confirmed`、`published_paths`。正常非零退出保留实际码；控制错误可能无退出码（-1）。输出统计为完整 attach 字节数，不是 Token 数。清理失败返回 `sandbox_cleanup_failed`，不能把它说成“已取消且已清理”。

## 部署（单机 Linux Docker Engine / Compose）

1. Docker daemon 必须支持 CPU quota、memory/swap、PID 限制。执行器读取 `/info`，缺少任一能力时拒绝运行；本轮真实资源验证使用 cgroup v2。镜像必须可信，不含平台凭据，不声明额外 `VOLUME`，不依赖启动时联网下载。
2. 按既有部署文档配置 MySQL、Redis、LLM/JWT 等平台参数；这些凭据留在 API 环境中。不要将 `.env`、SSH key、云凭据放入用户项目。
3. 提前构建两个独立镜像：

```bash
docker build -f docker/sandbox.Dockerfile -t enterprise-agent-sandbox:1 .
docker build -f docker/sandbox-reaper.Dockerfile -t enterprise-agent-sandbox-reaper:1 .
```

沙箱镜像包含 Python 3.12、Bash、coreutils timeout、pytest 8.4.2。额外语言/依赖应由管理员在构建阶段加入镜像并审查。镜像标签由服务器配置，模型不能选择镜像、挂载、网络或 Docker 参数；建议发布时改用受控镜像仓库中的 digest 固定版本。API Python 版本与执行镜像可不同，提示词明确 Shell OS 为 Linux，不推断用户项目技术栈。

4. 创建独立 staging 目录并配置 socket GID（以下是管理员在**新目录**执行的初始化命令，不要递归改现有工作区所有权）：

```bash
sudo install -d -m 0700 -o 10001 -g 10001 /srv/enterprise-agent/sandbox-staging
stat -c '%g' /var/run/docker.sock
```

将 `.env` 中的 `DOCKER_SOCKET_GID` 设置为上条输出；`SANDBOX_HOST_STAGING_BASE=/srv/enterprise-agent/sandbox-staging` 必须是 **Docker daemon 宿主机** 上的绝对路径。Compose 内 API 使用 `/sandbox-staging`，这两者必须映射同一目录，不能写成 API 内部路径。原 `workspace_data` 命名卷保留，避免迁移已有用户工作区；执行器只复制当前用户允许的文件到 staging，并仅挂载该次快照子目录。

5. 启动：

```bash
docker compose -f docker/docker-compose.yml config --quiet
docker compose -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml logs --tail=50 sandbox-reaper
```

Compose 固定 `AGENT_EXECUTOR=docker`、`SANDBOX_UID=10001`、`SANDBOX_GID=10001`；API 与沙箱文件归属一致。API 和 reaper 使用补充组访问 socket；执行容器**不继承**补充组/socket/平台环境。API 和 reaper 的 socket 访问是主机管理员级能力，socket 挂载即使标记 `:ro` 也不会使 Docker API 变成只读，不要将它当作权限收敛措施。限制平台管理员、服务端配置与镜像发布权限。

工作区/staging 建议位于独立有限额分区：CPU、内存、PID、tmpfs 和捕获输出有硬限制；普通 bind staging 写入尚无独立磁盘硬配额。不能将本 MVP 作为任意公开恶意代码托管服务。

## 配置表

| 配置 | 默认 | 含义 |
| --- | --- | --- |
| `AGENT_EXECUTOR` | `docker` | 仅 `docker` / 显式 `local`；未知值失败 |
| `SANDBOX_IMAGE` | `enterprise-agent-sandbox:1` | 预构建镜像，运行时不 pull |
| `SANDBOX_DOCKER_SOCKET` | `/var/run/docker.sock` | 控制侧 Unix socket，不支持远程 TCP daemon |
| `SANDBOX_DEPLOYMENT` | `enterprise-agent` | 本部署唯一标签；共享同一 daemon 的不同环境必须不同 |
| `SANDBOX_STAGING_BASE` | `/tmp/enterprise-agent-sandbox` | API 可见目录，必须 service-owned，禁止其他用户写入 |
| `SANDBOX_HOST_STAGING_BASE` | 空（同 staging） | daemon 可见目录；Compose 显式设置 |
| `SANDBOX_UID/GID` | 当前服务进程 UID/GID | 必须非零且与 API 身份一致，Compose 固定 10001 |
| `SANDBOX_CPUS` | `1` | CPU quota |
| `SANDBOX_MEMORY_MB` | `256` | 内存；memory-swap 同值，禁止额外 swap |
| `SANDBOX_PIDS` | `64` | 包含 shell、timeout 和全部子进程 |
| `SANDBOX_TMP_MB` | `64` | `/tmp` tmpfs，noexec/nosuid/nodev |
| `COMMAND_TIMEOUT_SECONDS` | `120` | 每次最长运行时间，后台参数只能收紧 |
| `TOOL_SOURCE_CAPTURE_MAX_BYTES` | `4,000,000` | stdout/stderr 各自捕获字节上限 |
| `SANDBOX_WORKSPACE_MAX_BYTES/FILES` | `100,000,000 / 10,000` | 快照/发布上限，非运行期磁盘配额 |

`/workspace` 中代码可执行（如 `python script.py`）；`/tmp noexec` 不能阻止解释器读取脚本，因此它不是第二道代码执行授权机制。权限审批仍在原工具调度链执行，沙箱没有放宽命令黑名单、HITL 或用户工具权限。

## 本地 macOS 与显式开发模式

本地 API 使用实际非 root UID/GID，无需修改仓库 `.env` 中的平台秘密；在当前终端设置覆盖参数：

```bash
export AGENT_EXECUTOR=docker
export SANDBOX_DOCKER_SOCKET="$HOME/.docker/run/docker.sock"
export SANDBOX_STAGING_BASE=/tmp/enterprise-agent-sandbox
export SANDBOX_HOST_STAGING_BASE=/tmp/enterprise-agent-sandbox
```

Docker Desktop 的文件共享必须包含 staging 所在目录。Linux API 在宿主运行时同理，但使用本机 socket。宿主 Windows 的本地后端仍使用 cmd.exe；Docker 后端需要在 Linux API 容器/WSL 中运行（本实现仅 Unix socket）。

需要无 Docker 的开发回归时，显式使用 `AGENT_EXECUTOR=local`；这会在 API 宿主执行代码，不是沙箱。`scripts/smoke_test.py` 是明确的 local 开发烟测入口，其结果包含 `container_isolation_tested: false`。pytest 旧回归 fixture 显式选择 local；独立 `docker_sandbox` 标记的测试必须真实运行 Docker。

## 停止、异常退出与恢复

- 正常完成、非零退出、超时、取消：kill（必要时）并 force remove **唯一容器**，确认后才发布文件/返回清理成功；即使子进程 `setsid` 脱离进程组，也仍在该容器 PID namespace 中。
- 网络/daemon 故障：不再启动宿主命令。无法确认删除时记录 `sandbox_cleanup_failed` 并保留快照；不可声称 cleanup 已完成。恢复 daemon 后 reaper 继续扫描。
- API 崩溃：镜像 PID 1 的 timeout 提供常规截止时间；代码可能暂停同 UID 的 timeout，所以不能只依赖它。独立 reaper 每 5 秒按可信配置的部署标签检查 `enterprise.deadline`（命令最长时间 + 15 秒控制缓冲），删除过期容器，包括已停止但未删除的容器。
- API 重启：启动时进行同一过期回收；尚未到 deadline 的旧命令不恢复到后台任务注册表，等待到期删除。MVP 不恢复崩溃期间输出，不自动重放命令。`live-restore` 等 daemon 行为不会替代 janitor；reaper/daemon 都不可用时没有按时回收保证。
- 遗留快照：仅在没有对应容器且年龄超过 24 小时后删除符合 UUID 命名的目录，避免误删在建快照。reaper 必须访问与 API 相同 staging；不会操作授权用户原工作区。
- 已完成的文件写入不是可撤销事务；Stop 不保证回滚已经发布的修改。合作文件工具受锁保护；直接 IDE/宿主进程写入仍可能与发布竞争，演示时避免同时编辑。多文件发布预检查冲突，但 I/O 故障下不保证多文件原子性。

## 排障

| 现象 | 检查/处理 |
| --- | --- |
| `sandbox_unavailable` | 检查 socket 存在、API 的补充组、daemon `/info` 资源能力、镜像是否已构建；不要开启 local 来掩盖故障 |
| 创建成功但 mount/start 失败 | 比较 `SANDBOX_STAGING_BASE` 与 daemon 的 `SANDBOX_HOST_STAGING_BASE`；Docker Desktop 检查共享目录 |
| UID/GID mismatch / Permission denied | API 和执行容器同 UID/GID；staging 0700 且 owner 正确，禁止用 0777 绕过 |
| `sandbox_oom` / 137 | 查看 Trace 中 OOM 错误；管理员按项目需求调整内存/预构建环境，不能由模型取消限额 |
| `tool_timeout` | 查看容器清理确认；拆分命令或由管理员调整总上限，后台不能无限延长 |
| 工作区 conflict | 用文件工具重新读取当前内容，再生成变更；失败不覆盖已检测到的并发修改 |
| link/special file / snapshot budget | 项目中链接/特殊文件不受支持，或超文件数量/大小；选择允许的普通文件项目/调整管理员上限 |
| `sandbox_cleanup_failed` | 恢复 daemon / reaper；按执行 ID 检查，确认后删除单个容器，禁止全局 prune |
| 背景输出不可恢复 | 内存捕获及后台注册表在 API 崩溃后不恢复；已存 ToolArtifact 与 Trace 保留，按当前任务重新规划 |

只查看本部署的容器（有敏感任务名称时不要公开完整 inspect/env）：

```bash
docker ps -a --filter label=enterprise.sandbox=enterprise-agent \
  --format '{{.ID}} {{.Names}} {{.Status}}'
```

测试与演示见 [sandbox-demo.md](sandbox-demo.md)，本轮证据见 [release-evidence/sandbox-mvp.md](release-evidence/sandbox-mvp.md)。

## 2026-09-14 运行时预检与混合示例镜像

`preflight.py`在镜像inspect后以不可变image ID启动空容器，复用禁网、非root、只读根、资源/时限约束，不挂用户workspace。检测解释器/包管理器及pytest、ruff、mypy模块；只缓存管理员镜像ID的能力（最多32项），不缓存用户项目内容。命令缺少明确必需能力时报`environment_unavailable`，Docker不存在/不具备资源能力仍失败，不回退宿主Shell。

动态项目依赖无法全部静态证明。运行后出现ModuleNotFoundError/Node缺模块时，保留真实非零码和输出，附`dependency_reported_missing`诊断，明确其来自不可信项目输出；它不授权联网安装、重放或宣称测试成功。管理员按项目manifest/锁文件将依赖构建进受控镜像。模型不能修改镜像、网络、挂载和凭据策略。

Python/Node演示复用已存在的本地基础镜像：

```bash
docker build --network=none -f docker/sandbox-node.Dockerfile \
  --build-arg PYTHON_RUNTIME_IMAGE=enterprise-agent-sandbox:1 \
  --build-arg NODE_RUNTIME_IMAGE=enterprise-agent-sandbox-node:1 \
  -t enterprise-agent-sandbox-python-node:1 .
```

管理员须先准备两个受控基础镜像，并在发布manifest记录实际image ID。本地演示override默认选择混合镜像；生产默认Python镜像保持原配置。真实Docker测试可设置`SANDBOX_MIXED_TEST_IMAGE`选择管理员测试镜像。当前实测Python3.12.13、Node22.23.2、pytest8.4.2；不代表支持任意npm依赖、原生扩展或联网安装。
