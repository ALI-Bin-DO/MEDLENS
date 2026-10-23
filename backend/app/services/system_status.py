"""Read-only status of MedLens's components for GET /admin/system, and the GET /admin/config allowlist.

Each component is probed independently (one failure never hides the others), in a worker thread,
with a per-call timeout (SYSTEM_PROBE_TIMEOUT_SECONDS) and an overall per-component bound:

    api      the process answering this request
    ollama   GET /api/version; installed models GET /api/tags; loaded models GET /api/ps;
             embedding dimension POST /api/show (metadata only). Nothing is generated, embedded,
             loaded, downloaded or reconfigured, so "installed"/"loaded" never mean "inference works".
    mysql    the read-only account runs the fixed statement SELECT 1 (connectivity only)
    index    the application's own open Qdrant client (never a second client): collection, points,
             counts by type, stored model and dimension, and whether the stored schema chunks still
             equal database/schema.sql. Read-only.

Snapshots are cached for SYSTEM_STATUS_CACHE_SECONDS; concurrent refreshes share one probe run.
There is no background polling: probes run only when the endpoint is called and the cache is stale.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone

import mysql.connector
from mysql.connector import errorcode
from starlette.concurrency import run_in_threadpool

from backend.app.config import MissingSettingError, Settings
from backend.app.models.admin_models import (
    AdminConfigResponse, ApiStatus, IndexStatus, ModelStatus, ModelsStatus, MySQLStatus, OllamaStatus,
    SystemComponents, SystemStatusResponse,
)
from backend.app.repositories.mysql_repository import MySQLRepository
from backend.app.repositories.vector_repository import IndexNotFoundError, VectorRepository, VectorStoreError
from backend.app.services import sql_correction
from backend.app.services.ollama_client import OllamaError, get_json, post_json
from backend.app.services.rag.context_builder import STRATEGY, expected_schema_chunks

logger = logging.getLogger(__name__)
CHUNK_TYPES = ("schema", "documentation", "example")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- probes (blocking; run in worker threads) ---------------------------------------------------

def probe_ollama(settings: Settings) -> tuple[OllamaStatus, ModelsStatus, int | None]:
    """Ollama service, configured models and the embedding model's dimension (if Ollama says)."""
    timeout = settings.system_probe_timeout_seconds
    base = settings.ollama_base_url
    configured = {"sql": settings.ollama_sql_model, "embedding": settings.ollama_embedding_model}

    def unknown_models(reason: str) -> ModelsStatus:
        return ModelsStatus(**{role: ModelStatus(role=role, name=name,
                                                  status="not_configured" if not name else "unknown",
                                                  reason="not_configured" if not name else reason)
                               for role, name in configured.items()})

    try:
        version = get_json(base, "/api/version", timeout).get("version")
    except MissingSettingError:
        return OllamaStatus(status="not_configured", reason="not_configured"), unknown_models("ollama_not_configured"), None
    except OllamaError as exc:
        reason = "timeout" if exc.kind == "timeout" else "unreachable"
        return OllamaStatus(status="unavailable", reason=reason), unknown_models(f"ollama_{reason}"), None

    try:
        installed = {m.get("name"): m for m in get_json(base, "/api/tags", timeout).get("models", [])}
        loaded = [m.get("name") for m in get_json(base, "/api/ps", timeout).get("models", [])]
    except (OllamaError, AttributeError, TypeError):
        return (OllamaStatus(status="degraded", reason="model_list_unavailable", version=version),
                unknown_models("model_list_unavailable"), None)

    def model(role: str, name: str | None) -> ModelStatus:
        if not name:
            return ModelStatus(role=role, status="not_configured", reason="not_configured")
        entry = installed.get(name) or installed.get(f"{name}:latest")
        is_loaded = any(n in (name, f"{name}:latest") for n in loaded)
        if entry is None:
            return ModelStatus(role=role, name=name, status="unavailable", reason="not_installed",
                               installed=False, loaded=is_loaded)
        details = entry.get("details") or {}
        return ModelStatus(role=role, name=name, status="ok",
                           reason="installed_loaded" if is_loaded else "installed_not_loaded",
                           installed=True, loaded=is_loaded, parameter_size=details.get("parameter_size"),
                           quantization=details.get("quantization_level"))

    models = ModelsStatus(sql=model("sql", configured["sql"]), embedding=model("embedding", configured["embedding"]))
    expected_size = None
    if models.embedding.installed:
        try:
            info = post_json(base, "/api/show", {"model": configured["embedding"]}, timeout).get("model_info", {})
            sizes = {v for k, v in info.items() if k.endswith(".embedding_length") and isinstance(v, int)}
            expected_size = sizes.pop() if len(sizes) == 1 else None
        except (OllamaError, AttributeError):
            expected_size = None
    return OllamaStatus(status="ok", version=version, loaded_models=[n for n in loaded if n]), models, expected_size


