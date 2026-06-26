# Enterprise Controlled Engineering Agent 从零开始理解指南

本文档是项目的完整理解指南，帮助你从零开始理解这个“企业内网部署的受控工程 Agent”代码库。

---

## 第一阶段：项目概览（1小时）

### 1.1 项目是什么？

这是一个**企业内网受控工程 Agent 平台**，核心目标是把 AI 编程能力放到企业服务器侧统一治理：
- 用户通过 Web 前端与工程 Agent 对话
- Agent 在受控 workspace 中读代码、改文件、运行命令、管理任务
- 支持多用户隔离、JWT 认证、工具确认、会话管理和分层记忆
- 适合公司内网部署，避免在个人电脑上安装高权限 Agent 或暴露私有代码

### 1.2 技术栈速览

| 层级 | 技术 | 用途 |
|------|------|------|
| **Agent引擎** | LangGraph + LangChain | 有状态工作流，工具绑定 |
| **后端API** | FastAPI | 异步REST API + SSE流式响应 |
| **前端** | Vue 3 + Vite | 响应式Web界面 |
| **短期记忆** | Redis | 会话状态、分布式锁、checkpointer |
| **长期记忆** | Chroma向量数据库 | 语义搜索、用户行为模式 |
| **认证数据库** | MySQL | 用户认证、会话管理 |
| **认证** | JWT | Token认证 + 权限控制 |

### 1.3 一句话理解核心流程

```
用户发送工程任务 → API 接收 → LangGraph 工作流执行 → Agent 调用 LLM → LLM 在权限边界内选择工具 → 执行文件/命令/记忆操作 → 返回可审计结果
```

---

## 第二阶段：从入口开始（2小时）

### 2.1 启动入口：`api/main.py`

**阅读顺序**：

1. **第1-21行**：导入和日志配置
2. **第24-60行**：`lifespan()` 函数 —— **这是理解启动的关键**

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时执行：
    # 1. 初始化 MySQL 表
    await init_db()
    
    # 2. 初始化 Chroma 向量数据库
    init_chroma()
    
    # 3. 初始化 Redis checkpointer（状态持久化）
    await setup_checkpointer()
    
    # 4. 启动记忆衰减清理任务
    cleanup_task = get_or_start_cleanup_task()
    
    yield  # 服务运行期间
    
    # 关闭时执行：
    cleanup_task.cancel()
    await close_db()
    await close_redis()
```

**理解要点**：
- `lifespan` 是 FastAPI 的生命周期管理
- 四个初始化步骤确保服务依赖都准备好
- Redis checkpointer 是 LangGraph 状态持久化的关键

3. **第62-88行**：FastAPI 应用创建和路由注册

```python
app = FastAPI(title=..., lifespan=lifespan)

# 注册路由
app.include_router(auth_router)      # /auth/*
app.include_router(chat_router)      # /chat/*
app.include_router(sessions_router)  # /sessions/*
app.include_router(workspace_router)  # /workspace/*
```

### 2.2 配置中心：`config/settings.py`

**阅读顺序**：

1. **第1-11行**：环境变量处理（清除 ANTHROPIC_AUTH_TOKEN）
2. **第16-118行**：Settings 类定义

**核心配置项分组**：

| 分组 | 配置项 | 说明 |
|------|--------|------|
| **LLM** | `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_BASE_URL`, `MODEL_ID` | 支持Anthropic/GLM/DeepSeek/OpenAI/MiMo |
| **数据库** | `MYSQL_*`, `REDIS_*`, `CHROMA_*` | MySQL认证、Redis短期、Chroma长期 |
| **认证** | `JWT_SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT配置 |
| **Agent行为** | `TOKEN_THRESHOLD`, `MAX_AGENT_ROUNDS`, `COMMAND_TIMEOUT_SECONDS` | 控制Agent执行边界 |
| **安全** | `ENABLE_TOOL_CONFIRMATION`, `SENSITIVE_TOOLS_LIST` | Human-in-the-loop确认 |

3. **第120-128行**：`validate_security()` —— 启动时校验JWT密钥

---

## 第三阶段：理解 Agent 工作流（4小时）—— **核心核心核心**

### 3.1 状态定义：`core/agent/state.py`

这是 Agent 的"大脑状态"，只有 **42 行**，必须完全理解。

