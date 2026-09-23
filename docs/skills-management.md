# 企业 Skill 管理与运行

本功能只有两类 Skill：**管理员发布**的公共 Skill，以及**用户管理**的 Skill。用户内容可限定个人或项目使用范围，范围不是另一类来源。导入只读取和校验文件，不执行脚本、不安装依赖、不注册 MCP。脚本继续使用平台现有工具权限、审批和 Docker 沙箱。

## 普通用户

工作台左侧 **Skills** 是管理入口。卡片默认只显示名称、用途、来源和停用状态，可按“全部 / 管理员发布 / 用户管理”筛选或搜索。在“用户管理”中输入工作区内的相对目录，点“切换范围”；留空查看个人范围；例如项目为 `GXH`，目录 Skill 位于 `workspace/GXH/.agents/skills/<name>/SKILL.md`。项目权限沿用现有每用户 workspace 边界。

1. 点“添加 Skill”，选择 ZIP 文件、Git 仓库、工作区或“自己创建”。
2. ZIP 可包含一个或多个 Skill。Git 支持 HTTPS URL、branch/tag/commit 和仓库子目录；不支持带令牌、密码或查询参数的 URL。工作区导入使用已有目录的相对路径。创建时填写名称、用途和操作说明。
3. 点“预览 Skill”，选择候选；“查看内容”可展开说明和文件。改动输入后需要重新预览。
4. 点“确认添加”。通常添加为个人技能；已选择项目时可指定项目范围。普通用户不能发布公共内容。
5. 点击卡片打开详情侧栏。受管理安装可启停、调整自动使用、更新或卸载；更新也须先预览。“使用说明”和“包含的文件”按需展开；ID、校验哈希和来源地址位于默认收起的“技术信息”。关闭详情返回原卡片，不占用主列表空间。

预览归当前用户所有，30 分钟过期，最多 5 个待安装预览。安装一个候选即消费该预览；安装另一个候选需重新预览。每用户最多 128 个安装。旧目录 Skill 是只读来源，可通过工作区导入建立受管理副本，不删除原目录。

聊天区域不再显示 Skills 配置面板。后台自动使用启用的技能，也可在消息中输入 `$python-quality`。同名歧义不会静默选择其他来源；后端 API 仍支持 `skill_ids` 精确指定、`project` 项目上下文及 `implicit_skills`，当前简化聊天 UI 不提供这些高级参数的选择器。详情里的“自动使用”开关适用于个人安装；禁用项不会用于新任务。

## 管理员发布与回滚

在用户菜单进入 **Admin Control Room → 公共 Skills**：

1. “新建公共 Skill”创建草稿，或上传完整 ZIP，选择候选并预览到草稿编辑器。包内可包含 `scripts/`、`references/`、`assets/`、`agents/openai.yaml`。
2. 保存草稿、检查校验和文件树；填写 changelog，发布版本。草稿正文编辑保留原附属文件；替换 ZIP 可更新整个包。
3. 其他用户下一次目录或聊天请求即可看到版本，不需要进程重启或手动刷新服务器文件。
4. 历史列表的回滚产生一个新版本，恢复历史正文和全部附属文件；当前草稿保留，避免覆盖未发布编辑。下架停止新请求发现和加载，不会从旧目录重新出现。

公共内容由原有 `admin:skills:publish` RBAC 和审计保护。版本分配持有数据库行锁；相同包重复发布返回 409。界面提交 revision/updated_at，过期编辑应重新载入。数据库唯一约束处理竞争创建和版本冲突。稳定 ID 为 `managed:<数据库ID>`，版本变更不改变 ID。

## 格式与兼容

```text
python-quality/
  SKILL.md
  scripts/check.py
  references/checks.md
  assets/example.json
  agents/openai.yaml     # 可选，作为普通资源；其声明不授予权限
```

```markdown
---
name: python-quality
description: |
  在修改 Python 工程后执行针对性质量检查，
  并报告真实执行结果。
metadata:
  team: engineering
---

先阅读项目要求。用 read_skill_resource 读取 references/checks.md。
需要运行 scripts/check.py 时使用已有 bash 工具并遵守审批。
```

名称为 1–80 个小写 slug 字符，可包含数字、下划线、连字符。描述必须是非空字符串。安全 YAML 支持引号、多行字符串和嵌套 metadata，拒绝重复键、别名、对象构造标签和非 JSON 类型。非法 YAML 会返回原因；目录扫描错误显示在用户列表。

`SKILL.md` 按 UTF-8、LF、单个结尾换行规范化，附属文件保留原始字节。整个规范化包的路径和 base64 字节映射生成 SHA-256。每次加载验证实际内容，不信任旧 manifest 声明。

旧 `user_<id>/.skills` 继续发现；无 frontmatter 或缺失字段的旧目录文件可在内存兼容，不改写源文件。新 ZIP/管理发布使用严格格式；Workspace 导入可把旧目录转换为规范包。旧数据库版本保留正文和原始哈希，在读取时校验并转换单文件包，不伪造历史资源。迁移不删除旧正文和已发布版本。

