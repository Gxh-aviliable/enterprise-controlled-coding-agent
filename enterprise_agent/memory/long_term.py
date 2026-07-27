"""Long-term memory using Chroma vector database.

Provides semantic search capability for conversation history and user patterns.
Replaces MySQL-based long-term memory with vector-based storage.
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from enterprise_agent.config.settings import settings
from enterprise_agent.db.chroma import (
    get_conversations_collection,
    get_patterns_collection,
)
from enterprise_agent.memory.base import MemoryBase
from enterprise_agent.memory.policy import (
    ACTIVE_QUALITY_STATUS,
    LEGACY_QUALITY_STATUS,
    MEMORY_SCHEMA_VERSION,
    memory_quality_status,
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_TERM_RE = re.compile(r"[a-z0-9_+#.-]{2,}")
_PATTERN_PROVENANCE_VERSION = 1


def _json_string_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item)]


def pattern_quality_status(metadata: dict[str, Any] | None) -> tuple[str, str]:
    """Patterns are recallable only when their source evidence still exists."""
    metadata = metadata or {}
    base_status = memory_quality_status(metadata)
    if base_status != ACTIVE_QUALITY_STATUS:
        return base_status, "legacy_unclassified"
    source_ids = _json_string_list(metadata.get("source_memory_ids_json"))
    if (
        metadata.get("provenance_version") != _PATTERN_PROVENANCE_VERSION
        or not source_ids
    ):
        return LEGACY_QUALITY_STATUS, "missing_source_provenance"
    return ACTIVE_QUALITY_STATUS, ""


def _lexical_tokens(text: str) -> set[str]:
    normalized = str(text or "").lower()
    tokens = set(_ASCII_TERM_RE.findall(normalized))
    cjk = "".join(_CJK_RE.findall(normalized))
    tokens.update(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
    return tokens


def _cjk_lexical_score(query: str, document: str) -> float:
    query_tokens = _lexical_tokens(query)
    if not query_tokens:
        return 0.0
    return len(query_tokens & _lexical_tokens(document)) / len(query_tokens)


def rank_memory_candidates(
    candidates: List[Dict[str, Any]],
    *,
    query: str,
    max_distance: float | None,
    include_rejected: bool,
    n_results: int,
) -> List[Dict[str, Any]]:
    """Apply language-aware relevance gates and expose every decision.

    ``all-MiniLM-L6-v2`` is kept for backward compatibility with existing
    collections, but it is weak for Chinese ranking. Chinese queries therefore
    use deterministic CJK bigram/engineering-term reranking and a relative
    cutoff. Other languages retain the configured vector-distance gate.
    """
    is_cjk_query = bool(_CJK_RE.search(query or ""))
    for candidate in candidates:
        candidate["semantic_rank"] = candidate.get("rank")
        candidate["lexical_score"] = round(
            _cjk_lexical_score(query, candidate.get("_search_text", "")),
            6,
        )

    if is_cjk_query:
        base_eligible = [
            candidate
            for candidate in candidates
            if not candidate.get("_rejection_reasons")
        ]
        best_score = max(
            (candidate["lexical_score"] for candidate in base_eligible),
            default=0.0,
        )
        cutoff = max(
            settings.MEMORY_CJK_LEXICAL_MIN_SCORE,
            best_score * settings.MEMORY_CJK_RELATIVE_SCORE,
        )
        for candidate in candidates:
            reasons = candidate["_rejection_reasons"]
            if not reasons and candidate["lexical_score"] < cutoff:
                reasons.append("cjk_lexical_below_threshold")
            candidate["retrieval_strategy"] = "cjk_lexical_rerank"
        candidates.sort(
            key=lambda item: (
                -item["lexical_score"],
                item.get("distance") if item.get("distance") is not None else float("inf"),
            )
        )
    else:
        for candidate in candidates:
            reasons = candidate["_rejection_reasons"]
            distance = candidate.get("distance")
            if (
                not reasons
                and max_distance is not None
                and distance is not None
                and distance > max_distance
            ):
                reasons.append("distance_above_threshold")
            candidate["retrieval_strategy"] = "semantic_top_k"

    finalized = []
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
        reasons = candidate.pop("_rejection_reasons")
        candidate.pop("_search_text", None)
        candidate["eligible"] = not reasons
        candidate["filter_reason"] = (
            "eligible" if not reasons else ",".join(reasons)
        )
        if include_rejected or candidate["eligible"]:
            finalized.append(candidate)
    limit = n_results * 4 if include_rejected else n_results
    return finalized[:limit]


class ChromaLongTermMemory(MemoryBase):
    """Long-term memory using Chroma vector database.

    Collections:
    - conversations: Message history with semantic search
    - user_patterns: User behavior patterns and preferences
    """

    def __init__(self, user_id: int = None):
        self.user_id = user_id
        self.conversations = get_conversations_collection()
        self.patterns = get_patterns_collection()

    async def store_conversation(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Dict[str, Any] = None
    ) -> str:
        """Store a conversation message with embedding.

        Args:
            session_id: Session identifier
            role: Message role (user/assistant/system/tool)
            content: Message content
            metadata: Additional metadata

        Returns:
            Document ID
        """
        doc_id = f"{session_id}:{uuid.uuid4().hex[:8]}"

        meta = {
            "user_id": self.user_id,
            "session_id": session_id,
            "role": role,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            meta.update(metadata)

        await asyncio.to_thread(
            self.conversations.add,
            documents=[content],
            metadatas=[meta],
            ids=[doc_id],
        )

        return doc_id

    async def store_task_summary(
        self,
        session_id: str,
        content: str,
        rounds: int = 0,
        has_tool_actions: bool = False,
        importance: float = 0.5,
        metadata: Dict[str, Any] = None
    ) -> str:
        """Store a structured task summary with embedding.

        Task summaries are generated by the MemoryAccumulator at task
        boundaries. They contain the user request, actions taken, result,
        and key findings — much richer than per-round fragments.

        Args:
            session_id: Session identifier
            content: Structured task summary text
            rounds: Number of rounds in the task
            has_tool_actions: Whether tools were used
            importance: Importance score (0-1)
            metadata: Additional metadata

        Returns:
            Document ID
        """
        task_metadata = {
            "role": "task_summary",
            "rounds": rounds,
            "has_tool_actions": has_tool_actions,
            "importance": importance,
            "access_count": 0,
            "memory_type": "task_outcome",
            "schema_version": MEMORY_SCHEMA_VERSION,
            "quality_status": ACTIVE_QUALITY_STATUS,
            "task_status": "succeeded",
        }
        if metadata:
            task_metadata.update(metadata)

        return await self.store_conversation(
            session_id=session_id,
            role="task_summary",
            content=content,
            metadata=task_metadata,
        )

    async def search_conversations(
        self,
        query: str,
        n_results: int = 10,
        session_id: str = None,
        role: str = None,
        active_only: bool = False,
        max_distance: float = None,
        retrieval_enabled_only: bool = False,
        include_rejected: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search conversations semantically.

        Args:
            query: Search query
            n_results: Number of results
            session_id: Filter by session (optional)
            role: Filter by role (optional)

        Returns:
            List of matching conversations
        """
        where_filter = None
        if session_id or role or self.user_id:
            conditions = []
            if self.user_id:
                conditions.append({"user_id": self.user_id})
            if session_id:
                conditions.append({"session_id": session_id})
            if role:
                conditions.append({"role": role})

            if len(conditions) == 1:
                where_filter = conditions[0]
            elif len(conditions) > 1:
                where_filter = {"$and": conditions}

        query_limit = (
            n_results * 4
            if active_only
            or max_distance is not None
            or retrieval_enabled_only
            or include_rejected
            else n_results
        )
        results = await asyncio.to_thread(
            self.conversations.query,
            query_texts=[query],
            n_results=max(query_limit, n_results),
            where=where_filter,
        )

        # Format results
        messages = []
        if results and results.get("documents"):
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                doc_id = results["ids"][0][i] if results.get("ids") else None
                distance = (
                    results["distances"][0][i]
                    if results.get("distances")
                    else None
                )
                quality_status = memory_quality_status(meta)
                rejection_reasons = []
                if active_only and quality_status != ACTIVE_QUALITY_STATUS:
                    rejection_reasons.append("quality_not_active")
                if (
                    retrieval_enabled_only
                    and meta.get("retrieval_enabled", True) is not True
                ):
                    rejection_reasons.append("retrieval_disabled")
                messages.append({
                    "id": doc_id,
                    "content": doc,
                    "metadata": meta,
                    "quality_status": quality_status,
                    "distance": distance,
                    "rank": i + 1,
                    "_search_text": doc,
                    "_rejection_reasons": rejection_reasons,
                })

        return rank_memory_candidates(
            messages,
            query=query,
            max_distance=max_distance,
            include_rejected=include_rejected,
            n_results=n_results,
        )

    async def get_session_history(
        self,
        session_id: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get all messages for a session.

        Args:
            session_id: Session identifier
            limit: Maximum number of messages

        Returns:
            List of messages in chronological order
        """
        results = await asyncio.to_thread(
            self.conversations.get,
            where={"session_id": session_id},
            limit=limit,
        )

        messages = []
        if results and results.get("documents"):
            for i, doc in enumerate(results["documents"]):
                meta = results["metadatas"][i] if results.get("metadatas") else {}
                messages.append({
                    "role": meta.get("role", "unknown"),
                    "content": doc,
                    "metadata": meta,
                })

        # Sort by timestamp
        messages.sort(key=lambda m: m["metadata"].get("timestamp", ""))

        return messages

    async def list_conversations(
        self,
        limit: int = 50,
        role: str = None,
        min_importance: float = 0.0,
        active_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """List conversation memories for the current user.

        This is used by management UIs where "show my memories" must be a
        deterministic listing rather than a semantic nearest-neighbor search.
        """
        conditions = []
        if self.user_id:
            conditions.append({"user_id": self.user_id})
        if role:
            conditions.append({"role": role})

        where_filter = None
        if len(conditions) == 1:
            where_filter = conditions[0]
        elif len(conditions) > 1:
            where_filter = {"$and": conditions}

        results = await asyncio.to_thread(
            self.conversations.get,
            where=where_filter,
            limit=limit,
        )

        memories = []
        if results and results.get("documents"):
            ids = results.get("ids") or []
            metadatas = results.get("metadatas") or []
            for i, doc in enumerate(results["documents"]):
                meta = metadatas[i] if i < len(metadatas) else {}
                importance = meta.get("importance", 0.0) or 0.0
                if importance < min_importance:
                    continue
                quality_status = memory_quality_status(meta)
                if active_only and quality_status != ACTIVE_QUALITY_STATUS:
                    continue
                memories.append({
                    "id": ids[i] if i < len(ids) else None,
                    "content": doc,
                    "metadata": meta,
                    "quality_status": quality_status,
                })

        memories.sort(
            key=lambda m: (
                m["metadata"].get("importance", 0.0) or 0.0,
                m["metadata"].get("timestamp", ""),
            ),
            reverse=True,
        )
        return memories[:limit]

    async def store_pattern(
        self,
        pattern_type: str,
        pattern_key: str,
        pattern_value: Dict[str, Any],
        source_memory_id: str,
        confidence: float = 1.0,
        source: str = "explicit_user_signal",
        source_trace_id: str = "",
        source_session_id: str = "",
    ) -> str:
        """Store a user behavior pattern.

        Args:
            pattern_type: Type of pattern (preference/workflow/shortcut)
            pattern_key: Pattern identifier
            pattern_value: Pattern data
            source_memory_id: Durable source record that produced this pattern
            confidence: Confidence score (0-1)

        Returns:
            Pattern ID
        """
        pattern_id = f"pattern:{self.user_id}:{pattern_type}:{pattern_key}"

        # Create searchable text from pattern
        if not source_memory_id:
            raise ValueError("source_memory_id is required for recallable patterns")

        pattern_text = (
            f"{pattern_type}: {pattern_key} = "
            f"{json.dumps(pattern_value, ensure_ascii=False)}"
        )

        now = datetime.now(timezone.utc).isoformat()
        existing = await asyncio.to_thread(
            self.patterns.get,
            ids=[pattern_id],
            include=["metadatas"],
        )
        previous_meta = (
            existing["metadatas"][0]
            if existing and existing.get("metadatas")
            else {}
        )
        evidence_count = int(previous_meta.get("evidence_count", 0) or 0) + 1
        source_memory_ids = _json_string_list(
            previous_meta.get("source_memory_ids_json")
        )
        if source_memory_id not in source_memory_ids:
            source_memory_ids.append(source_memory_id)
        source_trace_ids = _json_string_list(
            previous_meta.get("source_trace_ids_json")
        )
        if source_trace_id and source_trace_id not in source_trace_ids:
            source_trace_ids.append(source_trace_id)
        source_session_ids = _json_string_list(
            previous_meta.get("source_session_ids_json")
        )
        if source_session_id and source_session_id not in source_session_ids:
            source_session_ids.append(source_session_id)

        meta = {
            "user_id": self.user_id,
            "pattern_type": pattern_type,
            "pattern_key": pattern_key,
            "confidence": confidence,
            "timestamp": previous_meta.get("timestamp", now),
            "updated_at": now,
            "evidence_count": evidence_count,
            "value_json": json.dumps(pattern_value, ensure_ascii=False),
            "source": source,
            "schema_version": MEMORY_SCHEMA_VERSION,
            "quality_status": ACTIVE_QUALITY_STATUS,
            "retrieval_count": int(previous_meta.get("retrieval_count", 0) or 0),
            "last_retrieved_at": previous_meta.get("last_retrieved_at", ""),
            "retrieval_enabled": True,
            "provenance_version": _PATTERN_PROVENANCE_VERSION,
            "source_memory_ids_json": json.dumps(
                source_memory_ids,
                ensure_ascii=False,
            ),
            "source_trace_ids_json": json.dumps(
                source_trace_ids,
                ensure_ascii=False,
            ),
            "source_session_ids_json": json.dumps(
                source_session_ids,
                ensure_ascii=False,
            ),
        }

        await asyncio.to_thread(
            self.patterns.upsert,
            documents=[pattern_text],
            metadatas=[meta],
            ids=[pattern_id],
        )

        return pattern_id

    async def search_patterns(
        self,
        query: str,
        pattern_type: str = None,
        n_results: int = 5,
        active_only: bool = False,
        max_distance: float = None,
        retrieval_enabled_only: bool = False,
        include_rejected: bool = False,
    ) -> List[Dict[str, Any]]:
        """Search user patterns semantically.

        Args:
            query: Search query
            pattern_type: Filter by type (optional)
            n_results: Number of results

        Returns:
            List of matching patterns
        """
        where_filter = {"user_id": self.user_id}
        if pattern_type:
            where_filter = {
                "$and": [
                    {"user_id": self.user_id},
                    {"pattern_type": pattern_type},
                ]
            }

        query_limit = (
            n_results * 4
            if active_only
            or max_distance is not None
            or retrieval_enabled_only
            or include_rejected
            else n_results
        )
        results = await asyncio.to_thread(
            self.patterns.query,
            query_texts=[query],
            n_results=max(query_limit, n_results),
            where=where_filter,
        )

        patterns = []
        if results and results.get("documents"):
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                pattern_id = results["ids"][0][i] if results.get("ids") else None
                distance = (
                    results["distances"][0][i]
                    if results.get("distances")
                    else None
                )
                quality_status, quarantine_reason = pattern_quality_status(meta)
                rejection_reasons = []
                if active_only and quality_status != ACTIVE_QUALITY_STATUS:
                    rejection_reasons.append("quality_not_active")
                if (
                    retrieval_enabled_only
                    and meta.get("retrieval_enabled", True) is not True
                ):
                    rejection_reasons.append("retrieval_disabled")
                patterns.append({
                    "id": pattern_id,
                    "text": doc,
                    "pattern_type": meta.get("pattern_type"),
                    "pattern_key": meta.get("pattern_key"),
                    "confidence": meta.get("confidence"),
                    "evidence_count": meta.get("evidence_count", 0),
                    "updated_at": meta.get("updated_at"),
                    "value": meta.get("value_json", ""),
                    "quality_status": quality_status,
                    "quarantine_reason": quarantine_reason,
                    "source_memory_ids": _json_string_list(
                        meta.get("source_memory_ids_json")
                    ),
                    "distance": distance,
                    "rank": i + 1,
                    "_search_text": doc,
                    "_rejection_reasons": rejection_reasons,
                })

        return rank_memory_candidates(
            patterns,
            query=query,
            max_distance=max_distance,
            include_rejected=include_rejected,
            n_results=n_results,
        )

    async def get_all_patterns(self, active_only: bool = False) -> List[Dict[str, Any]]:
        """Get all patterns for user.

        Returns:
            List of all user patterns
        """
        results = await asyncio.to_thread(
            self.patterns.get,
            where={"user_id": self.user_id},
        )

        patterns = []
        if results and results.get("metadatas"):
            for i, meta in enumerate(results["metadatas"]):
                pattern_id = results["ids"][i] if results.get("ids") else None
                quality_status, quarantine_reason = pattern_quality_status(meta)
                if active_only and quality_status != ACTIVE_QUALITY_STATUS:
                    continue
                document = (
                    results["documents"][i]
                    if results.get("documents") and i < len(results["documents"])
                    else ""
                )
                patterns.append({
                    "id": pattern_id,
                    "pattern_type": meta.get("pattern_type"),
                    "pattern_key": meta.get("pattern_key"),
                    "confidence": meta.get("confidence"),
                    "evidence_count": meta.get("evidence_count", 0),
                    "updated_at": meta.get("updated_at"),
                    "retrieval_count": meta.get("retrieval_count", 0),
                    "last_retrieved_at": meta.get("last_retrieved_at", ""),
                    "value": meta.get("value_json", ""),
                    "text": document,
                    "quality_status": quality_status,
                    "quarantine_reason": quarantine_reason,
                    "source_memory_ids": _json_string_list(
                        meta.get("source_memory_ids_json")
                    ),
                })

        return patterns

    # MemoryBase interface implementation
    async def store(self, key: str, data: Dict[str, Any]) -> None:
        """Store data with given key (generic interface)."""
        await self.store_conversation(
            session_id=data.get("session_id", "unknown"),
            role=data.get("role", "unknown"),
            content=json.dumps(data),
            metadata={"key": key},
        )

    async def retrieve(self, key: str) -> Dict[str, Any]:
        """Retrieve data by key (generic interface)."""
        results = await asyncio.to_thread(
            self.conversations.get,
            where={"key": key},
        )

        if results and results.get("documents"):
            return json.loads(results["documents"][0])
        return {}

    async def delete(self, key: str) -> None:
        """Delete data by key (generic interface)."""
        await asyncio.to_thread(
            self.conversations.delete,
            where={"key": key},
        )

    async def update_access_count(self, doc_id: str) -> None:
        """Update access count and last_access timestamp for a document.

        Called when a memory is retrieved to track usage frequency.

        Args:
            doc_id: Document ID to update
        """
        try:
            # Get current metadata
            result = await asyncio.to_thread(
                self.conversations.get,
                ids=[doc_id],
                include=["metadatas"]
            )

            if result and result.get("metadatas"):
                meta = result["metadatas"][0].copy()
                now = datetime.now(timezone.utc).isoformat()
                retrieval_count = int(
                    meta.get("retrieval_count", meta.get("access_count", 0)) or 0
                ) + 1
                meta["retrieval_count"] = retrieval_count
                meta["last_retrieved_at"] = now
                # Compatibility fields retained for existing API consumers.
                meta["access_count"] = retrieval_count
                meta["last_access"] = now

                await asyncio.to_thread(
                    self.conversations.update,
                    ids=[doc_id],
                    metadatas=[meta]
                )
        except Exception:
            logging.warning(f"Failed to update access count for {doc_id}", exc_info=True)

    async def update_pattern_access_count(self, pattern_id: str) -> None:
        """Track pattern retrieval without claiming the model applied it."""
        try:
            result = await asyncio.to_thread(
                self.patterns.get,
                ids=[pattern_id],
                include=["metadatas"],
            )
            if result and result.get("metadatas"):
                meta = result["metadatas"][0].copy()
                meta["retrieval_count"] = int(meta.get("retrieval_count", 0) or 0) + 1
                meta["last_retrieved_at"] = datetime.now(timezone.utc).isoformat()
                await asyncio.to_thread(
                    self.patterns.update,
                    ids=[pattern_id],
                    metadatas=[meta],
                )
        except Exception:
            logging.warning(
                "Failed to update pattern retrieval count for %s",
                pattern_id,
                exc_info=True,
            )

    async def delete_conversation_with_dependents(
        self,
        doc_id: str,
    ) -> Dict[str, Any] | None:
        """Delete a memory and every pattern derived from that source.

        Verifies user ownership before deletion.

        Args:
            doc_id: ChromaDB document ID to delete

        Returns:
            Deletion receipt, or ``None`` when not found/not owned.
        """
        try:
            # Verify ownership: get the document first
            result = await asyncio.to_thread(
                self.conversations.get,
                ids=[doc_id],
                include=["metadatas"],
            )
            if not result or not result.get("ids"):
                return None

            meta = result["metadatas"][0] if result.get("metadatas") else {}
            owner_id = meta.get("user_id")
            if owner_id is not None and owner_id != self.user_id:
                logging.warning(
                    f"User {self.user_id} attempted to delete memory "
                    f"owned by user {owner_id}: {doc_id}"
                )
                return None

            linked_pattern_ids = []
            patterns = await asyncio.to_thread(
                self.patterns.get,
                where={"user_id": self.user_id},
                include=["metadatas"],
            )
            pattern_metadatas = patterns.get("metadatas") or []
            for index, pattern_id in enumerate(patterns.get("ids") or []):
                metadata = (
                    pattern_metadatas[index]
                    if index < len(pattern_metadatas)
                    else {}
                )
                if doc_id in _json_string_list(
                    metadata.get("source_memory_ids_json")
                ):
                    linked_pattern_ids.append(pattern_id)
            if linked_pattern_ids:
                # Conservative privacy semantics: a derived pattern is removed
                # entirely when any of its source memories is deleted. Without
                # a per-source value history we cannot safely reconstruct it.
                await asyncio.to_thread(
                    self.patterns.delete,
                    ids=linked_pattern_ids,
                )
            # Delete the parent only after dependants are gone. If Chroma
            # fails mid-operation, retaining the source memory is safer than
            # leaving a recallable derived preference after its source vanished.
            await asyncio.to_thread(
                self.conversations.delete,
                ids=[doc_id],
            )
            logging.info(
                "Deleted conversation memory %s and %s linked patterns",
                doc_id,
                len(linked_pattern_ids),
            )
            return {
                "id": doc_id,
                "deleted_pattern_ids": linked_pattern_ids,
                "deleted_pattern_count": len(linked_pattern_ids),
            }
        except Exception:
            logging.warning(f"Failed to delete conversation {doc_id}", exc_info=True)
            return None

    async def delete_conversation(self, doc_id: str) -> bool:
        """Backward-compatible boolean deletion API."""
        return await self.delete_conversation_with_dependents(doc_id) is not None

    async def delete_pattern(self, pattern_id: str) -> bool:
        """Delete a single user pattern by document ID.

        Verifies user ownership before deletion.

        Args:
            pattern_id: ChromaDB document ID to delete

        Returns:
            True if deleted, False if not found or not owned by user
        """
        try:
            # Verify ownership: get the document first
            result = await asyncio.to_thread(
                self.patterns.get,
                ids=[pattern_id],
                include=["metadatas"],
            )
            if not result or not result.get("ids"):
                return False

            meta = result["metadatas"][0] if result.get("metadatas") else {}
            owner_id = meta.get("user_id")
            if owner_id is not None and owner_id != self.user_id:
                logging.warning(
                    f"User {self.user_id} attempted to delete pattern "
                    f"owned by user {owner_id}: {pattern_id}"
                )
                return False

            await asyncio.to_thread(
                self.patterns.delete,
                ids=[pattern_id],
            )
            logging.info(f"Deleted pattern: {pattern_id}")
            return True
        except Exception:
            logging.warning(f"Failed to delete pattern {pattern_id}", exc_info=True)
            return False

    async def cleanup_low_retention(self, threshold: float = 0.1) -> int:
        """Remove memories with retention score below threshold.

        Uses decay calculator to determine retention scores based on:
        - importance (initial value)
        - recency (exponential decay)
        - access frequency (logarithmic boost)

        Args:
            threshold: Minimum retention score to keep (default 0.1)

        Returns:
            Number of deleted documents
        """
        from enterprise_agent.memory.decay import MemoryDecayCalculator

        calc = MemoryDecayCalculator()

        try:
            # Get all documents for this user
            where_filter = {"user_id": self.user_id} if self.user_id else None

            results = await asyncio.to_thread(
                self.conversations.get,
                where=where_filter,
                include=["metadatas"]
            )

            if not results or not results.get("ids"):
                return 0

            ids_to_delete = []
            for i, doc_id in enumerate(results["ids"]):
                meta = results["metadatas"][i]

                importance = meta.get("importance", 0.5)
                timestamp = meta.get("timestamp", datetime.now(timezone.utc).isoformat())
                access_count = meta.get("access_count", 0)
                last_access = meta.get("last_access")

                retention = calc.calculate_retention_score(
                    importance=importance,
                    timestamp=timestamp,
                    access_count=access_count,
                    last_access=last_access
                )

                if retention < threshold:
                    ids_to_delete.append(doc_id)

            if ids_to_delete:
                await asyncio.to_thread(
                    self.conversations.delete,
                    ids=ids_to_delete
                )
                logging.info(f"Deleted {len(ids_to_delete)} low-retention memories for user {self.user_id}")

            return len(ids_to_delete)

        except Exception:
            logging.warning("Failed to cleanup low retention memories", exc_info=True)
            return 0


# Per-user instance cache (avoids race condition on global singleton)
_long_term_memory_cache: Dict[int, ChromaLongTermMemory] = {}


def get_long_term_memory(user_id: int = None) -> ChromaLongTermMemory:
    """Get or create LongTermMemory instance.

    Args:
        user_id: User identifier for filtering

    Returns:
        ChromaLongTermMemory instance
    """
    if user_id is None:
        return ChromaLongTermMemory(user_id=None)

    if user_id not in _long_term_memory_cache:
        _long_term_memory_cache[user_id] = ChromaLongTermMemory(user_id=user_id)

    return _long_term_memory_cache[user_id]