```python
class AgentState(TypedDict):
    # === 消息历史 ===
    messages: Annotated[List[Dict], add_messages]  # LangGraph自动合并
    
    # === 会话信息 ===
    session_id: str      # 会话ID（用于Redis持久化）
    user_id: int         # 用户ID（用于权限过滤）
    
    # === 任务追踪 ===
    current_task: Optional[Dict]  # 当前任务
    todos: List[Dict]             # TODO列表
    
    # === 上下文管理 ===
    context_summary: Optional[str]  # 压缩后的摘要
    token_count: int               # Token计数（触发压缩）
    transcript_path: Optional[str]  # 压缩后保存的transcript路径
    
    # === 工具执行 ===
    pending_tool_calls: List[Dict]  # 待执行的工具调用
    tool_results: Dict              # 工具执行结果
    tool_call_stats: Dict           # 工具调用统计（防LLM幻觉）
    
    # === 工作流控制 ===
    round_count: int               # LLM调用轮次（防无限循环）
    should_compress: bool          # 是否需要压缩
    should_end: bool               # 是否结束
    should_end_after_save: bool    # 保存后是否结束
    
    # === TodoWrite提醒机制 ===
    rounds_without_todo: int       # 连续未用todo_update的轮数
    used_todo_last_round: bool     # 上轮是否用了todo_update
    has_open_todos: bool           # 是否有未完成的todo
```

**理解要点**：
- `messages` 用 `Annotated[..., add_messages]` 让 LangGraph 自动合并消息
- `session_id` + `user_id` 实现多用户隔离
- `should_end_after_save` 是文本响应完成后结束的信号

### 3.2 工作流图：`core/agent/graph.py`

这是 Agent 的"神经系统"，定义节点如何连接。

**阅读顺序**：

1. **第47-56行**：Redis checkpointer 配置

```python
_checkpointer_pool = redis.asyncio.ConnectionPool(
    host=settings.REDIS_HOST,
    ...
    decode_responses=False,  # 二进制协议，必须False
)
```

**理解要点**：RedisSaver 是 LangGraph 的状态持久化组件，会自动保存/恢复 AgentState。

2. **第59-150行**：`build_agent_graph()` —— **工作流定义**

```
完整工作流图：

init_context → check_background → check_inbox → pre_microcompact → llm_call
                                                                              |
                 +------------------------------------------------------------+
                 |                    |                                       |
            tool_confirm         compress_context                            END
                 |
            tool_executor
                 |
            save_memory
                 |
            route_after_tool
                 |
       +---------+---------+
       |                   |
  compress_context    pre_microcompact
                       |
                   llm_call
```

**节点功能速览**：

| 节点 | 功能 | 触发时机 |
|------|------|----------|
| `init_context` | 初始化状态，注入长期记忆 | 每次请求开始 |
| `check_background` | 注入后台任务通知 | 每次请求开始 |
| `check_inbox` | 注入队友消息 | 每次请求开始 |
| `pre_microcompact` | 清理旧工具结果 | **每次LLM调用前** |
| `llm_call` | 调用LLM，获取响应/工具调用 | 核心节点 |
| `tool_confirm` | Human-in-the-loop确认 | **敏感工具执行前** |
| `tool_executor` | 执行工具调用 | 工具调用时 |
| `save_memory` | 保存到Chroma，TodoWrite提醒 | 工具执行后 |
| `compress_context` | 上下文压缩 | Token超阈值时 |

3. **条件路由函数**（第948-1010行）：

```python
def route_after_llm(state: AgentState) -> str:
    # 优先级：
    # 1. 轮次超限 → save_memory（将结束）
    # 2. 有工具调用 → tool_confirm
    # 3. 手动压缩请求 → manual_compress
    # 4. Token超阈值 → compress_context
    # 5. 无工具无压缩 → save_memory → END

def route_after_tool(state: AgentState) -> str:
    # 优先级：
    # 1. should_end_after_save=True → END（文本响应完成）
    # 2. 轮次超限 → END
    # 3. 手动压缩请求 → manual_compress
    # 4. Token超阈值 → compress_context
    # 5. 继续LLM调用 → pre_microcompact
```

### 3.3 节点实现：`core/agent/nodes.py`

这是 Agent 的"肌肉"，每个节点的具体实现。**这个文件最长（1176行）**。

