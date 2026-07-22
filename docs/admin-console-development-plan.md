---
title: 内网管理员控制台设计与开发方案
date: 2026-07-21
status: implemented-mvp
tags:
  - admin-console
  - security
  - governance
  - vue
  - fastapi
---

# 内网管理员控制台设计与开发方案

## 1. 决策摘要

建设管理员控制台是合理且必要的。当前项目已经具备多用户、工作区隔离、Shared Skill、Trace 和数据库实时角色校验，但管理能力仍依赖直接操作 MySQL、Docker volume 或服务器文件，不适合真实内网运营，也缺少可展示的治理闭环。

本方案建议建设一个受控的 **Admin Control Room**，核心目标是：

> 让授权管理员能够管理用户、额度、Shared Skill 和运行状态，同时确保查看用户内容是有理由、有时限、有记录、默认只读的例外操作。

需求需要做三项边界调整：

1. “管理员查看所有用户文档”改为“默认查看工作区元数据；查看文件内容需要临时授权并写入审计日志”。
2. “删除 Shared Skill”改为“退役已发布版本”；保留历史版本、哈希、操作者和任务引用，避免运行结果无法复现。
3. “查看额度”不仅显示配置值，还必须区分配额上限、实时用量、结算周期和拦截记录，否则只是一个不可验证的数字页面。

内网不是可信边界。管理员账号被盗、内部人员越权、用户代码中含凭据、错误 Skill 污染全部 Agent 等风险，在内网环境中仍然存在；管理台应遵循最小权限、默认只读、操作留痕和敏感信息脱敏原则。

### 1.1 2026-07-21 实施状态

| 领域 | 已落地 | 待补证据/增强 |
|---|---|---|
| 管理面基础 | `/auth/me`、实时数据库角色、细粒度管理权限、Alembic、审计表与 Vue 入口 | 正式 RBAC 角色表、SSO/LDAP |
| 用户与会话 | 查询、启停、最后管理员保护、`auth_version` 会话撤销、API Key 停用 | 单个设备/单个 API Key 粒度的撤销 |
| 额度 | Redis 原子并发占位、MySQL 日任务数、Trace 日/月 token 检查、UI 设置与审计 | token 任务中检查点终止、Workspace 存储硬限、定时对账作业 |
| Workspace | 默认元数据、临时 actor/target/scope/TTL grant、只读预览、路径穿越/symlink/敏感文件拒绝、审计 | step-up 认证、内容脱敏规则扩展 |
| Shared Skill | 持久草稿、格式/凭据扫描、不可变版本、发布、回滚、退役、持久卷、缓存刷新、运行时版本/哈希 | 发布前 diff 界面、真实 Agent 加载 E2E、多副本缓存失效 |
| 任务/系统 | 跨用户脱敏摘要、运行任务取消、系统健康/容量、审计查询 | 取消运行任务的浏览器 E2E、审计导出与保留策略 |

已验证证据：后端 `357 passed`、Ruff 全通过、前端 `20 passed`、生产构建通过；现有 Docker MySQL 数据卷已实际迁移到 `20260721_0001`，全新隔离数据卷也已完成首次迁移并通过四服务 smoke；管理员总览实机返回 200，普通用户返回 403。

## 2. 实施前代码审计