def probe_mysql(repository: MySQLRepository, timeout_seconds: int) -> MySQLStatus:
    started = time.perf_counter()
    try:
        repository.check_connectivity(timeout_seconds)
    except MissingSettingError:
        return MySQLStatus(status="not_configured", reason="readonly_credentials_missing")
    except mysql.connector.Error as exc:
        errno = getattr(exc, "errno", None)
        reason = {errorcode.ER_ACCESS_DENIED_ERROR: "authentication_failed", errorcode.ER_BAD_DB_ERROR: "database_missing",
                  errorcode.CR_CONN_HOST_ERROR: "unreachable", errorcode.CR_CONNECTION_ERROR: "unreachable",
                  errorcode.CR_SERVER_LOST: "timeout"}.get(errno, "error")
        return MySQLStatus(status="unavailable", reason=reason)
    except Exception:
        logger.exception("MySQL status probe failed")
        return MySQLStatus(status="unavailable", reason="error")
    return MySQLStatus(status="ok", latency_ms=round((time.perf_counter() - started) * 1000, 1))


def probe_index(vectors: VectorRepository | None, settings: Settings) -> IndexStatus:
    """Read-only checks through the application's existing client; never opens another one."""
    base = dict(collection=settings.qdrant_collection, expected_embedding_model=settings.ollama_embedding_model)
    if vectors is None:
        return IndexStatus(status="unavailable", reason="not_open", **base)
    try:
        info = vectors.info()
        counts = vectors.count_by_type(CHUNK_TYPES)
        stored_schema = {c.id: c for c in vectors.get_chunks_by_type("schema")}
    except IndexNotFoundError:
        return IndexStatus(status="unavailable", reason="collection_missing", **base)
    except VectorStoreError:
        return IndexStatus(status="unavailable", reason="not_open", **base)
    except Exception:
        logger.exception("index status probe failed")
        return IndexStatus(status="unavailable", reason="error", **base)

    try:
        schema_fresh = stored_schema == {c.id: c for c in expected_schema_chunks()}
    except Exception:
        logger.exception("schema freshness could not be checked")
        schema_fresh = None
    stored_model = (info.get("metadata") or {}).get("embedding_model")
    expected_model = settings.ollama_embedding_model
    status = IndexStatus(
        status="ok", points=info["points"], counts_by_type=counts, vector_size=info["vector_size"],
        stored_embedding_model=stored_model,
        embedding_model_matches=None if not expected_model else stored_model == expected_model,
        schema_matches_schema_sql=schema_fresh, **base,
    )
    if info["points"] == 0:
        status.status, status.reason = "unavailable", "empty"
    elif status.embedding_model_matches is False:
        status.status, status.reason = "degraded", "embedding_model_mismatch"
    elif schema_fresh is False:
        status.status, status.reason = "degraded", "schema_changed_rebuild_index"
    return status


# --- snapshot with cache and coalescing ---------------------------------------------------------

def probe_bound(settings: Settings) -> float:
    """Overall seconds allowed per component: it may make several calls, each with its own timeout."""
    return settings.system_probe_timeout_seconds * 3 + 1