**按执行顺序阅读**：

#### 1. `init_context_node`（第283-406行）

```python
async def init_context_node(state: AgentState) -> Dict:
    # 1. 重置瞬态状态（token_count=0, pending_tool_calls=[]）
    # 2. 新会话：注入Chroma长期记忆
    #    - 搜索用户patterns（偏好、习惯）
    #    - 搜索相关历史对话
    #    - 更新access_count（追踪使用频率）
```

**理解要点**：Chroma长期记忆只在新会话第一条消息时注入，避免重复检索。

#### 2. `pre_llm_microcompact_node`（第409-421行）

```python
async def pre_llm_microcompact_node(state: AgentState) -> Dict:
    # Microcompact：每次LLM调用前清理旧工具结果
    # 保留最近3个消息，防止token膨胀
    compacted = ctx_mgr.microcompact_langchain(messages, keep_last=3)
    return {"messages": compacted}
```

**理解要点**：这是防止上下文爆炸的关键机制。

#### 3. `llm_call_node`（第424-518行）—— **最核心的节点**

```python
async def llm_call_node(state: AgentState) -> Dict:
    # 1. 清除中间的系统消息（Anthropic API要求）
    # 2. 转换消息格式为LangChain格式
    # 3. 插入系统提示（MAIN_SYSTEM_PROMPT）
    # 4. 调用LLM（带工具绑定）
    # 5. 提取工具调用（如果有）
    # 6. 更新token_count
    # 7. 设置should_end_after_save（无工具调用时=True）
```

**关键代码**：

```python
# 系统提示（第72-129行）
MAIN_SYSTEM_PROMPT = """You are an enterprise-grade AI assistant...

## Before You Act — Decision Framework:
1. PARALLELISM: Independent sub-tasks? -> spawn_teammate()
2. SKILLS: Domain knowledge needed? -> load_skill()
3. ISOLATED EXPLORATION: Search codebase? -> task(agent_type="Explore")
4. LONG-RUNNING: Commands > few seconds? -> background_run()
...
"""

# LLM调用（第459-473行）
response = await get_llm_with_tools().ainvoke(lc_messages)
```

#### 4. `tool_confirm_node`（第1060-1176行）—— **Human-in-the-loop**

```python
async def tool_confirm_node(state: AgentState) -> Dict:
    # 1. 分离敏感工具和非敏感工具
    # 2. 非敏感工具直接通过
    # 3. 敏感工具调用 interrupt() 暂停执行
    # 4. 等待用户确认（前端发送 resume）
    # 5. 返回批准的工具列表
```

**理解要点**：
- `interrupt()` 是 LangGraph 的暂停机制
- 前端收到 interrupt 事件后弹出确认对话框
- 用户确认后调用 `/stream/resume` 继续执行

#### 5. `tool_executor_node`（第546-663行）

```python
async def tool_executor_node(state: AgentState) -> Dict:
    # 1. 遍历pending_tool_calls
    # 2. 执行每个工具（支持重试）
    # 3. 收集工具结果
    # 4. 追踪todo_update使用（用于nag reminder）
    # 5. 返回tool_result_messages
```

**工具重试机制**（第521-543行）：
- 只重试幂等工具（read_file, list_skills 等）
- 不重试有副作用工具（write_file, bash 等）

#### 6. `save_memory_node`（第666-851行）

```python
async def save_memory_node(state: AgentState) -> Dict:
    # === TodoWrite nag reminder机制 ===
    # 1. 更新 rounds_without_todo 计数
    # 2. 如果连续3轮没用todo_update且有未完成todo → 添加提醒
    
    # === Chroma长期记忆存储 ===
    # 1. 找到最后一条user消息和assistant响应
    # 2. 评估重要性（LLM评估 + 启发式规则）
    # 3. 高重要性 → 存储到Chroma
    # 4. 超高重要性 → 提取用户pattern
```

#### 7. `compress_context_node`（第854-907行）

```python
async def compress_context_node(state: AgentState) -> Dict:
    # 1. 检查token_count是否超阈值
    # 2. 调用 ctx_mgr.auto_compact(messages, session_id)
    #    - 保存transcript到文件
    #    - LLM生成摘要
    #    - 替换messages为摘要
    # 3. 存储摘要到Chroma作为长期记忆
```