同名项全部保留；没有名称优先级覆盖。唯一名称的旧 `load_skill(name)` 调用继续有效；歧义返回可选择的 ID。目录项 ID 由用户、来源、项目及相对路径确定；受管理个人/项目项使用 `installed:<UUID>`。

## 持久化、并发与运行语义

- **事实来源**：公共草稿和版本、个人/项目安装、启用状态均在 MySQL。公共 active_version 与版本内容、审计在同一事务提交。发布不提前修改运行时磁盘。事务失败不会发布新包。
- **派生副本**：`MANAGED_SHARED_SKILLS_DIR/.packages/<sha256>.json` 是经校验的完整包副本。开始任务时从数据库生成，可重新构建；按哈希原子写入允许多进程同时准备。旧 `<name>/SKILL.md` 托管物化目录不再作为 API/Agent 发布事实来源，不会使已下架内容复活。
- **跨进程更新**：每次新 HTTP 目录/聊天请求读数据库，无每用户进程缓存。数据库读事务确定请求的一致视图；刷新或新请求读取新版本。
- **任务固定版本**：LangGraph state 保存用户、项目、可见 ID/来源/版本/哈希和显式选择；不把整个包写入 checkpoint。运行中的任务继续使用旧哈希，启停/更新/卸载/下架对新任务生效。任务中 reload_skills 不切换版本。正常服务重启保留持久卷和 checkpoint，仍可读取原快照。若主动删除缓存，已有任务明确失败，新任务从数据库重建；不要在运行中清空缓存。
- **模型上下文**：自动目录最多 64 项，每项 description 500 字符，总 description 16,000 字符，JSON 64 KB。正文仅显式选中或 load_skill 后提供。一次最多 8 个显式项、正文合计 128 KB；任务完整资源最多 32 MiB，超限的自动项不加入快照。
- **资源访问**：`read_skill_resource(skill_id, path, offset, limit)`读取固定包内 UTF-8 资源，单次最多 32,000 字符。Docker 将任务包复制到执行快照，通过只读嵌套挂载提供 `/workspace/.skill-resources/<sha256>/...`；不把宿主路径交给模型。这些资源不参与 workspace 发布回写。
- **执行与证据**：例如 `python /workspace/.skill-resources/<sha256>/scripts/check.py` 仍由 bash 工具现有权限/审批控制。显式正文加载记录 `explicit_skill_loaded` Trace 事件；load_skill 和资源工具结果包含实际 ID、来源、版本和哈希。Skill 文字及 agents/dependencies 声明不能覆盖平台、当前用户或项目指令，不能自行安装依赖或授权 MCP。

本轮不增加缓存自动回收策略：旧哈希可能由尚未结束的 checkpoint 引用。备份/清理应结合任务保留期，避免删除仍被任务使用的资源。

## 安全部署与迁移

本地或新发布环境执行（先备份，本文命令不会自动针对生产执行）：

```bash
python3 -m uv sync
python3 -m uv run alembic upgrade head
python3 -m uv run alembic check
```

最新 revision 为 `20260923_0008`，依赖 `20260923_0007`。请把已有未跟踪的 0005/0006 迁移与 0007/0008 及 `migrations/data/20260923_0008_legacy_skills.json` 一起纳入交付。旧数据库必须迁移后启动新服务；`create_all` 不能代替已有表的增量迁移。0007 的降级会删除安装/预览表和 package 列，只应在完成备份和停用新功能后执行。0008 的降级保留已转归管理员的记录，防止删除后续编辑。

0008 对已有用户的旧部署一次性迁移原有四个公共包（agent-interviewer、fastapi、langgraph、python），写入可编辑草稿、已发布版本及迁移审计，不冒用管理员身份。已有同名草稿、发布或下架记录优先，重跑不会覆盖或重新上架。全新空数据库不安装任何默认 Skill；之后也没有启动时自动填充。`shared_skills` 运行时目录和 `SHARED_SKILLS_DIR` 配置已移除，旧环境变量不再生效。如果旧部署曾自定义该目录，应先导出完整包，通过管理员入口发布后再升级。正在执行的任务仍遵循原快照，新任务只发现数据库发布内容。

Docker Compose 原有 `managed_shared_skills:/data/shared-skills` 卷覆盖新包缓存，MySQL 卷保存事实来源，workspace 卷保存旧目录 Skill 和项目资源。备份包含数据库、managed_shared_skills 卷及 workspace；保留 Redis/checkpoint 的原有部署策略。多 API 实例共享数据库和包缓存持久卷；缓存目录仅服务用户可写，不可放入普通用户 workspace。数据库故障时新目录/安装请求失败，不能回退使用过时托管目录。

API 镜像安装 Git 与 CA 证书，并预建归属 `10001:10001` 的 `/data/shared-skills`。升级旧卷时须检查目录所有者：已有卷不会随新镜像自动改变权限。本次 175 服务器旧卷为 `root:root 0755`，已通过 `docker exec -u 0 mini-claude-api-1 chown 10001:10001 /data/shared-skills` 修正卷根目录，未递归改写旧文件；API 用户写入 `.packages` 及真实沙箱验证通过。Nginx 的 `client_max_body_size 16m` 与 ZIP 接口上限保持一致。

