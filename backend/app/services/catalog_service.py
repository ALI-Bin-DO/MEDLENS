"""Builds the data catalog from the source files, read as text and never executed.

    database/schema.sql            authoritative structure: tables, columns, types, nullability,
                                   primary/unique keys, indexes, foreign keys, ENUM values
    knowledge_base/data_catalog.md descriptions only (table purpose, grain, column meanings)

Independent of Ollama, Qdrant and MySQL. CREATE TABLE statements are extracted with the same
chunker used for RAG and parsed with SQLGlot; DROP / CREATE DATABASE / grants are never read.

Caching: the catalog is parsed on the first request and then kept in memory until the
application restarts, so edits to the source files need a restart. A failed load is not cached:
the next request tries again.
"""

import logging
import re
import threading
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.app.config import DATA_CATALOG_FILE, SCHEMA_FILE
from backend.app.models.catalog_models import (
    CatalogResponse, ColumnInfo, ColumnReference, ForeignKeyInfo, IncomingReference, KeyInfo, TableDetail, TableSummary,
)
from backend.app.services.rag.chunker import ChunkingError, chunk_documentation, chunk_schema
from backend.app.services.rag.document_loader import Document

logger = logging.getLogger(__name__)
DIALECT = "mysql"


class CatalogUnavailableError(RuntimeError):
    """A catalog source is missing or invalid. The message is for logs; clients get a fixed text."""


class TableNotFoundError(LookupError):
    """No table with that exact name."""


class CatalogService:
    def __init__(self, schema_file: Path = SCHEMA_FILE, catalog_file: Path = DATA_CATALOG_FILE) -> None:
        self._schema_file = schema_file
        self._catalog_file = catalog_file
        self._lock = threading.Lock()
        self._tables: dict[str, TableDetail] | None = None

    def list_tables(self) -> CatalogResponse:
        tables = self._load()
        return CatalogResponse(tables=[TableSummary(**t.model_dump(include=set(TableSummary.model_fields)))
                                       for t in tables.values()])

    def get_table(self, name: str) -> TableDetail:
        """Look the name up in the parsed catalog (exact match); user input never becomes a path."""
        table = self._load().get(name)
        if table is None:
            raise TableNotFoundError(name)
        return table

    def _load(self) -> dict[str, TableDetail]:
        with self._lock:
            if self._tables is None:
                self._tables = build_catalog(self._schema_file, self._catalog_file)
            return self._tables


def build_catalog(schema_file: Path, catalog_file: Path) -> dict[str, TableDetail]:
    """Parse both sources into {table name: TableDetail}, in schema.sql order."""
    try:
        schema_chunks = chunk_schema(_read(schema_file, "schema"))
        doc_chunks = chunk_documentation(_read(catalog_file, "documentation"))
    except (OSError, UnicodeDecodeError, ValueError, ChunkingError) as exc:
        raise CatalogUnavailableError(f"cannot read catalog sources: {type(exc).__name__}: {exc}") from exc
    if not schema_chunks:
        raise CatalogUnavailableError("schema.sql contains no CREATE TABLE statements")

    docs = {c.title: _parse_doc_section(c.content) for c in doc_chunks}
    tables: dict[str, TableDetail] = {}
    for chunk in schema_chunks:
        try:
            create = sqlglot.parse_one(chunk.content, read=DIALECT)
        except SqlglotError as exc:
            raise CatalogUnavailableError(f"cannot parse {chunk.id}: {exc}") from exc
        table = _table_from_create(create, docs.get(chunk.title, {}))
        if table.name in tables:
            raise CatalogUnavailableError(f"table {table.name} is defined twice")
        tables[table.name] = table

    _link_relationships(tables)
    return tables


def _read(path: Path, doc_type: str) -> Document:
    """Read a source file as UTF-8 text (never executed)."""
    return Document(source=path.name, type=doc_type, text=path.read_text(encoding="utf-8"))