---

## 第四阶段：工具系统（2小时）

### 4.1 工具注册：`core/agent/tools/__init__.py`

**工具分类**：

```python
ALL_TOOLS = [
    # === 文件操作 ===
    read_file, write_file, edit_file,
    
    # === Shell ===
    bash,
    
    # === 任务管理（双层）===
    todo_update,        # 短期清单（内存，最多20项）
    task_create/get/update/list/claim,  # 持久任务（.tasks/*.json）
    
    # === 子代理 ===
    subagent_task,      # 委托任务给子代理
    
    # === 后台任务 ===
    background_run,     # 异步执行命令
    check_background,   # 检查后台任务状态
    
    # === 技能 ===
    load_skill, list_skills, reload_skills,
    
    # === 团队协作 ===
    spawn_teammate, list_teammates, send_message, read_inbox,
    broadcast, shutdown_request, plan_approval, idle,
    
    # === 上下文管理 ===
    compress, list_transcripts, get_transcript, context_status,
]
```

### 4.2 敏感工具定义

```python
SENSITIVE_TOOLS = {
    "bash",           # Shell命令
    "write_file",     # 写文件
    "edit_file",      # 编辑文件
    "task_create",    # 创建后台任务
    "spawn_teammate", # 创建队友
    "send_message",   # 发送消息
    "broadcast",      # 广播消息
}

SAFE_TOOLS = {
    "read_file", "list_skills", "list_teammates", ...
}
```

### 4.3 重点工具阅读

按重要性顺序：

1. **`file_ops.py`** - 文件读写（Edit/Write后自动验证）
2. **`shell.py`** - Shell命令执行（白名单安全校验）
3. **`task.py`** - 双层任务系统（TodoManager + TaskManager）
4. **`team.py`** - 多Agent协作（最复杂，700+行）
5. **`subagent.py`** - 子代理委托
6. **`background.py`** - 后台任务管理

---

## 第五阶段：记忆系统（2小时）

### 5.1 分层记忆架构

```
┌────────────────────────────────────────────────────────────┐
│                     Memory Architecture                     │
│                                                            │
│  ┌─────────────────────┐     ┌─────────────────────────┐  │
│  │   Short Term        │     │      Long Term          │  │
│  │   (Redis)           │     │      (Chroma)           │  │
│  │                     │     │                         │  │
│  │   RedisSaver        │     │   向量语义存储          │  │
│  │   (LangGraph)       │     │                         │  │
│  │                     │     │   Collections:          │  │
│  │   自动保存/恢复     │     │   - conversations       │  │
│  │   AgentState        │     │   - user_patterns       │  │
│  │                     │     │                         │  │
│  │   无需手动管理      │     │   Embedding:            │  │
│  │                     │     │   all-MiniLM-L6-v2      │  │
│  └─────────────────────┘     └─────────────────────────┘  │
│                                                            │
│  Flow:                                                     │
│  Request → RedisSaver自动恢复状态 → Process → 自动保存    │
│         → Chroma选择性存储（高重要性）                     │
└────────────────────────────────────────────────────────────┘
```

### 5.2 Chroma长期记忆：`memory/long_term.py`

**核心方法**：

```python
class ChromaLongTermMemory:
    # === 存储方法 ===
    async store_conversation(session_id, role, content, metadata)
    async store_pattern(pattern_type, pattern_key, pattern_value, confidence)
    
    # === 搜索方法 ===
    async search_conversations(query, n_results=10)  # 语义搜索
    async search_patterns(query, pattern_type=None)  # 搜索用户模式
    
    # === 清理方法 ===
    async cleanup_low_retention(threshold=0.1)  # 衰减清理
```

### 5.3 重要性评估：`memory/importance.py`

```python
class ImportanceEvaluator:
    async evaluate(content, role, context, enable_llm=True) -> float:
        # 1. LLM评估（如果启用）
        # 2. 启发式规则：
        #    - 工具调用 > 纯文本
        #    - 用户主动询问 > 被动回答
        #    - 代码相关 > 一般对话
        #    - 错误/问题解决 > 成功确认
```

### 5.4 记忆衰减：`memory/decay.py`