Git 仅 HTTPS，公共域名默认 `github.com,gitlab.com`，不允许未列出的主机或非 443 端口。可信内网需显式设置精确 `hostname:port`，例如：

```dotenv
SKILL_GIT_HOSTS=github.com,gitlab.com
SKILL_GIT_INTERNAL_HOSTS=git.corp.example:443
SKILL_GIT_TIMEOUT_SECONDS=60
SKILL_GIT_CA_BUNDLE=/etc/enterprise/git-ca.pem
```

使用私有 CA 时由管理员只读挂载对应 PEM 文件；公开受信证书可留空。证书校验始终开启，不提供跳过 TLS 校验的用户选项。

内网配置只放行指定端点；其他解析到私网/保留 IP 的目标仍拒绝。解析地址通过 Git `http.curloptResolve` 固定，关闭重定向、代理继承、系统/用户 Git 配置、交互凭据及 hooks，不递归 submodule、不执行 checkout/filter。导入前检查 Git 支持该配置，不支持时拒绝联网并要求升级；不支持带凭据 URL，受限仓库可离线导出 ZIP。相关配置依据 [Git 官方 git-config 文档](https://git-scm.com/docs/git-config)。

导入限额：ZIP 上传 16 MiB，展开/仓库落盘预算 32 MiB，仓库 2,048 条目、64 候选；单 Skill 256 文件、4 MiB，SKILL.md 100 KB。拒绝绝对路径、穿越、符号链接、硬链接/特殊文件、重复/大小写与 Unicode 归一化冲突、嵌套 Skill 和文件/父目录冲突。Git 使用超时及进程组终止和磁盘预算监测，不拼接 shell。路径读取通过目录描述符和 no-follow 防止租户目录竞态逃逸。macOS 系统 `/var` 等别名单独兼容；安全目录导入针对 macOS/Linux，Windows 可使用 ZIP 路径，但生产执行要求既有 Linux Docker 沙箱。

## 可复现演示

仓库提供 `docs/demo-fixtures/skills/quality-demo/`，包含参考文档和示例脚本。

1. 在本地运行 `python3 scripts/skill_demo_zip.py /tmp/quality-demo.zip` 生成离线包；此脚本只打包，不执行 Skill 内脚本。
2. 管理员通过公共 Skills 上传 ZIP → 选择 quality-demo → 预览 → 保存草稿 → changelog → 发布。
3. 普通用户打开 Skills，确认公共项及按需展开的详情；上传同一个 ZIP 添加到个人作用域。两个 quality-demo 均保留并标注来源；消息中 `$quality-demo` 会报歧义，API 可通过 `skill_ids` 指定具体项。
4. Git 来源填 `https://github.com/anthropics/skills`，ref `main`，子目录 `skills/algorithmic-art`，发现候选、预览并安装；详情显示本次解析的 commit。外网不可达时改用 ZIP。
5. 通过现有文件上传把演示目录放到自己的 `offline/quality-demo/`，Workspace 来源输入 `offline`，选中后安装到已存在项目 `GXH`。切换其他项目或其他用户不应出现该项目项。
6. 聊天选择公共 quality-demo，关闭自动选择，发送“读取参考文件，并在获授权的沙箱中运行示例脚本”。检查工具调用/Trace 的 ID、版本、哈希，脚本输出 `skill-demo-ok`；尝试写资源应只读拒绝。
7. 更新 ZIP 中的参考文档，保存草稿时原发布版本不变；发布后新请求看到新哈希，回滚恢复全部旧文件。个人项禁用/卸载后新请求不能调用，运行中任务仍固定原版本。

## 验证边界

定向测试位于 `tests/skills/`；真实网络、MySQL、Docker 用显式 opt-in，不把 mock 当集成验收。`frontend/tests/SkillsPanel.spec.js` 和聊天测试覆盖管理操作、失败提示、卸载确认和 ID 传输。实际执行结果与截图见 `docs/release-evidence/skills-20260923/`，本次开发记录包含最终计数。

```bash
python3 -m uv run pytest -m 'not integration and not docker_sandbox'
RUN_DOCKER_SANDBOX_TESTS=1 python3 -m uv run pytest tests/skills/test_docker_resources.py
RUN_SKILL_GIT_IMPORT_TESTS=1 python3 -m uv run pytest tests/skills/test_lifecycle.py::test_real_git_subdirectory_import_and_install
# URL 必须指向自行创建且已迁移的临时数据库；不要使用生产数据库。
SKILL_TEST_DATABASE_URL='<临时 MySQL URL>' python3 -m uv run pytest tests/skills/test_mysql.py
python3 -m uv run ruff check enterprise_agent tests
npm --prefix frontend test
npm --prefix frontend run build
docker compose -f docker/docker-compose.yml config --quiet
```
