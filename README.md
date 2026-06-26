# Mini Claude Code — Enterprise Controlled Engineering Agent

面向企业内网部署的受控工程 Agent 平台。它不是要和 Claude Code、Codex、Cursor 在个人本机体验上正面竞争，而是把 AI 编程能力放进企业可治理的服务器环境中：代码不出内网，Agent 只在受控 workspace 中操作，敏感工具可审批，文件访问可隔离，长期项目知识可沉淀。

## 产品定位

企业内部往往不希望开发者在个人电脑上安装高权限 Agent，或把私有代码直接交给外部 SaaS。这个项目的目标是提供一个可部署在公司内网的工程 Agent 工作台：

- 开发者通过浏览器使用 Agent，不需要在个人电脑授予高权限。
- Agent 在服务器上的隔离 workspace 中读写代码、运行命令、查看文件、总结任务。
- 平台负责用户认证、权限边界、工具确认、会话管理、项目记忆和审计基础。
- 企业可以接入 DeepSeek、Qwen、GLM、OpenAI-compatible 或私有模型 endpoint。

一句话：

```text
一个部署在企业内网的安全工程 Agent 平台，让开发者在浏览器里让 Agent 受控地阅读、修改、测试和总结代码。
```

## 适用场景

- 企业内网代码助手：在公司服务器上统一部署 Agent，不把代码散落到个人本机插件。
- 受控代码修改：限制 Agent 只能访问当前用户或项目 workspace。
- 内部项目知识沉淀：保存项目约定、架构决策、问题排查记录和任务摘要。
- 安全工具执行：对 shell、文件写入、删除、子 Agent 等敏感操作进行确认和策略控制。
- 多用户工程工作台：每个用户独立 workspace，文件树、会话、长期记忆按用户隔离。
- 私有模型适配：支持多种 LLM provider 和企业自建 OpenAI-compatible 服务。

## 核心能力

### 1. 受控工程 Workspace

- 用户 workspace 隔离：`/workspaces/user_<id>` 或自定义服务器路径。
- 所有文件 API 和 Agent 文件工具都通过 workspace 路径解析，防止路径穿越。
- 支持文件树、文件阅读、上传下载、移动、删除和目录创建。
- 支持本地 VS Code / Web VSCode 打开当前用户 workspace。
- 自动为用户 workspace 初始化安全的 `.vscode/settings.json`，避免编辑器插件误读全局配置。

### 2. 有状态 Code Agent

- 基于 LangGraph StateGraph 构建有状态工程 Agent。
- RedisSaver 按 `session_id/thread_id` 持久化会话状态。
- SSE 逐 token 流式响应，支持中断、取消和恢复。
- 支持文件读写、shell、任务管理、上下文压缩、子 Agent、团队协作等工具。
- 支持 Todo 跟踪和后台任务，适合多步骤工程任务。

### 3. 企业安全与治理基础

- JWT 认证，邮箱登录，刷新 token。
- 忘记密码流程，开发模式下验证码写入后端日志，可配置 SMTP 邮箱发送。
- 敏感工具人工确认：shell、写文件、编辑文件、任务创建、子 Agent 等可走确认流。
- Shell 命令黑名单和破坏性操作检测。
- 前端 Markdown 渲染使用 DOMPurify 做 XSS 清理。
- 会话和文件访问按用户隔离。

### 4. 企业项目记忆

当前长期记忆基于 ChromaDB，支持任务摘要、用户模式和语义检索。后续定位会从普通聊天记忆升级为企业工程记忆：

- 用户偏好：编辑器、开发习惯、确认偏好。
- 项目事实：启动方式、目录约定、架构决策、常见故障。
- 任务摘要：完成了什么修改、涉及哪些文件、验证结果。
- 检索增强：Agent 可用 `search_memory` 查询过去任务和项目知识。

### 5. 多模型与内网部署

- 支持 Anthropic、DeepSeek、GLM、OpenAI、MiMo 等 provider。
- 支持 `LLM_BASE_URL` 配置企业内网或私有模型 endpoint。
- Chroma embedding 使用本地 sentence-transformers，可脱离外部 embedding API。
- MySQL / Redis / Chroma 可部署在内网环境。

## 技术栈

| 层 | 技术 |
|---|------|
| Agent 引擎 | LangGraph StateGraph + LangChain |
| API | FastAPI + SSE + JWT |
| 前端 | Vue 3 + Vite + marked + DOMPurify + highlight.js |
| 会话状态 | Redis + LangGraph RedisSaver |
| 长期记忆 | ChromaDB + sentence-transformers |
| 认证/会话 | MySQL + SQLAlchemy async |
| 模型接入 | DeepSeek / Anthropic / GLM / OpenAI / MiMo / OpenAI-compatible |
| 部署 | Docker Compose |

## 快速启动

```bash
# 1. 克隆项目
git clone https://github.com/Gxh-aviliable/my_mini_claude_code.git
cd my_mini_claude_code
git checkout develop

# 2. 启动数据库
cd docker && docker compose up -d mysql redis && cd ..

# 3. 安装依赖
uv sync
cd frontend && npm install && cd ..

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env：填入 LLM_API_KEY、JWT_SECRET_KEY、数据库密码等

# 5. 启动后端
uv run uvicorn enterprise_agent.api.main:app --reload

# 6. 启动前端
cd frontend && npm run dev

# 访问 http://localhost:3000
```