| 领域 | 当前实现 | 可复用基础 | 主要缺口 |
|---|---|---|---|
| 身份 | `users.is_superuser` 二元字段 | 权限从数据库实时计算，升降级立即生效 | 没有 `/auth/me`；前端只能从 JWT 读取数字 `sub`；没有管理员 API |
| 权限 | `admin:users`、`admin:analytics` | 已有 `require_permission()` | 超级管理员直接获得全部权限，粒度不足 |
| Workspace | API 始终从登录用户 ID 解析目录 | 已有路径穿越和用户隔离防护 | 没有受控的跨用户只读访问，也没有查看审计 |
| Shared Skill | `shared_skills/<name>/SKILL.md` | 已有全局/个人 Skill 加载和缓存 | 文件被打进 `/app` 镜像，运行时修改不持久且缺少版本/发布机制 |
| 用户额度 | 全局 `TASK_TOKEN_BUDGET`、工具调用上限 | Trace 已记录 token、工具和任务终态 | 没有用户级配额、周期用量、原子扣减与超限记录 |
| 用量分析 | 用户自己的 `/tasks/metrics` | 已有任务成功率、token、耗时、人工介入等指标 | Trace 是用户目录下的单机 JSON；没有跨用户管理查询 |
| 工具日志 | 存在 `ToolUsageLog` 模型 | 字段可表达成功、耗时和错误 | 当前未形成完整写入和管理员聚合闭环 |
| 数据库演进 | 启动时 `Base.metadata.create_all()` | 新环境启动简单 | 无法可靠修改已有表结构，应在管理台数据模型落地前引入迁移机制 |
| 前端 | Vue 3 单页工作台，通过 `mainView` 切换视图 | 现有视觉变量、懒加载组件和用户菜单可复用 | 没有路由、用户资料接口或管理员入口 |

### 2.1 实施前 Shared Skill 的实际来源

当前有 4 个镜像内置全局 Skill：`python`、`fastapi`、`langgraph`、`agent-interviewer`。Docker 构建时执行 `COPY shared_skills ./shared_skills`，容器以非 root 用户运行，且 Compose 没有为 `/app/shared_skills` 挂载持久卷。因此不能把“直接编辑当前目录”作为管理员 Skill 管理方案。

### 2.2 实施前额度口径

现有额度更接近单次任务保护阈值，而不是企业用户配额：

- 单任务 token 预算；
- 单任务工具调用上限；
- Agent 最大轮数；
- Trace 中的任务实际 token 和工具调用。

管理台需要在这些保护阈值之外，新增“用户在某个周期内可以消耗多少资源”的产品额度。

## 3. 管理员职责与禁止事项

### 3.1 MVP 管理能力

| 模块 | 管理员可以做什么 | 默认风险级别 |
|---|---|---:|
| 总览 | 查看用户数、运行任务、今日 token、失败率、拦截数和服务健康 | 低 |
| 用户 | 查询用户、启用/停用账号、查看角色、撤销登录会话/API Key | 中 |
| 额度 | 查看和调整用户周期额度，查看超限原因和历史变更 | 中 |
| Workspace | 查看目录、文件类型、大小和更新时间；经临时授权只读查看文件 | 高 |
| Shared Skill | 创建草稿、校验、查看差异、发布新版本、回滚、退役 | 高 |
| 任务与 Trace | 查看跨用户摘要和脱敏 Trace，取消失控任务 | 中/高 |
| 审计 | 查询管理员操作、失败操作和敏感内容访问记录 | 低 |
| 系统 | 查看依赖健康、版本、队列和存储容量，不展示密钥值 | 低 |

### 3.2 推荐补充能力

- 强制撤销指定用户的 refresh token 和 API Key。
- 停用用户后拒绝新请求，并允许正在运行的任务进入可控取消流程。
- 查看用量趋势和异常用户，例如短时间高 token、持续 Shell 失败、反复触发危险命令。
- 查看长期记忆数量和治理状态；默认只看计数，内容访问沿用临时授权机制。
- 查看当前生效的 Skill 来源、版本和哈希，回答“某次任务使用了哪套规则”。
- 发布全局公告或维护状态，但不在第一阶段实现复杂通知系统。
- 导出脱敏的用量和审计报表，供内网安全检查或成本结算使用。

### 3.3 管理员不能做什么

- 不能查看用户密码、API Key 原文、模型密钥或 `.env` 原文。
- 不能静默冒充用户发起 Agent 任务。
- 不能绕过工作区路径校验、Shell 风险策略或 HITL。
- 不能无审计地下载整个用户 Workspace。
- 不能在 UI 中物理删除审计日志。
- 不能删除最后一个有效超级管理员，也不能直接提升自己未拥有的权限。
- 不能原地覆盖已发布 Skill；只能发布新版本或退役旧版本。
- 不能将管理员权限仅建立在前端隐藏按钮上；所有校验必须在后端执行。

