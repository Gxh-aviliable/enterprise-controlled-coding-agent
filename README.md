# Mini Claude Code — Enterprise Agent System

企业级多用户 AI Agent 系统，基于 LangGraph + FastAPI + Vue 3 构建。

## 技术栈

| 层 | 技术 |
|---|------|
| Agent 引擎 | LangGraph StateGraph + LangChain（有状态工作流、Checkpointer 持久化） |
| API | FastAPI（异步）+ SSE 逐 token 流式响应 + JWT 认证 |
| 前端 | Vue 3 + Vite + highlight.js + marked + DOMPurify |
| 短期记忆 | Redis（会话状态、分布式锁、LangGraph RedisSaver checkpointer） |
| 长期记忆 | Chroma 向量数据库（语义搜索、重要性评估、衰减清理） |
| 数据库 | MySQL（用户认证、会话管理） |
| LLM | DeepSeek / Anthropic / GLM / OpenAI / MiMo — 5 个 Provider |
| 可观测性 | LangSmith tracing（可选） |
| 部署 | Docker Compose |

## 快速启动

```bash
# 1. 克隆项目（默认 develop 分支）
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
# 编辑 .env：填入 LLM_API_KEY、JWT_SECRET_KEY

# 5. 启动后端（端口 8000）
uv run uvicorn enterprise_agent.api.main:app --reload

# 6. 启动前端（端口 3000，新终端）
cd frontend && npm run dev

# 访问: http://localhost:3000
```

## 项目结构

```
my_mini_claude_code/
├── enterprise_agent/              # Python 后端包
│   ├── api/                       # FastAPI 路由 + 中间件
│   │   ├── main.py                # 应用入口、lifespan、CORS、health 检查
│   │   ├── middleware/auth.py     # JWT Bearer 认证依赖
│   │   ├── routes/
│   │   │   ├── auth.py            # /auth/* (register, login, refresh)
│   │   │   ├── chat.py            # /chat/* (stream/resume/confirm) + /sessions/* (CRUD + 消息历史)
│   │   │   └── workspace.py       # /workspace/* (文件树、读写、上传下载)
│   │   └── schemas/               # Pydantic 请求/响应模型
│   ├── core/agent/                # LangGraph Agent 核心
│   │   ├── graph.py               # StateGraph + AsyncRedisSaver checkpointer
│   │   ├── state.py               # AgentState TypedDict
│   │   ├── nodes.py               # 图节点 (llm_call, tool_executor, compress, etc.)
│   │   ├── context.py             # 上下文压缩 (TranscriptManager, TokenEstimator)
│   │   ├── llm_factory.py         # 多 LLM Provider 工厂
│   │   └── tools/                 # Agent 工具集
│   │       ├── __init__.py        # ALL_TOOLS 注册 + 权限过滤
│   │       ├── shell.py           # bash 命令（安全黑名单）
│   │       ├── file_ops.py        # 文件读写
│   │       ├── workspace.py       # 工作区路径解析 (ContextVar 隔离)
│   │       ├── skills.py          # SkillLoader 多租户 Skill 系统
│   │       ├── team.py            # 多 Agent 协作 (spawn_teammate)
│   │       ├── subagent.py        # task() 通用子 Agent
│   │       ├── background.py      # 后台任务管理
│   │       └── todo.py            # Todo 管理
│   ├── memory/                    # 三层记忆
│   │   ├── short_term.py          # Redis 短期记忆 (24h TTL)
│   │   ├── long_term.py           # Chroma 向量长期记忆
│   │   ├── accumulator.py         # 记忆累积器 (20 轮 flush)
│   │   ├── importance.py          # LLM 重要性评分
│   │   └── decay.py               # 记忆衰减清理
│   ├── db/                        # 数据库连接
│   │   ├── mysql.py               # SQLAlchemy async engine
│   │   ├── redis.py               # Redis 异步客户端
│   │   └── chroma.py              # Chroma PersistentClient + embedding
│   ├── models/                    # ORM 模型
│   │   ├── session.py             # Session (id, user_id, title, status)
│   │   └── user.py                # User (id, username, email, password_hash)
│   └── config/settings.py         # Pydantic Settings（所有配置项）
│
├── frontend/                      # Vue 3 + Vite 前端
│   ├── src/
│   │   ├── App.vue                # 根组件（CSS 变量、暗色/亮色主题）
│   │   ├── api/client.js          # fetch API 客户端（SSE、token 刷新锁）
│   │   ├── stores/auth.js         # 认证状态（reactive）
│   │   ├── composables/
│   │   │   └── useToast.js        # Toast 通知 composable
│   │   └── components/
│   │       ├── LoginForm.vue       # 登录/注册
│   │       ├── Sidebar.vue         # 侧边栏（会话列表 + 文件树）
│   │       ├── ChatPanel.vue       # 聊天面板（SSE 流式、Markdown、Tool 确认）
│   │       ├── FileTree.vue        # 文件树
│   │       ├── TreeNode.vue        # 递归树节点（展开/折叠/右键菜单）
│   │       ├── FileViewer.vue      # 文件内容查看器（语法高亮、下载）
│   │       ├── ToolCallCard.vue    # 工具调用可视化卡片
│   │       ├── ThemeToggle.vue     # 暗色/亮色主题切换
│   │       ├── Toast.vue           # Toast 通知
│   │       └── FileManager.vue     # 文件管理器（备用）
│   └── vite.config.js             # Vite 配置（代理 /api → :8000）
│
├── shared_skills/                 # 全局 Skill（所有用户可见）
│   ├── python/SKILL.md
│   ├── langgraph/SKILL.md
│   ├── fastapi/SKILL.md
│   └── agent-interviewer/SKILL.md
│
├── tests/                         # pytest 测试（17 个 skill 测试，核心 Agent 测试）
├── docker/                        # Dockerfile + docker-compose.yml
└── CLAUDE.md                      # Claude Code 项目文档
```

