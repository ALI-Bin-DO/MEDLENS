"""HTTP layer for the data catalog: thin adapters over CatalogService.

Works without Ollama, Qdrant or MySQL; reads only the parsed source files (never SQL execution).
    GET /catalog/tables               200 CatalogResponse | 503 CatalogError
    GET /catalog/tables/{table_name}  200 TableDetail     | 404 CatalogError | 503 CatalogError
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from backend.app.models.catalog_models import CatalogError, CatalogResponse, TableDetail
from backend.app.services.catalog_service import CatalogService, CatalogUnavailableError, TableNotFoundError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/catalog", tags=["catalog"])

_UNAVAILABLE = "The data catalog is not available right now."


def get_catalog_service(request: Request) -> CatalogService:
    return request.app.state.catalog_service


def _error(status_code: int, status: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=CatalogError(status=status, message=message).model_dump())


@router.get("/tables", response_model=CatalogResponse, responses={503: {"model": CatalogError}})
def list_tables(service: CatalogService = Depends(get_catalog_service)):
    """The tables a user can ask about, with descriptions. No row counts."""
    try:
        return service.list_tables()
    except CatalogUnavailableError:
        logger.exception("data catalog could not be loaded")
        return _error(503, "unavailable", _UNAVAILABLE)


@router.get("/tables/{table_name}", response_model=TableDetail,
            responses={404: {"model": CatalogError}, 503: {"model": CatalogError}})
def get_table(table_name: str, service: CatalogService = Depends(get_catalog_service)):
    """One table's columns, types, keys, ENUM values and relationships."""
    try:
        return service.get_table(table_name)
    except TableNotFoundError:
        return _error(404, "not_found", "No table with that name. GET /catalog/tables lists the available tables.")
    except CatalogUnavailableError:
        logger.exception("data catalog could not be loaded")
        return _error(503, "unavailable", _UNAVAILABLE)
