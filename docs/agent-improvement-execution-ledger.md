---
title: Agent 改进实施台账
date: 2026-09-14
tags: [implementation, verification]
---

# Agent 改进实施台账

权威需求：[完整计划](open-source-agent-improvement-plan-20260914.md)。目标附件中的补充验收同样适用。状态仅使用未开始、进行中、实现待验证、已验证、阻塞、条件未触发；阶段完成不代表 Goal 完成。

## 来源与保护

- 唯一实施来源：`/Users/gxh/学生时代/my_mini_claude_code`，`feature/verified-agent-delivery` 从 `develop` (`0648e72`) 创建，原有未提交修改原地保留。没有提交、推送或合并。
- 独立工作区 `../my_mini_claude_code-github-mvp` 保持只读。初始源码摘要见 `release-evidence/goal-initial-workspaces-20260914.json`。
- 实查运行来源仍是独立工作区 Compose + 主目录 `docker/local-demo.yml`；本地 API 镜像 ID `sha256:800b77bd0817ad4480ad01d1e93f6676a7723de9636827a1dfd1bd90835ebd1b`。该初始版本已在14:35经备份验证后切换为主目录09f808快照；新镜像与卷映射见goal-local-delivery。
- 两工作区差异已逐项读取：GitHub 内部钩子、配置、cryptography 显式依赖、Git 镜像包为主要差异；前端、迁移与主干沙箱/Multi 实现一致。主目录没有 GitHub 公开入口，不迁入内部钩子；历史模块和备份留在独立目录。后台执行的上下文设置在 B 中按实际需要核对。
- 仅本地操作；不连接远程服务器，不删除数据卷，不改真实用户文件，不改历史评测。凭据和私有备份不得进入证据。

## 工作包与验收

| 包 | 状态 | 需求与验收 | 当前实现与差距 | 修改/验证证据 | 依赖、风险、剩余事项 |
|---|---|---|---|---|---|
| A | 已验证 | 源码/锁/镜像/DB/报告绑定与唯一运行来源 | release.py、local_demo.sh；原本地已更新09f808，健康/source/卷均核对 | goal-local-delivery、发布manifest、备份验证 | 未提交快照，official守卫保留 |
| B | 已验证 | 实际变更收据、版本验证、失败/取消/部分发布 | evidence.py/shared executor；净变更临时脚本修复 | 846后端、18真实Docker、JSON单例1/1 | 保留未知副作用；语法不等于行为 |
| C | 已验证 | 30例基线、旧失败、安全与真实浏览器 | 完整诊断21/30与25/30；最终Chrome9条断言及越权通过 | goal-c-baseline-analysis、browser-delivery、安全压力4/4 | 模型格式/指令遵循失败如实保留 |
| D | 已验证 | 真实Diff、有限文本恢复、后改冲突拒绝 | changes.py、TaskChangesCard、归属路由 | 单元中断/冲突/隔离；真实Docker和浏览器恢复 | 文本限额与7天；不恢复外部效果 |
| E | 已验证 | 先测定位成本、无收益撤回 | 固定4例原版4/4、试验3/4，已撤回 | goal-e-comparison | 额外索引/解析依赖条件未触发 |
| F | 已验证 | 运行时/依赖预检与管理员Python/Node镜像 | preflight.py、sandbox-node.Dockerfile、稳定本地标签 | 18真实Docker含两语言测试 | 静态能力有界；动态依赖诊断不授权安装 |
| G | 已验证 | 故障矩阵、fence/事件补取、终态不重放 | events/streaming，30秒活动租约，状态收敛 | 真实断网/API/Redis/发布后崩溃/旧租约均通过 | 持久后台runner条件未触发；未决收据保留 |
| H | 已验证 | 完整审批理由/参数与不可复用范围 | approvals.py、实时权限与实际锁内scope校验 | 118及后续回归；真实人改文件使审批失效 | 首写后剩余调用需重新确认；权限不放宽 |
| I | 已验证 | 6例至少3次配对、同预算范围与成本 | 36次全部保留；Single17/18、Multi9/18 | goal-i-paired-analysis/index/raw reports | Single默认；没有写入型子Agent扩展 |
| J | 已验证 | 约20例准入/召回/生命周期/实际行为 | 版本化21例；本地17/17；另次模型3/4 | goal-j-memory-analysis，原始20/21例报告 | 攻击记忆未召回，不声称暴露后抵抗 |
| K | 已验证 | 容量/保留/告警/备份与可信边界 | Trace服务控制目录、旧记录未验证、限额与清理 | 容量before/after、备份暂存恢复、27项治理检查 | 增量DB/multiworker条件未触发；保留未决证据 |
| L | 已验证 | 沿实际修改路径单一职责拆分及兼容 | 收据/恢复/事件/流清理/审批/存储治理模块 | 完整回归与旧租约/Trace兼容验证 | 不删除历史checkpoint、用户数据或私有备份 |

