"""Finds the knowledge chunks most relevant to a question (raw semantic retrieval).

Reads from an index that backend/scripts/build_index.py built earlier; it never builds the index itself.
The VectorRepository passed in must already be open (see vector_repository.py).
context_builder.py builds the LLM context on top of this; retrieve() stays available for comparison.
"""

from collections.abc import Sequence

from backend.app.repositories.vector_repository import RetrievedChunk, VectorRepository
from backend.app.services.rag.embedding_service import EmbeddingService


class Retriever:
    """Embeds the question and searches the vector store."""

    def __init__(self, embeddings: EmbeddingService, vectors: VectorRepository) -> None:
        self.embeddings = embeddings
        self.vectors = vectors

    def retrieve(self, question: str, top_k: int = 5, types: Sequence[str] | None = None) -> list[RetrievedChunk]:
        """Return up to top_k chunks, most similar first, with cosine similarity scores.

        types limits the search to those chunk types (e.g. ["documentation", "example"]);
        None searches every type. Raises IndexNotFoundError / IndexIncompatibleError if the
        index is not usable.
        """
        vector = self.embeddings.embed_query(question)
        return self.vectors.search(vector, top_k=top_k, embedding_model=self.embeddings.model_name, types=types)
