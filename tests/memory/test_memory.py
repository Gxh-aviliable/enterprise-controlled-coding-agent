"""Tests for memory modules (long_term, short_term, importance, pattern_extractor)."""

import asyncio
import hashlib
import math
import re
import uuid

import chromadb
import pytest

from enterprise_agent.memory.long_term import (
    ChromaLongTermMemory,
    rank_memory_candidates,
)


class DeterministicEmbedding:
    """Small offline embedding for exercising real Chroma collection behavior."""

    dimensions = 32

    @staticmethod
    def name() -> str:
        return "test-deterministic-embedding"

    @staticmethod
    def build_from_config(config):
        return DeterministicEmbedding()

    def get_config(self):
        return {"dimensions": self.dimensions}

    def __call__(self, input):
        embeddings = []
        for text in input:
            vector = [0.0] * self.dimensions
            tokens = re.findall(r"[a-z0-9_]+", text.lower())
            for token in tokens:
                bucket = hashlib.sha256(token.encode("utf-8")).digest()[0] % self.dimensions
                vector[bucket] += 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            embeddings.append([value / norm for value in vector])
        return embeddings

    def embed_query(self, input):
        return self(input)


@pytest.fixture
def long_term_memory():
    """Create real in-process Chroma collections without downloading a model."""
    client = chromadb.EphemeralClient()
    embedding = DeterministicEmbedding()
    suffix = uuid.uuid4().hex
    conversations_name = f"test_conversations_{suffix}"
    patterns_name = f"test_patterns_{suffix}"
    memory = ChromaLongTermMemory.__new__(ChromaLongTermMemory)
    memory.user_id = 42
    memory.conversations = client.create_collection(
        conversations_name,
        embedding_function=embedding,
    )
    memory.patterns = client.create_collection(
        patterns_name,
        embedding_function=embedding,
    )
    yield memory
    client.delete_collection(conversations_name)
    client.delete_collection(patterns_name)


class TestImportanceEvaluator:
    """Test importance evaluation for conversation storage."""

    def test_importance_module_exists(self):
        """Test that importance module exists."""
        from enterprise_agent.memory import importance
        assert importance is not None

    def test_importance_threshold_settings(self):
        """Test importance threshold settings."""
        from enterprise_agent.config.settings import settings
        assert settings.IMPORTANCE_THRESHOLD_STORE >= 0
        assert settings.IMPORTANCE_THRESHOLD_STORE <= 1
        assert settings.IMPORTANCE_THRESHOLD_PATTERN >= settings.IMPORTANCE_THRESHOLD_STORE


class TestLongTermMemoryInterface:
    """Test long-term memory interface."""

    def test_long_term_module_exists(self):
        """Test that long_term module exists."""
        from enterprise_agent.memory import long_term
        assert long_term is not None

    def test_get_long_term_memory_function_exists(self):
        """Test get_long_term_memory function exists."""
        from enterprise_agent.memory.long_term import get_long_term_memory
        assert get_long_term_memory is not None

    def test_chinese_queries_use_relative_lexical_reranking(self):
        candidates = [
            {
                "id": "python",
                "rank": 3,
                "distance": 0.7,
                "_search_text": "Python 项目默认使用 uv，修改代码后运行 pytest 和 ruff。",
                "_rejection_reasons": [],
            },
            {
                "id": "frontend",
                "rank": 1,
                "distance": 0.3,
                "_search_text": "前端项目使用 Vue 3 和 Vitest。",
                "_rejection_reasons": [],
            },
            {
                "id": "legacy",
                "rank": 2,
                "distance": 0.4,
                "_search_text": "代码修改测试偏好。",
                "_rejection_reasons": ["quality_not_active"],
            },
        ]

        results = rank_memory_candidates(
            candidates,
            query="代码修改后按照我的 Python 测试偏好进行验证",
            max_distance=0.8,
            include_rejected=True,
            n_results=3,
        )

        python = next(item for item in results if item["id"] == "python")
        frontend = next(item for item in results if item["id"] == "frontend")
        legacy = next(item for item in results if item["id"] == "legacy")
        assert python["rank"] < frontend["rank"]
        assert python["semantic_rank"] == 3
        assert python["eligible"] is True
        assert python["retrieval_strategy"] == "cjk_lexical_rerank"
        assert frontend["eligible"] is False
        assert frontend["filter_reason"] == "cjk_lexical_below_threshold"
        assert "quality_not_active" in legacy["filter_reason"]


class TestPatternExtractorInterface:
    """Test pattern extractor interface."""

    def test_pattern_extractor_module_exists(self):
        """Test pattern_extractor module exists."""
        from enterprise_agent.memory import pattern_extractor
        assert pattern_extractor is not None

    def test_get_pattern_extractor_function_exists(self):
        """Test get_pattern_extractor function exists."""
        from enterprise_agent.memory.pattern_extractor import get_pattern_extractor
        assert get_pattern_extractor is not None