## 关键配置

```bash
# LLM
LLM_PROVIDER=deepseek
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-v4-pro

# Auth
JWT_SECRET_KEY=your-secret-key-change-in-production

# Workspace
WORKSPACE_BASE=/workspaces
FILE_OPEN_MODE=local-vscode
VSCODE_WEB_BASE_URL=
VSCODE_WEB_URL_TEMPLATE=
VSCODE_WORKSPACE_PATH=/srv/workspaces

# Password reset email (optional)
SMTP_HOST=
SMTP_PORT=465
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_EMAIL=
SMTP_USE_SSL=true
```

## 项目结构

```text
my_mini_claude_code/
├── enterprise_agent/
│   ├── api/                       # FastAPI 路由、中间件、schemas
│   ├── auth/                      # JWT、密码、邮件验证码
│   ├── core/agent/                # LangGraph Agent 核心
│   │   ├── graph.py               # StateGraph + RedisSaver
│   │   ├── nodes.py               # LLM、工具执行、记忆保存、压缩节点
│   │   ├── state.py               # AgentState
│   │   └── tools/                 # 文件、shell、workspace、skills、team、memory 等工具
│   ├── memory/                    # Chroma 长期记忆、重要性评分、衰减清理
│   ├── db/                        # MySQL / Redis / Chroma 连接
│   ├── models/                    # User、Session ORM 模型
│   └── config/settings.py         # 环境变量配置
├── frontend/                      # Vue 3 工程工作台
├── shared_skills/                 # 企业共享技能
├── tests/                         # pytest 测试
├── docker/                        # Docker Compose
└── docs/                          # 架构、计划和审计文档
```

## API 概览

### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/register` | 注册用户 |
| POST | `/auth/login` | 邮箱登录 |
| POST | `/auth/refresh` | 刷新 token |
| POST | `/auth/forgot-password` | 请求邮箱验证码 |
| POST | `/auth/reset-password` | 验证码重置密码 |

### 对话与会话

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat/completions` | 非流式对话 |
| POST | `/chat/stream` | SSE 流式对话 |
| POST | `/chat/stream/resume` | 工具确认后恢复 |
| POST | `/chat/stream/cancel` | 取消生成 |
| GET | `/sessions/` | 列出有历史消息的会话 |
| POST | `/sessions/` | 创建会话 |
| GET | `/sessions/{id}/messages` | 读取会话历史 |
| DELETE | `/sessions/{id}` | 删除会话 |

### Workspace

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/workspace/tree` | 文件树 |
| GET | `/workspace/read` | 读取文件 |
| GET | `/workspace/open-url` | 打开当前用户 workspace 的 VSCode URL |
| GET | `/workspace/download` | 下载文件 |
| GET | `/workspace/download-zip` | 批量下载 |
| POST | `/workspace/upload` | 上传文件 |
| POST | `/workspace/mkdir` | 创建目录 |
| PUT | `/workspace/move` | 移动/重命名 |
| DELETE | `/workspace/delete` | 删除 |

### Memory

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/memory/conversations` | 查看长期任务摘要 |
| DELETE | `/memory/conversations/{doc_id}` | 删除记忆 |
| GET | `/memory/patterns` | 查看用户模式 |
| DELETE | `/memory/patterns/{pattern_id}` | 删除用户模式 |

## 与 Claude Code / Codex 的差异

这个项目不试图替代成熟的个人本机 Coding Agent。它的差异化是企业治理场景：

| 个人 Coding Agent | 本项目定位 |
|---|---|
| 本机运行，权限依赖个人环境 | 服务器内网运行，权限由平台治理 |
| 偏个人效率工具 | 偏企业受控工程工作台 |
| 代码和工具权限分散在个人电脑 | workspace、工具、会话集中管理 |
| 记忆偏个人上下文 | 记忆偏项目事实、团队约定、任务结果 |
| 审计能力依赖外部平台 | 可沉淀企业内部审计与操作记录 |

## 后续路线

- 记忆重构：从普通会话摘要升级为 `用户画像 / 项目事实 / 任务结果` 三类工程记忆。
- 权限策略：按角色、项目、工具类型控制 Agent 能力。
- 审计日志：记录命令、文件变更、审批结果、任务摘要。
- Git 集成：分支创建、diff 展示、commit、PR、代码评审。
- CI/CD 集成：允许 Agent 在受控环境触发测试、构建、静态检查。
- 企业知识库：接入内部文档、规范、接口文档和故障手册。

## 测试

```bash
uv run pytest
uv run ruff check enterprise_agent tests
cd frontend && npm run build
```

## 当前状态

项目仍处于面试/原型向企业工程平台演进阶段。核心 Agent、认证、workspace、记忆、文件管理、VSCode 打开、忘记密码开发模式和多用户隔离已经具备基础实现，后续重点是权限策略、审计和工程记忆质量。

## License

MIT
