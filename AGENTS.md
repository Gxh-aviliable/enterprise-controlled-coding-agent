# AGENTS.md

## 项目身份

这是一个面向企业内网部署的受控工程 Agent 平台。Codex 在本项目中主要协助完成：

- 后端：FastAPI、LangGraph Agent、工具权限、workspace 隔离、记忆系统
- 前端：Vue 3 + Vite 工程工作台
- 部署：Docker Compose、Linux 服务器部署、Web VSCode / code-server 集成
- 文档：开发记录、部署说明、架构和调试笔记

## 工作原则

- 优先遵循本仓库已有结构、命名和技术栈。
- 开发新功能前先确认当前分支，较大改动应从 `develop` 创建 `feature/*` 分支。
- 修改代码后要进行针对性验证；无法验证时，在最终回复和开发日志中说明原因。
- 不要直接覆盖用户已有改动；发现未预期变更时先识别来源和影响。
- 本项目正在从 Windows 本地开发过渡到 macOS 调试与 Linux 服务器部署，涉及 shell、路径、Docker、VS Code 打开方式时要保持跨平台意识。

## 每日项目修改记录

Codex 每次对当前项目做出文件修改后，都必须维护当天的开发日志。

### 日志路径

- 日志目录：`docs/dev-logs/`
- 日志文件：`docs/dev-logs/YYYY-MM-DD.md`
- 同一天多次修改：追加到同一个文件
- 新的一天：创建新的日志文件

日期使用当前本地日期，格式为 `YYYY-MM-DD`。

### 记录时机

以下情况必须更新当天日志：

- 修改代码、测试、配置、Docker、前端或文档
- 创建、切换或整理开发分支
- 运行关键验证命令并得到有用结果
- 发现影响后续开发的重要问题，例如依赖安装失败、Docker 构建失败、跨平台兼容风险

以下情况可不更新日志：

- 只回答概念性问题，没有修改文件
- 只做只读检查，且没有产生需要长期保留的项目结论
- 用户明确要求不要记录

### 日志格式

每个日志文件使用 YAML frontmatter：

```markdown
---
title: YYYY-MM-DD 项目开发记录
date: YYYY-MM-DD
tags:
  - dev-log
  - enterprise-agent
---

# YYYY-MM-DD 项目开发记录

## 修改记录

### HH:mm - 简短标题

- 修改内容：
- 修改原因：
- 涉及文件：
- 验证结果：
- 后续事项：
```

### 记录内容要求

每条记录尽量包含：

- **修改内容**：实际改了什么，保持具体
- **修改原因**：为什么要改，关联用户目标或项目问题
- **涉及文件**：使用相对路径，如 `enterprise_agent/core/agent/nodes.py`
- **验证结果**：写明跑过的命令、通过/失败、失败原因
- **后续事项**：下一步要补的测试、部署、重构或确认点

### 示例

```markdown
### 22:30 - 跨平台 shell 提示改造

- 修改内容：将 Agent 系统提示中的 Windows 命令硬编码改为根据运行系统动态生成。
- 修改原因：项目后续需要在 macOS 本地调试并部署到 Linux 服务器，固定提示 `cmd.exe` 会导致 Agent 在非 Windows 环境生成错误命令。
- 涉及文件：
  - `enterprise_agent/core/agent/nodes.py`
  - `enterprise_agent/core/agent/tools/shell.py`
- 验证结果：已运行目标测试；依赖下载较慢，必要时切换清华源重试。
- 后续事项：继续补充服务器部署文档，并验证 Docker build。
```

## Git 与分支记录

如果本次工作涉及分支，应在日志中记录：

- 当前分支
- 从哪个分支创建
- 是否已提交
- 是否已推送

示例：

```markdown
- Git 分支：`feature/cross-platform-dev`，从 `develop` 创建。
- 提交状态：尚未提交，等待本地测试完成。
```

## 验证命令记录

常见验证命令：

```bash
python3 -m uv run pytest
python3 -m uv run ruff check enterprise_agent tests
cd frontend && npm run build
docker compose -f docker/docker-compose.yml config
```

如果 `uv` 下载依赖失败或速度过慢，可使用清华源重试：

```bash
UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple python3 -m uv run pytest
```

记录验证结果时不要只写“已测试”，要写清楚命令和结果。

## 最终回复要求

每次完成修改后，最终回复中简要说明：

- 改了哪些文件
- 是否更新了当天开发日志
- 验证命令是否通过
- 如有失败，失败原因和建议下一步
