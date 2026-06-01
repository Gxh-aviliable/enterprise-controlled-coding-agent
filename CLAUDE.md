# Mini Claude Code — Enterprise Agent System

基于 LangGraph + FastAPI + Vue 3 构建的多用户 AI Agent 系统。

## Git 分支策略

```
main        ← 稳定版本（仅从 develop 合并，打 tag 发布）
develop     ← 日常开发汇总（默认工作分支）
feature/*   ← 单个功能分支（从 develop 分出，完成后合并回 develop）
hotfix/*    ← 紧急修复（从 main 分出，修复后合并回 main + develop）
```

### 日常开发流程（每次修改后必做）

```bash
# 0. 确保在 develop 分支
git checkout develop

# 1. 查看变更
git status

# 2. 暂存所有修改
git add -A

# 3. 提交
git commit -m "type: summary

Co-Authored-By: Claude <noreply@anthropic.com>"

# 4. 推送到远程 develop
git push origin develop
```

### 大功能开发流程（涉及多个文件/模块时）

```bash
# 1. 从 develop 创建 feature 分支
git checkout develop
git pull origin develop
git checkout -b feature/my-feature-name

# 2. 开发 + 多次提交
git add -A && git commit -m "feat: part 1"
git add -A && git commit -m "feat: part 2"

# 3. 推送 feature 分支
git push origin feature/my-feature-name

# 4. 合并回 develop（功能完成后）
git checkout develop
git pull origin develop
git merge feature/my-feature-name
git push origin develop

# 5. 删除 feature 分支
git branch -d feature/my-feature-name
git push origin --delete feature/my-feature-name
```

### 发布流程（版本稳定后合并到 main）

```bash
git checkout main
git pull origin main
git merge develop
git tag -a v0.3.0 -m "v0.3.0: description"
git push origin main --tags
```

### Commit 类型规范

| 前缀 | 用途 | 示例 |
|------|------|------|
| `fix:` | Bug 修复 | `fix: FileViewer scroll broken` |
| `feat:` | 新功能 | `feat: add syntax highlighting` |
| `refactor:` | 重构 | `refactor: SkillLoader multi-source` |
| `docs:` | 文档 | `docs: update CLAUDE.md` |
| `style:` | UI/CSS | `style: dark mode toggle` |
| `chore:` | 构建/依赖 | `chore: install highlight.js` |

**规则：每次修改后必须 commit + push 到 develop，不要积累大量未提交变更。**

## 启动命令

```bash
# 工作目录
cd my_mini_claude_code

# 后端 (端口 8000)
uv run uvicorn enterprise_agent.api.main:app --reload

# 前端 (端口 3000, 新终端)
cd frontend && npm run dev
```

## 项目架构

```
my_mini_claude_code/
├── enterprise_agent/          # Python 后端包
│   ├── api/                   # FastAPI 路由 + SSE 流式
│   │   ├── main.py            # app 入口、lifespan、CORS、health
│   │   ├── routes/
│   │   │   ├── auth.py        # /auth/* (register, login, refresh)
│   │   │   ├── chat.py        # /chat/* (流式/非流式) + /sessions/* (CRUD + 消息历史)
│   │   │   └── workspace.py   # /workspace/* (文件树、读写、上传)
│   │   ├── middleware/
│   │   │   └── auth.py        # JWT Bearer 认证 + get_current_user 依赖
│   │   └── schemas/           # Pydantic 请求/响应模型
│   ├── core/agent/            # LangGraph Agent 核心
│   │   ├── graph.py           # StateGraph 构建 + AsyncRedisSaver checkpointer
│   │   ├── state.py           # AgentState TypedDict 定义
│   │   ├── nodes.py           # 所有图节点 (llm_call, tool_executor, compress, etc.)
│   │   ├── context.py         # 上下文压缩 (TranscriptManager, TokenEstimator)
│   │   ├── llm_factory.py     # LLM 工厂 (DeepSeek/Anthropic/GLM/OpenAI)
│   │   └── tools/             # Agent 工具集
│   │       ├── __init__.py    # ALL_TOOLS 注册 + 权限过滤
│   │       ├── shell.py       # bash 命令执行 (安全黑名单)
│   │       ├── file_ops.py    # 文件读写编辑
│   │       ├── workspace.py   # workspace 路径解析 + ContextVar 隔离
│   │       ├── skills.py      # SkillLoader 多租户 Skill 系统
│   │       ├── team.py        # 子 Agent spawn
│   │       ├── subagent.py    # task() 通用子 Agent
│   │       ├── background.py  # 后台任务管理
│   │       └── todo.py        # Todo 管理
│   ├── memory/                # 三层记忆
│   │   ├── short_term.py      # Redis 短期记忆 (24h TTL)
│   │   ├── long_term.py       # Chroma 向量长期记忆
│   │   ├── accumulator.py     # 记忆累积器 (20轮flush)
│   │   ├── importance.py      # LLM 重要性评分
│   │   └── decay.py           # 记忆衰减清理
│   ├── db/                    # 数据库连接
│   │   ├── mysql.py           # SQLAlchemy async engine + session
│   │   ├── redis.py           # Redis 客户端 (异步)
│   │   └── chroma.py          # Chroma PersistentClient + embedding
│   ├── models/                # SQLAlchemy ORM 模型
│   │   ├── session.py         # Session (id, user_id, title, status)
│   │   └── user.py            # User (id, username, email, password_hash)
│   └── config/
│       └── settings.py        # Pydantic Settings (所有配置项)
├── frontend/                  # Vue 3 + Vite 前端
│   ├── src/
│   │   ├── App.vue            # 根组件 (登录/主布局切换, CSS 变量)
│   │   ├── main.js            # Vue 入口
│   │   ├── api/client.js      # fetch API 客户端 (SSE, token刷新锁)
│   │   ├── stores/auth.js     # 认证状态 (reactive)
│   │   ├── composables/
│   │   │   └── useToast.js    # Toast 通知 composable
│   │   └── components/
│   │       ├── LoginForm.vue   # 登录/注册页
│   │       ├── Sidebar.vue     # 侧边栏 (会话列表 + 文件树 tabs)
│   │       ├── ChatPanel.vue   # 聊天主面板 (SSE流式, Markdown, Tool确认)
│   │       ├── FileTree.vue    # 文件树组件
│   │       ├── TreeNode.vue    # 递归树节点 (展开/折叠/右键菜单)
│   │       ├── FileViewer.vue  # 文件内容查看器 (代码高亮, 下载)
│   │       ├── FileManager.vue # 文件管理器 (已废弃, 保留备用)
│   │       └── Toast.vue       # 通知组件
│   └── vite.config.js         # Vite 配置 (代理 /api → localhost:8000)
├── shared_skills/             # 全局 Skill (所有用户可见)
│   ├── python/SKILL.md
│   ├── langgraph/SKILL.md
│   ├── fastapi/SKILL.md
│   └── agent-interviewer/SKILL.md
├── tests/                     # pytest 测试
└── .claude/skills/            # Claude Code CLI 技能 (非项目 Skill)
```

