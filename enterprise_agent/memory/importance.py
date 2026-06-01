"""Importance evaluation for memory storage.

Implements a dual-layer evaluation mechanism:
1. Rule-based quick evaluation (low cost, fast)
2. LLM-based deep evaluation (higher cost, more accurate)

Only borderline cases (rule score 0.3-0.6) trigger LLM evaluation.
"""

import json
import re
from typing import Dict, List

from enterprise_agent.config.settings import settings


class RuleEvaluator:
    """Rule-based importance evaluator.

    Uses heuristics like length, keywords, code detection, etc.
    Fast and cheap - suitable for all messages.
    """

    # Keywords that indicate important content
    IMPORTANT_KEYWORDS = [
        "重要", "偏好", "喜欢", "习惯", "设置", "配置", "问题", "错误",
        "解决", "优化", "改进", "建议", "要求", "规则", "决策",
        "prefer", "like", "config", "setting", "important", "issue",
        "error", "solve", "optimize", "improve", "suggest", "require",
    ]

    # Technical terms that indicate substantive content
    TECHNICAL_TERMS = [
        "API", "SDK", "数据库", "database", "算法", "algorithm",
        "架构", "architecture", "测试", "test", "部署", "deploy",
        "函数", "function", "类", "class", "接口", "interface",
        "git", "python", "typescript", "javascript", "react", "vue",
    ]

    def evaluate_importance(self, content: str, role: str) -> float:
        """Evaluate importance based on rules.

        Args:
            content: Message content
            role: Message role (user/assistant/system)

        Returns:
            Importance score (0-1)
        """
        score = 0.0

        # Length heuristic
        if len(content) > 200:
            score += 0.2
        elif len(content) > 100:
            score += 0.1
        elif len(content) < 20:
            score -= 0.3

        # Keyword matching (important topics)
        for kw in self.IMPORTANT_KEYWORDS:
            if kw.lower() in content.lower():
                score += 0.15
                break  # Only count once to avoid over-weighting

        # Code/technical content detection
        if self._has_code_blocks(content):
            score += 0.25
        if self._has_technical_terms(content):
            score += 0.1

        # Decision/preference expression detection
        if self._expresses_preference(content):
            score += 0.3

        # Role-based weighting (user messages often more important)
        if role == "user" and score > 0:
            score += 0.1

        # Ensure score is in valid range
        return max(0.0, min(1.0, score))

    def _has_code_blocks(self, content: str) -> bool:
        """Detect if content contains code blocks."""
        # Check for common code patterns
        patterns = [
            r"```[\s\S]*?```",  # Markdown code blocks
            r"`[^`]+`",  # Inline code
            r"(def |class |function |import |from )",  # Python/JS keywords
            r"(SELECT|INSERT|UPDATE|DELETE|CREATE)",  # SQL
            r"(\$|npm|pip|git )",  # Commands
        ]
        return any(re.search(p, content, re.IGNORECASE) for p in patterns)

    def _has_technical_terms(self, content: str) -> bool:
        """Detect if content contains technical terminology."""
        return any(term.lower() in content.lower() for term in self.TECHNICAL_TERMS)

    def _expresses_preference(self, content: str) -> bool:
        """Detect if content expresses a preference or decision."""
        patterns = [
            r"我(喜欢|偏好|习惯|想要|希望)",
            r"(prefer|like to|want to|would rather)",
            r"建议.*使用",
            r"(最好|应该|必须|需要)",
            r"不用.*代替",
            r"(don't use|use.*instead)",
        ]
        return any(re.search(p, content, re.IGNORECASE) for p in patterns)


