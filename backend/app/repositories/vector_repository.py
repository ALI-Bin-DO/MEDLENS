"""Direct access to the Qdrant vector store in persistent local mode (files under QDRANT_PATH).

Local mode locks the storage folder: only one client, in one process, may have it open at a
time. Use VectorRepository as a context manager so the client is always closed:

    with VectorRepository() as repo:
        repo.search(vector, top_k=5, embedding_model="nomic-embed-text:v1.5")

Constructing VectorRepository does not open or create the store. Incompatible collections
(different vector size, distance or embedding model) are never deleted or recreated
automatically; a clear IndexIncompatibleError asks for a rebuild instead.
"""

import math
import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any

from qdrant_client import QdrantClient, models

from backend.app.config import Settings, get_settings
from backend.app.services.rag.chunker import Chunk

# Fixed namespace so a chunk id always maps to the same Qdrant point UUID.
_POINT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "medlens/knowledge-chunk")
_PAYLOAD_FIELDS = ("id", "source", "type", "title", "content")
_UPSERT_BATCH = 64
_SCROLL_PAGE = 256


class VectorStoreError(RuntimeError):
    """Base class for vector store problems."""


class StorageLockedError(VectorStoreError):
    """The storage folder is already open in another client or process."""


class IndexNotFoundError(VectorStoreError):
    """The collection does not exist or is empty; build_index.py has not been run."""


class IndexIncompatibleError(VectorStoreError):
    """The stored collection was built with a different vector size, distance or model."""


@dataclass(frozen=True)
class RetrievedChunk:
    """A stored chunk and its cosine similarity score to the query (higher = more similar).

    The score is a similarity, not a confidence or probability.
    """

    chunk: Chunk
    score: float


@dataclass(frozen=True)
class SyncResult:
    """What one indexing run changed."""

    points: int  # points in the collection after the run
    upserted: int
    removed: int
    created: bool  # True if the collection did not exist before


def point_id(chunk_id: str) -> str:
    """Deterministic UUID for a chunk id such as "schema:billing"."""
    return str(uuid.uuid5(_POINT_NAMESPACE, chunk_id))