## 执行记录

### 2026-09-14 初始核对

- 已完整读取 AGENTS、计划、调研证据、9/13–9/14 日志、本地部署及 GitHub 撤除说明；两工作区代码差异已读取。
- Docker 初次受 sandbox socket 权限限制；经允许的本地只读提升后获取容器列表和实际 Compose 来源，未改容器。
- 后续从 A manifest 和 B 共享执行收据开始。尚未运行新回归或评测，未作当前版本质量结论。

### 2026-09-14 13:01 — A/B 实现与 C 首次诊断

- A：新增 `enterprise_agent/observability/release.py`，诊断 manifest 记录源码/锁文件/报告 SHA、镜像 ID 和数据库 revision（未验证则为空），健康信息读取构建内标识。`scripts/local_demo.sh` 默认构建来源改为主目录，build 前生成忽略入库的 `_build_manifest.json`。尚未 build/up 本地应用，因此当前运行仍是旧独立目录版本。benchmark 保留 --official 原守卫，额外记录运行前后实际源码摘要。
- B/L：新增 `core/execution/evidence.py`，从共享执行边界记录文件/前台/后台收据，放在 workspace 外服务控制目录 `.workspace-locks/evidence`。记录运行中及完成事件、序号、真实输入版本/时间戳、命令/argv/cwd、退出/取消/超时、输出摘要、实际变更；ToolExecutionRecord 引用 execution_ids。文件删除和部分发布异常仍留收据；当前内容或执行过程中修改使旧结果失效。后台线程传递上下文，启动即记录 running，漏轮询也能查询证据。
- B 分类：仅支持明确入口和简单 `cd 相对路径 && 命令`；支持 Python 的 -B/-I/-E/-s/-u 参数。echo/版本/帮助/仅收集/掩盖失败不确认验证。pytest/npm 缓存不计源码。test/build/lint/syntax 分开，语法检查可以满足语法验证任务，但 `behavioral_validation=false`，不得宣称行为测试通过；同版本后续失败覆盖该类型旧成功。
- 单元/共享链路：`pytest` evidence/lifecycle/file_ops/background/contracts/sandbox workspace+contract/release/benchmark runner 合计 **154 passed**，原 v2 30/30 断言未修改；XML `release-evidence/goal-ab-regression-20260914.xml`。早期 111 项通过报告也保留。
- 真实 Docker：首轮 **13 passed / 1 failed**（独立 reaper 的 macOS socket 路径不能直接作为 Linux daemon 挂载来源）。补测试专用 `SANDBOX_REAPER_HOST_SOCKET` / `SANDBOX_REAPER_SOCKET_GID`，复验 **15 passed**，包含真实容器验证收据、伪验证、改后旧证据失效、测试自身写代码；两份 XML 均保留。宿主 socket `/Users/gxh/.docker/run/docker.sock`，daemon 挂载 `/var/run/docker.sock`，GID 0，仅测试配置。
- C：初次 platform 使用默认 Docker，在 sandbox socket 权限下失败且 runner 的 artifact 步骤抛出异常；日志保留，不能当模型失败。显式离线本地执行诊断首份 **14/30**，运行中源码变化，`source_snapshot.unchanged=false`，只能诊断。缺识别 -B 和 npm 缓存误失效修复后固定快照 **29/30**（`20260914T045920Z-platform-single.json`）；剩余明确 py_compile 任务促使分开行为/语法口径，未改夹具或降低断言；最新完整报告待写。
- F 提前准备原因：C 需要 Python/Node 混合用例。实查已有 Node 镜像只有 Node 22.23.2，没有 Python；从已有本地 Python/Node 镜像用禁网多阶段构建 `docker/sandbox-node.Dockerfile` 成功，生成 `enterprise-agent-sandbox-python-node:goal-20260914`。未运行模型或替换生产镜像。预检分类和两类真实验收尚未完成。
- 剩余：收据脱敏/容量与失效边界继续回归；最新源码 Docker 和完整应用集成待做。C 真实模型、浏览器五链路和安全集未做；D/E/G/H/I/J/K 尚未开始。Goal 未完成。

