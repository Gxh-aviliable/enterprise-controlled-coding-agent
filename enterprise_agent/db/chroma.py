"""Chroma vector database client for long-term memory.

Provides persistent vector storage with semantic search capability.
Uses sentence-transformers for local embedding (all-MiniLM-L6-v2).
"""

import os

# Set HuggingFace offline mode BEFORE importing any HF libraries
# This prevents network requests when models are already cached
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from enterprise_agent.config.settings import settings

# Global Chroma client and embedding function (cached singletons)
_chroma_client: Optional[chromadb.Client] = None
_embedding_function = None


def get_chroma_client() -> chromadb.Client:
    """Get or create Chroma client instance.

    Uses persistent storage in settings.CHROMA_PERSIST_DIR.
    """
    global _chroma_client

    if _chroma_client is None:
        persist_dir = Path(settings.CHROMA_PERSIST_DIR)
        persist_dir.mkdir(parents=True, exist_ok=True)

        _chroma_client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=ChromaSettings(
                anonymized_telemetry=False,
                allow_reset=settings.DEBUG,  # Only allow reset in dev mode
            )
        )

    return _chroma_client


def get_embedding_function():
    """Get embedding function for Chroma (cached singleton).

    Uses sentence-transformers local model (all-MiniLM-L6-v2).
    No API key required.
    """
    global _embedding_function
    if _embedding_function is None:
        from chromadb.utils import embedding_functions
        _embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=settings.EMBEDDING_MODEL
        )
    return _embedding_function


def get_conversations_collection() -> chromadb.Collection:
    """Get or create conversations collection.

    Stores conversation history with semantic search capability.
    """
    client = get_chroma_client()
    embedding_fn = get_embedding_function()

    return client.get_or_create_collection(
        name=settings.CHROMA_COLLECTION_CONVERSATIONS,
        embedding_function=embedding_fn,
        metadata={"description": "Conversation history for semantic search"}
    )


def get_patterns_collection() -> chromadb.Collection:
    """Get or create user patterns collection.

    Stores user behavior patterns and preferences.
    """
    client = get_chroma_client()
    embedding_fn = get_embedding_function()

    return client.get_or_create_collection(
        name=settings.CHROMA_COLLECTION_PATTERNS,
        embedding_function=embedding_fn,
        metadata={"description": "User behavior patterns"}
    )


def init_chroma() -> None:
    """Initialize Chroma collections."""
    get_conversations_collection()
    get_patterns_collection()


def reset_chroma() -> None:
    """Reset Chroma database (delete all collections).

    WARNING: This will delete all stored data.
    """
    client = get_chroma_client()
    client.reset()

    # Reinitialize collections
    init_chroma()