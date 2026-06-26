# Enterprise Controlled Engineering Agent 架构详解

本文档详细介绍了从 `mini_claude_code.py` 演进到“企业内网部署的受控工程 Agent 平台”的完整架构。

## 0. 产品边界

本项目偏向 **Code Agent / Engineering Agent**，但重点不是和 Claude Code、Codex、Cursor 争夺个人本机编码体验，而是服务企业内部的受控部署场景：

- Agent 运行在公司服务器或内网环境中，而不是散落在个人电脑。
- 文件、Shell、VSCode 打开和长期记忆都围绕用户 workspace 隔离。
- 敏感工具执行可以通过 human-in-the-loop 确认。
- 后续可以扩展审计日志、权限策略、Git/CI 集成和项目知识库。

因此，架构优先级是：安全边界、可治理性、多用户隔离、工程任务闭环、项目知识沉淀。

---

## 1. 整体架构图

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                  Enterprise Controlled Engineering Agent                       │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                         API Layer (FastAPI)                          │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐   │    │
│  │  │ Auth Routes │  │ Chat Routes │  │ Sessions    │  │ Middleware│   │    │
│  │  │ /auth/*     │  │ /chat/*     │  │ /sessions/* │  │ (JWT)     │   │    │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └───────────┘   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                     │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     Core Agent Layer (LangGraph)                     │    │
│  │                                                                      │    │
│  │   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐      │    │
│  │   │ init_    │───▶│ load_    │───▶│ llm_     │───▶│ tool_    │      │    │
│  │   │ context  │    │ memory   │    │ call     │    │ executor │      │    │
│  │   └──────────┘    └──────────┘    └──────────┘    └──────────┘      │    │
│  │        │                              │              │               │    │
│  │        │              ┌───────────────┼──────────────┘               │    │
│  │        │              │               │                              │    │
│  │   ┌────▼────┐    ┌────▼────┐    ┌────▼────┐                        │    │
│  │   │ save_   │◀───│compress │◀───│ route   │  (conditional edges)   │    │
│  │   │ memory  │    │ context │    │ after   │                        │    │
│  │   └─────────┘    └─────────┘    │ llm     │                        │    │
│  │                                  └─────────┘                        │    │
│  │   + Tool Registry: file_ops, shell, task, subagent, background,    │    │
│  │                     skills, team, context_tools                    │    │
│  │   + LLM Factory: Anthropic, GLM, DeepSeek, OpenAI                  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                     │                                        │
│  ┌───────────────────┐  ┌───────────────────────────────────────────┐      │
│  │   Memory Layer    │  │           Data Layer                      │      │
│  │  ┌─────────────┐  │  │  ┌─────────────┐  ┌───────────────────┐   │      │
│  │  │ Short Term  │  │  │  │ MySQL       │  │ Redis             │   │      │
│  │  │ (Redis)     │  │  │  │ (Auth only) │  │ (Session Cache)   │   │      │
│  │  │ - messages  │  │  │  │ - User      │  │ - messages        │   │      │
│  │  │ - state     │  │  │  │ - Session   │  │ - state           │   │      │
│  │  │ - locks     │  │  │  │ - APIKey    │  │ - tool cache      │   │      │
│  │  └─────────────┘  │  │  │ - ToolLogs  │  │ - locks           │   │      │
│  │  ┌─────────────┐  │  │  └─────────────┘  └───────────────────┘   │      │
│  │  │ Long Term   │  │  │                                           │      │
│  │  │ (Chroma)    │  │  │                                           │      │
│  │  │ - conversations│  │                                           │      │
│  │  │ - patterns │  │  │                                           │      │
│  │  │ - semantic │  │  │                                           │      │
│  │  │   search   │  │  │                                           │      │
│  │  └─────────────┘  │  │                                           │      │
│  └───────────────────┘  └───────────────────────────────────────────┘      │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     Config Layer (Pydantic Settings)                 │    │
│  │  Settings: JWT, DB, Redis, LLM_PROVIDER, Chroma, Memory thresholds  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 目录结构

```
enterprise_agent/
├── api/                    # FastAPI 接口层
│   ├── main.py             # 应用入口、路由注册、lifespan (初始化 Chroma)
│   ├── middleware/
│   │   └── auth.py         # JWT 认证中间件
│   ├── routes/
│   │   ├── auth.py         # /auth/register, /login, /refresh
│   │   └── chat.py         # /chat/completions, /stream, /sessions/*
│   └── schemas/
│       ├── auth.py         # 注册/登录请求响应模型
│       └── chat.py         # ChatRequest, ChatResponse, SessionResponse
│
├── core/agent/             # LangGraph 核心引擎
│   ├── state.py            # AgentState TypedDict (消息、任务、工具状态)
│   ├── graph.py            # StateGraph 构建、节点连接、条件路由
│   ├── nodes.py            # 11 个节点函数 (使用 get_llm())
│   ├── llm_factory.py      # **新增** LLM 工厂函数 (多模型支持)
│   ├── context.py          # ContextManager + TranscriptManager
│   └── tools/              # 工具注册
│       ├── __init__.py     # ALL_TOOLS 汇总 + 权限过滤
│       ├── file_ops.py     # read_file, write_file, edit_file
│       ├── shell.py        # bash
│       ├── task.py         # todo_update, task_create/get/update/list, claim
│       ├── subagent.py     # task (子代理，支持多模型)
│       ├── background.py   # background_run, check_background
│       ├── skills.py       # load_skill, list_skills, reload_skills
│       ├── context_tools.py # compress, list_transcripts, context_status
│       └── team.py         # spawn_teammate, send_message, broadcast, etc.
│
├── memory/                 # 记忆管理
│   ├── base.py             # MemoryBase 抽象接口
│   ├── short_term.py       # ShortTermMemory (Redis)
│   └── long_term.py        # **新增** ChromaLongTermMemory (向量数据库)
│
├── auth/                   # 认证授权
│   ├── jwt_handler.py      # JWT 创建/验证、密码哈希
│   └── permissions.py      # 权限定义
│
├── models/                 # SQLAlchemy ORM 模型 (仅认证相关)
│   ├── user.py             # User 表
│   ├── session.py          # Session 表
│   ├── tool_usage.py       # ToolUsageLog
│   └── api_key.py          # APIKey
│   # 注意: conversation.py, user_pattern.py 已删除，改用 Chroma
│
├── db/                     # 数据库连接
│   ├── mysql.py            # AsyncEngine, session_factory, get_db()
│   ├── redis.py            # Redis connection pool, get_redis()
│   └── chroma.py           # **新增** Chroma 客户端初始化
│
├── config/
│   └── settings.py         # Pydantic Settings (多模型配置)
│
└── utils/
```

---

## 3. LangGraph 工作流

### 3.1 工作流图

```python
# graph.py - 核心工作流
graph = StateGraph(AgentState)

# 节点定义
graph.add_node("init_context", init_context_node)    # 重置状态
graph.add_node("load_memory", load_memory_node)      # 从 Redis 加载历史
graph.add_node("llm_call", llm_call_node)            # LLM 调用 + 工具绑定
graph.add_node("tool_executor", tool_executor_node)  # 执行工具调用
graph.add_node("save_memory", save_memory_node)      # 保存到 Redis
graph.add_node("compress_context", compress_context_node)  # 上下文压缩
```

### 3.2 流程图

```
init_context → load_memory → llm_call
                          ↓
              route_after_llm() [条件路由]
                          ↓
         ┌────────────────┼────────────────┐
         │                │                │
    tool_call?       compress?         end?
         │                │                │
    tool_executor   compress_context    END
         │                │
    save_memory      llm_call
         │
    llm_call ─────────────┘
```

### 3.3 节点功能说明

| 节点 | 功能 | 输入 | 输出 |
|------|------|------|------|
| `init_context` | 重置状态字段 | state | token_count=0, pending_tool_calls=[], tool_results={} |
| `load_memory` | 从 Redis 加载历史 | session_id | messages (历史消息列表) |
| `llm_call` | LLM 调用 | messages | response, tool_calls, token_count |
| `tool_executor` | 执行工具调用 | pending_tool_calls | tool_results |
| `save_memory` | 保存到 Redis | messages, session_id | 无 |
| `compress_context` | 上下文压缩 | token_count > threshold | context_summary, messages (压缩后) |

---

## 4. AgentState 状态定义

```python
class AgentState(TypedDict):
    """LangGraph代理状态定义"""

    # 消息历史（LangGraph自动合并）
    messages: Annotated[List[Dict], add_messages]

    # 用户和会话信息
    session_id: str
    user_id: int

    # 任务追踪
    current_task: Optional[Dict[str, Any]]
    todos: List[Dict[str, Any]]

    # 上下文管理
    context_summary: Optional[str]
    token_count: int

    # 工具执行
    pending_tool_calls: List[Dict[str, Any]]
    tool_results: Dict[str, Any]

    # 子代理
    sub_agents: Dict[str, Any]

    # 记忆引用
    short_term_memory_ref: Optional[str]
    long_term_memory_refs: List[str]

    # 工作流控制
    should_compress: bool
    should_end: bool
```

---

## 5. 工具系统架构

### 5.1 工具分类

```python
# tools/__init__.py - 工具注册

ALL_TOOLS = [
    # 文件操作
    read_file, write_file, edit_file,

    # Shell
    bash,

    # 任务管理 (双层)
    todo_update, task_create, task_get, task_update, task_list, claim_task,

    # 子代理
    subagent_task,

    # 后台任务
    background_run, check_background,

    # 技能
    load_skill, list_skills, reload_skills,

    # 团队协作
    spawn_teammate, list_teammates, send_message, read_inbox,
    broadcast, shutdown_request, plan_approval, idle,
]
```

### 5.2 权限控制

```python
permission_map = {
    "tools:file": [read_file, write_file, edit_file],
    "tools:shell": [bash],
    "tools:task": [todo_update, task_create, task_get, task_update, task_list, claim_task],
    "tools:subagent": [subagent_task],
    "tools:background": [background_run, check_background],
    "tools:skills": [load_skill, list_skills, reload_skills],
    "tools:team": [spawn_teammate, list_teammate, send_message, ...],
    "tools:all": ALL_TOOLS,
}
```

---

## 6. 分层记忆架构

### 6.1 架构图

```
┌────────────────────────────────────────────────────────────┐
│                     Memory Architecture                     │
│                                                            │
│  ┌─────────────────────┐     ┌─────────────────────────┐  │
│  │   Short Term        │     │      Long Term          │  │
│  │   (Redis)           │     │      (Chroma)           │  │
│  │                     │     │                         │  │
│  │   TTL: 24h          │     │   Persistent            │  │
│  │   Max: 100 msgs     │     │   + Semantic Search     │  │
│  │                     │     │                         │  │
│  │   Keys:             │     │   Collections:          │  │
│  │   session:{id}:     │     │   - conversations       │  │
│  │     messages        │     │     (对话历史+向量搜索) │  │
│  │     state           │     │   - user_patterns       │  │
│  │     tools_cache     │     │     (用户行为模式)     │  │
│  │   lock:session:{id} │     │                         │  │
│  │                     │     │   Embedding:            │  │
│  │                     │     │   all-MiniLM-L6-v2      │  │
│  │                     │     │   (本地模型,无需API)    │  │
│  └─────────────────────┘     └─────────────────────────┘  │
│                                                            │
│  Flow:                                                     │
│  Request → load_memory (Redis) → Process → save_memory    │
│         → store to Chroma (长期记忆+语义搜索)             │
│                                                            │
│  Note: MySQL 仅用于认证 (User, Session, APIKey, ToolLogs) │
└────────────────────────────────────────────────────────────┘
```

### 6.2 ShortTermMemory 类 (Redis)

| 方法 | 功能 |
|------|------|
| `append_message()` | 追加消息到会话历史 |
| `get_messages()` | 获取会话消息 |
| `clear_messages()` | 清空会话消息 |
| `get_state()` | 获取会话状态 |
| `set_state()` | 设置会话状态 |
| `acquire_lock()` | 获取会话锁 |
| `release_lock()` | 释放会话锁 |
| `cache_tool_result()` | 缓存工具执行结果 |
| `get_cached_tool_result()` | 获取缓存的工具结果 |

### 6.3 ChromaLongTermMemory 类 (新增)

| 方法 | 功能 |
|------|------|
| `store_conversation()` | 存储对话消息 (带 embedding) |
| `search_conversations()` | **语义搜索**对话历史 |
| `get_session_history()` | 获取会话所有消息 |
| `store_pattern()` | 存储用户行为模式 |
| `search_patterns()` | **语义搜索**用户模式 |
| `get_all_patterns()` | 获取用户所有模式 |

---

## 7. 认证流程

### 7.1 流程图

```
┌────────────┐     ┌────────────┐     ┌────────────────────┐
│  Register  │     │   Login    │     │   API Request      │
│            │     │            │     │                    │
│ POST       │     │ POST       │     │ Authorization:     │
│ /auth/     │     │ /auth/     │     │ Bearer <token>     │
│ register   │     │ login      │     │                    │
└────────────┘     └────────────┘     └────────────────────┘
      │                  │                     │
      ▼                  ▼                     ▼
┌────────────┐     ┌────────────┐     ┌────────────────────┐
│  MySQL     │     │  Verify    │     │   Middleware       │
│  INSERT    │     │  Password  │     │   auth.py          │
│  User      │     │            │     │                    │
└────────────┘     └────────────┘     │   jwt_handler.     │
                   │      │           │   verify_token()   │
                   ▼      ▼           │                    │
              ┌────────────┐          │   Extract:         │
              │  JWT       │          │   - user_id        │
              │  Handler   │          │   - permissions    │
              │            │          └────────────────────┘
              │  create_   │                    │
              │  tokens()  │                    ▼
              │            │          ┌────────────────────┐
              │  Returns:  │          │   get_current_user │
              │  access_   │          │   (Depends)        │
              │  refresh_  │          └────────────────────┘
              └────────────┘
```

### 7.2 JWT Token 结构

```python
# Access Token Payload
{
    "sub": user_id,
    "iat": issued_at,
    "exp": expires_at,
    "type": "access",
    "permissions": ["tools:file", "tools:task", ...]
}

# Refresh Token Payload
{
    "sub": user_id,
    "iat": issued_at,
    "exp": expires_at,  # 7 days
    "type": "refresh"
}
```

---

## 8. API 端点汇总

| 模块 | 端点 | 功能 |
|------|------|------|
| **认证** | `POST /auth/register` | 用户注册 |
| | `POST /auth/login` | 登录获取 JWT |
| | `POST /auth/refresh` | 刷新 Token |
| **对话** | `POST /chat/completions` | 非流式对话 |
| | `POST /chat/stream` | SSE 流式对话 |
| **会话** | `GET /sessions/` | 列出会话 |
| | `POST /sessions/` | 创建会话 |
| | `DELETE /sessions/{id}` | 删除会话 |
| **系统** | `GET /health` | 健康检查 |
| | `GET /` | API 信息 |

---

## 9. 与原始 `mini_claude_code.py` 的对比

| 原始版本 | 重构版本 | 变化 |
|----------|----------|------|
| 单文件 740 行 | 多模块包 | 模块化拆分 |
| 本地 REPL | FastAPI 服务 | 多用户 API |
| 无认证 | JWT + 权限 | 企业级认证 |
| 内存 Todo | Redis + MySQL | 持久化存储 |
| 手动循环 | LangGraph StateGraph | 有状态工作流 |
| 单用户 | 多用户隔离 | 用户级隔离 |
| 无配置管理 | Pydantic Settings | 环境变量配置 |

---

## 10. 核心技术栈

| 技术 | 用途 |
|------|------|
| **LangGraph** | 有状态 AI 工作流图 |
| **LangChain** | LLM 抽象 + 工具绑定 + 多模型支持 |
| **FastAPI** | 异步 REST API |
| **Redis** | 短期记忆缓存 (消息、状态、锁) |
| **Chroma** | 长期向量记忆 (语义搜索) |
| **MySQL** | 用户认证 + 会话管理 (仅 Auth) |
| **JWT** | 认证 + 权限控制 |
| **Pydantic** | 配置 + 数据验证 |
| **SQLAlchemy 2.0** | 异步 ORM |
| **sentence-transformers** | 本地 Embedding 模型 |

---

## 11. 配置说明

### 11.1 Settings 类

```python
class Settings(BaseSettings):
    # App
    APP_NAME: str = "Enterprise Agent"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # MySQL (仅认证)
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "agent_user"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "enterprise_agent"

    # Redis (短期记忆)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None

    # Chroma (长期向量记忆)
    CHROMA_PERSIST_DIR: str = "./chroma_data"
    CHROMA_COLLECTION_CONVERSATIONS: str = "conversations"
    CHROMA_COLLECTION_PATTERNS: str = "user_patterns"
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    # LLM Provider (多模型支持)
    LLM_PROVIDER: str = "anthropic"  # anthropic | glm | deepseek | openai
    LLM_API_KEY: str = ""
    LLM_BASE_URL: Optional[str] = None
    MODEL_ID: str = "claude-sonnet-4-6"

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Memory
    SHORT_TERM_TTL_HOURS: int = 24
    MAX_MESSAGES_PER_SESSION: int = 100
    TOKEN_THRESHOLD: int = 100000
```

### 11.2 环境变量 (.env)

```bash
# === LLM Provider ===
# 选择提供商: anthropic, glm, deepseek, openai
LLM_PROVIDER=anthropic

# API Key (通用)
LLM_API_KEY=your-api-key

# 模型 ID (可选，有默认值)
MODEL_ID=claude-sonnet-4-6

# === 数据库 ===
MYSQL_PASSWORD=your-db-password
REDIS_PASSWORD=

# === Chroma ===
CHROMA_PERSIST_DIR=./chroma_data

# === 安全 ===
JWT_SECRET_KEY=your-secret-key

# === 可选 ===
DEBUG=true
API_PORT=8000

# === 多模型配置示例 ===
# 使用 GLM-4
# LLM_PROVIDER=glm
# LLM_API_KEY=your-glm-key
# MODEL_ID=glm-4

# 使用 DeepSeek
# LLM_PROVIDER=deepseek
# LLM_API_KEY=your-deepseek-key
# MODEL_ID=deepseek-chat
```

---

## 12. 多模型支持 (新增)

### 12.1 支持的模型提供商

| 提供商 | LLM_PROVIDER 值 | API 格式 | 工具调用 | 默认模型 |
|--------|-----------------|----------|----------|----------|
| Anthropic | `anthropic` | Native | ✅ | claude-sonnet-4-6 |
| GLM (智谱) | `glm` | OpenAI兼容 | ✅ | glm-4 |
| DeepSeek | `deepseek` | OpenAI兼容 | ✅ | deepseek-chat |
| OpenAI | `openai` | Native | ✅ | gpt-4 |

### 12.2 LLM Factory

```python
# core/agent/llm_factory.py
def get_llm() -> BaseChatModel:
    """根据 LLM_PROVIDER 配置返回对应的 LLM 实例"""
    provider = settings.LLM_PROVIDER

    if provider == "anthropic":
        return ChatAnthropic(model=settings.MODEL_ID, api_key=settings.LLM_API_KEY)

    elif provider == "glm":
        return ChatOpenAI(
            model=settings.MODEL_ID or "glm-4",
            api_key=settings.LLM_API_KEY,
            base_url="https://open.bigmodel.cn/api/paas/v4"
        )

    elif provider == "deepseek":
        return ChatOpenAI(
            model=settings.MODEL_ID or "deepseek-chat",
            api_key=settings.LLM_API_KEY,
            base_url="https://api.deepseek.com"
        )
```

### 12.3 使用位置

| 文件 | 使用方式 |
|------|----------|
| `nodes.py` | `llm = get_llm()` |
| `context.py` | `self.llm = get_llm()` |
| `team.py` | `llm = get_llm()` |
| `subagent.py` | `llm = get_llm()` |

---

## 13. Chroma 向量数据库 (新增)

### 13.1 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Chroma Vector Database                    │
│                                                             │
│  Collections:                                               │
│  ┌─────────────────────┐                                    │
│  │ conversations       │  对话历史 + 语义搜索               │
│  │ - metadata: user_id │                                    │
│  │ - embedding: text   │                                    │
│  │ - semantic search   │                                    │
│  └─────────────────────┘                                    │
│                                                             │
│  ┌─────────────────────┐                                    │
│  │ user_patterns       │  用户行为模式                      │
│  │ - pattern_type      │                                    │
│  │ - pattern_key       │                                    │
│  │ - embedding: pattern│                                    │
│  └─────────────────────┘                                    │
│                                                             │
│  Embedding Model:                                           │
│  - all-MiniLM-L6-v2 (本地 sentence-transformers)            │
│  - 无需 API Key                                             │
│  - 384 维向量                                               │
│                                                             │
│  Persistence:                                               │
│  - ./chroma_data/                                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 13.2 关键文件

| 文件 | 功能 |
|------|------|
| `db/chroma.py` | Chroma 客户端初始化、Collection 管理 |
| `memory/long_term.py` | ChromaLongTermMemory 类、语义搜索 |
| `api/main.py` | lifespan 中调用 `init_chroma()` |

### 13.3 语义搜索示例

```python
# 搜索相关对话
results = await long_term_memory.search_conversations(
    query="如何实现上下文压缩",
    n_results=10,
)

# 搜索用户模式
patterns = await long_term_memory.search_patterns(
    query="用户偏好的工具",
    pattern_type="preference",
)
```

---

## 12. 快速启动

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env

# 3. 启动数据库
cd docker
docker-compose up -d mysql redis

# 4. 初始化数据库
uv run python scripts/init_db.py

# 5. 启动服务
uv run serve
# 或
uv run uvicorn enterprise_agent.api.main:app --reload

# 6. 访问 API
# http://localhost:8000/docs (Swagger UI)
```

---

## 13. 扩展指南

### 13.1 添加新工具

1. 在 `enterprise_agent/core/agent/tools/` 创建新模块
2. 使用 `@tool` 装饰器定义工具函数
3. 在 `__init__.py` 中导入并添加到 `ALL_TOOLS`
4. 在 `permission_map` 中添加权限映射

### 13.2 添加新节点

1. 在 `nodes.py` 中定义异步节点函数
2. 在 `graph.py` 中调用 `graph.add_node()`
3. 使用 `graph.add_edge()` 或 `graph.add_conditional_edges()` 连接

### 13.3 添加新 API 端点

1. 在 `api/schemas/` 定义请求/响应模型
2. 在 `api/routes/` 创建路由函数
3. 在 `api/main.py` 注册路由

---

## 14. 简历亮点

1. **架构设计**: 多层架构，清晰的模块边界
2. **技术栈现代化**: LangGraph 有状态 AI 工作流
3. **企业特性**: 多用户隔离、认证授权、分层记忆
4. **生产部署**: Docker 容器化、异步高性能

---

## 15. 上下文管理架构 (新增)

### 15.1 三阶段压缩机制

```
┌─────────────────────────────────────────────────────────────┐
│                   Context Compression Flow                   │
│                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │ Microcompact│    │ Auto Compact│    │ Manual      │     │
│  │ (每次LLM前) │    │ (阈值触发)  │    │ Compress    │     │
│  │             │    │             │    │ (工具触发)  │     │
│  │ 清理旧      │    │ 保存transcript│    │ 立即执行    │     │
│  │ tool_result │    │ 生成摘要    │    │             │     │
│  │ 保留最近3个 │    │ 替换context │    │             │     │
│  └─────────────┘    └─────────────┘    └─────────────┘     │
│                                                             │
│  触发条件:                                                   │
│  - Microcompact: 每次LLM调用前                              │
│  - Auto Compact: token_count > TOKEN_THRESHOLD (100K)     │
│  - Manual: compress 工具调用                               │
└─────────────────────────────────────────────────────────────┘
```

### 15.2 关键组件

| 组件 | 文件 | 功能 |
|------|------|------|
| `ContextManager` | `core/agent/context.py` | Token估算、microcompact、auto_compact |
| `TranscriptManager` | `core/agent/context.py` | Transcript保存/加载 |
| `pre_llm_microcompact_node` | `core/agent/nodes.py` | LLM前清理节点 |
| `compress_context_node` | `core/agent/nodes.py` | 自动压缩节点 |
| `manual_compress_node` | `core/agent/nodes.py` | 手动压缩节点 |

### 15.3 新增工具

```python
# context_tools.py
compress          # 手动触发压缩
list_transcripts  # 列出已保存的transcript
get_transcript    # 加载特定transcript
context_status    # 查看当前上下文状态
```

---

## 16. Teammate 异步架构 (新增)

### 16.1 多Agent架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Multi-Agent System                        │
│                                                             │
│  ┌─────────────────┐                                        │
│  │   Lead Agent    │  主控 LangGraph 工作流                  │
│  │   (main graph)  │                                        │
│  └─────────────────┘                                        │
│         │                                                   │
│         │ spawn_teammate()                                  │
│         ▼                                                   │
│  ┌─────────────────┐  ┌─────────────────┐                  │
│  │  TeammateRunner │  │  TeammateRunner │  ...             │
│  │  (asyncio task) │  │  (asyncio task) │                  │
│  │                 │  │                 │                  │
│  │  ┌───────────┐  │  │  ┌───────────┐  │                  │
│  │  │Work Phase │  │  │  │Work Phase │  │                  │
│  │  │- LLM调用  │  │  │  │- LLM调用  │  │                  │
│  │  │- 工具执行 │  │  │  │- 工具执行 │  │                  │
│  │  │- 消息处理 │  │  │  │- 消息处理 │  │                  │
│  │  └─────┬─────┘  │  │  └─────┬─────┘  │                  │
│  │       │ idle() │  │       │ idle() │  │                  │
│  │  ┌─────▼─────┐  │  │  ┌─────▼─────┐  │                  │
│  │  │Idle Phase │  │  │  │Idle Phase │  │                  │
│  │  │- 轮询inbox│  │  │  │- 轮询inbox│  │                  │
│  │  │- 自动认领 │  │  │  │- 自动认领 │  │                  │
│  │  │- 等待任务 │  │  │  │- 等待任务 │  │                  │
│  │  └───────────┘  │  │  └───────────┘  │                  │
│  └─────────────────┘  └─────────────────┘                  │
│                                                             │
│  消息通道: AsyncMessageBus (.team/inbox/*.jsonl)           │
└─────────────────────────────────────────────────────────────┘
```

### 16.2 Work-Idle 循环

```
Work Phase (最多50轮):
    1. 检查 inbox → 处理 shutdown_request / 消息
    2. Microcompact 清理上下文
    3. LLM 调用 + 工具绑定
    4. 工具执行 (idle → 进入Idle Phase)
    5. 继续或结束

Idle Phase (最多60秒):
    1. 每5秒轮询 inbox
    2. 检查未认领任务 (pending + no owner + no blockedBy)
    3. 自动认领 → 恢复 Work Phase
    4. 无任务 → 超时后 shutdown
```

### 16.3 关键组件

| 组件 | 文件 | 功能 |
|------|------|------|
| `AsyncMessageBus` | `core/agent/tools/team.py` | 异步消息传递 |
| `TeammateConfig` | `core/agent/tools/team.py` | 队友配置持久化 |
| `TeammateRunner` | `core/agent/tools/team.py` | 队友运行器 (asyncio task) |
| `TeammateManager` | `core/agent/tools/team.py` | 多队友管理 |

### 16.4 消息类型

```python
VALID_MSG_TYPES = {
    "message",              # 普通消息
    "broadcast",            # 广播消息
    "shutdown_request",     # 关闭请求 (带 request_id)
    "shutdown_response",    # 关闭响应
    "plan_approval_response", # 计划审批响应
    "auto_claimed_task",    # 自动认领任务通知
}
```

---

## 17. TodoWrite Nag Reminder 机制 (新增)

### 17.1 机制说明

TodoWrite nag reminder 是防止 agent 忘记更新任务状态的重要机制。当 agent 有未完成的 todo 项时，如果连续3轮没有使用 `todo_update` 工具，系统会自动添加提醒消息。

### 17.2 工作原理

```
┌─────────────────────────────────────────────────────────────┐
│                  TodoWrite Nag Reminder Flow                 │
│                                                             │
│  Tool Execution                                             │
│       │                                                     │
│       ▼                                                     │
│  ┌─────────────────┐                                        │
│  │ Check todo_update│                                       │
│  │ usage            │                                       │
│  │                  │                                       │
│  │ used_todo=True?  │                                       │
│  └─────────────────┘                                        │
│       │                                                     │
│       ├──── Yes ────► rounds_without_todo = 0               │
│       │                                                     │
│       ├──── No ─────► rounds_without_todo += 1              │
│       │                                                     │
│       ▼                                                     │
│  ┌─────────────────┐                                        │
│  │ Check conditions│                                        │
│  │                 │                                        │
│  │ has_open_todos? │                                        │
│  │ rounds >= 3?    │                                        │
│  └─────────────────┘                                        │
│       │                                                     │
│       ├──── Both True ───► Add reminder message             │
│       │                      │                              │
│       │                      ▼                              │
│       │               "<reminder>Update your todos.</reminder>" │
│       │                      │                              │
│       │                      ▼                              │
│       │               rounds_without_todo = 0               │
│       │                                                     │
│       ├──── False ────► Continue normal flow                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 17.3 状态字段

```python
# state.py 新增字段
class AgentState(TypedDict):
    # ...

    # TodoWrite nag reminder state
    rounds_without_todo: int      # 连续未使用todo_update的轮数
    used_todo_last_round: bool    # 上一轮是否使用了todo_update
    has_open_todos: bool          # 是否有未完成的todo项
```

### 17.4 关键节点

| 节点 | 功能 |
|------|------|
| `init_context_node` | 初始化 rounds_without_todo=0, used_todo_last_round=False, has_open_todos=False |
| `tool_executor_node` | 检测 todo_update 使用，检查 has_open_todos |
| `save_memory_node` | 更新计数器，添加提醒消息 |

### 17.5 原始实现参考

```python
# mini_claude_code.py 第698-700行
rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
if TODO.has_open_items() and rounds_without_todo >= 3:
    results.append({"type": "text", "text": "<reminder>Update your todos.</reminder>"})
```

---

## License

MIT
