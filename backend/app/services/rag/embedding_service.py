"""Turns text into embedding vectors with the local Ollama model in OLLAMA_EMBEDDING_MODEL.

Uses Ollama's POST /api/embed through the standard library (urllib), with a finite timeout
(OLLAMA_TIMEOUT_SECONDS) and truncate=false, so text that is too long for the model fails
loudly instead of being cut.

nomic-embed-text expects a task prefix on every input. The prefixes are added here and only
here: embed_documents() for knowledge chunks, embed_query() for user questions. Callers pass
the original text, which is never modified.

Constructing EmbeddingService does not contact Ollama or download a model. Every failure
raises; no fake or empty vectors are ever returned.
"""

import math

from backend.app.config import MissingSettingError, Settings, get_settings
from backend.app.services.ollama_client import OllamaError, post_json

DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

# Inputs sent per request; keeps request bodies small as the knowledge base grows.
BATCH_SIZE = 32


class EmbeddingError(OllamaError):
    """Raised when Ollama cannot produce valid embeddings."""


class EmbeddingService:
    """Produces one vector per input text. The same model must be used for indexing and search."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def model_name(self) -> str:
        """The configured embedding model; stored with the index so searches can check it."""
        if not self.settings.ollama_embedding_model:
            raise MissingSettingError("OLLAMA_EMBEDDING_MODEL must be set in .env")
        return self.settings.ollama_embedding_model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed knowledge chunks (adds "search_document: "). Returns vectors in input order."""
        return self._embed([DOCUMENT_PREFIX + text for text in _check_texts(texts)])

    def embed_query(self, question: str) -> list[float]:
        """Embed one user question (adds "search_query: ")."""
        return self._embed([QUERY_PREFIX + text for text in _check_texts([question])])[0]

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        """Embed already-prefixed inputs in batches, checking that all vectors share one dimension."""
        vectors: list[list[float]] = []
        for start in range(0, len(inputs), BATCH_SIZE):
            vectors.extend(self._request(inputs[start : start + BATCH_SIZE]))
        if vectors and len({len(vector) for vector in vectors}) != 1:
            raise EmbeddingError("Ollama returned vectors with different dimensions")
        return vectors

    def _request(self, inputs: list[str]) -> list[list[float]]:
        """Send one /api/embed request and return its validated vectors."""
        payload = post_json(
            self.settings.ollama_base_url,
            "/api/embed",
            {"model": self.model_name, "input": inputs, "truncate": False},
            timeout=self.settings.ollama_timeout_seconds,
            error=EmbeddingError,
        )
        return _validate_vectors(payload, expected=len(inputs))


def _check_texts(texts: list[str]) -> list[str]:
    """Reject non-string or blank inputs; embedding them would only hide a bug upstream."""
    for i, text in enumerate(texts):
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"input {i} must be a non-empty string")
    return texts


def _validate_vectors(payload: object, expected: int) -> list[list[float]]:
    """Check there is one non-empty vector of finite numbers per input, all the same length."""
    embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
    if not isinstance(embeddings, list):
        raise EmbeddingError("Ollama response has no 'embeddings' list")
    if len(embeddings) != expected:
        raise EmbeddingError(f"Ollama returned {len(embeddings)} vectors for {expected} inputs")

    vectors = []
    for i, vector in enumerate(embeddings):
        if not isinstance(vector, list) or not vector:
            raise EmbeddingError(f"vector {i} is empty or not a list")
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in vector):
            raise EmbeddingError(f"vector {i} contains non-numeric or non-finite values")
        vectors.append([float(x) for x in vector])
    if len({len(vector) for vector in vectors}) > 1:
        raise EmbeddingError("Ollama returned vectors with different dimensions")
    return vectors
