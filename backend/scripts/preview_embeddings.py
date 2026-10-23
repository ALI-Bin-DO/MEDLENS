"""Embed the current knowledge chunks and one sample question with the local Ollama model,
and print a short summary. Nothing is saved. Run from the project root:

    python -m backend.scripts.preview_embeddings
"""

import sys

from backend.app.config import MissingSettingError, get_settings
from backend.app.services.rag.chunker import chunk_documents
from backend.app.services.rag.document_loader import load_documents
from backend.app.services.rag.embedding_service import EmbeddingError, EmbeddingService

SAMPLE_QUESTION = "What is the total amount of paid bills?"


def first_values(vector: list[float], n: int = 5) -> str:
    return ", ".join(f"{x:.6f}" for x in vector[:n])


def main() -> None:
    chunks = chunk_documents(load_documents())
    service = EmbeddingService()
    try:
        document_vectors = service.embed_documents([chunk.content for chunk in chunks])
        query_vector = service.embed_query(SAMPLE_QUESTION)
    except (EmbeddingError, MissingSettingError) as exc:
        sys.exit(f"Embedding failed: {exc}")

    print(f"Model:            {get_settings().ollama_embedding_model}")
    print(f"Chunks:           {len(chunks)}")
    print(f"Document vectors: {len(document_vectors)}")
    print(f"Dimensions:       {sorted({len(v) for v in document_vectors})} (documents), {len(query_vector)} (query)")
    print()
    print(f"Document [{chunks[0].id}] first 5: {first_values(document_vectors[0])}")
    print(f"Query    [{SAMPLE_QUESTION}] first 5: {first_values(query_vector)}")


if __name__ == "__main__":
    main()
