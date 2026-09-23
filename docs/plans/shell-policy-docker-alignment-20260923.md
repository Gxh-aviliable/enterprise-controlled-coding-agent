# Shell 策略适配 Docker 沙箱：执行计划与状态

- 状态：本地实现、自动化/真实 Docker/真实 Agent 验收及交付已完成，未发布服务器。结果、限制及失败修复记录见 [交付报告](../shell-policy-docker-delivery-20260923.md)。
- 制定日期：2026-09-23。
- 项目：`/Users/gxh/学生时代/my_mini_claude_code`。
- 目标：减少正常开发命令被 `policy_blocked` 拦截，让现有 Docker 隔离承担主要执行边界，同时保留必要的用户隔离、审批、执行记录和文件冲突检查。
- 用户偏好：秋招展示项目，优先功能完整和实现简单，不增加权限管理页面、通用 Shell 解析器、新服务或一套复杂策略配置。
- 执行方式：使用 Ponytail skill，先读代码和调用链，复用已有机制；按阶段完成实现与验证。不要重新启动此前的 A–L 全项目改进 Goal。

## 一、已确认的问题

`validate_command` 当前同时用于前台 `bash`、`background_run`、`resolve_tool_risk`，不区分 Docker 与 local 执行环境。

2026-09-23 只调用校验函数，未实际执行命令，观察到：

| 命令 | 当前结果 | 本次目标 |
| --- | --- | --- |
| `find . -not -path './.git/*'` | 命中敏感路径，拒绝 | Docker 下允许正常目录筛选，不把排除条件当成访问凭据 |
| `python -c "print(1 + 1)"` | 内联代码被拒绝 | Docker 下允许，经正常风险分类和审批 |
| `pytest -q 2>&1` | 重定向被拒绝 | 允许正常重定向，执行和验证结果仍准确 |
| `ls missing 2>/dev/null` | 丢弃输出被拒绝 | 允许，保留实际非零退出码，不能误报成功 |
| 含 `$` 的命令 | 不分上下文全部拒绝 | Docker 支持普通变量/替换语法；不能因此被误分为 SAFE |
| `curl -I https://example.com` | curl 全部拒绝 | 在允许联网的 Docker 内可调用；是否安装与执行成败单独处理 |
| `cd /workspace && python -m pytest -q` | 绝对路径被拒绝 | 容器内合法绝对路径可用，不映射为宿主机真实目录 |
| `python script.py`、`python -m pip install requests` | 已允许通过文本检查 | 保留，不宣称本次新增了脚本执行/联网安装能力 |

单纯禁止 `python -c`，同时允许运行同样内容的 Python 文件，不能构成可靠的能力隔离。Shell 文本检查不能证明任意脚本没有副作用；不要继续靠扩大黑名单追求这种保证。

## 二、执行前核对与保护

- [x] 阅读仓库 `AGENTS.md`、Ponytail skill、本计划、当天日志及最新部署文档。
- [x] 核对分支、未提交改动、当前源码和运行版本；只读识别其他窗口的改动，不覆盖、reset、clean、自动 stash、提交或推送。
- [x] 制定时分支为 `feature/enterprise-skills`，存在大量既有未提交内容。按 AGENTS.md 处理分支；若需要隔离，必须保留当前实现作为基线，不能从干净 develop 丢失现有功能。
- [x] 阅读 `docs/server-175-skill-ownership-20260923.md`。最近文档记录服务器为 `20260923-skill-ownership`、数据库 `20260923_0008`；这些只是交接信息，部署前必须重新核实。
- [x] 保留已上线的 Skill 归属功能、断线消息修复、Diff 颜色、目录上传、审批滚动及执行器联网。不要套用旧服务器脚本或回退到 `20260923-tool-history` / 09-16 的整包源码。

## 三、实现范围与处理原则

### 1. 按真实执行环境区分策略

- [x] 前台、后台及风险分类复用同一份策略入口，避免审批说可执行、实际执行又被旧规则拒绝。
- [x] Docker 的策略选择只来自服务端 `AGENT_EXECUTOR`，不能由模型工具参数、用户文本或环境赋值决定。
- [x] Docker 模式支持正常 Shell 语法，包括管道、重定向、变量、命令替换、多行脚本/必要的 heredoc、内联 Python/Node 和容器内部路径。
- [x] local 模式保留原严格限制。Docker 不可用、镜像缺失、资源约束不满足时明确失败，不回退宿主 Shell。
- [x] 优先采用少量分支和现有函数。不要为了静态理解任意 Shell 编写解释器；不要新增每种语法一个配置开关。

### 2. 明确允许、审批与拒绝的边界

