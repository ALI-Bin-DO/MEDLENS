"""Builds the knowledge context sent to the SQL-generating LLM.

Strategy: "full schema + retrieved documentation/examples".
    - Schema: every CREATE TABLE chunk, always included. They are read from the stored
      payloads by type (not ranked by similarity) and carry no score.
    - Documentation and examples: the top_k most similar chunks, found by one semantic
      search that is filtered to those two types inside Qdrant. ChatService leaves out the
      lowest-ranked of them if the SQL prompt would not fit num_ctx (the schema is never cut).
The database has only five tables, so sending the whole schema is cheap and guarantees that
every table a question might need (e.g. doctors for "physician") is present.

Raw semantic retrieval (Retriever.retrieve) stays available separately for comparison.
"""

from dataclasses import dataclass

from backend.app.config import SCHEMA_FILE, get_settings
from backend.app.repositories.vector_repository import RetrievedChunk, VectorRepository, VectorStoreError
from backend.app.services.rag.chunker import Chunk, chunk_documents
from backend.app.services.rag.document_loader import load_document
from backend.app.services.rag.retriever import Retriever

STRATEGY = "full schema + retrieved documentation/examples"
RETRIEVED_TYPES = ("documentation", "example")


class SchemaContextError(VectorStoreError):
    """The stored schema chunks do not match database/schema.sql (missing, extra or outdated)."""


@dataclass(frozen=True)
class LLMContext:
    """The context for one question, kept in its parts so it can be inspected and evaluated."""

    strategy: str
    question: str
    schema: list[Chunk]  # always included, in schema.sql order, no scores
    retrieved: list[RetrievedChunk]  # semantic matches (documentation/examples), most similar first

    @property
    def chunk_ids(self) -> list[str]:
        return [c.id for c in self.schema] + [r.chunk.id for r in self.retrieved]

    @property
    def text(self) -> str:
        """The exact text that fills {context} in the SQL prompts."""
        documentation = [r.chunk for r in self.retrieved if r.chunk.type == "documentation"]
        examples = [r.chunk for r in self.retrieved if r.chunk.type == "example"]
        sections = [
            _section("SCHEMA (all tables, always included)", self.schema),
            _section("DOCUMENTATION (retrieved for this question)", documentation),
            _section("EXAMPLES (retrieved for this question)", examples),
        ]
        return "\n\n".join(sections)


def _section(heading: str, chunks: list[Chunk]) -> str:
    body = "\n\n".join(f"[{c.id}]\n{c.content}" for c in chunks) or "(none)"  # the id names the source
    return f"=== {heading} ===\n\n{body}"


def expected_schema_chunks() -> list[Chunk]:
    """The schema chunks the current database/schema.sql produces (reads and splits the file; no embedding)."""
    return chunk_documents([load_document(SCHEMA_FILE, "schema")])


class ContextBuilder:
    """Combines all schema chunks with semantically retrieved documentation/examples."""

    def __init__(self, retriever: Retriever, vectors: VectorRepository, top_k: int | None = None) -> None:
        self.retriever = retriever
        self.vectors = vectors
        self.top_k = get_settings().rag_context_top_k if top_k is None else top_k
        if not isinstance(self.top_k, int) or isinstance(self.top_k, bool) or self.top_k < 0:
            raise ValueError("top_k must be a whole number >= 0")

    def build(self, question: str) -> LLMContext:
        """Build the context for one question. Raises SchemaContextError if the index's schema is stale."""
        schema = self._stored_schema()
        retrieved = []
        if self.top_k > 0:
            retrieved = self.retriever.retrieve(question, top_k=self.top_k, types=RETRIEVED_TYPES)

        # Schema types are excluded from the search, but never let one chunk appear twice.
        seen = {c.id for c in schema}
        unique = []
        for r in retrieved:
            if r.chunk.id not in seen:
                seen.add(r.chunk.id)
                unique.append(r)
        return LLMContext(strategy=STRATEGY, question=question, schema=schema, retrieved=unique)

    def _stored_schema(self) -> list[Chunk]:
        """All stored schema chunks, in schema.sql order, after checking they match the file."""
        expected = expected_schema_chunks()
        stored = {c.id: c for c in self.vectors.get_chunks_by_type("schema")}
        expected_ids = [c.id for c in expected]

        missing = [i for i in expected_ids if i not in stored]
        extra = sorted(set(stored) - set(expected_ids))
        outdated = [c.id for c in expected if c.id in stored and stored[c.id] != c]
        if missing or extra or outdated:
            details = "; ".join(
                f"{label}: {', '.join(ids)}"
                for label, ids in (("missing", missing), ("not in schema.sql", extra), ("outdated", outdated))
                if ids
            )
            raise SchemaContextError(
                f"Stored schema chunks do not match database/schema.sql ({details}). "
                "Run: python -m backend.scripts.build_index"
            )
        return [stored[i] for i in expected_ids]