### 2026-09-14 13:15 — 完整回归与隔离集成

- 固定源码 `7232fb105ed67163969b9bb12b64f373882cdfda696037dacf5364e92a9c259b` 的 platform **30/30**，原始报告 `benchmarks/results/20260914T050120Z-platform-single.json`，运行前后摘要一致；没有改 v2 夹具或断言。
- 后端 `pytest -q -m 'not integration and not docker_sandbox'`：**794 passed / 21 deselected**（集成单独验收）；前端 `npm test -- --run`：**99 passed**；`npm run build`、前后端 Docker build 通过。
- 独立 Compose 项目 `goal-agent-20260914` 使用全新数据卷，端口前端 13000/API 18000/MySQL 13307/Redis 16379；配置 `/private/tmp/agent-goal-integration-20260914/override.yml`。没有替换原 `docker` 项目的服务。初次 Chroma 模型下载后所有服务健康。
- `alembic current` 为 `20260914_0006`，`alembic check` 无 schema 差异。独立 Redis DB15 `test_children_redis.py`：**6 passed**。API 镜像内 **90 个 Python 文件**与主目录一致，证据 `goal-isolated-runtime-20260914.json`，完整关联清单 `goal-p0-release-manifest-20260914.json`。
- F：混合镜像 Python 3.12.13、Node 22.23.2、pytest 8.4.2 已实查；实际 Docker executor 的 Python pytest 与 Node --test 两条测试均退出 0、收据 fresh/ok，目录清理完成。初始只读探针遗漏 tmpfs，pytest 报临时目录不可用；补实际 executor 使用的 tmpfs 后通过，没有改变镜像安全约束。
- C 浏览器：现有插件因运行时禁止导入 node:process 无法初始化。读完安装技能/排障说明后，临时目录安装 Playwright，使用全新无持久 profile 的 Chrome 会话测试隔离应用。真实 UI 登录成功，临时账号只在隔离库存在。
- **发现必修 B/C 缺陷**：只读提示 `Read calculator.py. Do not edit files or run commands. Tell me what add(2, 3) currently returns.` 被原 `_request_requires_execution` 关键词规则误判（否定句 run/edit 和函数名 add）。模型正确只读却被完成门逼迫执行并终结 failed。原始浏览器失败与 Trace 位于 `/private/tmp/agent-goal-browser-20260914/{e2e.json,read-failure-trace.json}`。需要修复请求分类并复验，不能通过改提示绕过原验收。
- 完整当前模型诊断已启动，只运行一次原30例，采用现有 deepseek-v4-flash、原20轮/25工具/4M task token 上限；模型 Shell 使用专用禁网 Docker 镜像和临时数据。保持源码冻结直到报告生成，期间仅做测试、镜像和文档工作。已观察原轮数上限失败和连接错误重试，均保留。尚未将模型任务终态视为断言成绩。
- 浏览器继续独立的修改审批/拒绝/Stop 场景，原只读失败仍未解决；完整浏览器验收未通过。后续先修该真实缺陷，再推进 D 等实现。

### 13:28 - 当前模型诊断与只读/发布缺陷修复

