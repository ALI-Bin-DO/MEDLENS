"""Show the exact knowledge context that would be sent to the SQL-generating LLM for a question.
Strategy: "full schema + retrieved documentation/examples". Nothing is saved. Run from the project root:

    python -m backend.scripts.preview_context "How many visits are booked with each physician?"
    python -m backend.scripts.preview_context "..." --top-k 3      # override RAG_CONTEXT_TOP_K
"""

import argparse
import sys

from backend.app.config import MissingSettingError
from backend.app.repositories.vector_repository import VectorRepository, VectorStoreError
from backend.app.services.rag.context_builder import ContextBuilder
from backend.app.services.rag.embedding_service import EmbeddingError, EmbeddingService
from backend.app.services.rag.retriever import Retriever

RULE = "-" * 72


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview the LLM context for a question.")
    parser.add_argument("question", help="question in natural language (quote it)")
    parser.add_argument("--top-k", type=int, default=None,
                        help="documentation/example chunks to retrieve (default: RAG_CONTEXT_TOP_K)")
    args = parser.parse_args()
    if args.top_k is not None and args.top_k < 0:
        parser.error("--top-k must be 0 or more")

    try:
        with VectorRepository() as repo:
            builder = ContextBuilder(Retriever(EmbeddingService(), repo), repo, top_k=args.top_k)
            context = builder.build(args.question)
    except (EmbeddingError, MissingSettingError, VectorStoreError) as exc:
        sys.exit(f"Context build failed: {exc}")

    text = context.text
    print(f"Question: {context.question}")
    print(f"Strategy: {context.strategy}")
    print(f"Schema chunks (always included, not ranked): {len(context.schema)}")
    for chunk in context.schema:
        print(f"  - {chunk.id}  ({chunk.source})")
    print(f"Retrieved chunks (semantic search over documentation/examples, top_k={builder.top_k}):")
    for r in context.retrieved:
        print(f"  - {r.chunk.id}  similarity {r.score:.4f}  ({r.chunk.source})")
    print(f"Context: {len(context.chunk_ids)} chunks, {len(text)} characters")
    print(f"\n{RULE}\nEXACT CONTEXT TEXT (fills {{context}} in the prompt)\n{RULE}")
    print(text)
    print(RULE)


if __name__ == "__main__":
    main()