## 4. 权限模型

### 4.1 建议权限

```text
admin:console
admin:users:read
admin:users:write
admin:quotas:read
admin:quotas:write
admin:workspace:metadata
admin:workspace:content
admin:skills:read
admin:skills:publish
admin:tasks:read
admin:tasks:cancel
admin:audit:read
admin:system:read
```

MVP 可以继续以 `is_superuser` 作为管理员来源，但每个接口仍应声明明确权限。后续再增加 `roles`、`role_permissions`、`user_roles` 表，将角色拆成：

- `platform_admin`：用户、额度、系统和任务治理；
- `skill_maintainer`：维护 Shared Skill，无用户内容权限；
- `security_auditor`：只读 Trace、审计和安全事件；
- `support_operator`：查看用户状态和工作区元数据，不能读取内容。

权限仍然必须从数据库实时解析，不能信任 JWT 中可能已经过期的管理员声明。

### 4.2 内容访问的 Break-glass 流程

用户文件内容不应因为 `is_superuser=1` 自动可见。建议流程：

```text
选择用户和文件
  → 只能先看到元数据
  → 点击“申请临时只读访问”
  → 填写工单/故障原因并再次确认
  → 服务端签发 5–30 分钟 access grant
  → 只读展示脱敏内容
  → 到期自动失效并写入审计日志
```

MVP 约束：

- grant 绑定管理员、目标用户和访问范围；
- 默认 10 分钟，最长 30 分钟；
- 每次读取记录目标路径、文件哈希、结果和 grant ID，不记录完整文件正文；
- `.env`、私钥、凭据目录等沿用敏感路径拒绝策略，即使管理员也默认不可读；
- 文件内容按纯文本渲染，禁止执行 HTML/SVG/脚本；
- MVP 不提供跨用户写入、删除和整包下载。

后续可以加入管理员重新输入密码、TOTP 或双人审批作为真正的 step-up authentication。

## 5. 信息架构与页面设计

### 5.1 页面定位

- 产品：企业内网 AI Coding Agent 管理面。
- 用户：平台管理员、运维、安全审计和 Skill 维护者。
- 单一任务：快速判断“谁在使用什么、是否安全、是否超额、该如何处置”。

管理端应保持当前 Mini Claude Code 的靛蓝品牌语言，但比聊天工作台更紧凑、更偏向运行控制台，避免做成通用电商式后台。

### 5.2 导航结构

```text
Admin Control Room
├── Overview             运行总览
├── Users                用户与账号状态
│   └── User detail
│       ├── Profile
│       ├── Usage & quota
│       ├── Workspace
│       ├── Tasks & traces
│       └── Memory summary
├── Shared Skills        草稿、版本、发布、退役
├── Tasks                跨用户任务与异常运行
├── Audit Log            管理员和安全操作
└── System               服务健康、版本、存储
```

### 5.3 MVP 集成方式

当前前端没有 Vue Router。第一阶段不为一个管理页强行重构全站路由：

- 新增懒加载的 `AdminConsole.vue`；
- `/auth/me` 返回管理员状态和实时权限；
- 只有具备 `admin:console` 时，用户菜单才显示 `Admin Control Room`；
- `App.vue` 继续通过 `mainView = 'admin'` 切换；
- `AdminConsole` 内部维护左侧 section，后续需要可分享深链接时再迁移 Vue Router。

前端隐藏入口只负责体验，后端接口仍需逐个鉴权。

### 5.4 视觉方案

沿用现有字体，避免管理台和工作台产生两个品牌：

- 标题/正文：`DM Sans`；
- Trace ID、用量、Skill 哈希和审计字段：`JetBrains Mono`；
- `Canvas`：`#F8F9FB`；
- `Surface`：`#FFFFFF`；
- `Control Indigo`：`#4F46E5`；
- `Healthy Teal`：`#0F9F7A`；
- `Review Amber`：`#C47A00`；
- `Critical Red`：`#D14343`；
- `Ink`：`#1A1A2E`。