- [x] 普通简单只读命令保持可自动执行；不能为了放宽命令，把所有 Shell 都改成必须审批。
- [x] 内联脚本、复合命令、命令替换、网络访问及写操作按现有 REVIEW 路径处理。不能仅因命令开头是 `echo`、`find`、`ls` 就判 SAFE。
- [x] 对语义不确定但 Docker 可以容纳的命令，采用 REVIEW，而不是一律 `policy_blocked`。检查 `find -exec`、`find -delete`、重定向、变量和嵌套 Shell 等情况，防止错误归为 SAFE。
- [x] 不增加第二套审批。复用现有审批范围、过期、工作区版本及用户权限校验。
- [x] Docker 下允许针对明确工作区临时文件的常规删除，例如 `rm -- generated.tmp`，经 REVIEW 且有变更收据；Agent 默认仍可优先用 `delete_paths`。用户明确要求某个专用工具时必须遵守，不能为了通过新策略修改任务要求。
- [x] 保留对明显灾难性整根目录删除、格式化、关机等请求的明确拒绝，不为这些操作新增能力。此处是有限防误操作检查，不声称能识别所有脚本等价变体。
- [x] Docker 内的 `curl`/`wget` 不再仅因名称被拒绝；沿用既有联网配置和 REVIEW。镜像未装工具应报告真实缺失，不能自动转宿主机执行。
- [x] 网络关闭时仍由执行器禁网；网络开启不等于允许工具获得平台 API Key、Docker socket、其他用户文件或宿主挂载。不要新增网络管控服务作为本次前提。

### 3. 保留真实隔离与发布检查

- [x] 不变更非 root、只读容器根、能力裁剪、CPU/内存/PID/超时约束及限定挂载。
- [x] 平台凭据不进入执行器环境；工作区快照继续过滤敏感文件和 Agent 内部目录。修复 `.git` 排除参数误拦截，不意味着把整个 `.git`、`.env` 或私钥复制进容器。
- [x] 允许容器内 `/workspace`、`/tmp` 等路径，不向模型暴露或挂载宿主真实工作区目录。
- [x] 保留用户隔离、快照链接检查、写入回收、版本冲突检查和停止后的清理逻辑。
- [x] 保留依赖安装与跨命令复用机制。联网操作、已写入依赖卷等外部副作用不能冒称可随文件恢复一起撤销。
- [x] 不恢复已撤除的 GitHub 集成。由于 `.git` 仍被过滤，不承诺一般 Git 仓库操作自动变得可用。

### 4. 对齐提示词、错误分类与验证证据

- [x] 更新模型系统提示里的绝对禁止语句，例如永远不能用绝对路径、必须分开 stdout/stderr、所有删除只能走专用工具，使其与当前执行模式一致。
- [x] `policy_blocked` 只表示实际策略拒绝；命令未安装、测试失败、网络不可达分别保留真实错误，不混成策略拦截。
- [x] 放宽重定向/管道后，验证识别仍保守：`echo pytest`、`pytest --version`、失败测试后 `|| true`、被管道掩盖的失败不能获得“测试通过”的证据。无法可靠识别的复合命令可以执行，但不发行为验证通过收据。
- [x] 前端可继续展示已有工具错误，不新增独立功能页面。仅在确有需要时调整现有说明文字。

## 四、主要阅读与修改入口

按实际需要修改，不要求机械改完所有文件：

- `enterprise_agent/core/agent/tools/shell.py`：校验与前台执行。
- `enterprise_agent/core/agent/tools/background.py`：后台执行入口。
- `enterprise_agent/core/agent/tools/contracts.py`：SAFE/REVIEW/DANGEROUS 与错误归一化。
- `enterprise_agent/core/agent/nodes.py`：模型提示、工具审批与执行。
- `enterprise_agent/core/agent/tools/workspace.py`：敏感及内部路径定义。
- `enterprise_agent/sandbox/executor.py`、`workspace.py`、`preflight.py`、`dependencies.py`：真实边界、快照回写、能力探测和依赖。
- `enterprise_agent/core/execution/evidence.py`、`approvals.py`：验证证据和审批绑定。
- `tests/core/tools/test_shell.py`、`test_background.py`、`test_contracts.py`；`tests/core/execution/test_lifecycle_nodes.py`、`test_evidence.py`、`test_approvals.py`；`tests/sandbox/`。
- `docs/agent-sandbox.md`、`docs/runtime-dependencies.md`：核对旧说明，不把已经开放的联网安装写回“默认禁网”。

## 五、验收顺序

### A. 定向自动化回归

- [x] 用参数化测试覆盖上面的误拦截案例，分别验证 Docker 与 local。
- [x] 校验风险分类和实际执行一致；至少覆盖内联代码、`find -exec`、变量/替换、重定向、网络、明确工作区删除、灾难性请求。
- [x] 前台与后台行为一致，用户拒绝和过期审批仍生效。
- [x] 保护旧测试的真实目标；只调整确实改变的策略预期，记录原因。不要删除负例、降低断言或修改模型 benchmark 原题来刷通过率。

参考命令，先确认当前环境依赖可用：

```bash
.venv/bin/python -m pytest -q \
  tests/core/tools/test_shell.py tests/core/tools/test_background.py \
  tests/core/tools/test_contracts.py tests/core/execution/test_lifecycle_nodes.py \
  tests/core/execution/test_evidence.py tests/core/execution/test_approvals.py \
  tests/sandbox/test_contract.py tests/sandbox/test_workspace.py tests/sandbox/test_preflight.py
```

