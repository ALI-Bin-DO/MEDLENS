"""Read-only administration endpoints for the local demo (request metrics, activity, system status, config).

NO authentication or role authorization is implemented: anyone who can reach the API (bound to
127.0.0.1) can read these. Responses contain request metadata only, never questions, SQL or rows.

    GET /admin/metrics?window=1h|24h|7d                         200 MetricsResponse
    GET /admin/activity?window=...&limit=1..100&offset=0..10000  200 ActivityResponse
    503 {"status": "unavailable", "message": ...} if the monitoring store cannot be read
    GET /admin/system   200 SystemStatusResponse (per-component status; cached briefly; never fails as a whole)
    GET /admin/config   200 AdminConfigResponse (explicit allowlist of non-secret settings)
"""

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from backend.app.models.admin_models import (
    ActivityResponse, AdminConfigResponse, MetricsResponse, SystemStatusResponse, Window,
)
from backend.app.repositories.monitoring_repository import MonitoringRepository
from backend.app.services import monitoring_service
from backend.app.services.system_status import SystemStatusService, admin_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

MAX_LIMIT = 100
MAX_OFFSET = 10000
_UNAVAILABLE = {"status": "unavailable", "message": "Monitoring data is not available right now."}


def get_monitoring(request: Request) -> MonitoringRepository:
    return request.app.state.monitoring


def get_system_status(request: Request) -> SystemStatusService:
    return request.app.state.system_status


@router.get("/system", response_model=SystemStatusResponse)
async def get_system(service: SystemStatusService = Depends(get_system_status)) -> SystemStatusResponse:
    """Independent read-only status of the API, Ollama, configured models, MySQL and the knowledge index.
    Installed or loaded models are not proof that inference works; MySQL is a connectivity check only."""
    return await service.get()


@router.get("/config", response_model=AdminConfigResponse)
def get_config(service: SystemStatusService = Depends(get_system_status)) -> AdminConfigResponse:
    """Non-secret settings useful for the dashboard (explicit allowlist)."""
    return admin_config(service.settings)


@router.get("/metrics", response_model=MetricsResponse, responses={503: {"description": "Monitoring store unavailable"}})
def get_metrics(window: Window = "24h", monitoring: MonitoringRepository = Depends(get_monitoring)):
    """Request counts, operational answer success and latency for a fixed window."""
    try:
        return monitoring_service.metrics(monitoring, window)
    except Exception:
        logger.exception("monitoring metrics could not be read")
        return JSONResponse(status_code=503, content=_UNAVAILABLE)


@router.get("/activity", response_model=ActivityResponse, responses={503: {"description": "Monitoring store unavailable"}})
def get_activity(window: Window = "24h", limit: int = Query(50, ge=1, le=MAX_LIMIT),
                 offset: int = Query(0, ge=0, le=MAX_OFFSET), monitoring: MonitoringRepository = Depends(get_monitoring)):
    """Recent /chat requests, newest first (metadata only)."""
    try:
        return monitoring_service.activity(monitoring, window, limit, offset)
    except Exception:
        logger.exception("monitoring activity could not be read")
        return JSONResponse(status_code=503, content=_UNAVAILABLE)
