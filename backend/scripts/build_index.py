"""Build or update the RAG index from the knowledge files. Run once at setup and again whenever
database/schema.sql or knowledge_base/ changes - never on app startup or per question:

    python -m backend.scripts.build_index

Requires Ollama running with OLLAMA_EMBEDDING_MODEL pulled. Every chunk is embedded before
the store is opened, so a failed embedding never touches the existing index. Re-running
updates chunks in place and then removes chunks that no longer exist.
"""

import sys

from backend.app.config import MissingSettingError
from backend.app.repositories.vector_repository import SyncResult, VectorRepository, VectorStoreError
from backend.app.services.rag.chunker import chunk_documents
from backend.app.services.rag.document_loader import load_documents
from backend.app.services.rag.embedding_service import EmbeddingError, EmbeddingService


def build_index() -> SyncResult:
    """Load, chunk, embed and store all knowledge."""
    chunks = chunk_documents(load_documents())
    embeddings = EmbeddingService()
    vectors = embeddings.embed_documents([chunk.content for chunk in chunks])
    with VectorRepository() as repo:
        return repo.sync(chunks, vectors, embedding_model=embeddings.model_name)


def main() -> None:
    try:
        result = build_index()
    except (EmbeddingError, MissingSettingError, VectorStoreError) as exc:
        sys.exit(f"Indexing failed: {exc}")
    action = "Created" if result.created else "Updated"
    print(f"{action} index: {result.upserted} chunks upserted, {result.removed} obsolete removed, "
          f"{result.points} points in collection.")


if __name__ == "__main__":
    main()
