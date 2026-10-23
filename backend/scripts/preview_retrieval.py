"""Search the knowledge index with a question and print the closest chunks. Run from the project root:

    python -m backend.scripts.preview_retrieval "What is the total amount of paid bills?" --top-k 5

Scores are cosine similarities between the question and each chunk (higher = more similar);
they are not confidence percentages. Build the index first with backend/scripts/build_index.py.
"""

import argparse
import sys

from backend.app.config import MissingSettingError
from backend.app.repositories.vector_repository import VectorRepository, VectorStoreError
from backend.app.services.rag.embedding_service import EmbeddingError, EmbeddingService
from backend.app.services.rag.retriever import Retriever


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview MedLens knowledge retrieval.")
    parser.add_argument("question", help="question in natural language (quote it)")
    parser.add_argument("--top-k", type=int, default=5, help="number of chunks to return (default 5)")
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be at least 1")

    try:
        with VectorRepository() as repo:
            results = Retriever(EmbeddingService(), repo).retrieve(args.question, top_k=args.top_k)
    except (EmbeddingError, MissingSettingError, VectorStoreError) as exc:
        sys.exit(f"Retrieval failed: {exc}")

    print(f"Question: {args.question}")
    print(f"Top {len(results)} chunks (score = cosine similarity, not a confidence):\n")
    width = max(len(r.chunk.id) for r in results)
    for rank, r in enumerate(results, 1):
        print(f"{rank:>2}. {r.score:.4f}  {r.chunk.id:<{width}}  {r.chunk.title:<18}  {r.chunk.source}")


if __name__ == "__main__":
    main()
