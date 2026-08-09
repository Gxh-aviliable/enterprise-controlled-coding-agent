# 测试说明

本目录覆盖后端配置、API、状态机、Agent 图、工具、记忆、Trace、管理员控制面和 smoke 基线。外部模型任务成功率不属于 pytest，统一由 `benchmarks/` 记录。

## 当前基线

截至 2026-08-10：

- 后端：561 passed
- 前端：77 passed
- Ruff：0 findings

## 目录

```text
tests/
├── admin/          # 额度和 Shared Skill 治理
├── api/            # 认证、会话、Workspace 读取/乐观并发写入、Memory、Trace、Admin 安全
├── benchmark/      # benchmark runner 与 memory evaluator
├── config/         # 配置与运行时安全校验
├── core/
│   ├── execution/  # 七态状态机、暂停控制和生命周期
│   ├── test_context.py        # artifact-first、reducer 替换、transcript 与摘要续跑
│   ├── test_tool_artifacts.py # 路径隔离、原子写入、脱敏、校验和双限长
│   └── tools/      # 文件、Shell、任务、Skill、后台、委派和 Workspace
├── db/             # Chroma 行为
├── memory/         # 准入、召回、偏好和删除
├── observability/  # Trace 存储与跨层集成
└── smoke/          # 不依赖完整外部服务的本地基线
```

## 常用命令

完整后端回归：

```bash
uv run pytest -q
```

静态检查：

```bash
uv run ruff check enterprise_agent migrations tests benchmarks scripts
```

针对性测试：

```bash
uv run pytest -q tests/core/tools
uv run pytest -q tests/core/execution
uv run pytest -q tests/memory tests/observability
uv run pytest -q tests/admin tests/api/test_admin_security.py
uv run pytest -q tests/api/test_workspace_read_service.py tests/api/test_workspace_write.py
```

覆盖率：

```bash
uv run pytest --cov=enterprise_agent --cov-report=term-missing
```

前端：

```bash
npm test --prefix frontend -- --run
npm run build --prefix frontend
```

不调用外部聊天模型的 smoke 与平台评测：

```bash
uv run python scripts/smoke_test.py
uv run python -m benchmarks.run --backend platform --mode single --no-artifacts
```

## 测试边界

- Chroma 测试使用进程内 collection 和确定性离线 embedding。
- MySQL/Redis 路由逻辑主要通过替身和隔离 API 测试，完整服务启动由 Docker smoke 覆盖。
- Pause/Continue 同时覆盖 Redis 控制键、真实 LangGraph checkpoint 恢复和副作用恰好一次；它证明安全边界暂停，不代表能够强杀正在进行的模型或前台工具调用。
- `platform` benchmark 不调用模型，不代表 Agent 推理能力。
- `agent` benchmark 会调用配置的第三方或私有模型 endpoint，发送合成提示和工具上下文并产生费用。
- 当前 Shell 测试证明策略行为，不证明内核级隔离。
- Workspace 写入测试覆盖 SHA-256 乐观锁、原子替换、路径/符号链接/敏感目录拒绝、UTF-8 与 1 MiB 边界；它不证明 Docker volume 权限或浏览器实机行为，本轮两项仍待验证。

## 新增测试原则

- 优先验证公开行为、状态转换和安全不变量，不锁死内部实现细节。
- 文件与数据库测试使用临时目录/隔离数据，不依赖个人 Workspace。
- 安全测试同时覆盖“模型侧不调用”和“平台侧确定性拦截”两种结果。
- 修复线上或实机问题时先补回归，再修改实现。
- 测试结果发生变化时同步更新 README、能力矩阵和当天开发日志。