## 关键架构决策

### State Management
- **AgentState** (`core/agent/state.py`): TypedDict，`messages` 用 `add_messages` reducer
- **Checkpointer**: `AsyncRedisSaver` 持久化到 Redis，`thread_id` = `session_id`
- **ContextVar**: `set_current_user_id()` 在工具执行时隔离用户

### LLM 调用链路
```
用户消息 → ChatPanel.send() → api.streamMessage() → SSE fetch
  → POST /chat/stream → chat_stream() → graph.astream()
  → llm_call_node → SystemMessage (MAIN_SYSTEM_PROMPT + skills)
  → get_llm_with_tools().ainvoke() → 返回 text 或 tool_calls
  → tool_executor_node → 执行工具 → 结果追加 messages → 回 LLM
  → 压缩 (token 超阈值) → compress_context_node
```

### 消息历史
- `GET /sessions/{id}/messages` → `graph.aget_state()` → 从 Redis 加载
- 过滤 `tool`/`system` 角色，跳过空内容消息
- TTL 24h (`CHECKPOINT_TTL_HOURS`)

### Skill 多租户
- 全局: `shared_skills/` (所有用户)，用户: `user_{id}/.skills/` (个人)
- 加载优先级: user > global (同名覆盖)
- System prompt 自动注入可用列表 (`{available_skills}`)

### 前端状态
- **无路由**: 纯 `v-if`/`v-show` 切换 (LoginForm ↔ App Layout)
- **认证**: localStorage `access_token` + `refresh_token`
- **Token 刷新**: `_tryRefreshToken()` 原子锁防并发 401 风暴
- **SSE 中止**: `AbortController` 在会话切换时 abort

### 安全措施
- Shell: 黑名单 + 破坏性操作正则 (不在沙箱中运行)
- XSS: `marked` + `DOMPurify` 白名单净化
- Tool Confirmation: `interrupt()` → 前端确认弹窗 → `Command(resume=...)`

## 环境变量 (.env)

```env
# LLM
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-v4-pro

# 数据库
MYSQL_USER=agent_user
MYSQL_PASSWORD=
REDIS_HOST=localhost

# Auth
JWT_SECRET_KEY=your-secret-key

# 可选
DEBUG=true
CORS_ORIGINS=http://localhost:3000
WORKSPACE_BASE=/workspaces
```

## 常用命令

```bash
# 后端测试
uv run pytest tests/ -v

# 仅 Skill 测试
uv run pytest tests/core/tools/test_skills.py -v

# 前端构建检查
cd frontend && npm run build

# 导入检查 (不启动服务)
uv run python -c "from enterprise_agent.api.main import app; print('OK')"

# 安装前端依赖
cd frontend && npm install

# 查看 git 日志
git log --oneline -10
```

## 当前版本: v0.2.0

上次提交: `61a0d10` — frontend redesign, security fixes, skill multi-tenancy