标志性交互是页面顶部常驻的 **Access Scope Bar**：始终显示当前处于“普通元数据模式”还是“用户 17 的临时内容访问模式”，并显示剩余时间。这样管理员不会在不知情的情况下长时间浏览用户内容。

### 5.5 线框图

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ Admin Control Room   Scope: METADATA ONLY          Health ●  14:32:08   │
├─────────────────┬────────────────────────────────────────────────────────┤
│ Overview        │  Attention queue                                     │
│ Users           │  ┌──────────────┬──────────────┬───────────────────┐  │
│ Shared Skills   │  │ 2 failed     │ 1 over quota │ 3 safety blocks   │  │
│ Tasks           │  └──────────────┴──────────────┴───────────────────┘  │
│ Audit Log       │                                                        │
│ System          │  Usage trend           Recent governed actions         │
│                 │  [token line chart]    [actor / action / target]        │
└─────────────────┴────────────────────────────────────────────────────────┘
```

用户详情页：

```text
┌ User 17 / zhangsan ─ Active ───────────────── [Disable account] ┐
│ Quota: 62% monthly  │ Tasks: 18/20 today │ Workspace: 184 MB     │
├──────────────────────────────────────────────────────────────────┤
│ Profile │ Usage │ Workspace │ Tasks │ Memory summary             │
├──────────────────────────────────────────────────────────────────┤
│ Workspace tree                  │ Metadata / guarded preview      │
│ app/                            │ path: app/main.py               │
│ tests/                          │ size: 4.2 KB                    │
│ pyproject.toml                  │ [Request temporary read access] │
└──────────────────────────────────────────────────────────────────┘
```

Shared Skill 发布页必须突出 diff、校验结果、目标版本和影响范围，而不是只提供一个文本编辑器和“保存”按钮。

## 6. 后端 API 设计

新增 `enterprise_agent/api/routes/admin.py`，统一使用 `/admin` 前缀。建议先增加：

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| `GET` | `/auth/me` | 登录用户 | 返回用户资料、角色和数据库实时权限 |
| `GET` | `/admin/overview` | `admin:console` | 总览指标和待处理异常 |
| `GET` | `/admin/users` | `admin:users:read` | 分页、搜索、状态筛选 |
| `GET` | `/admin/users/{id}` | `admin:users:read` | 用户详情，不返回密码哈希 |
| `PATCH` | `/admin/users/{id}/status` | `admin:users:write` | 启用/停用账号，要求 reason |
| `POST` | `/admin/users/{id}/revoke-sessions` | `admin:users:write` | 撤销 refresh token/API Key |
| `GET` | `/admin/users/{id}/usage` | `admin:quotas:read` | 周期用量和历史趋势 |
| `GET/PATCH` | `/admin/users/{id}/quota` | `admin:quotas:*` | 查询或调整额度，记录 before/after |
| `GET` | `/admin/users/{id}/workspace/tree` | `admin:workspace:metadata` | 跨用户目录元数据 |
| `POST` | `/admin/access-grants` | `admin:workspace:content` | 创建临时只读 grant |
| `GET` | `/admin/users/{id}/workspace/read` | `admin:workspace:content` | 携带 grant ID 读取单个文件 |
| `GET/POST` | `/admin/skills` | `admin:skills:*` | 列表或创建草稿 |
| `GET/PUT` | `/admin/skills/{name}/draft` | `admin:skills:*` | 查看或编辑草稿 |
| `POST` | `/admin/skills/{name}/validate` | `admin:skills:publish` | YAML、大小、命名和内容检查 |
| `POST` | `/admin/skills/{name}/publish` | `admin:skills:publish` | 发布不可变版本并刷新缓存 |
| `POST` | `/admin/skills/{name}/retire` | `admin:skills:publish` | 退役，不物理删除历史 |
| `GET` | `/admin/tasks` | `admin:tasks:read` | 跨用户任务摘要 |
| `GET` | `/admin/tasks/{trace_id}` | `admin:tasks:read` | 脱敏 Trace 详情 |
| `POST` | `/admin/tasks/{trace_id}/cancel` | `admin:tasks:cancel` | 取消运行任务，要求 reason |
| `GET` | `/admin/audit-logs` | `admin:audit:read` | 审计查询和导出 |
| `GET` | `/admin/system/health` | `admin:system:read` | MySQL/Redis/Chroma/存储状态 |

列表接口统一支持 `cursor` 或分页、`limit`、搜索和时间范围；不允许无上限返回全部用户或 Trace。所有写操作支持请求 ID/幂等键，避免浏览器重试产生重复发布或重复额度调整。

## 7. 数据模型

管理台落地前先引入 Alembic 或等价的显式迁移流程。`create_all()` 不会修改生产环境已有表，不能承担后续数据库演进。

### 7.1 `user_quotas`

```text
id
user_id unique
daily_task_limit
daily_token_limit
monthly_token_limit
concurrent_task_limit
workspace_bytes_limit
enabled
updated_by
updated_at
version
```

### 7.2 `user_usage_daily`

```text
user_id + usage_date unique
task_count
input_tokens
output_tokens
total_tokens
tool_calls
failed_tasks
safety_interceptions
updated_at
```

Redis 用于任务并发和周期额度的原子计数；MySQL 保存可查询、可结算的最终记录。任务结束时根据 Trace 结算，后台任务定期对账，不能只依赖前端统计。

### 7.3 `admin_audit_logs`

```text
id
actor_user_id
action
target_type
target_id
reason
before_json
after_json
request_id
source_ip
user_agent
outcome
created_at
```

日志只保存必要的结构化差异；文件正文、密码、token 和 API Key 不进入审计字段。

### 7.4 `admin_access_grants`

```text
id UUID
actor_user_id
target_user_id
scope
reason
expires_at
revoked_at
created_at
```

### 7.5 Shared Skill 版本

```text
shared_skills:
  id, name, description, status, active_version_id, created_by, created_at

