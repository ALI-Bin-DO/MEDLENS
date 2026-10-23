"""Pydantic shapes for the data catalog (GET /catalog/tables and /catalog/tables/{table_name}).

Structure (names, types, nullability, keys, ENUM values, relationships) comes from
database/schema.sql; descriptions come from knowledge_base/data_catalog.md and are null when
not documented. No row counts, no data values, no file paths, no SQL.
"""

from typing import Literal

from pydantic import BaseModel, Field

KeyRole = Literal["primary_key", "unique", "unique_part", "foreign_key", "indexed"]


class TableSummary(BaseModel):
    name: str = Field(description="Stable table name, as used in SQL and in /catalog/tables/{table_name}.")
    display_name: str = Field(description="Readable name derived from the table name.")
    description: str | None = Field(None, description="Purpose from data_catalog.md, or null if undocumented.")
    grain: str | None = Field(None, description="What one row represents, from data_catalog.md, or null.")
    column_count: int


class CatalogResponse(BaseModel):
    """All tables the user can ask about, in schema.sql order."""

    tables: list[TableSummary]


class ColumnReference(BaseModel):
    table: str
    column: str


class ColumnInfo(BaseModel):
    name: str
    description: str | None = Field(None, description="From data_catalog.md, or null if undocumented.")
    data_type: str = Field(description='MySQL type as declared, e.g. "VARCHAR(10)", "DECIMAL(10, 2)", "ENUM".')
    allowed_values: list[str] | None = Field(None, description="ENUM values in declared order; null for other types.")
    nullable: bool
    key_roles: list[KeyRole] = Field(default_factory=list, description=(
        "primary_key; unique (single-column unique constraint); unique_part (part of a composite unique "
        "constraint); foreign_key; indexed (part of a non-unique index)."))
    references: ColumnReference | None = Field(None, description="Referenced column, for single-column foreign keys.")


class ForeignKeyInfo(BaseModel):
    name: str | None = None
    columns: list[str]
    references_table: str
    references_columns: list[str]


class IncomingReference(BaseModel):
    """Another table's foreign key pointing at this table."""

    name: str | None = None
    from_table: str
    from_columns: list[str]
    to_columns: list[str]


class KeyInfo(BaseModel):
    """A named unique constraint or non-unique index; composite keys keep all columns in order."""

    name: str | None = None
    columns: list[str]


class TableDetail(TableSummary):
    primary_key: list[str]
    columns: list[ColumnInfo]
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list, description="This table's outgoing references.")
    referenced_by: list[IncomingReference] = Field(default_factory=list)
    unique_constraints: list[KeyInfo] = Field(default_factory=list)
    indexes: list[KeyInfo] = Field(default_factory=list, description="Non-unique indexes only.")


class CatalogError(BaseModel):
    status: Literal["not_found", "unavailable"]
    message: str