class TestShortTermMemory:
    """Test short-term memory module."""

    def test_short_term_module_exists(self):
        """Test that short_term module exists."""
        from enterprise_agent.memory import short_term
        assert short_term is not None


class TestDecayModule:
    """Test memory decay module."""

    def test_decay_module_exists(self):
        """Test that decay module exists."""
        from enterprise_agent.memory import decay
        assert decay is not None


class TestMemoryBase:
    """Test memory base module."""

    def test_base_module_exists(self):
        """Test that base module exists."""
        from enterprise_agent.memory import base
        assert base is not None


class TestLongTermMemoryIntegration:
    """Integration tests against real in-process Chroma collections."""

    @pytest.mark.asyncio
    async def test_store_conversation(self, long_term_memory):
        """Test storing conversation to ChromaDB."""
        doc_id = await long_term_memory.store_conversation(
            session_id="session-store",
            role="user",
            content="Remember the repository testing policy",
            metadata={"importance": 0.8},
        )

        stored = long_term_memory.conversations.get(ids=[doc_id])
        assert stored["documents"] == ["Remember the repository testing policy"]
        assert stored["metadatas"][0]["user_id"] == 42
        assert stored["metadatas"][0]["session_id"] == "session-store"
        assert stored["metadatas"][0]["importance"] == 0.8

    @pytest.mark.asyncio
    async def test_search_conversations(self, long_term_memory):
        """Test searching conversations from ChromaDB."""
        await long_term_memory.store_conversation(
            session_id="session-python",
            role="assistant",
            content="Python pytest repository validation workflow",
        )
        await long_term_memory.store_conversation(
            session_id="session-design",
            role="assistant",
            content="CSS typography and visual layout guidance",
        )

        results = await long_term_memory.search_conversations(
            "Python pytest validation",
            n_results=1,
        )

        assert len(results) == 1
        assert results[0]["metadata"]["session_id"] == "session-python"
        assert results[0]["metadata"]["user_id"] == 42

    @pytest.mark.asyncio
    async def test_store_pattern(self, long_term_memory):
        """Test storing user pattern."""
        source_id = await long_term_memory.store_task_summary(
            session_id="pattern-source",
            content="The user explicitly requires uv run pytest.",
        )
        pattern_id = await long_term_memory.store_pattern(
            pattern_type="preference",
            pattern_key="test_command",
            pattern_value={"command": "uv run pytest"},
            source_memory_id=source_id,
            confidence=0.9,
        )

        stored = long_term_memory.patterns.get(ids=[pattern_id])
        assert stored["ids"] == [pattern_id]
        assert stored["metadatas"][0]["user_id"] == 42
        assert stored["metadatas"][0]["pattern_key"] == "test_command"
        assert stored["metadatas"][0]["confidence"] == 0.9
        assert stored["metadatas"][0]["schema_version"] == 2
        assert stored["metadatas"][0]["quality_status"] == "active"
        assert stored["metadatas"][0]["evidence_count"] == 1
        assert stored["metadatas"][0]["source_memory_ids_json"] == f'["{source_id}"]'

        await long_term_memory.store_pattern(
            pattern_type="preference",
            pattern_key="test_command",
            pattern_value={"command": "uv run pytest -q"},
            source_memory_id=source_id,
            confidence=0.95,
        )
        updated = long_term_memory.patterns.get(ids=[pattern_id])
        assert updated["metadatas"][0]["evidence_count"] == 2
        assert updated["metadatas"][0]["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_search_patterns(self, long_term_memory):
        """Test searching user patterns."""
        source_id = await long_term_memory.store_task_summary(
            session_id="search-pattern-source",
            content="Durable Python validation preferences.",
        )
        await long_term_memory.store_pattern(
            pattern_type="workflow",
            pattern_key="python_validation",
            pattern_value={"steps": ["pytest", "ruff"]},
            source_memory_id=source_id,
            confidence=0.95,
        )
        await long_term_memory.store_pattern(
            pattern_type="preference",
            pattern_key="theme",
            pattern_value={"value": "dark"},
            source_memory_id=source_id,
            confidence=0.7,
        )

        results = await long_term_memory.search_patterns(
            "python validation pytest ruff",
            pattern_type="workflow",
            n_results=2,
        )

        assert len(results) == 1
        assert results[0]["pattern_type"] == "workflow"
        assert results[0]["pattern_key"] == "python_validation"
        assert results[0]["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_active_only_search_quarantines_legacy_records(self, long_term_memory):
        legacy_id = await long_term_memory.store_conversation(
            session_id="legacy-session",
            role="task_summary",
            content="legacy repository pytest workflow",
            metadata={"importance": 1.0},
        )
        active_id = await long_term_memory.store_task_summary(
            session_id="active-session",
            content="active repository pytest workflow",
            importance=0.8,
        )

        all_results = await long_term_memory.list_conversations(
            role="task_summary",
        )
        active_results = await long_term_memory.list_conversations(
            role="task_summary",
            active_only=True,
        )

        assert {item["id"] for item in all_results} == {legacy_id, active_id}
        assert [item["id"] for item in active_results] == [active_id]
        assert next(
            item for item in all_results if item["id"] == legacy_id
        )["quality_status"] == "legacy"

    @pytest.mark.asyncio
    async def test_retrieval_audit_reports_filtered_candidates(self, long_term_memory):
        disabled_id = await long_term_memory.store_conversation(
            session_id="disabled-session",
            role="task_summary",
            content="Python uv pytest validation workflow",
            metadata={
                "importance": 1.0,
                "schema_version": 2,
                "quality_status": "active",
                "retrieval_enabled": False,
            },
        )

        candidates = await long_term_memory.search_conversations(
            "Python uv pytest validation workflow",
            n_results=3,
            role="task_summary",
            active_only=True,
            max_distance=0.8,
            retrieval_enabled_only=True,
            include_rejected=True,
        )

        disabled = next(item for item in candidates if item["id"] == disabled_id)
        assert disabled["eligible"] is False
        assert "retrieval_disabled" in disabled["filter_reason"]
        assert disabled["rank"] >= 1

    @pytest.mark.asyncio
    async def test_retrieval_counts_track_injection_not_application(self, long_term_memory):
        memory_id = await long_term_memory.store_task_summary(
            session_id="retrieved-session",
            content="Python uv pytest validation workflow",
            importance=0.9,
        )
        pattern_id = await long_term_memory.store_pattern(
            pattern_type="workflow",
            pattern_key="python_validation",
            pattern_value={"steps": ["pytest", "ruff"]},
            source_memory_id=memory_id,
            confidence=1.0,
        )

        await long_term_memory.update_access_count(memory_id)
        await long_term_memory.update_pattern_access_count(pattern_id)

        memory_meta = long_term_memory.conversations.get(
            ids=[memory_id],
            include=["metadatas"],
        )["metadatas"][0]
        pattern_meta = long_term_memory.patterns.get(
            ids=[pattern_id],
            include=["metadatas"],
        )["metadatas"][0]
        assert memory_meta["retrieval_count"] == 1
        assert memory_meta["last_retrieved_at"]
        assert pattern_meta["retrieval_count"] == 1
        assert pattern_meta["last_retrieved_at"]

    @pytest.mark.asyncio
    async def test_pattern_without_source_provenance_is_quarantined(
        self,
        long_term_memory,
    ):
        """Old derived preferences must not remain recallable after v2."""
        pattern_id = "pattern:42:preference:orphan"
        long_term_memory.patterns.add(
            ids=[pattern_id],
            documents=['preference: package_manager = {"value":"uv"}'],
            metadatas=[{
                "user_id": 42,
                "pattern_type": "preference",
                "pattern_key": "package_manager",
                "confidence": 1.0,
                "schema_version": 2,
                "quality_status": "active",
                "retrieval_enabled": True,
            }],
        )

        all_patterns = await long_term_memory.get_all_patterns(active_only=False)
        active_patterns = await long_term_memory.get_all_patterns(active_only=True)
        search_candidates = await long_term_memory.search_patterns(
            "package manager uv",
            active_only=True,
            retrieval_enabled_only=True,
            include_rejected=True,
        )

        orphan = next(item for item in all_patterns if item["id"] == pattern_id)
        searched_orphan = next(
            item for item in search_candidates if item["id"] == pattern_id
        )
        assert orphan["quality_status"] == "legacy"
        assert orphan["quarantine_reason"] == "missing_source_provenance"
        assert active_patterns == []
        assert searched_orphan["eligible"] is False
        assert "quality_not_active" in searched_orphan["filter_reason"]

    @pytest.mark.asyncio
    async def test_deleting_source_memory_cascades_to_derived_patterns(
        self,
        long_term_memory,
    ):
        source_id = await long_term_memory.store_task_summary(
            session_id="cascade-source",
            content="Use uv for this repository.",
        )
        pattern_id = await long_term_memory.store_pattern(
            pattern_type="preference",
            pattern_key="package_manager",
            pattern_value={"value": "uv"},
            source_memory_id=source_id,
        )

        receipt = await long_term_memory.delete_conversation_with_dependents(
            source_id
        )

        assert receipt == {
            "id": source_id,
            "deleted_pattern_ids": [pattern_id],
            "deleted_pattern_count": 1,
        }
        assert long_term_memory.conversations.get(ids=[source_id])["ids"] == []
        assert long_term_memory.patterns.get(ids=[pattern_id])["ids"] == []


class TestLongTermMemoryErrorHandling:
    """Unit tests that do not require a real ChromaDB instance."""

    def test_update_access_count_logs_failure_without_reraising(self):
        """Logging an access-count failure should not introduce a NameError."""
        from enterprise_agent.memory.long_term import ChromaLongTermMemory

        class BrokenCollection:
            def get(self, *args, **kwargs):
                raise RuntimeError("boom")

        memory = ChromaLongTermMemory.__new__(ChromaLongTermMemory)
        memory.conversations = BrokenCollection()

        asyncio.run(memory.update_access_count("doc-1"))