shared_skill_versions:
  id, skill_id, version, content_path, content_sha256,
  validation_json, changelog, created_by, published_at
```

Skill 正文第一阶段存放在新的持久卷 `/data/shared-skills`，数据库保存注册信息、版本和哈希。发布时采用临时文件 + 原子替换，随后刷新 SkillLoader 缓存。解析优先级建议为：

```text
安全/工具策略（不属于 Skill，永远优先）
  > 用户 Personal Skill
  > 管理员发布的 Shared Skill
  > 镜像内置 Built-in Skill
```

安全规则不能放在可被 Personal Skill 覆盖的提示词层。每次任务 Trace 应记录实际加载 Skill 的名称、版本、scope 和 SHA-256，保证复现。

单机 Compose 可以使用本地 named volume；将来部署多个 API 实例时，必须切换到共享存储或以数据库/对象存储为权威源，并通过 Redis 通知缓存失效。

## 8. 额度执行规则

建议 MVP 支持：

- 每日任务数；
- 每日和每月 token；
- 并发任务数；
- Workspace 存储空间；
- 单任务 token、工具数和轮数继续沿用全局安全上限，不允许用户额度放宽安全阈值。

请求执行流程：

```text
请求进入
  → 检查用户状态
  → 检查并发和周期余额
  → Redis 原子占位
  → Agent 执行，每次模型调用后累计真实 usage
  → 超限则在安全检查点终止
  → 任务终态结算 MySQL + 释放并发占位
  → 异常退出由对账任务修复悬挂占位
