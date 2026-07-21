"""Pattern extraction from conversations.

Automatically identifies user preferences, workflows, and shortcuts
from high-importance conversations using LLM analysis.
"""

import json
import logging
import re
from typing import Dict, List

from enterprise_agent.memory.policy import has_durable_pattern_signal

logger = logging.getLogger(__name__)


class PatternExtractor:
    """Extract user patterns from conversations.

    Pattern types:
    - preference: User likes/dislikes (e.g., "喜欢用 TypeScript")
    - workflow: User habits/methods (e.g., "习惯先写测试")
    - shortcut: User shortcuts/conventions (e.g., "常用 git commit -m")
    """

    async def extract_patterns_from_conversation(
        self,
        user_msg: str,
        assistant_msg: str,
        context: List[Dict] = None,
        min_confidence: float = 0.7
    ) -> List[Dict]:
        """Extract user patterns from conversation using LLM.

        Args:
            user_msg: User message content
            assistant_msg: Assistant response
            context: Recent conversation context (optional)
            min_confidence: Minimum confidence to store pattern (default 0.7)

        Returns:
            List of extracted patterns:
            [
              {
                "type": "preference|workflow|shortcut",
                "key": "<pattern identifier>",
                "value": "<pattern description>",
                "confidence": <0-1>
              }
            ]
        """
        if not has_durable_pattern_signal(user_msg):
            logger.debug("Skipped pattern extraction: no durable user preference signal")
            return []

        from langchain_core.messages import HumanMessage

        from enterprise_agent.core.agent.llm_factory import get_llm

        # Format context
        context_str = ""
        if context:
            context_parts = []
            for msg in context[-5:]:  # Last 5 messages
                role_ctx = msg.get("role", "unknown")
                content_ctx = msg.get("content", "")
                if isinstance(content_ctx, str):
                    context_parts.append(f"[{role_ctx}]: {content_ctx[:100]}")
            context_str = "\n".join(context_parts)

        prompt = f"""
Analyze this conversation and extract user behavior patterns/preferences.

User message: {user_msg}
Assistant response: {assistant_msg[:500] if len(assistant_msg) > 500 else assistant_msg}
Recent context: {context_str if context_str else "None"}

Extract patterns in these categories:
1. **Preference**: What the user likes/dislikes (e.g., "喜欢用 TypeScript", "不喜欢 JavaScript")
2. **Workflow**: User's working habits/methods (e.g., "习惯先写测试再写代码", "先查文档再提问")
3. **Shortcut**: User's commonly used shortcuts/conventions
   (e.g., "常用 git commit -m 'fix: ...'", "习惯用别名 ll 代替 ls -l")

Guidelines:
- Extract patterns only from the USER'S explicit, durable statements.
- A task instruction is not a preference. "Write this story as fantasy",
  "use multi-agent for this task", and other one-off constraints must return [].
- Never infer a preference from the assistant's response, chosen implementation,
  tool usage, or a single task topic.
- Only extract CLEAR patterns that should still apply in a future session.
- Set confidence based on how explicit the pattern is:
  - 0.9-1.0: Very explicit ("我喜欢...", "我习惯...", "我通常...")
  - 0.7-0.9: Moderately explicit ("最好...", "建议...", implied preference)
  - Below 0.7: Do not extract (too uncertain)

Return ONLY a JSON array (or empty array [] if no patterns found):
[
  {{
    "type": "preference|workflow|shortcut",
    "key": "<concise identifier, e.g., 'typescript_preference'>",
    "value": {{<pattern details as object>}},
    "confidence": <0-1>
  }}
]

If no clear patterns found, return: []
"""

        try:
            llm = get_llm()

            response = await llm.with_config(
                {"callbacks": [], "tags": ["memory_internal"]}
            ).ainvoke([HumanMessage(content=prompt)])
            text = response.content
            # Handle potential markdown wrapper
            if "```json" in text:
                text = re.search(r"```json\s*(\[.*?\])\s*```", text, re.DOTALL).group(1)

            patterns_raw = json.loads(text.strip())

            # Filter by confidence threshold
            patterns = []
            for pattern in patterns_raw:
                if pattern.get("confidence", 0) >= min_confidence:
                    patterns.append({
                        "type": pattern.get("type", "preference"),
                        "key": pattern.get("key", "unknown"),
                        "value": pattern.get("value", {}),
                        "confidence": pattern.get("confidence", 0.7),
                    })

            if patterns:
                logger.info(f"Extracted {len(patterns)} patterns from conversation")
            return patterns

        except Exception as e:
            logger.warning(f"Pattern extraction failed: {e}")
            return []


# Singleton instance
_extractor_instance: PatternExtractor = None


def get_pattern_extractor() -> PatternExtractor:
    """Get or create pattern extractor instance."""
    global _extractor_instance
    if _extractor_instance is None:
        _extractor_instance = PatternExtractor()
    return _extractor_instance
