"""Tests for deterministic local-first Chroma embedding initialization."""

import pytest

from enterprise_agent.db import chroma


def test_embedding_model_uses_local_cache_without_network_fallback(monkeypatch):
    calls = []
    sentinel = object()

    def fake_embedding(**kwargs):
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(chroma, "_embedding_function", None)
    monkeypatch.setattr(
        "chromadb.utils.embedding_functions.SentenceTransformerEmbeddingFunction",
        fake_embedding,
    )

    assert chroma.get_embedding_function() is sentinel
    assert calls == [{
        "model_name": chroma.settings.EMBEDDING_MODEL,
        "local_files_only": True,
    }]


def test_embedding_model_downloads_only_when_cache_is_missing(monkeypatch):
    calls = []
    sentinel = object()

    def fake_embedding(**kwargs):
        calls.append(kwargs)
        if kwargs.get("local_files_only"):
            raise OSError("cache miss")
        return sentinel

    monkeypatch.setattr(chroma, "_embedding_function", None)
    monkeypatch.setattr(chroma.settings, "EMBEDDING_ALLOW_DOWNLOAD", True)
    monkeypatch.setattr(
        "chromadb.utils.embedding_functions.SentenceTransformerEmbeddingFunction",
        fake_embedding,
    )

    assert chroma.get_embedding_function() is sentinel
    assert calls == [
        {
            "model_name": chroma.settings.EMBEDDING_MODEL,
            "local_files_only": True,
        },
        {"model_name": chroma.settings.EMBEDDING_MODEL},
    ]


def test_embedding_model_fails_clearly_when_offline_cache_is_missing(monkeypatch):
    def fake_embedding(**kwargs):
        raise OSError("cache miss")

    monkeypatch.setattr(chroma, "_embedding_function", None)
    monkeypatch.setattr(chroma.settings, "EMBEDDING_ALLOW_DOWNLOAD", False)
    monkeypatch.setattr(
        "chromadb.utils.embedding_functions.SentenceTransformerEmbeddingFunction",
        fake_embedding,
    )

    with pytest.raises(RuntimeError, match="Preload the model volume"):
        chroma.get_embedding_function()
