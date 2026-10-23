"""Splits knowledge Documents into self-contained chunks for embedding and retrieval.

Rules per source type:
    - schema:        one chunk per complete CREATE TABLE (columns, keys, foreign keys).
                     DROP, USE and CREATE DATABASE are skipped; any other statement is an
                     error, so new kinds of statements are never dropped silently.
    - documentation: one chunk per "## " section (heading + body). Text before the first
                     section is kept as its own chunk if it holds more than the "# " title.
    - example:       one chunk per "-- Example N" block: the question comments and the
                     complete SQL stay together.

Chunk ids are derived from the content's own names (table, heading, example number),
so they stay the same between runs as long as those names do not change.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from backend.app.services.rag.document_loader import Document


@dataclass(frozen=True)
class Chunk:
    """A piece of knowledge stored in the vector index."""

    id: str  # e.g. "schema:patients", "documentation:query-rules", "example:3"
    source: str  # path relative to the project root
    type: str  # "schema", "documentation" or "example"
    title: str  # table name, section heading, or "Example N"
    content: str  # full text of the chunk


class ChunkingError(ValueError):
    """Raised when a knowledge file does not have the structure the chunker expects."""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# --- schema.sql -------------------------------------------------------------------------

_CREATE_TABLE = re.compile(r"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?", re.IGNORECASE)
_SKIPPED_STATEMENT = re.compile(r"^(DROP\b|USE\b|CREATE\s+(DATABASE|SCHEMA)\b)", re.IGNORECASE)


def _split_sql_statements(sql: str) -> list[str]:
    """Split SQL on ";" outside "--" comments and quotes. Each statement keeps its ";"."""
    statements, start, i, quote = [], 0, 0, None
    while i < len(sql):
        ch = sql[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif sql.startswith("--", i):
            newline = sql.find("\n", i)
            i = len(sql) if newline == -1 else newline
            continue
        elif ch == ";":
            statements.append(sql[start : i + 1])
            start = i + 1
        i += 1
    statements.append(sql[start:])  # trailing text after the last ";" (normally only comments)
    return statements


def _strip_leading_comments(statement: str) -> str:
    """Drop blank lines and "--" comment lines that precede the statement's first keyword."""
    lines = statement.strip().splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
        lines.pop(0)
    return "\n".join(lines).strip()


def chunk_schema(doc: Document) -> list[Chunk]:
    """One chunk per complete CREATE TABLE statement."""
    chunks = []
    for raw in _split_sql_statements(doc.text):
        statement = _strip_leading_comments(raw)
        if not statement or _SKIPPED_STATEMENT.match(statement):
            continue
        match = _CREATE_TABLE.match(statement)
        if not match:
            raise ChunkingError(f"{doc.source}: unexpected statement: {statement.splitlines()[0]}")
        if not statement.endswith(";") or statement.count("(") != statement.count(")"):
            raise ChunkingError(f"{doc.source}: incomplete CREATE TABLE {match.group(1)}")
        table = match.group(1)
        chunks.append(Chunk(f"schema:{table}", doc.source, doc.type, table, statement))
    return chunks


# --- data_catalog.md --------------------------------------------------------------------

_SECTION_HEADING = re.compile(r"^## +(.+?)\s*$", re.MULTILINE)


def chunk_documentation(doc: Document) -> list[Chunk]:
    """One chunk per "## " section, plus the introduction if it has more than the title."""
    headings = list(_SECTION_HEADING.finditer(doc.text))
    chunks = []

    intro = doc.text[: headings[0].start()] if headings else doc.text
    intro_body = "\n".join(line for line in intro.splitlines() if not line.startswith("# "))
    if intro_body.strip():
        chunks.append(Chunk("documentation:introduction", doc.source, doc.type, "Introduction", intro.strip()))

    for i, heading in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(doc.text)
        title = heading.group(1)
        content = doc.text[heading.start() : end].strip()
        chunks.append(Chunk(f"documentation:{_slug(title)}", doc.source, doc.type, title, content))
    return chunks


# --- examples.sql -----------------------------------------------------------------------

_EXAMPLE_MARKER = re.compile(r"^--\s*Example\s+(\d+)\s*$", re.MULTILINE | re.IGNORECASE)


def chunk_examples(doc: Document) -> list[Chunk]:
    """One chunk per "-- Example N" block, containing its question comments and full SQL."""
    markers = list(_EXAMPLE_MARKER.finditer(doc.text))
    if not markers:
        raise ChunkingError(f"{doc.source}: no '-- Example N' markers found")
    if doc.text[: markers[0].start()].strip():
        raise ChunkingError(f"{doc.source}: text before the first example would be lost")

    chunks = []
    for i, marker in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(doc.text)
        content = doc.text[marker.start() : end].strip()
        number = marker.group(1)
        if "-- Question:" not in content or not content.endswith(";"):
            raise ChunkingError(f"{doc.source}: Example {number} needs a '-- Question:' and a SQL query ending in ';'")
        chunks.append(Chunk(f"example:{number}", doc.source, doc.type, f"Example {number}", content))
    return chunks


# --- entry point ------------------------------------------------------------------------

_CHUNKERS: dict[str, Callable[[Document], list[Chunk]]] = {
    "schema": chunk_schema,
    "documentation": chunk_documentation,
    "example": chunk_examples,
}


def chunk_documents(documents: list[Document]) -> list[Chunk]:
    """Chunk every Document. Raises ChunkingError on empty chunks or duplicate ids."""
    chunks = [chunk for doc in documents for chunk in _CHUNKERS[doc.type](doc)]
    seen = set()
    for chunk in chunks:
        if not chunk.content.strip():
            raise ChunkingError(f"empty chunk: {chunk.id}")
        if chunk.id in seen:
            raise ChunkingError(f"duplicate chunk id: {chunk.id}")
        seen.add(chunk.id)
    return chunks