class VectorRepository:
    """Stores knowledge chunks with their embeddings and finds the nearest ones."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.collection = self.settings.qdrant_collection
        self._client: QdrantClient | None = None
        # The app shares one client between chat requests and admin status probes; serialize its use.
        self.lock = threading.RLock()

    # --- lifecycle ---------------------------------------------------------------------

    def open(self) -> None:
        """Open the local storage folder, taking its lock."""
        if self._client is not None:
            raise VectorStoreError("VectorRepository is already open; reuse it instead of opening twice")
        path = self.settings.qdrant_path
        try:
            self._client = QdrantClient(path=str(path))
        except RuntimeError as exc:
            if "already accessed" in str(exc):
                raise StorageLockedError(
                    f"{path} is in use by another process (e.g. build_index or the API). "
                    "Local storage allows one process at a time; stop the other one and retry."
                ) from exc
            raise

    def close(self) -> None:
        """Close the client and release the folder lock. Safe to call more than once."""
        if self._client is not None:
            client, self._client = self._client, None
            client.close()

    def __enter__(self) -> "VectorRepository":
        self.open()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            raise VectorStoreError("VectorRepository is not open; use 'with VectorRepository() as repo:'")
        return self._client

    # --- writing -----------------------------------------------------------------------

    def sync(self, chunks: list[Chunk], vectors: list[list[float]], embedding_model: str) -> SyncResult:
        """Make the collection hold exactly these chunks.

        All current chunks are upserted first (same chunk id -> same point, so no duplicates);
        points for chunks that no longer exist are removed only after that succeeded.
        """
        size = _validate_batch(chunks, vectors)
        created = self._ensure_collection(size, embedding_model)

        points = [
            models.PointStruct(id=point_id(c.id), vector=v, payload={f: getattr(c, f) for f in _PAYLOAD_FIELDS})
            for c, v in zip(chunks, vectors)
        ]
        for start in range(0, len(points), _UPSERT_BATCH):
            self.client.upsert(self.collection, points=points[start : start + _UPSERT_BATCH], wait=True)

        current = {p.id for p in points}
        obsolete = [pid for pid in self._all_point_ids() if pid not in current]
        if obsolete:
            self.client.delete(self.collection, points_selector=models.PointIdsList(points=obsolete), wait=True)

        total = self.client.count(self.collection, exact=True).count
        return SyncResult(points=total, upserted=len(points), removed=len(obsolete), created=created)

    def _ensure_collection(self, size: int, embedding_model: str) -> bool:
        """Create the collection if missing; otherwise require it to be compatible. Returns True if created."""
        if self.client.collection_exists(self.collection):
            self._check_compatible(size, embedding_model)
            return False
        self.client.create_collection(
            self.collection,
            vectors_config=models.VectorParams(size=size, distance=models.Distance.COSINE),
            metadata={"embedding_model": embedding_model, "vector_size": size},
        )
        return True

    def _all_point_ids(self) -> list[str]:
        ids, offset = [], None
        while True:
            page, offset = self.client.scroll(
                self.collection, limit=_SCROLL_PAGE, offset=offset, with_payload=False, with_vectors=False
            )
            ids.extend(str(p.id) for p in page)
            if offset is None:
                return ids

    # --- reading -----------------------------------------------------------------------

    def search(
        self, vector: list[float], top_k: int, embedding_model: str, types: Sequence[str] | None = None
    ) -> list[RetrievedChunk]:
        """Return up to top_k chunks, most similar first, after checking the index matches.

        If types is given, only chunks of those types are searched (the filter is applied
        inside Qdrant's search, not to the results afterwards).
        """
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        with self.lock:
            self._require_collection()
            self._check_compatible(len(vector), embedding_model)
            if self.client.count(self.collection, exact=True).count == 0:
                raise IndexNotFoundError(f"Collection {self.collection!r} is empty. Run: python -m backend.scripts.build_index")
            hits = self.client.query_points(
                self.collection, query=vector, limit=top_k, with_payload=True, query_filter=_type_filter(types)
            ).points
        return [RetrievedChunk(chunk=_chunk_from_payload(hit.payload), score=hit.score) for hit in hits]

    def get_chunks_by_type(self, chunk_type: str) -> list[Chunk]:
        """Every stored chunk of one type, read from payloads (no similarity ranking), in no particular order."""
        with self.lock:
            self._require_collection()
            chunks, offset = [], None
            while True:
                page, offset = self.client.scroll(
                    self.collection, scroll_filter=_type_filter([chunk_type]), limit=_SCROLL_PAGE,
                    offset=offset, with_payload=True, with_vectors=False,
                )
                chunks.extend(_chunk_from_payload(p.payload) for p in page)
                if offset is None:
                    return chunks

    def _require_collection(self) -> None:
        if not self.client.collection_exists(self.collection):
            raise IndexNotFoundError(
                f"Collection {self.collection!r} not found in {self.settings.qdrant_path}. "
                "Run: python -m backend.scripts.build_index"
            )

    def count_by_type(self, chunk_types: Sequence[str]) -> dict[str, int]:
        """Exact point count per chunk type (read-only)."""
        with self.lock:
            self._require_collection()
            return {t: self.client.count(self.collection, count_filter=_type_filter([t]), exact=True).count
                    for t in chunk_types}

    def info(self) -> dict[str, Any]:
        """Point count and stored metadata, for previews and checks."""
        with self.lock:
            self._require_collection()
            config = self.client.get_collection(self.collection).config
            return {
                "points": self.client.count(self.collection, exact=True).count,
                "vector_size": config.params.vectors.size,
                "distance": config.params.vectors.distance.value,
                "metadata": dict(config.metadata or {}),
            }

    # --- compatibility -----------------------------------------------------------------

    def _check_compatible(self, size: int, embedding_model: str) -> None:
        """Raise IndexIncompatibleError unless vector size, distance and model all match."""
        config = self.client.get_collection(self.collection).config
        params = config.params.vectors
        stored_model = (config.metadata or {}).get("embedding_model")
        problems = []
        if not isinstance(params, models.VectorParams):
            problems.append("collection does not use a single unnamed vector")
        else:
            if params.size != size:
                problems.append(f"vector size is {params.size}, expected {size}")
            if params.distance != models.Distance.COSINE:
                problems.append(f"distance is {params.distance.value}, expected Cosine")
        if stored_model != embedding_model:
            problems.append(f"built with embedding model {stored_model!r}, current model is {embedding_model!r}")
        if problems:
            raise IndexIncompatibleError(
                f"Collection {self.collection!r} is incompatible ({'; '.join(problems)}). Rebuild required: "
                f"stop all MedLens processes, delete the collection folder under {self.settings.qdrant_path} "
                "(or set a new QDRANT_COLLECTION), then run: python -m backend.scripts.build_index"
            )


def _validate_batch(chunks: list[Chunk], vectors: list[list[float]]) -> int:
    """Check counts, unique ids, and one shared dimension of finite numbers. Returns the dimension."""
    if not chunks:
        raise ValueError("refusing to sync an empty chunk list (it would remove every stored chunk)")
    if len(chunks) != len(vectors):
        raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
    ids = [c.id for c in chunks]
    if len(set(ids)) != len(ids) or len({point_id(i) for i in ids}) != len(ids):
        raise ValueError("chunk ids must be unique")
    sizes = {len(v) for v in vectors}
    if len(sizes) != 1 or 0 in sizes:
        raise ValueError(f"all vectors must be non-empty and the same size, got sizes {sorted(sizes)}")
    for v in vectors:
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v):
            raise ValueError("vectors must contain only finite numbers")
    return sizes.pop()


def _type_filter(types: Sequence[str] | None) -> models.Filter | None:
    """Qdrant filter matching the payload field "type" against any of types (None = no filter)."""
    if types is None:
        return None
    if not types:
        raise ValueError("types must not be empty; pass None to search all types")
    return models.Filter(must=[models.FieldCondition(key="type", match=models.MatchAny(any=list(types)))])


def _chunk_from_payload(payload: dict[str, Any] | None) -> Chunk:
    if not payload or any(not isinstance(payload.get(f), str) for f in _PAYLOAD_FIELDS):
        raise IndexIncompatibleError("A stored point is missing chunk fields; rebuild the index")
    return Chunk(**{f: payload[f] for f in _PAYLOAD_FIELDS})
