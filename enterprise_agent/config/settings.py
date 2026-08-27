import os
from pathlib import Path
from typing import Optional

# Clear system environment variables that may override .env config
# ANTHROPIC_AUTH_TOKEN is set by Claude Code CLI and overrides .env values
# When using custom LLM providers (DeepSeek, GLM, etc.), this causes authentication issues
if os.getenv("ANTHROPIC_BASE_URL") or os.getenv("LLM_BASE_URL"):
    # User is using a custom LLM endpoint, remove Claude Code's auth token
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Enterprise Agent Configuration Settings"""

    # App
    APP_NAME: str = "Enterprise Agent"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

    # Database - MySQL (for auth/session only)
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "agent_user"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "enterprise_agent"

    # Database - Redis (short-term memory)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None

    # Database - Chroma (long-term vector memory)
    CHROMA_PERSIST_DIR: str = str(Path(__file__).resolve().parent.parent.parent / "chroma_data")

    # Workspace
    WORKSPACE_BASE: str = "/workspaces"

    # Skills — shared global skills directory
    SHARED_SKILLS_DIR: str = str(
        Path(__file__).resolve().parent.parent.parent / "shared_skills"
    )
    MANAGED_SHARED_SKILLS_DIR: str = str(
        Path(__file__).resolve().parent.parent.parent / "managed_shared_skills"
    )
    CHROMA_COLLECTION_CONVERSATIONS: str = "conversations"
    CHROMA_COLLECTION_PATTERNS: str = "user_patterns"

    # Auth
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Password reset email (optional; logs code when SMTP is not configured)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_USE_SSL: bool = True
    PASSWORD_RESET_CODE_TTL_SECONDS: int = 600
    PASSWORD_RESET_CODE_LENGTH: int = 6

    # LLM Provider Configuration
    # Supported: "anthropic" | "glm" | "deepseek" | "openai" | "mimo"
    # DeepSeek supports both OpenAI-compatible (/v1) and Anthropic-compatible (/anthropic) endpoints
    LLM_PROVIDER: str = "deepseek"
    LLM_API_KEY: str = ""  # Universal API key
    LLM_BASE_URL: Optional[str] = "https://api.deepseek.com/anthropic"  # Anthropic-compatible endpoint
    MODEL_ID: str = "deepseek-v4-flash"  # Model identifier
    # Keep enough room for thinking models to reach a tool call or a complete
    # answer. Completion integrity still fails closed when this limit is hit.
    MODEL_MAX_OUTPUT_TOKENS: int = 16_384

    # Legacy Anthropic config (for backward compatibility)
    ANTHROPIC_API_KEY: str = ""

    # Memory
    SHORT_TERM_TTL_HOURS: int = 24
    CHECKPOINT_TTL_HOURS: int = 24  # RedisSaver checkpoint 过期时间（对话历史自动清理）
    MAX_MESSAGES_PER_SESSION: int = 100
    # Legacy fallback for tests/offline callers that omit a model window. Normal
    # runtime compaction is derived directly from MODEL_CONTEXT_WINDOW_TOKENS.
    TOKEN_THRESHOLD: int = 500_000
    # The default model, deepseek-v4-flash, documents a 1M context window. This
    # hard model boundary is distinct from cumulative task/session spend budgets.
    MODEL_CONTEXT_WINDOW_TOKENS: int = 1_000_000
    CONTEXT_COMPRESSION_RATIO: float = 0.8

    # Tool output limits
    TOOL_OUTPUT_MAX_CHARS: int = 50000  # Truncation limit for tool outputs
    # Foreground/background process capture is file-backed and read with this
    # byte cap, preventing unbounded stdout/stderr from entering API memory.
    TOOL_SOURCE_CAPTURE_MAX_BYTES: int = 4_000_000
    # Private artifact captures are larger than model previews but still bounded
    # to prevent one hostile command from exhausting workspace storage.
    TOOL_ARTIFACT_MAX_CHARS: int = 2_000_000
    # Auto-compact: how much recent text (chars) the summarizer LLM sees. This is
    # an input cap for the summarizer, not the main-context compression threshold.
    CONTEXT_SUMMARY_TRIGGER_CHARS: int = 200000
    CONTEXT_SUMMARY_MAX_TOKENS: int = 50_000
    CONTEXT_SUMMARY_OUTPUT_RESERVE_TOKENS: int = 4_096

    # Agent behavior
    MICROCOMPACT_KEEP_LAST: int = 6  # Messages to keep during microcompact
    MICROCOMPACT_MIN_CHARS: int = 1000  # Avoid receipts larger than small outputs
    NAG_REMINDER_THRESHOLD: int = 3  # Rounds without TodoWrite before reminder
    COMMAND_TIMEOUT_SECONDS: int = 120  # Shell/background command timeout
    AGENT_INVOKE_TIMEOUT_SECONDS: int = 600  # Max seconds for a single graph invocation
    # Cross-worker execution ownership. Runners renew this Redis lease while
    # active; checkpoints still provide the durable fallback if a worker dies.
    ACTIVE_TRACE_LEASE_SECONDS: int = 1200
    CANCEL_CONVERGENCE_WAIT_SECONDS: float = 5.0
    MAX_AGENT_ROUNDS: int = 20  # Fail fast instead of allowing long no-progress loops
    MAX_TOOL_CALLS_PER_TASK: int = 25  # Framework-enforced tool-call budget
    # Cumulative usage guards; 0 disables that guard while usage remains tracked.
    # Keep a per-task fuse even when sessions are unlimited: at an 800K working
    # threshold, 4M permits roughly five full-window-equivalent model turns.
    TASK_TOKEN_BUDGET: int = 4_000_000
    SESSION_TOKEN_BUDGET: int = 0
    # MySQL history is injected only when the Redis checkpoint is unavailable.
    # Bound both rows and characters before it reaches the model.
    DURABLE_HISTORY_MAX_CHARS: int = 120_000
    SUBAGENT_MAX_ROUNDS: int = 30  # Max rounds for subagent execution
    TODO_MAX_ITEMS: int = 20  # Max todo items per session
    TODO_MAX_IN_PROGRESS: int = 1  # Max concurrent in_progress todos
    VERIFICATION_MAX_ATTEMPTS: int = 2  # Automatic prompts to verify code changes
    ENABLE_MULTI_AGENT: bool = False  # Single-Agent is the measured/default baseline
    ENABLE_LONG_TERM_MEMORY: bool = True  # Benchmarks can disable Chroma side effects

    # Memory accumulator (task-level storage, replaces per-round fragments)
    MEMORY_ACCUMULATOR_MAX_ROUNDS: int = 20  # Max rounds before forcing flush (safety valve)
    MEMORY_ADMISSION_MIN_IMPORTANCE: float = 0.65  # 自动长期记忆还需通过确定性准入策略
    MEMORY_RELEVANCE_MAX_DISTANCE: float = 0.8  # Chroma L2 距离上限，避免注入弱相关记忆
    MEMORY_CJK_LEXICAL_MIN_SCORE: float = 0.08  # 中文查询最低字符/术语重合率
    MEMORY_CJK_RELATIVE_SCORE: float = 0.75  # 仅保留接近本次最佳中文候选的记录
    MEMORY_DEDUP_MAX_DISTANCE: float = 0.3  # 相同用户请求的近重复判定阈值

    # LangSmith tracing (optional — if API key is set, tracing auto-enables)
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "enterprise-agent"

    # Embedding (for Chroma)
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"  # Local sentence-transformers model
    EMBEDDING_ALLOW_DOWNLOAD: bool = True  # 缓存缺失时允许首次下载；已有缓存始终离线加载

    # Memory Enhancement (Chroma long-term memory)
    IMPORTANCE_THRESHOLD_STORE: float = 0.5  # 低于此值不存储到 Chroma（提高阈值避免存储低价值信息）
    IMPORTANCE_THRESHOLD_PATTERN: float = 0.7  # 高于此值提取用户 pattern（提高阈值确保高质量）
    MEMORY_DECAY_LAMBDA: float = 0.1  # 衰减系数（0.1 = ~7天衰减50%）
    MEMORY_CLEANUP_THRESHOLD: float = 0.1  # 留存分数低于此值则清理
    MEMORY_CLEANUP_INTERVAL_HOURS: int = 1  # 清理任务间隔（小时）
    ENABLE_LLM_IMPORTANCE_EVAL: bool = True  # 是否启用 LLM 重要性评估
    IMPORTANCE_EVAL_MODEL: str = "deepseek-v4-flash"  # 重要性评估使用的模型

    # Output Verification (trust but verify - prevent hallucination)
    ENABLE_EDIT_VERIFICATION: bool = True  # Auto re-read after edit_file
    ENABLE_WRITE_VERIFICATION: bool = True  # Auto re-read after write_file
    VERIFICATION_PREVIEW_LINES: int = 10  # Lines to show in verification preview

    # Human-in-the-loop confirmation (sensitive tool execution)
    # SSE + interrupt integration now supported via astream(stream_mode="updates")
    ENABLE_TOOL_CONFIRMATION: bool = True  # Enable tool confirmation with SSE interrupt support
    SENSITIVE_TOOLS_LIST: list[str] = [
        "bash",
        "write_file",
        "edit_file",
        "task_create",
        "spawn_teammate",
        "send_message",
        "broadcast",
    ]  # Tools requiring confirmation
    CONFIRMATION_TIMEOUT_SECONDS: int = 300  # Timeout for user confirmation (5 minutes)

    # Workspace file opening
    FILE_OPEN_MODE: str = "local-vscode"  # "local-vscode" or "web-vscode"
    VSCODE_WEB_BASE_URL: str = ""
    VSCODE_WEB_URL_TEMPLATE: str = ""
    VSCODE_WORKSPACE_PATH: str = ""

    model_config = {
        "env_file": str(Path(__file__).resolve().parent.parent.parent / ".env"),
        "case_sensitive": True,
        "extra": "ignore"
    }

    def validate_runtime_security(self) -> None:
        """Fail server startup on placeholder production credentials.

        This is deliberately called by the FastAPI lifespan rather than while
        importing settings, so the offline smoke test works before `.env` is
        created while an actual server still fails closed.
        """
        if self.MODEL_CONTEXT_WINDOW_TOKENS <= 0:
            raise RuntimeError(
                "MODEL_CONTEXT_WINDOW_TOKENS must be a positive value matching the "
                "selected model; zero disables the safety boundary."
            )
        if not 0.1 <= self.CONTEXT_COMPRESSION_RATIO <= 0.95:
            raise RuntimeError(
                "CONTEXT_COMPRESSION_RATIO must be between 0.1 and 0.95."
            )
        if self.TASK_TOKEN_BUDGET < 0 or self.SESSION_TOKEN_BUDGET < 0:
            raise RuntimeError(
                "TASK_TOKEN_BUDGET and SESSION_TOKEN_BUDGET must be non-negative; "
                "use zero to disable a cumulative token guard."
            )
        if self.ACTIVE_TRACE_LEASE_SECONDS <= max(
            self.AGENT_INVOKE_TIMEOUT_SECONDS,
            self.CONFIRMATION_TIMEOUT_SECONDS,
        ):
            raise RuntimeError(
                "ACTIVE_TRACE_LEASE_SECONDS must outlive both one Agent invocation "
                "and the tool-confirmation timeout."
            )
        if self.CANCEL_CONVERGENCE_WAIT_SECONDS <= 0:
            raise RuntimeError("CANCEL_CONVERGENCE_WAIT_SECONDS must be positive.")
        if self.DURABLE_HISTORY_MAX_CHARS <= 0:
            raise RuntimeError("DURABLE_HISTORY_MAX_CHARS must be positive.")

        placeholders = {
            "",
            "change-me-in-production",
            "your-secret-key-change-in-production",
            "changeme",
        }
        if not self.DEBUG and (
            self.JWT_SECRET_KEY.lower() in placeholders or len(self.JWT_SECRET_KEY) < 32
        ):
            raise RuntimeError(
                "JWT_SECRET_KEY must be a non-placeholder value of at least 32 characters. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )

    def get_effective_api_key(self) -> str:
        """Get effective API key based on provider or legacy config."""
        if self.LLM_API_KEY:
            return self.LLM_API_KEY
        # Fallback to legacy Anthropic key
        if self.LLM_PROVIDER == "anthropic" and self.ANTHROPIC_API_KEY:
            return self.ANTHROPIC_API_KEY
        return ""

    def get_effective_base_url(self) -> Optional[str]:
        """Get effective base URL based on provider."""
        if self.LLM_BASE_URL:
            return self.LLM_BASE_URL

        # Default URLs for each provider
        defaults = {
            "glm": "https://open.bigmodel.cn/api/paas/v4",
            "deepseek": "https://api.deepseek.com",
            "openai": "https://api.openai.com/v1",
            "mimo": "https://api.xiaomimimo.com/anthropic",
        }
        return defaults.get(self.LLM_PROVIDER)

    def get_effective_model_id(self) -> str:
        """Get effective model ID based on provider."""
        return self.MODEL_ID


settings = Settings()