class LLMEvaluator:
    """LLM-based importance evaluator.

    More accurate but slower. Only called for borderline cases
    where rule-based evaluation is uncertain (score 0.3-0.6).
    """

    async def evaluate_importance(self, content: str, context: str = "") -> float:
        """Use LLM to evaluate content importance.

        Args:
            content: Message content
            context: Recent conversation context (optional)

        Returns:
            Importance score (0-1)
        """
        # Import LLM client
        from enterprise_agent.core.agent.llm_factory import get_llm
        from langchain_core.messages import HumanMessage

        prompt = f"""
Evaluate the importance of this conversation content for future reference.

Content: {content}

Recent context (if available): {context[:500] if context else "None"}

Score the importance (0-1) based on:
1. **Information value**: Does it contain useful technical information, decisions, or solutions?
2. **Personalization**: Does it reveal user preferences, workflow habits, or personal settings?
3. **Reusability**: Would this be helpful in similar future tasks or conversations?

Guidelines:
- Score 0.0-0.3: Low importance (greetings, simple acknowledgments, casual chat)
- Score 0.3-0.6: Medium importance (some useful info but not critical)
- Score 0.6-1.0: High importance (clear preferences, technical decisions, problem solutions)

Return ONLY a JSON object with this exact format:
{{"importance": <score>, "reason": "<brief explanation>"}}
"""

        try:
            llm = get_llm()

            # Use cheaper model for evaluation (if configured)
            model = getattr(settings, "IMPORTANCE_EVAL_MODEL", settings.MODEL_ID)
            # Note: LangChain uses the model from settings by default

            # Suppress callbacks so LangGraph stream_mode doesn't capture
            response = await llm.with_config({"callbacks": []}).ainvoke(
                [HumanMessage(content=prompt)]
            )
            text = response.content

            # Parse JSON response
            # Handle potential markdown wrapper
            if "```json" in text:
                text = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL).group(1)

            result = json.loads(text.strip())
            importance = result.get("importance", 0.5)

            # Validate range
            return max(0.0, min(1.0, importance))

        except Exception as e:
            # Fallback to medium score on error
            return 0.5


class HybridImportanceEvaluator:
    """Hybrid importance evaluator combining rule-based and LLM-based methods.

    Strategy:
    1. Quick rule-based evaluation first
    2. LLM evaluation only for borderline cases (0.3-0.6)
    3. Weighted average for borderline scores
    """

    def __init__(self):
        self.rule_evaluator = RuleEvaluator()
        self.llm_evaluator = LLMEvaluator()

        # Thresholds from settings (or defaults)
        self.threshold_low = getattr(settings, "IMPORTANCE_THRESHOLD_STORE", 0.3)
        self.threshold_high = getattr(settings, "IMPORTANCE_THRESHOLD_PATTERN", 0.6)

    async def evaluate(
        self,
        content: str,
        role: str,
        context: List[Dict] = None,
        enable_llm: bool = True
    ) -> float:
        """Evaluate importance using hybrid approach.

        Args:
            content: Message content
            role: Message role (user/assistant/system)
            context: Recent conversation context (optional)
            enable_llm: Whether to use LLM for borderline cases (default True)

        Returns:
            Importance score (0-1)
        """
        # Step 1: Rule-based quick evaluation
        rule_score = self.rule_evaluator.evaluate_importance(content, role)

        # Step 2: LLM evaluation only for borderline cases
        if enable_llm and self.threshold_low <= rule_score <= self.threshold_high:
            # Format context for LLM
            context_str = ""
            if context:
                context_parts = []
                for msg in context[-5:]:  # Last 5 messages
                    # Handle LangChain message objects (HumanMessage, AIMessage, ToolMessage)
                    if hasattr(msg, 'type'):
                        # LangChain messages use .type attribute ("human", "ai", "tool")
                        role_ctx = msg.type
                        content_ctx = msg.content if hasattr(msg, 'content') else str(msg)
                    elif isinstance(msg, dict):
                        role_ctx = msg.get("role", "unknown")
                        content_ctx = msg.get("content", "")
                    else:
                        role_ctx = "unknown"
                        content_ctx = str(msg) if msg else ""
                    if isinstance(content_ctx, str):
                        context_parts.append(f"[{role_ctx}]: {content_ctx[:100]}")
                context_str = "\n".join(context_parts)

            llm_score = await self.llm_evaluator.evaluate_importance(content, context_str)

            # Weighted average (give slightly more weight to LLM for borderline)
            return (rule_score * 0.4 + llm_score * 0.6)

        return rule_score


# Singleton instance for reuse
_evaluator_instance: HybridImportanceEvaluator = None


def get_importance_evaluator() -> HybridImportanceEvaluator:
    """Get or create hybrid importance evaluator instance."""
    global _evaluator_instance
    if _evaluator_instance is None:
        _evaluator_instance = HybridImportanceEvaluator()
    return _evaluator_instance