class SystemStatusService:
    def __init__(self, settings: Settings, vectors: Callable[[], VectorRepository | None], mysql: MySQLRepository,
                 version: str, started_epoch: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self._vectors = vectors
        self._mysql = mysql
        self._version = version
        self._started_epoch = started_epoch
        self._clock = clock
        self._snapshot: SystemStatusResponse | None = None
        self._snapshot_at = 0.0
        self._lock: asyncio.Lock | None = None

    def _fresh(self) -> bool:
        return self._snapshot is not None and self._clock() - self._snapshot_at < self.settings.system_status_cache_seconds

    async def get(self) -> SystemStatusResponse:
        if self._fresh():
            return self._snapshot
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:  # concurrent callers wait here and reuse the same new snapshot
            if not self._fresh():
                self._snapshot = await self._probe_all()
                self._snapshot_at = self._clock()
        return self._snapshot

    async def _probe_all(self) -> SystemStatusResponse:
        s = self.settings
        bound = probe_bound(s)

        async def bounded(fn, *args, fallback):
            try:
                return await asyncio.wait_for(run_in_threadpool(fn, *args), timeout=bound)
            except asyncio.TimeoutError:
                return fallback()
            except Exception:
                logger.exception("status probe failed unexpectedly")
                return fallback(reason="error")

        def ollama_fallback(reason="probe_timeout"):
            models = {role: ModelStatus(role=role, name=name, status="not_configured" if not name else "unknown",
                                        reason="not_configured" if not name else reason)
                      for role, name in (("sql", s.ollama_sql_model), ("embedding", s.ollama_embedding_model))}
            return OllamaStatus(status="unknown", reason=reason), ModelsStatus(**models), None

        checked_at = _now_iso()
        (ollama, models, expected_size), mysql_status, index = await asyncio.gather(
            bounded(probe_ollama, s, fallback=ollama_fallback),
            bounded(probe_mysql, self._mysql, s.system_probe_timeout_seconds,
                    fallback=lambda reason="probe_timeout": MySQLStatus(status="unknown", reason=reason)),
            bounded(probe_index, self._vectors(), s,
                    fallback=lambda reason="probe_timeout": IndexStatus(status="unknown", reason=reason,
                                                                        collection=s.qdrant_collection)),
        )
        if index.vector_size is not None and expected_size is not None:
            index.expected_vector_size = expected_size
            index.vector_size_matches = index.vector_size == expected_size
            if not index.vector_size_matches and index.status == "ok":
                index.status, index.reason = "degraded", "vector_size_mismatch"
        api = ApiStatus(version=self._version,
                        started_at=datetime.fromtimestamp(self._started_epoch, timezone.utc).isoformat(),
                        uptime_seconds=round(time.time() - self._started_epoch, 1))
        return SystemStatusResponse(checked_at=checked_at, cache_ttl_seconds=s.system_status_cache_seconds,
                                    components=SystemComponents(api=api, ollama=ollama, models=models,
                                                                mysql=mysql_status, index=index))


def admin_config(settings: Settings) -> AdminConfigResponse:
    """Explicit allowlist; never serialize Settings itself."""
    return AdminConfigResponse(
        models={"sql_model": settings.ollama_sql_model, "embedding_model": settings.ollama_embedding_model},
        rag={"context_strategy": STRATEGY, "retrieved_top_k": settings.rag_context_top_k},
        sql_generation={"num_ctx": settings.ollama_sql_num_ctx, "num_predict": settings.ollama_sql_num_predict,
                        "generation_timeout_seconds": settings.ollama_generation_timeout_seconds,
                        "max_corrections": sql_correction.MAX_CORRECTIONS},
        query_execution={"max_execution_ms": settings.mysql_max_execution_ms, "row_cap": settings.mysql_row_cap},
        charts={"max_points": settings.chart_max_points},
        monitoring={"retention_days": settings.monitoring_retention_days, "max_events": settings.monitoring_max_events},
    )