def _table_from_create(create: exp.Expression, doc: dict) -> TableDetail:
    schema = create.this if isinstance(create, exp.Create) else None
    if not isinstance(schema, exp.Schema) or not isinstance(schema.this, exp.Table):
        raise CatalogUnavailableError("unexpected CREATE TABLE structure")
    name = schema.this.name

    column_defs = [e for e in schema.expressions if isinstance(e, exp.ColumnDef)]
    primary_key: list[str] = []
    uniques: list[KeyInfo] = []
    indexes: list[KeyInfo] = []
    foreign_keys: list[ForeignKeyInfo] = []
    for e in schema.expressions:
        if isinstance(e, exp.PrimaryKey):
            primary_key = [c.name for c in e.expressions]
        elif isinstance(e, exp.UniqueColumnConstraint) and isinstance(e.this, exp.Schema):
            uniques.append(KeyInfo(name=e.this.this.name if e.this.this else None, columns=[c.name for c in e.this.expressions]))
        elif isinstance(e, exp.IndexColumnConstraint):
            indexes.append(KeyInfo(name=e.this.name if e.this else None, columns=[c.name for c in e.expressions]))
        elif isinstance(e, exp.Constraint):
            for fk in e.expressions:
                if isinstance(fk, exp.ForeignKey) and fk.args.get("reference") is not None:
                    ref = fk.args["reference"].this
                    foreign_keys.append(ForeignKeyInfo(name=e.this.name if e.this else None,
                                                       columns=[c.name for c in fk.expressions],
                                                       references_table=ref.this.name,
                                                       references_columns=[c.name for c in ref.expressions]))

    column_docs = doc.get("columns", {})
    columns = []
    for cd in column_defs:
        kind = cd.args["kind"]
        is_enum = kind.this == exp.DataType.Type.ENUM
        roles = []
        if cd.name in primary_key:
            roles.append("primary_key")
        if any(u.columns == [cd.name] for u in uniques):
            roles.append("unique")
        elif any(cd.name in u.columns for u in uniques):
            roles.append("unique_part")
        single_fk = next((fk for fk in foreign_keys if fk.columns == [cd.name]), None)
        if single_fk or any(cd.name in fk.columns for fk in foreign_keys):
            roles.append("foreign_key")
        if any(cd.name in i.columns for i in indexes):
            roles.append("indexed")
        columns.append(ColumnInfo(
            name=cd.name,
            description=column_docs.get(cd.name),
            data_type="ENUM" if is_enum else kind.sql(DIALECT),
            allowed_values=[v.name for v in kind.expressions] if is_enum else None,
            nullable=_nullable(cd, primary_key),
            key_roles=roles,
            references=ColumnReference(table=single_fk.references_table, column=single_fk.references_columns[0])
            if single_fk else None,
        ))

    unknown = sorted(set(primary_key + [c for k in uniques + indexes for c in k.columns]) - {c.name for c in columns})
    if unknown:
        raise CatalogUnavailableError(f"{name}: key refers to unknown column(s) {unknown}")
    return TableDetail(name=name, display_name=name.replace("_", " ").title(), description=doc.get("purpose"),
                       grain=doc.get("grain"), column_count=len(columns), primary_key=primary_key, columns=columns,
                       foreign_keys=foreign_keys, unique_constraints=uniques, indexes=indexes)


def _nullable(column: exp.ColumnDef, primary_key: list[str]) -> bool:
    """MySQL: NOT NULL -> False; explicit NULL or no constraint -> True; primary key columns are NOT NULL."""
    if column.name in primary_key:
        return False
    for c in column.constraints:
        if isinstance(c.kind, exp.NotNullColumnConstraint):
            return bool(c.kind.args.get("allow_null"))
    return True


def _link_relationships(tables: dict[str, TableDetail]) -> None:
    for table in tables.values():
        for fk in table.foreign_keys:
            target = tables.get(fk.references_table)
            if target is None or not set(fk.references_columns) <= {c.name for c in target.columns}:
                raise CatalogUnavailableError(f"{table.name}.{fk.name}: references unknown {fk.references_table}{fk.references_columns}")
            if not set(fk.columns) <= {c.name for c in table.columns}:
                raise CatalogUnavailableError(f"{table.name}.{fk.name}: unknown local column(s) {fk.columns}")
            target.referenced_by.append(IncomingReference(name=fk.name, from_table=table.name, from_columns=fk.columns,
                                                          to_columns=fk.references_columns))


_FIELD = re.compile(r"^(Purpose|Grain):\s*(.+)$", re.MULTILINE)
_COLUMN_LINE = re.compile(r"^-\s*([A-Za-z_][A-Za-z0-9_]*):\s*(.+)$")


def _parse_doc_section(content: str) -> dict:
    """Purpose, Grain and the "Columns:" list of one "## table" section of data_catalog.md."""
    doc: dict = {key.lower(): value.strip() for key, value in _FIELD.findall(content)}
    columns: dict[str, str] = {}
    in_columns = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "Columns:":
            in_columns = True
        elif in_columns and (match := _COLUMN_LINE.match(stripped)):
            columns[match.group(1)] = match.group(2).strip()
        elif in_columns and stripped and not stripped.startswith("-"):
            in_columns = False  # next heading such as "Relationships:"
    doc["columns"] = columns
    return doc