- 修改内容：只读请求分类忽略否定行动及函数名，保留明确后续修改请求；Shell 发布允许管理员基目录的系统别名，继续拒绝工作区根及内部链接；验证语法只额外接受字面 `PYTHONDONTWRITEBYTECODE=1`，按运行器和 cwd 分组，Node 成功不再覆盖 Python 失败；澄清后仍有未完成执行不能终结成功。
- 修改原因：真实模型诊断 21/30（历史23/30未改写），发现 macOS `/var` 系统别名导致发布误拦截、只读命令解释被强制执行、验证结果漏识别及终态不实。
- 涉及文件：`enterprise_agent/core/agent/nodes.py`、`enterprise_agent/core/execution/evidence.py`、`enterprise_agent/sandbox/workspace.py`、对应 tests。
- 验证结果：只读/evidence/nodes/lifecycle 132 passed；发布/分类42 passed；真实 Docker 复验执行中。模型原始 `benchmarks/results/20260914T052049Z-agent-single.json` 共30例，21通过、9失败、1系统完整性错误，平均85978.3 token，共约2579349 token，源码快照7232fb...运行前后一致。没有重跑挑选结果或修改断言。
- 浏览器：隔离API更新到只读修复快照7af2c4...；原失败截图/报告保存在临时 initial-failures 目录。新合成账号登录、原只读提示、修改审批验证已通过；拒绝未落盘，Stop/新任务及断网刷新仍在执行。
- 审核：首次 seed+写入命令因硬编码未经核实的用户ID被自动审核拒绝，未执行；脚本修正为新建账号实际返回UID且校验合成邮箱，重新审核通过，没有写其他用户文件。
- Git 分支：`feature/verified-agent-delivery`，从 develop 创建；未提交、未推送。原本地应用未替换，远程未连接。
- 后续事项：保留全部模型失败并分类；完成浏览器复测和安全回归，继续D–L。Goal未完成。

### 13:38 - D 任务Diff与有限文本恢复

- 修改内容：新增 `core/execution/changes.py`，在执行前持久保存有界文本快照，放在服务控制的 `.workspace-locks/evidence`，不送入模型上下文、不挂入执行容器；收据聚合增改删与连续性，返回可信Diff、真实验证命令/版本/退出状态。新增 `/tasks/{trace}/changes` 与 `/restore`，沿用身份归属检查与 workspace lock；聊天结果/历史新增按需展开的 TaskChangesCard，用户可选择支持恢复的路径并确认。
- 恢复边界：UTF-8文本，每文件256KB、每任务100文件/序列化存储4MB、7天；排除敏感路径、检测到的凭据、二进制、链接、超额/过期快照。冲突对全部所选文件预检，任何冲突整批拒绝。恢复用目录描述符固定父目录，禁止链接重定向；逐文件日志记录 attempting_path/applied_paths，未完成恢复拒绝盲目重试。外部网络/数据库效果不能恢复。过期访问清除文本并留过期标记，无人访问的定期清理将在K完成。
- 涉及文件：evidence.py、sandbox/executor.py、api/routes/tasks.py、api/services/chat_history.py、前端 ChatPanel/TaskChangesCard/api client 及针对性测试。
- 验证结果：后端首轮75 passed，API/持久历史37 passed，目录固定恢复/API9 passed；真实Docker16 passed（含真实Shell改动→Diff→恢复及原安全集）；前端原99项增加恢复冲突交互1项，100 passed；构建通过。原2个UI顺序断言因新结果卡变化更新为保留原顺序并在末尾断言task_result，未改benchmark断言。最终预算/过期测试执行中。
- C/G 浏览器：新账号原只读、修改审批验证、拒绝不落盘、Stop→新任务通过；断网刷新90秒未收敛，原始失败和最新只读Trace已保留。断线后Redis仍为running、Trace停在pre_microcompact；精确trace取消返回cancelling，未伪报取消。当前隔离API仍为D之前的7af2c4...源码，需修复G断线清理并完整复测，尚未替换原应用。
- 后续事项：D真实浏览器与覆盖告知、恢复中断重核对继续完善；G断线取消/失联runner优先修复，随后E/F/H/I/J/K/L。分支未提交未推送；未触碰远程。

### 13:43 - G 断线心跳、短租约及事件补取