### B. 真实 Docker 验证，不能只停留在校验函数通过

- [x] 使用独立测试用户/工作区、测试容器标签和 staging，不使用真实用户项目。
- [x] 从 `bash`/后台工具入口实际运行新允许的命令，验证输出、退出码、收据、变更文件和容器清理。
- [x] 验证普通重定向、内联脚本、管道、命令替换、必要的多行/heredoc、`/workspace` 路径、`.git` 排除条件。
- [x] 使用专门的合成文件验证工作区删除及其变更记录；网络请求使用可控测试端点或无敏感数据的公开地址。
- [x] 同时验证只读根、凭据/其他用户文件/socket 不可见，禁网模式仍有效、写回冲突仍拒绝、取消/超时能清理。
- [x] 测试镜像、UID/GID、Docker socket、daemon 可见 staging 路径按实际环境设置。macOS Docker Desktop 与 Linux 不同，不硬编码 root 或凭空填宿主路径。

现有真实测试采用 `RUN_DOCKER_SANDBOX_TESTS=1` 开关；参考：

```bash
RUN_DOCKER_SANDBOX_TESTS=1 .venv/bin/python -m pytest -q tests/sandbox/test_docker.py
```

此命令以配置完成为前提。Docker 不可用时记录阻塞，不能把跳过当作验收通过。

### C. 完整回归与版本绑定

- [x] 运行后端非外部服务回归：`.venv/bin/python -m pytest -q -m 'not integration and not docker_sandbox'`。
- [x] 运行实际修改文件的 Ruff、`git diff --check`；修改提示词或模型节点时覆盖相关节点测试。
- [x] 前端未改不强制重复前端全套；若改前端，执行 `npm --prefix frontend test` 和 `npm --prefix frontend run build`。
- [x] 必要的 Compose 配置检查使用 `config --quiet`，不输出包含凭据的展开配置。
- [x] 用一个合成任务验证真实 Agent：先执行曾被误拦的正常命令，再修复一个测试、验证结果。使用现有模型配置和小预算，不把用户真实聊天/文件发作评测数据。
- [x] 无模型配置时明确标记真实 Agent 验收未完成，不用 Mock 冒充。
- [x] 生成源码/锁文件摘要和验证报告，绑定到同一快照；保留旧报告，不把本轮定向结果冒充 30 题新成绩。

## 六、部署准备与可选发布阶段

本计划必做交付是实现、测试、文档与可部署版本。生产发布遵循新窗口中的有效用户授权；已授权则继续，不重复询问。若新窗口仅要求本地执行，则完成发布准备后交付，不自行扩大到服务器。

- [ ] 发布前重新核对服务器，不凭本文日期或旧标签猜测运行版本。已知 SSH 别名 `mini-claude-server`，公网 `http://175.24.166.236:8082`，软链 `/home/ubuntu/mini-claude-current`，Compose 项目 `mini-claude`。
- [ ] 使用最新发布源码，保留服务器 `.env`，不上传本地凭据；本次预计不需要数据库迁移。
- [ ] 确认没有正在执行的用户任务，备份当前配置、数据库、Redis 与业务数据。不得直接中断其他用户任务来完成切换。
- [ ] 新镜像使用唯一标签；先检查非 root 能导入模块、源码目录可遍历。此前发生过打包 umask 077 导致源码目录 0700、API 无法启动的问题，避免重现。
- [ ] 尽量只替换 API；只有镜像确实缺少本次必需命令时，才最小更新执行镜像。保留当前前端与 Skill 数据，不重建数据库或执行 `down -v`。
- [ ] 当前三层配置为 `docker/docker-compose.yml`、`docker/server.yml`、`docker/workbench-server.yml`；执行前检查最新配置，不能套用已过期的前端补丁覆盖。
- [ ] 验证公网健康、运行源码摘要、临时账号工具调用及数据保留；测试后只清理自身资源。
- [ ] 记录回退到本次发布前镜像的办法，不降级数据库，不自动恢复整个旧数据库。

## 七、交付清单与停止条件

- [x] 实际改动及每项理由。
- [x] 一张旧行为→新行为的命令对照表，明确 Docker/local 差异。
- [x] 自动化、真实 Docker、真实 Agent 各自的结果及对应版本；未验证事项明确列出。
- [x] 当天 `docs/dev-logs/YYYY-MM-DD.md`，说明当前分支、未提交/未推送状态。
- [ ] 本轮未发布，不适用：若发布：运行版本、备份、健康检查、数据保全、回退说明。

验收完成的含义：正常开发命令不再因旧语法规则被无差别拒绝；有副作用的命令没有被误分 SAFE；用户隔离、凭据过滤、审批和文件发布检查仍正常；真实 Docker 证据与交付代码对应。不能以“删掉黑名单、单元测试通过”作为完成标准。