```

界面必须同时显示：`已用 / 上限`、周期起止时间、最近更新时间、软提醒阈值和最后一次拦截原因。额度修改需要填写原因，并记录修改前后值。

## 9. Shared Skill 治理流程

```text
Draft → Validate → Review diff → Publish immutable version → Observe → Retire/Roll back
```

校验至少包括：

- `SKILL.md` YAML frontmatter、name 和 description；
- name 只允许稳定 slug，禁止路径分隔符；
- 文件大小、编码和最大 token 估算；
- 与 built-in/personal 命名冲突提示；
- 敏感信息模式扫描；
- Markdown/代码块完整性；
- 发布前展示相对上一版本的 diff；
- 发布后用最小 prompt 做加载 smoke test。

Skill 本身会进入高权限系统上下文，应视为“提示词供应链代码”。仅通过格式校验不能证明内容安全，发布动作必须由具备 `admin:skills:publish` 的人员明确确认。

## 10. 审计事件

以下事件必须留痕：

- 登录失败、管理员权限变化；
- 用户启用/停用、会话撤销；
- 额度修改和额度拦截；
- Workspace 内容访问 grant 的申请、使用、过期和撤销；
- Shared Skill 草稿修改、校验、发布、回滚和退役；
- 管理员取消任务；
- 审计日志导出；
- 管理端 API 的 403、路径越界和敏感文件拒绝。

审计日志按时间追加，普通管理接口不能更新或删除。生产部署应设置独立保留期，并定期导出到只追加的日志系统。

## 11. 实施阶段

### Phase 0：管理面基础

- 增加 `/auth/me`；
- 增加细粒度 Permission 和 `get_current_admin` 依赖；
- 引入数据库迁移机制；
- 创建 `AdminAuditLog` 和审计写入服务；
- 前端增加权限感知的管理入口和空壳布局。

验收：普通用户无法发现或访问管理接口；管理员可以进入页面；升降级无需等待 JWT 过期；管理员 API 测试覆盖 401/403。

### Phase 1：用户、额度和总览

- 用户列表、详情、启用/停用；
- 用户额度、Redis 原子计数和 MySQL 日结；
- 总览指标、异常列表和用量趋势；
- 会话/API Key 撤销。

验收：两个测试用户的额度完全隔离；并发请求不能穿透硬额度；修改历史可追踪；停用账号立即失效。

### Phase 2：受控 Workspace 查看

- 跨用户目录元数据；
- Break-glass grant；
- 只读、分页和文本预览；
- 敏感路径拒绝与内容脱敏；
- Access Scope Bar。

验收：无 grant、过期 grant、错误用户 grant、路径穿越和 symlink 逃逸全部失败；每次成功读取都有审计记录。

### Phase 3：Shared Skill 管理

- 持久化 managed Shared Skill；
- 草稿、校验、版本 diff、发布、退役和回滚；
- 原子写入和缓存刷新；
- Trace 记录 Skill 版本与哈希。

验收：容器重建后管理员 Skill 仍存在；运行中的任务不被中途换版本；退役后新任务不再加载，旧 Trace 仍可解释。

### Phase 4：任务治理、系统状态和审计完善

- 跨用户脱敏 Trace；
- 运行任务取消；
- 系统健康和容量；
- 审计搜索、分页与脱敏导出；
- 管理端浏览器 E2E 和安全回归。

验收：管理员可以从异常指标定位到具体 Trace，但看不到密钥；取消任务进入 `cancelled` 终态并释放额度占位。

## 12. 测试计划

### 后端

- 普通用户访问每一个 `/admin/*` 均返回 403；未登录返回 401。
- 数据库降级管理员后，旧 JWT 立即失去权限。
- 列表分页、过滤和 IDOR 隔离。
- 不允许停用或删除最后一个超级管理员。
- Workspace 路径穿越、绝对路径、symlink、敏感文件测试。
- access grant 的 owner、target、scope、TTL 和撤销测试。
- 额度并发、重复回调、任务异常退出和日结对账测试。
- Skill 非法名称、超大内容、无效 YAML、发布冲突、回滚和缓存失效测试。
- 审计字段脱敏测试，确保正文、密码和 token 不落日志。

### 前端

- 普通用户不显示管理入口；直接切换视图也不能加载管理数据。
- 管理员刷新页面后仍能正确恢复权限。
- 用户状态、额度和 Skill 写操作都有确认与错误反馈。
- Workspace 内容在 grant 前不渲染；grant 到期后立即关闭预览。
- 管理台键盘可操作、焦点可见、窄屏可滚动、减少动画设置生效。
- 401 自动退出，403 显示缺少的权限，409 显示版本冲突，429 显示额度或频率限制。

### Docker 与恢复

- 重建 API/Frontend 容器后，MySQL、Workspace、Managed Skill 和审计数据保持。
- 备份恢复后，Skill 哈希与数据库版本一致。
- Redis 清空后可以从 MySQL/Trace 对账恢复额度，不永久锁死用户并发名额。

## 13. 代码改动建议

```text
enterprise_agent/
├── api/routes/admin.py
├── api/schemas/admin.py
├── admin/
│   ├── audit.py
│   ├── quotas.py
│   ├── access_grants.py
│   └── skill_registry.py
├── models/
│   ├── admin_audit_log.py
│   ├── admin_access_grant.py
│   ├── user_quota.py
│   ├── user_usage_daily.py
│   └── shared_skill.py
└── migrations/

frontend/src/
├── components/admin/
│   ├── AdminConsole.vue
│   ├── AdminOverview.vue
│   ├── UserDirectory.vue
│   ├── UserDetail.vue
│   ├── SharedSkillManager.vue
│   ├── AuditLogViewer.vue
│   └── AccessScopeBar.vue
└── stores/admin.js
```

不要直接复制现有 `/workspace` 路由并把 `user_id` 改成请求参数。应抽取共享的只读 tree/read service，再分别由用户路由和管理员 grant 路由调用，避免管理员路径遗漏现有隔离校验。

## 14. MVP 非目标

- 不实现 LDAP/AD/SSO；保留接口，为后续内网身份源接入做准备。
- 不实现管理员远程终端或替用户执行 Shell。
- 不允许在线编辑用户 Workspace。
- 不提供模型密钥查看或通用环境变量编辑器。
- 不在第一阶段实现复杂审批流、组织树和多租户计费。
- 不把 Trace JSON 临时存储包装成无限规模的企业可观测平台。

## 15. 最终验收清单

- [x] 普通用户与管理员页面、API 权限隔离。
- [x] `/auth/me` 返回实时角色和权限，前端不再把数字 `sub` 当用户名。
- [x] 管理员能查询、停用和恢复用户，且不能破坏最后一个管理员。
- [ ] 用户额度可配置、并发可原子执行、可解释；定时对账作业与 Workspace 硬限待实现。
- [x] 管理员默认只能看到 Workspace 元数据。
- [x] 文件内容访问需要短期 grant，并产生审计证据。
- [x] Shared Skill 支持草稿、校验、版本、发布、退役和回滚。
- [ ] 容器重建后数据库和 Managed Skill 持久卷已存在；含已发布 Skill 的破坏性恢复演练待执行。
- [ ] `load_skill` 已输出实际版本和哈希；真实 Agent Trace E2E 待执行。
- [ ] 管理员可定位并取消非终态任务；运行中任务的浏览器 E2E 待执行。
- [x] 管理端不返回密码哈希、token、API Key 原文和敏感文件内容。
- [ ] 后端权限/路径/额度测试、前端组件回归与旧库 Docker 迁移已通过；完整前端 E2E 和备份恢复测试待执行。

## 16. 是否值得作为作品集功能

值得，但展示重点不应是“管理员能看所有文件”，而应是以下工程取舍：

- 数据库实时授权，避免旧 JWT 保留已撤销权限；
- 用户内容的 break-glass 临时访问与不可抵赖审计；
- Redis 原子额度控制与 MySQL 可结算对账；
- Shared Skill 的版本化发布、回滚和任务可复现；
- 管理动作与 Agent Trace 串联，形成内网治理闭环。

这些能力与当前项目的可靠性、安全、可观测性方向一致，也比继续增加 Agent 数量更能体现企业工程价值。