- 修改内容：新增 `core/execution/streaming.py`，单一生产协程保持Graph上下文，以单事件缓冲和1秒心跳检查Stop/租约；断线取消后用受保护且有时间上限的清理关闭任务/持久消息/runner。Stream活动租约30秒，静默模型也续租；审批等待仍用原长租约。过期无owner的running/pending checkpoint先原子占用fence再记failed，不重放Graph或工具。Redis不可验证runner时同步Shell停止，保留故障证据。
- 事件协议：新增 `core/execution/events.py`，可信控制存储按用户/工作区/trace记录递增seq与最多512条事件索引，SSE含id、trace和fence。索引只含类型/工具ID/状态，不保存片段文本，防止凭据跨chunk被原样落盘。`/tasks/{trace}/events?after=`补取索引，截断时gap=true；正文统一从原有归属校验后的MySQL历史补取。前端seq去重，断线后有界轮询权威状态，终态替换历史，不调用resume重放写入。
- 验证结果：后端stream/API首轮149 passed；新增失联/并发owner/有界cursor隔离等后169 passed（goal-g-regression XML）；前端新增seq重复投递及断线自动收敛验证，102 passed；新模块Ruff与git diff --check通过。D补充存储预算/过期/隔离后47 passed。Stream启动租约断言从旧1200秒更新为新30秒，保留原生命周期断言。
- 真实验收状态：D/G镜像正在构建，尚未将上述单元结果冒充浏览器验收。旧隔离版本的断网任务仍在cancelling，已保存准确trace，后续将作为重启/失联兼容样本核对。原应用与用户卷未变动。
- 后续事项：真实断网、Stop、API重启、Redis故障、部分发布矩阵和D恢复浏览器；继续F/H及完整E/I/J/K。未提交未推送，Goal未完成。

### 13:59 - D/G 真实浏览器故障验收与 F/H 回归

- 修改内容：F新增按不可变镜像ID缓存的管理员空容器能力预检，不挂workspace、不执行项目脚本；H审批绑定参数/身份/任务/权限/工作区版本/有效期，API在确认及实际执行前重查账号权限；pytest与Python模块命令按可执行项目代码要求审批。前端展示完整命令、路径、风险、有效范围及恢复覆盖边界。
- 验证结果：F真实Docker **18 passed**（含缺Node明确失败、Python/Node真实测试、真实Diff恢复），报告goal-f-docker XML；H基础回归258 passed，新增scope/实际写入/旧checkpoint拒绝/实时权限回归 **118 passed**，报告goal-h-regression XML；前端 **102 passed**。H之前后端广回归825 passed/24 deselected。
- 真实浏览器：隔离源码538003…登录读取、修改审批验证、拒绝不写、Stop后新任务、短断网刷新收敛均通过；D查看Diff/恢复/重复恢复409通过。API SIGKILL/重启后租约到期终结failed，非幂等写仅一次；Redis首次故障注入错过执行窗口，失败实验保留，第二次精确在运行中注入停5秒通过，写仅一次。证据/脚本/截图归档docs/release-evidence/browser-538003-20260914，不含临时账号凭据。
- 涉及文件：sandbox/preflight.py、executor.py、core/execution/approvals.py、nodes/state/contracts、ChatPanel/TaskChangesCard、tests/core/execution/test_approvals.py等。
- 测试隔离：新增tests/conftest.py默认临时workspace，避免未指定目录的事件测试写入开发目录；已有workspaces目录来源混合，未删除。
- 后续事项：H/F新源码及修正后的卡片样式尚未进入浏览器镜像；E定位量化、I配对、J记忆、K存储治理继续；原本地应用尚未切换。feature/verified-agent-delivery未提交未推送，远程未连接。

### 14:15 - E对照撤回、H真实审批、J记忆及K可信存储

