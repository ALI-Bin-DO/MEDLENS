"""Shapes for POST /export/csv.

The body is parsed by the export service itself (to enforce the size limit while reading and to
keep JSON numbers exact), so ExportRequest documents the contract for OpenAPI rather than being
used for parsing.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Cell = str | int | float | bool | None


class ExportRequest(BaseModel):
    """A table taken from a /chat answer (columns, rows, truncated). Client-supplied, not verified."""

    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(description="Column names in order; duplicates allowed; at least one.")
    rows: list[list[Cell]] = Field(description="Each row has exactly len(columns) scalar cells.")
    truncated: bool = Field(description="Copy of the answer's truncated flag; true marks the export as partial.")


class ExportError(BaseModel):
    status: Literal["invalid_request", "too_large"]
    message: str
