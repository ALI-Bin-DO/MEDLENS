"""HTTP layer for CSV export: POST /export/csv.

Exports the client-supplied table only (no SQL, model, index or database; nothing stored).
The body is read in chunks and refused with 413 as soon as it exceeds EXPORT_MAX_BYTES.

    200  text/csv attachment; headers: Content-Disposition (application-generated filename),
         X-MedLens-Partial (true/false), X-MedLens-Row-Count, X-MedLens-Neutralized-Cells
    413  {"status": "too_large", ...}        body, columns, rows or a cell over its limit
    422  {"status": "invalid_request", ...}  not JSON, wrong shape, row width, nested value, ...
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from backend.app.config import get_settings
from backend.app.models.export_models import ExportError, ExportRequest
from backend.app.services.csv_export import ExportRequestError, parse_export_request, render_csv

router = APIRouter(prefix="/export", tags=["export"])

PARTIAL_HEADER = "X-MedLens-Partial"
EXPOSED_HEADERS = ["Content-Disposition", PARTIAL_HEADER, "X-MedLens-Row-Count", "X-MedLens-Neutralized-Cells"]


def _error(exc: ExportRequestError) -> JSONResponse:
    status = "too_large" if exc.status_code == 413 else "invalid_request"
    return JSONResponse(status_code=exc.status_code, content=ExportError(status=status, message=str(exc)).model_dump())


async def _read_body(request: Request, limit: int) -> bytes:
    """Read at most limit bytes; refuse early from Content-Length, and again while streaming."""
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit():
            raise ExportRequestError("Invalid Content-Length header.")
        if int(declared) > limit:
            raise ExportRequestError(f"Request body is larger than {limit} bytes.", 413)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise ExportRequestError(f"Request body is larger than {limit} bytes.", 413)
    return bytes(body)


@router.post(
    "/csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "CSV file (UTF-8 with BOM)"},
               413: {"model": ExportError}, 422: {"model": ExportError}},
    openapi_extra={"requestBody": {"required": True,
                                   "content": {"application/json": {"schema": ExportRequest.model_json_schema()}}}},
)
async def export_csv(request: Request) -> Response:
    """Download the supplied table as CSV. The data is client-supplied and not verified against MySQL."""
    settings = get_settings()
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    try:
        if content_type != "application/json":
            raise ExportRequestError("Content-Type must be application/json.")
        table = parse_export_request(await _read_body(request, settings.export_max_bytes), settings)
    except ExportRequestError as exc:
        return _error(exc)

    export = render_csv(table)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"medlens-export-{stamp}{'-partial' if table.truncated else ''}.csv"
    return Response(
        content=export.content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            PARTIAL_HEADER: "true" if table.truncated else "false",
            "X-MedLens-Row-Count": str(export.row_count),
            "X-MedLens-Neutralized-Cells": str(export.neutralized_cells),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