- E：冻结源码4个定位任务对照，原版4/4、提示优化3/4；token中位数17010→17202.5，p95耗时5218→4206ms。未达token收益且有额外失败，已撤回3行提示，未增加索引/cache/parser依赖；同夹具project_context仅998–1111字节、p95 0.05–0.12ms。原始报告与goal-e-comparison均保留。受限网络首轮8例连接失败；网络重试曾被自动审核以载荷授权不明确拒绝，补充实际setup_files及api.deepseek.com目的地检查后重审通过，没有绕过审核。
- H/D/C：1d8434隔离真实浏览器完整链路通过，新增审批等待期间人改README→旧scope拒绝、再次审批拒绝不写文件通过。Diff卡展开截图已检查，字体/宽度/按钮可读。跨用户真实Trace/Diff/events/restore返回404，路径逃逸虽被拦截却返回500；已将共享API读取/下载路径校验映射为400，待新版镜像复验。
- J：新增版本化20例，原6检索+6准入+4真实Chroma更新/级联删除/跨用户隔离全通过；4真实模型行为3通过1失败（使用当前pip但额外解释且复述uv，严格格式失败保留）。命中与行为分开：注入记忆样本未召回，不冒充“读到注入后抵抗”。再增明确拒绝记忆保存用例至21例，并修复高价值工程任务仍可能被自动入库的问题；新增opt-out及nodes回归97 passed。
- K/L：Trace迁出用户workspace到服务控制目录，旧记录标legacy_unverified且不可授权恢复；有界事件5,000/8MiB、终态30天、快照7天，小时清理/容量告警/未决证据保留；备份只还原到新暂存目录且复验SHA。前后容量报告及真实文件备份演练通过；27项治理/Trace/API测试通过。单写进程2,000事件p95约12ms，MySQL增量迁移条件未触发。
- B/F：关闭执行时等最终收据持久化后再置done；审批在实际文件写锁/容器snapshot锁内再次校验，补调度后人改文件的竞态；预检扩展测试/lint模块，运行时依赖错误明确标不可信诊断。840后端/102前端/构建通过；真实Docker17通过1失败为测试硬编码旧Trace路径，已修正检查新可信路径，待复验，原失败XML保留。
- C安全压力集：独立safety-pressure-v1四例README/AGENTS/log/refusal均通过，无合成canary泄露、无修改；旧v2断言和失败不改写。I固定快照e763…配对36次执行中，第一对Single6/6、Multi2/6，主要子任务/总任务预算耗尽，尚无完整结论。
- 涉及文件：memory/policy.py、observability/trace_store/storage_governance、sandbox/preflight/executor、api/main/workspace/tasks、前端审批/Trace、benchmarks/context_diagnostics/paired/memory_governance及版本化数据、对应tests。维护说明新增docs/agent-storage-and-recovery.md。
- 更正13:15早期F记录：goal-runtime-preflight原始报告的Python退出0但sandbox_error，不能称有效通过；后续修复macOS发布路径后真实Docker混合运行测试通过，以对应新版测试报告为准。
- 分支feature/verified-agent-delivery未提交未推送；原docker应用仍旧镜像，尚未备份更新；不连接远程。其余验收继续，Goal未完成。


### 14:39 - 最终交付核对

- 原本地应用已更新为09f808；完整私有备份142539的SHA/归档/权限通过，原MySQL/Redis容器及命名卷不变。第一次142225备份因辅助镜像ENTRYPOINT需要timeout参数失败，旧API已恢复，显式管理员备份入口后复验通过；失败资料保留。
- B最后修复：任务临时新增又删除的验证脚本不再让JSON结果永久要求代码验证；raw收据保留，净Diff排除无净变化；已有代码改回原样仍要求验证。143项针对性及真实原JSON单例1/1通过，不拼成26/30。
- 最终回归846 passed/24显式服务开关skip；真实Docker18/18、真实Redis6/6单独通过；前端102/102、build、Compose、隔离/原应用alembic current/check通过。
- 最终09f808 Chrome全流程与跨用户检查通过；额外真实容器发布后kill API→写一次、failed、pending_execution保留通过；旧长租约正常到期后failed，无租约强删。
- E无收益撤回；I三组全量Single17/18、Multi9/18；J准入2TP/0FP/0FN/5TN，记忆实际行为3/4；K写p95及查询测量、清理和暂存恢复完成。
- 所有A–L基础项已核对；条件扩展明确未触发。详见agent-improvement-delivery-20260914.md。feature/verified-agent-delivery未提交、未推送，独立工作区只读，远程未连接。


### 14:43 - 发布证据封存

- 最终09f808离线platform原30例全部通过（20260914T064231Z），运行前后摘要一致；这是显式本地确定性夹具验证，原应用Docker执行配置不变。
- 原运行容器111个部署文件含全部96个应用Python文件与构建清单一致；独立旧工作区240个初始文件及分支未变；新增模块Ruff和git diff --check通过。
- 发布manifest关联最终源码/锁/不可变镜像/DB0006及原始报告；已检查证据不含现有配置秘密，私有fixture及备份未进入公开证据。
- A–L基础工作完成；无必需项阻塞或实现未验证项。模型失败与条件未触发项保留在交付报告。未提交未推送，未操作远程。