```python
class MemoryDecayCalculator:
    def calculate_retention_score(importance, timestamp, access_count, last_access):
        # 衰减公式：
        # retention = importance × exp(-λ × days_elapsed)
        #            × (1 + log(1 + access_count))
        
        # λ = 0.1 时，约7天衰减50%
```

---

## 第六阶段：API层（2小时）

### 6.1 对话路由：`api/routes/chat.py`

**核心端点**：

#### 1. `/chat/completions`（非流式）

```python
@router.post("/completions")
async def chat_completion(request: ChatRequest, user_id: int = Depends(...)):
    # 1. 设置用户上下文（工作区隔离）
    set_current_user_id(user_id)
    
    # 2. 执行Agent工作流
    result = await get_agent_graph().ainvoke(
        {"session_id": ..., "user_id": ..., "messages": [...]},
        config={"configurable": {"thread_id": session_id}}
    )
    
    # 3. 返回响应
```

#### 2. `/chat/stream`（SSE流式）

```python
@router.post("/stream")
async def chat_stream(request: ChatRequest, user_id: int = Depends(...)):
    async def generate():
        async for update in graph.astream(..., stream_mode="updates"):
            # === 处理 interrupt（Human-in-the-loop）===
            if "__interrupt__" in update:
                yield '{"event": "interrupt", "data": ...}'
                return  # 等待前端确认
            
            # === 处理 LLM 输出 ===
            if "llm_call" in update:
                yield '{"delta": ...}'
            
            # === 处理工具执行 ===
            if "tool_executor" in update:
                yield '{"event": "tool_start", "name": ...}'
                yield '{"event": "tool_result", ...}'
        
        yield "[DONE]"
```

#### 3. `/stream/resume`（继续执行）

```python
@router.post("/stream/resume")
async def chat_stream_resume(session_id, approved, body):
    # 用户确认后继续执行
    async for update in graph.astream(
        Command(resume={"approved": approved, "approved_ids": ...}),
        ...
    ):
        # 同上
```

### 6.2 认证流程：`api/routes/auth.py` + `auth/jwt_handler.py`

```
注册流程：
POST /auth/register → MySQL INSERT User → 返回 user_id

登录流程：
POST /auth/login → Verify Password → JWT Handler.create_tokens()
                → 返回 access_token + refresh_token

API请求：
Authorization: Bearer <token> → Middleware验证 → get_current_user()
```

---

## 第七阶段：前端（1小时）

### 7.1 入口：`frontend/src/App.vue`

```vue
<template>
  <LoginForm v-if="!auth.loggedIn" />  <!-- 未登录：显示登录表单 -->
  
  <div v-else class="app-layout">
    <Sidebar ... />     <!-- 左侧：会话列表 -->
    
    <div class="main-area">
      <ChatPanel ... />     <!-- 聊天面板 -->
      <FileManager ... />   <!-- 文件管理器 -->
    </div>
  </div>
</template>
```

### 7.2 API客户端：`frontend/src/api/client.js`

```javascript
// SSE流式请求
export async function streamChat(sessionId, content, callbacks) {
  const response = await fetch('/chat/stream', {
    method: 'POST',
    body: JSON.stringify({ session_id, content }),
  })
  
  const reader = response.body.getReader()
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    
    // 解析 SSE 事件
    const event = JSON.parse(line.slice(6))
    if (event.delta) callbacks.onDelta(event.delta)
    if (event.event === 'interrupt') callbacks.onInterrupt(event.data)
    if (event.event === 'tool_start') callbacks.onToolStart(event.name)
  }
}

// 继续执行（用户确认后）
export async function resumeStream(sessionId, approved, approvedIds) {
  await fetch(`/stream/resume?session_id=${sessionId}&approved=${approved}`, ...)
}
```

### 7.3 聊天面板：`frontend/src/components/ChatPanel.vue`

核心功能：
- 消息列表渲染（支持Markdown）
- SSE流式接收（实时显示）
- 工具确认对话框（Human-in-the-loop）

---

## 第八阶段：LLM多Provider支持（1小时）

### 8.1 工厂模式：`core/agent/llm_factory.py`

