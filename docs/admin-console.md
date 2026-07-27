---
title: 内网管理员控制台
date: 2026-07-23
status: implemented-mvp
---

# 内网管理员控制台

管理员控制台是当前受控 Coding Agent 的治理入口，用于管理用户、额度、运行状态、临时内容访问和 Shared Skill。它不是“管理员可以无条件读取所有用户数据”的后门。

## 当前能力

| 领域 | 已实现行为 |
|---|---|
| 用户 | 列表、详情、启停账号、撤销既有会话与 API Key |
| 额度 | 日任务、日/月 token、并发任务、Workspace 容量的查看和调整 |
| 任务 | 跨用户脱敏任务概览、Trace 状态查看和任务取消 |
| Workspace | 默认只查看目录元数据；正文访问需要临时授权 |
| 临时授权 | 绑定管理员、目标用户、理由和过期时间，使用后写入审计 |
| Shared Skill | 创建草稿、凭据扫描、校验、发布不可变版本、回滚和退役 |
| 审计 | 保存操作者、目标、理由、before/after、结果和时间 |
| 健康 | 汇总 API 依赖、运行状态和治理数据 |

前端入口是 `frontend/src/components/admin/AdminConsole.vue`，仅对实时数据库角色为管理员的用户显示。后端入口统一位于 `/admin/*`。

## 权限模型

当前运行时主要区分普通用户和管理员：

- JWT 用于认证身份；
- 每次管理请求都重新查询数据库角色，不能只相信签发时的旧角色声明；
- `require_admin` 拒绝普通用户访问；
- 用户被停用或会话世代被撤销后，既有令牌不能继续获得管理能力；
- 高级 Agent 工具权限与管理员页面权限仍经过各自的服务端检查。

当前还不是完整企业 RBAC。组织、项目、岗位、审批人分离和企业 SSO 属于后续生产化工作。

## Workspace 内容访问

管理员查看用户 Workspace 分两层：

1. **元数据层**：目录、文件名、类型、大小等，可用于排障和容量治理。
2. **正文层**：需要先创建临时 access grant，提供明确理由和有效期。

正文读取仍受以下限制：

- 只读，不提供跨用户修改入口；
- 只能访问 grant 指定的目标用户；
- grant 过期后自动失效；
- 敏感路径和二进制/超大文件继续由 Workspace 服务拒绝；
- 创建授权和实际读取都会记录审计事件。

“内网”不是放宽访问控制的理由。用户代码可能包含商业逻辑、密钥或个人信息，因此默认行为必须是最小权限和可追责。

## Shared Skill 生命周期

已发布 Skill 不直接物理删除，而采用版本化治理：

```text
draft → validate → publish immutable version
                    ├─ rollback to previous version
                    └─ retire from active loaders
```

校验阶段检查：

- Skill 名称和 frontmatter；
- 内容大小与格式；
- 疑似凭据、私钥和危险配置；
- 当前版本是否可被安全 materialize。

发布后生成不可变版本和内容哈希，并刷新运行中的 Skill loader。退役只移除 Active 版本，不破坏审计、版本历史和既有任务的可复现性。

## 主要 API

| 功能 | 路径 |
|---|---|
| 总览 | `GET /admin/overview` |
| 用户 | `GET /admin/users`、`GET /admin/users/{user_id}` |
| 启停与撤销 | `PATCH /admin/users/{user_id}/status`、`POST /admin/users/{user_id}/revoke-sessions` |
| 用量与额度 | `GET /admin/users/{user_id}/usage`、`GET/PATCH /admin/users/{user_id}/quota` |
| Workspace 元数据 | `GET /admin/users/{user_id}/workspace/tree` |
| 临时授权与读取 | `POST /admin/access-grants`、`GET /admin/users/{user_id}/workspace/read` |
| Shared Skill | `/admin/skills*` |
| 任务 | `GET /admin/tasks`、`POST /admin/tasks/{trace_id}/cancel` |
| 审计 | `GET /admin/audit-logs` |
| 系统健康 | `GET /admin/system/health` |

精确请求和响应 schema 以 FastAPI `/docs` 与 `enterprise_agent/api/schemas/admin.py` 为准。

## 数据模型

管理员相关 Alembic 表和 SQLAlchemy 模型包括：

- `UserQuota`
- `UserUsageDaily`
- `AdminAuditLog`
- `AdminAccessGrant`
- `SharedSkill`
- `SharedSkillVersion`

部署时必须运行 Alembic migration。不要通过手工修改数据库代替版本化 schema 演进。

## 验证入口

```bash
uv run pytest -q tests/admin tests/api/test_admin_security.py
npm test --prefix frontend -- --run
```

重点回归包括普通用户拒绝、实时角色降级、grant 过期、Workspace 内容隔离、Skill 校验/发布/回滚和审计记录。

## 当前边界

- 尚未接入 LDAP、OIDC、企业 SSO 或组织/项目级 RBAC。
- Trace 和部分治理聚合仍是单进程基线。
- Workspace 正文 grant 尚未接入双人审批。
- 额度计量适合作品集和单实例 MVP，多副本生产环境需要集中式原子结算。
- Shared Skill 已有版本治理和凭据扫描，但尚未实现签名、供应链证明和恶意代码沙箱。
