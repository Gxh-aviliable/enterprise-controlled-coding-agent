"""Memory decay mechanism for Chroma long-term memory.

Implements retention score calculation based on:
- Importance (initial value)
- Recency (exponential decay over time)
- Access frequency (logarithmic boost for frequently accessed memories)

Provides periodic cleanup task to remove low-retention memories.
"""

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class MemoryDecayCalculator:
    """Calculator for memory retention scores.

    Formula: retention_score = importance × recency_factor × access_factor

    Where:
    - recency_factor = exp(-λ_age × age_days)
    - access_factor = 1 + log(1 + access_count)
    - Optional: last_access bonus for recently accessed memories
    """

    def __init__(self, decay_lambda: float = None):
        """Initialize decay calculator.

        Args:
            decay_lambda: Decay rate (default from settings or 0.1)
        """
        from enterprise_agent.config.settings import settings
        self.decay_lambda = decay_lambda or getattr(settings, "MEMORY_DECAY_LAMBDA", 0.1)
        self.last_access_lambda = 0.05  # Smaller decay for last access

    def calculate_retention_score(
        self,
        importance: float,
        timestamp: str,
        access_count: int,
        last_access: Optional[str] = None
    ) -> float:
        """Calculate retention score using exponential decay.

        Args:
            importance: Initial importance score (0-1)
            timestamp: Creation timestamp (ISO format)
            access_count: Number of times memory was retrieved
            last_access: Last retrieval timestamp (ISO format, optional)

        Returns:
            Retention score (0 to importance)
        """
        # Parse timestamp
        try:
            created_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - created_at).days
        except Exception:
            # Fallback to age 0 if timestamp parsing fails
            age_days = 0

        # Recency decay (λ=0.1 means 50% decay after ~7 days)
        recency_factor = math.exp(-self.decay_lambda * age_days)

        # Access boost (logarithmic growth to avoid unbounded scores)
        access_factor = 1 + math.log(1 + access_count)

        # Last access bonus (recently accessed memories retain more value)
        if last_access:
            try:
                last_access_dt = datetime.fromisoformat(last_access.replace("Z", "+00:00"))
                days_since_access = (datetime.now(timezone.utc) - last_access_dt).days
                # Small decay for access recency
                access_factor *= math.exp(-self.last_access_lambda * days_since_access)
            except Exception:
                pass  # Ignore parsing errors

        return importance * recency_factor * access_factor


async def memory_cleanup_task(
    cleanup_interval_hours: int = None,
    retention_threshold: float = None,
    max_users_per_run: int = 100
):
    """Background task to periodically clean up low-retention memories.

    Args:
        cleanup_interval_hours: Hours between cleanup runs (default from settings)
        retention_threshold: Minimum retention score to keep (default from settings)
        max_users_per_run: Maximum users to process per run (to limit resource usage)

    This task runs indefinitely and should be started in background:
        asyncio.create_task(memory_cleanup_task())
    """
    from enterprise_agent.config.settings import settings
    from enterprise_agent.memory.long_term import _long_term_memory_cache, get_long_term_memory

    interval = cleanup_interval_hours or getattr(settings, "MEMORY_CLEANUP_INTERVAL_HOURS", 1)
    threshold = retention_threshold or getattr(settings, "MEMORY_CLEANUP_THRESHOLD", 0.1)

    logger.info(f"Memory cleanup task started (interval={interval}h, threshold={threshold})")

    while True:
        try:
            # Sleep for interval (convert hours to seconds)
            await asyncio.sleep(interval * 3600)

            # Get list of active users from memory cache
            active_users = list(_long_term_memory_cache.keys())[:max_users_per_run]

            if not active_users:
                logger.debug("No active users to clean up")
                continue

            total_deleted = 0
            for user_id in active_users:
                try:
                    memory = get_long_term_memory(user_id)
                    deleted = await memory.cleanup_low_retention(threshold=threshold)
                    total_deleted += deleted

                    if deleted > 0:
                        logger.info(f"Cleaned {deleted} low-retention memories for user {user_id}")
                except Exception as e:
                    logger.warning(f"Cleanup failed for user {user_id}: {e}")

            if total_deleted > 0:
                logger.info(f"Total deleted memories: {total_deleted}")

        except asyncio.CancelledError:
            logger.info("Memory cleanup task cancelled")
            break
        except Exception as e:
            logger.error(f"Memory cleanup task error: {e}", exc_info=True)
            # Continue running despite errors


def start_cleanup_task() -> asyncio.Task:
    """Start the memory cleanup background task.

    Returns:
        asyncio.Task instance

    Usage:
        cleanup_task = start_cleanup_task()
        # Later, to stop:
        cleanup_task.cancel()
    """
    return asyncio.create_task(memory_cleanup_task())


# Global cleanup task reference
_cleanup_task: Optional[asyncio.Task] = None


def get_or_start_cleanup_task() -> asyncio.Task:
    """Get existing cleanup task or start a new one.

    Returns:
        asyncio.Task instance
    """
    global _cleanup_task

    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = start_cleanup_task()

    return _cleanup_task