## API 接口

### 认证
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/register` | 用户注册 |
| POST | `/auth/login` | 登录获取 JWT |
| POST | `/auth/refresh` | 刷新 Token |

### 对话
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat/completions` | 非流式对话 |
| POST | `/chat/stream` | 流式对话（SSE，逐 token） |
| POST | `/chat/stream/resume` | 中断后恢复流式 |
| POST | `/chat/confirm` | 工具确认回调 |

### 会话管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/sessions` | 列出用户会话 |
| POST | `/sessions` | 创建新会话（自动提取标题） |
| GET | `/sessions/{id}/messages` | 加载会话历史消息 |
| DELETE | `/sessions/{id}` | 删除会话 |

### 工作区
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/workspace/tree` | 获取文件树 |
| GET | `/workspace/read` | 读取文件内容 |
| GET | `/workspace/download` | 下载单个文件 |
| GET | `/workspace/download-zip` | 批量下载 zip |
| POST | `/workspace/upload` | 上传文件 |
| POST | `/workspace/mkdir` | 创建目录 |
| PUT | `/workspace/move` | 移动/重命名 |
| DELETE | `/workspace/delete` | 删除文件或目录 |

### 系统
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查（检测 MySQL + Redis 连通性） |

## 环境变量 (.env)

```bash
# LLM
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-v4-pro

# 数据库
MYSQL_HOST=localhost
MYSQL_USER=agent_user
MYSQL_PASSWORD=

# JWT
JWT_SECRET_KEY=your-secret-key-change-me

# 可选
DEBUG=true
CORS_ORIGINS=http://localhost:3000
WORKSPACE_BASE=/workspaces
LANGSMITH_API_KEY=     # 开启 LangSmith tracing
```

## 核心特性

### 1. LangGraph 有状态 Agent
- StateGraph + `add_messages` reducer
- `AsyncRedisSaver` checkpointer 自动持久化（TTL 24h）
- 逐 token SSE 流式（`stream_mode=["messages", "updates"]`）
- 人工确认中断/恢复（`interrupt()` + `Command(resume=...)`）
- Token 阈值自动压缩（CJK 字符独立估算）

### 2. 三层记忆
- **Redis 短期**: 会话状态、分布式锁、checkpointer（TTL 24h）
- **Chroma 长期**: 向量语义存储、重要性评估、衰减清理
- **上下文压缩**: 自动摘要 + Transcript 备份

### 3. 企业级 Skill 多租户
- 全局 Skill（`shared_skills/`，所有用户可见）
- 用户 Skill（`user_{id}/.skills/`，个人 DIY，覆盖全局同名）
- System prompt 自动注入可用 Skill 列表
- `list_skills()` / `load_skill()` / `reload_skills()` 工具

### 4. 前端
- DeepSeek 风格浅色主题 + 暗色模式切换
- Markdown 渲染（`marked` + `DOMPurify`）+ 代码语法高亮（`highlight.js`）
- 工具调用可视化卡片（折叠/展开，状态指示，耗时显示）
- IDE 布局：侧边栏（会话 + 文件树）+ 右侧内容区（聊天 / 文件预览）
- Toast 通知系统
- Session 消息历史加载 + 自动标题提取

### 5. 安全
- JWT 认证 + Token 轮换（刷新锁防并发风暴）
- Shell 命令黑名单 + 破坏性操作正则检测
- 敏感工具人工确认（`ENABLE_TOOL_CONFIRMATION`）
- XSS 防护（`DOMPurify` 白名单净化）
- CORS 精确配置（禁止 `*` + `credentials`）

### 6. 多 Agent 协作
- `spawn_teammate()` 创建协作 Agent
- `task(agent_type="Explore")` 委派子任务
- `background_run()` 后台任务管理

## Git 分支策略

```
main        ← 稳定版本（打 tag 发布）
develop     ← 日常开发（默认工作分支）
feature/*   ← 大功能分支
```

详见 [CLAUDE.md](CLAUDE.md)。

## 测试

```bash
uv run pytest                              # 全部测试
uv run pytest tests/core/tools/ -v         # 工具测试
uv run pytest tests/core/tools/test_skills.py -v  # Skill 测试（17 个）
uv run pytest --cov=enterprise_agent       # 带覆盖率
```

## 当前版本: v0.2.0

上次提交: `d746c42` — FileViewer scroll fix

## License

MIT
