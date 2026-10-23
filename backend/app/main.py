"""FastAPI entry point. Run from the project root with ONE worker, bound to localhost:

    python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1

Startup opens the local Qdrant storage once (one application-owned client, closed on shutdown).
It does not contact Ollama or MySQL, build any index, or download models. While the API runs it
holds the Qdrant storage lock: stop it before CLI commands that open the same storage
(build_index, preview_retrieval, preview_context, preview_sql, preview_answer, run_dev_eval).
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from backend.app.config import Settings, get_settings
from backend.app.controllers import admin_controller, catalog_controller, chat_controller, export_controller
from backend.app.models.chat_models import ChatResponse
from backend.app.repositories.monitoring_repository import MonitoringRepository
from backend.app.repositories.mysql_repository import MySQLRepository
from backend.app.repositories.vector_repository import StorageLockedError, VectorRepository
from backend.app.services.catalog_service import CatalogService
from backend.app.services.chat_service import ChatService
from backend.app.services.conversation_store import ConversationStore
from backend.app.services.inference_gate import InferenceGate
from backend.app.services.monitoring_service import build_event, record_safely
from backend.app.services.system_status import SystemStatusService

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, open_vector_store: bool = True,
               monitoring: MonitoringRepository | None = None) -> FastAPI:
    """Build the app. Tests pass open_vector_store=False, a temporary monitoring store, and
    override the dependencies."""
    settings = settings or get_settings()
    monitoring = monitoring or MonitoringRepository(settings.monitoring_db_path, settings.monitoring_retention_days,
                                                    settings.monitoring_max_events)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.gate = InferenceGate(settings.chat_max_waiting_requests)
        try:
            await run_in_threadpool(monitoring.prune)  # retention cleanup at startup
        except Exception:
            logger.exception("monitoring retention cleanup failed; continuing without it")
        vectors = None
        if open_vector_store:
            vectors = VectorRepository(settings)
            try:
                vectors.open()
            except StorageLockedError:
                logger.error("Qdrant storage is locked by another process; data questions return 503 until restart")
                vectors = None
        # Routing and fixed replies work without the index; data questions need it.
        app.state.chat_service = ChatService(settings, vectors)
        try:
            yield
        finally:
            if vectors is not None:
                vectors.close()

    app = FastAPI(title="MedLens", version="0.1.0", lifespan=lifespan)
    app.state.catalog_service = CatalogService()  # parsed lazily on first request, cached until restart
    app.state.monitoring = monitoring
    app.state.conversations = ConversationStore(settings.conversation_ttl_seconds, settings.conversation_max_count,
                                                settings.conversation_max_turns)  # in memory only
    # Status probes reuse the app's own Qdrant client (opened in lifespan) and never open another.
    app.state.system_status = SystemStatusService(
        settings, lambda: getattr(getattr(app.state, "chat_service", None), "vectors", None),
        MySQLRepository(settings), app.version, time.time())
    if settings.cors_allowed_origins:  # explicit origins only; never "*"
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_allowed_origins),
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
                           expose_headers=export_controller.EXPOSED_HEADERS)
    app.include_router(chat_controller.router)
    app.include_router(catalog_controller.router)
    app.include_router(export_controller.router)
    app.include_router(admin_controller.router)

    @app.middleware("http")
    async def monitor_chat(request: Request, call_next):
        """One metadata-only monitoring event per POST /chat, written after the response is sent.
        Total duration uses a monotonic clock. Recording failures never affect the response."""
        if request.method != "POST" or request.url.path.rstrip("/") != "/chat":
            return await call_next(request)
        request_id, started_epoch, started = uuid.uuid4().hex, time.time(), time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            event = build_event(request_id, started_epoch, time.perf_counter() - started, 500, None)
            await run_in_threadpool(record_safely, monitoring, event)
            raise
        try:
            body, trace = getattr(request.state, "chat_outcome", (None, None))
            event = build_event(request_id, started_epoch, time.perf_counter() - started, response.status_code, body, trace)
            response.headers["X-Request-ID"] = request_id
            if response.background is None:
                response.background = BackgroundTask(record_safely, monitoring, event)
            else:
                await run_in_threadpool(record_safely, monitoring, event)
        except Exception:
            logger.exception("monitoring event could not be prepared; response unaffected")
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Bad /chat bodies get the normal ChatResponse shape; other routes keep FastAPI's format."""
        if request.url.path.rstrip("/") != "/chat":
            return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})
        problems = "; ".join(str(e.get("msg", "invalid value")).removeprefix("Value error, ") for e in exc.errors())
        return chat_controller.respond(ChatResponse(status="invalid_request", message=f"Invalid request: {problems}"),
                                       request)

    @app.exception_handler(NotImplementedError)
    async def not_implemented_handler(request: Request, exc: NotImplementedError) -> JSONResponse:
        """Report unfinished features as 501 instead of a generic 500."""
        return JSONResponse(status_code=501, content={"detail": f"Not implemented yet: {exc}"})

    @app.get("/health")
    def health() -> dict[str, str]:
        """The API process is running. Does NOT check Qdrant, Ollama or MySQL readiness."""
        return {"status": "ok"}

    return app


app = create_app()
