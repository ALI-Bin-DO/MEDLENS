"""Reads the knowledge sources as whole UTF-8 text files for the RAG index.

Sources:
    - database/schema.sql            -> type "schema" (the single schema source; read, never executed)
    - knowledge_base/data_catalog.md -> type "documentation"
    - knowledge_base/examples.sql    -> type "example"

Splitting into chunks, and leaving out DROP / USE / CREATE DATABASE, is done by chunker.py.
"""

from dataclasses import dataclass
from pathlib import Path

from backend.app.config import DATA_CATALOG_FILE, EXAMPLES_FILE, PROJECT_ROOT, SCHEMA_FILE

# (file, type) for every knowledge source, in index order.
KNOWLEDGE_SOURCES: list[tuple[Path, str]] = [
    (SCHEMA_FILE, "schema"),
    (DATA_CATALOG_FILE, "documentation"),
    (EXAMPLES_FILE, "example"),
]


@dataclass(frozen=True)
class Document:
    """The full text of one knowledge source, before chunking."""

    source: str  # path relative to the project root, with "/" separators
    type: str  # "schema", "documentation" or "example"
    text: str


def load_document(path: Path, doc_type: str) -> Document:
    """Read one source file as UTF-8 (line endings normalized to "\\n")."""
    return Document(
        source=path.resolve().relative_to(PROJECT_ROOT).as_posix(),
        type=doc_type,
        text=path.read_text(encoding="utf-8"),
    )


def load_documents() -> list[Document]:
    """Read every knowledge source. Paths come from config, not the terminal's cwd."""
    return [load_document(path, doc_type) for path, doc_type in KNOWLEDGE_SOURCES]