```python
def get_llm() -> BaseChatModel:
    provider = settings.LLM_PROVIDER
    
    providers = {
        "anthropic": _get_anthropic_llm,   # ChatAnthropic
        "glm": _get_openai_compatible_llm, # ChatOpenAI + Zhipu URL
        "deepseek": _get_deepseek_llm,     # 支持Anthropic/OpenAI两种endpoint
        "openai": _get_openai_compatible_llm,
        "mimo": _get_mimo_llm,             # Anthropic兼容endpoint
    }
    
    return providers[provider]()
```

### 8.2 Provider信息

```python
PROVIDER_INFO = {
    "anthropic": {"models": ["claude-opus-4-6", "claude-sonnet-4-6", ...]},
    "glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4"},
    "deepseek": {"base_url": "https://api.deepseek.com", "anthropic_base_url": "..."},
    "mimo": {"base_url": "https://api.xiaomimimo.com/anthropic"},
}
```

---

## 第九阶段：测试理解（1小时）

### 9.1 测试文件结构

```
tests/
├── conftest.py          # pytest fixtures
├── core/
│   ├── test_nodes.py    # 节点测试
│   ├── test_state.py    # 状态测试
│   └── tools/
│       ├── test_shell.py
│       ├── test_file_ops.py
│       ├── test_background.py
│       └── test_team.py
│       └── ...
└── memory/
    └── test_memory.py   # 记忆系统测试
```

### 9.2 运行测试

```bash
# 运行所有测试
uv run pytest

# 运行指定模块
uv run pytest tests/core/test_nodes.py

# 带覆盖率
uv run pytest --cov=enterprise_agent
```

---

## 第十阶段：简历亮点总结

### 10.1 技术亮点

| 亮点 | 代码位置 | 说明 |
|------|----------|------|
| **LangGraph有状态工作流** | `graph.py` + `nodes.py` | StateGraph替代手动循环，RedisSaver持久化 |
| **Human-in-the-loop** | `nodes.py:tool_confirm_node` | interrupt()暂停，用户确认后resume |
| **多LLM Provider** | `llm_factory.py` | 工厂模式，5个Provider无缝切换 |
| **分层记忆** | `memory/long_term.py` + `RedisSaver` | Redis短期 + Chroma向量长期 |
| **记忆衰减清理** | `memory/decay.py` | 指数衰减 + 访问频率加权 |
| **重要性评估** | `memory/importance.py` | LLM评估 + 启发式规则 |
| **用户模式提取** | `memory/pattern_extractor.py` | 自动识别用户偏好/习惯 |
| **SSE流式响应** | `api/routes/chat.py` | stream + interrupt处理 |
| **多Agent协作** | `tools/team.py` | spawn_teammate + 消息传递 |
| **TodoWrite nag reminder** | `nodes.py:save_memory_node` | 防止Agent忘记更新任务 |

### 10.2 代码量统计

| 模块 | 文件数 | 行数 | 复杂度 |
|------|--------|------|--------|
| `core/agent/` | 12 | ~2500 | 高 |
| `api/` | 8 | ~800 | 中 |
| `memory/` | 6 | ~400 | 中 |
| `frontend/` | 10 | ~600 | 中 |

---

## 附录：阅读顺序速查表

```
第一天（入门）：
1. README.md（项目概览）
2. config/settings.py（配置理解）
3. api/main.py（入口理解）

第二天（核心）：
4. core/agent/state.py（状态定义）
5. core/agent/graph.py（工作流图）
6. core/agent/llm_factory.py（多Provider）

第三天（深入）：
7. core/agent/nodes.py（节点实现）—— 最重要，最耗时
8. core/agent/tools/__init__.py（工具注册）

第四天（支撑）：
9. memory/long_term.py（长期记忆）
10. memory/importance.py（重要性评估）
11. memory/decay.py（记忆衰减）

第五天（应用）：
12. api/routes/chat.py（对话API）
13. frontend/src/App.vue（前端入口）

第六天（测试）：
14. tests/（测试理解）
15. examples/mini_claude_code.py（对照原始实现）
```

---

## 快速启动命令

```bash
# 1. 启动数据库
cd docker && docker compose up -d mysql redis && cd ..

# 2. 安装依赖
uv sync

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY 和 JWT_SECRET_KEY

# 4. 启动后端
uv run serve

# 5. 启动前端
cd frontend && npm install && npm run dev

# 6. 访问
# 前端: http://localhost:3000
# API文档: http://localhost:8000/docs
```

---

祝阅读愉快！有问题随时问。
