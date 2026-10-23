"""Decides whether a generated SQL query may be executed, by parsing it (SQLGlot, MySQL dialect)
and inspecting the whole syntax tree. Offline: it never connects to MySQL.

Fail-closed: only a deliberately small subset of SELECT is accepted, and anything not
explicitly allowed is rejected with a code and a reason. Nothing is repaired or rewritten;
an accepted query is returned exactly as given.

Supported (v1):
    - exactly one SELECT statement (one trailing ";" is fine)
    - columns, *, table.*, aliases, DISTINCT
    - FROM one table plus explicit [INNER] JOIN / LEFT [OUTER] JOIN / RIGHT [OUTER] JOIN ... ON ...
    - WHERE, GROUP BY, HAVING, ORDER BY, LIMIT n [OFFSET m] / LIMIT m, n with integer literals
    - comparisons, AND/OR/NOT, IN (value list), BETWEEN, LIKE, IS [NOT] NULL, arithmetic, CASE, IF, CAST
    - functions in ALLOWED_FUNCTIONS (aggregates, ROUND, COALESCE, date helpers, ...)
Not supported (v1), rejected clearly: subqueries, CTEs (WITH), UNION/INTERSECT/EXCEPT,
window functions, comma/CROSS/NATURAL/STRAIGHT joins, JOIN ... USING, index hints,
GROUP BY ... WITH ROLLUP, three-part column names, any comment, double-quoted strings,
backslashes.

Always rejected: writes/DDL/CALL/SET/SHOW and other non-SELECT statements, multiple
statements, INTO (OUTFILE/DUMPFILE/variables), FOR UPDATE / LOCK IN SHARE MODE, user/system
variables and assignments, placeholders, SLEEP/BENCHMARK/LOAD_FILE/GET_LOCK and every function
not on the allowlist, executable /*! */ comments and /*+ */ hints, tables outside database/schema.sql,
databases other than medlens, unknown or ambiguous columns.

Join keys (relationship check). Key columns come from database/schema.sql: a PRIMARY KEY column
is its own key, and a FOREIGN KEY column takes the key it references (billing.treatment_id ->
treatments.treatment_id). For every "=" between columns of two different table references (in ON,
WHERE or HAVING):
    - both are key columns: they must have the same key (appointments.patient_id = billing.patient_id
      is fine, both reference patients.patient_id; appointments.appointment_id = billing.treatment_id
      is rejected as invalid_join_key);
    - exactly one is a key column: rejected (a key compared with an unrelated value);
    - neither is a key column: allowed, because the check cannot establish anything about it.
Every JOIN must also be linked by at least one key equality in its ON clause (combined with AND)
between the joined table and a table joined before it; otherwise join_without_key, since the
relationship cannot be established (this also refuses joins only on dates or names). The check does
not prove that the joins answer the question, and does not detect totals duplicated by joining
several one-to-many tables; documentation and review cover that.

Limits of this layer: passing validation does NOT mean the query answers the user's question
correctly, or that it is cheap to run (a valid join can still be slow or return many rows).
It also does not check GROUP BY semantics (MySQL's ONLY_FULL_GROUP_BY) or value types; MySQL
reports those when the query runs.
It supplements, and does not replace, the read-only MySQL account (database/readonly_user.sql).
Execution timeouts and row limits belong to the future execution layer.
"""

from dataclasses import dataclass
from functools import lru_cache

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

from backend.app.config import SCHEMA_FILE
from backend.app.services.rag.chunker import chunk_documents
from backend.app.services.rag.document_loader import load_document

DATABASE = "medlens"
DIALECT = "mysql"

# Functions a query may call, as SQLGlot AST node types (the SQL names they cover are noted).
ALLOWED_FUNCTIONS: dict[type[exp.Expression], str] = {
    exp.Count: "COUNT", exp.Sum: "SUM", exp.Avg: "AVG", exp.Min: "MIN", exp.Max: "MAX",
    exp.Round: "ROUND", exp.Abs: "ABS",
    exp.Coalesce: "COALESCE / IFNULL", exp.Nullif: "NULLIF", exp.If: "IF / CASE branch",
    exp.Concat: "CONCAT", exp.Lower: "LOWER", exp.Upper: "UPPER", exp.Length: "LENGTH", exp.Trim: "TRIM",
    exp.Year: "YEAR", exp.Month: "MONTH", exp.Day: "DAY", exp.TsOrDsToDate: "DATE",
    exp.TimeToStr: "DATE_FORMAT / MONTHNAME", exp.DateDiff: "DATEDIFF", exp.TimestampDiff: "TIMESTAMPDIFF",
    exp.DateAdd: "DATE_ADD", exp.DateSub: "DATE_SUB", exp.CurrentDate: "CURDATE / CURRENT_DATE",
    exp.Cast: "CAST / CONVERT(x, type)",
}
# Helper nodes SQLGlot inserts inside allowed date functions; never valid on their own.
_IMPLICIT_HELPERS = {exp.TsOrDsToTimestamp: (exp.TimeToStr,)}

# Non-function nodes that make up the supported SELECT subset.
_ALLOWED_NODES: set[type[exp.Expression]] = {
    exp.Select, exp.From, exp.Join, exp.Table, exp.TableAlias, exp.Identifier, exp.Column, exp.Star,
    exp.Alias, exp.Where, exp.Group, exp.Having, exp.Order, exp.Ordered, exp.Limit, exp.Offset,
    exp.Distinct, exp.Literal, exp.Null, exp.Boolean, exp.Paren, exp.Tuple,
    exp.And, exp.Or, exp.Not, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
    exp.Is, exp.In, exp.Between, exp.Like, exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.Neg,
    exp.Case, exp.DataType, exp.DataTypeParam, exp.Interval,
}
_DATE_UNITS = {"DAY", "WEEK", "MONTH", "QUARTER", "YEAR"}
_DANGEROUS_FUNCTIONS = {
    "SLEEP", "BENCHMARK", "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK", "RELEASE_ALL_LOCKS",
    "IS_FREE_LOCK", "IS_USED_LOCK", "SYS_EXEC", "SYS_EVAL",
}
_SELECT_ARGS = {"expressions", "from_", "joins", "where", "group", "having", "order", "limit", "offset", "distinct"}
_TABLE_ARGS = {"this", "db", "alias"}
_JOIN_ARGS = {"this", "on", "side", "kind"}

# Reason codes
PARSE_ERROR = "parse_error"
EMPTY = "empty"
MULTIPLE_STATEMENTS = "multiple_statements"
NOT_SELECT = "not_select"
COMMENT = "comment"
EXECUTABLE_COMMENT = "executable_comment"
QUOTING = "quoting"
UNSUPPORTED = "unsupported_construct"
INTO = "into_clause"
LOCKING = "locking_clause"
VARIABLE = "variable"
DANGEROUS_FUNCTION = "dangerous_function"
FUNCTION_NOT_ALLOWED = "function_not_allowed"
UNSUPPORTED_JOIN = "unsupported_join"
CROSS_DATABASE = "cross_database"
UNKNOWN_TABLE = "unknown_table"
UNKNOWN_ALIAS = "unknown_alias"  # a qualifier that is not a table or alias in FROM/JOIN
DUPLICATE_TABLE_ALIAS = "duplicate_table_alias"
UNKNOWN_COLUMN = "unknown_column"
AMBIGUOUS_COLUMN = "ambiguous_column"
INVALID_LIMIT = "invalid_limit"
INVALID_JOIN_KEY = "invalid_join_key"  # columns of two tables compared although they identify different things
JOIN_WITHOUT_KEY = "join_without_key"  # a JOIN with no key equality to the tables before it
ROW_LEVEL_GROUP_BY = "row_level_group_by"  # single-table aggregate grouped by that table's primary key


@dataclass(frozen=True)
class ValidatedSQL:
    """SQL that passed validation, unchanged. Only instances of this may be executed by MySQLRepository."""

    sql: str


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validate. sql is always the original text, never modified."""

    ok: bool
    sql: str
    code: str | None = None  # one of the reason codes above when rejected
    reason: str | None = None
    tables: tuple[str, ...] = ()  # schema tables the query reads (when accepted)

    def require(self) -> ValidatedSQL:
        """Return ValidatedSQL, or raise SQLValidationError if the query was rejected."""
        if not self.ok:
            raise SQLValidationError(self.code or PARSE_ERROR, self.reason or "rejected")
        return ValidatedSQL(self.sql)


class SQLValidationError(ValueError):
    """Raised when a query is rejected; the message is meant to be fed back to the LLM on retry."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


class _Reject(Exception):
    def __init__(self, code: str, reason: str) -> None:
        self.code, self.reason = code, reason


@lru_cache
def schema_columns() -> dict[str, frozenset[str]]:
    """{table: lower-case column names}, parsed from the CREATE TABLE statements in database/schema.sql."""
    tables: dict[str, frozenset[str]] = {}
    for chunk in chunk_documents([load_document(SCHEMA_FILE, "schema")]):
        create = sqlglot.parse_one(chunk.content, read=DIALECT)
        schema = create.this if isinstance(create, exp.Create) else None
        if not isinstance(schema, exp.Schema) or not isinstance(schema.this, exp.Table):
            raise RuntimeError(f"cannot read table definition {chunk.id} from {SCHEMA_FILE.name}")
        columns = frozenset(c.name.lower() for c in schema.expressions if isinstance(c, exp.ColumnDef))
        if not columns:
            raise RuntimeError(f"no columns found for {chunk.id} in {SCHEMA_FILE.name}")
        tables[schema.this.name] = columns
    return tables


@lru_cache
def schema_keys() -> dict[tuple[str, str], tuple[str, str]]:
    """{(table, column): canonical key (table, column)} for PRIMARY KEY and FOREIGN KEY columns of
    database/schema.sql. Foreign keys are followed to the primary key they reference."""
    direct: dict[tuple[str, str], tuple[str, str]] = {}
    for chunk in chunk_documents([load_document(SCHEMA_FILE, "schema")]):
        schema = sqlglot.parse_one(chunk.content, read=DIALECT).this
        table = schema.this.name
        for node in schema.expressions:
            if isinstance(node, exp.PrimaryKey):
                for col in node.expressions:
                    direct[(table, col.name.lower())] = (table, col.name.lower())
            for fk in node.find_all(exp.ForeignKey):
                ref = fk.args["reference"].this
                for col, target in zip(fk.expressions, ref.expressions):
                    direct[(table, col.name.lower())] = (ref.this.name, target.name.lower())

    def canonical(key: tuple[str, str], seen: frozenset = frozenset()) -> tuple[str, str]:
        target = direct[key]
        return key if target == key or key in seen else canonical(target, seen | {key})

    return {key: canonical(key) for key in direct}


@lru_cache
def required_parents() -> dict[tuple[str, str], str]:
    """{(table, column): referenced table} for the NOT NULL foreign keys of database/schema.sql. Every row of
    the table has exactly one row in the referenced table, so an inner join along that key never drops it."""
    found = {}
    for chunk in chunk_documents([load_document(SCHEMA_FILE, "schema")]):
        schema = sqlglot.parse_one(chunk.content, read=DIALECT).this
        not_null = {c.name.lower() for c in schema.expressions if isinstance(c, exp.ColumnDef)
                    and any(isinstance(k.args.get("kind"), exp.NotNullColumnConstraint)
                            and not k.args["kind"].args.get("allow_null") for k in c.constraints)}
        for fk in schema.find_all(exp.ForeignKey):
            target = fk.args["reference"].this.this.name
            for col in fk.expressions:
                if col.name.lower() in not_null:
                    found[(schema.this.name, col.name.lower())] = target
    return found


def key_path(source: str, target: str, upward_only: bool = False) -> list[str]:
    """Join conditions leading from table source to table target along schema keys, preferring the
    parent direction (treatments -> appointments -> patients); [] if source == target or no path.
    upward_only: only follow foreign keys from child to parent."""
    keys = schema_keys()
    edges = [((t, c), k) for (t, c), k in keys.items() if (t, c) != k]  # (child table, fk column) -> parent key
    for upward_only in ((True,) if upward_only else (True, False)):
        paths, frontier, seen = {source: []}, [source], {source}
        while frontier:
            nxt = []
            for table in frontier:
                for (child, col), (parent, pk) in edges:
                    steps = [(child, parent, f"{child}.{col} = {parent}.{pk}")]
                    if not upward_only:
                        steps.append((parent, child, f"{child}.{col} = {parent}.{pk}"))
                    for a, b, cond in steps:
                        if a == table and b not in seen:
                            seen.add(b)
                            paths[b] = paths[table] + [cond]
                            nxt.append(b)
            frontier = nxt
        if target in paths:
            return paths[target]
    return []


def join_paths() -> str:
    """Human-readable list of the schema's valid join keys (for correction diagnostics and docs)."""
    keys = schema_keys()
    pairs = sorted(f"{t}.{c} = {k[0]}.{k[1]}" for (t, c), k in keys.items() if (t, c) != k)
    return "; ".join(pairs)


def validate(sql: str) -> ValidationResult:
    """Check sql against the supported subset and the schema. Never raises for bad SQL."""
    if not isinstance(sql, str):
        raise TypeError("sql must be a string")
    try:
        tables = _check(sql)
    except _Reject as r:
        return ValidationResult(False, sql, r.code, r.reason)
    return ValidationResult(True, sql, tables=tables)


def validate_sql(sql: str) -> ValidatedSQL:
    """Return ValidatedSQL if the query is allowed, otherwise raise SQLValidationError."""
    return validate(sql).require()


# --- checks ------------------------------------------------------------------------------

def _check(sql: str) -> tuple[str, ...]:
    if not sql.strip():
        raise _Reject(EMPTY, "the query is empty")
    _check_tokens(sql)
    select = _parse_single_select(sql)
    _check_nodes(select)
    _check_select_shape(select)
    sources = _resolve_sources(select)
    _check_columns(select, sources)
    _check_join_keys(select, sources)
    _check_group_by(select, sources)
    _check_limit(select)
    return tuple(sorted({table for table, _ in sources.values()}))


def _check_tokens(sql: str) -> None:
    """Lexical rules that the AST cannot show: comments are dropped from it, but MySQL executes /*! */."""
    if "\\" in sql:
        raise _Reject(QUOTING, "backslashes are not allowed (their meaning depends on the server's SQL mode)")
    try:
        tokens = sqlglot.tokenize(sql, read=DIALECT)
    except SqlglotError as exc:
        raise _Reject(PARSE_ERROR, f"cannot tokenize the query: {_first_line(exc)}") from None
    for token in tokens:
        if token.token_type == TokenType.HINT:
            raise _Reject(EXECUTABLE_COMMENT, "optimizer hints (/*+ ... */) are not allowed")
        for comment in token.comments:
            if comment.lstrip().startswith(("!", "+")) or token.text.startswith("/*!"):
                raise _Reject(EXECUTABLE_COMMENT, "executable MySQL comments (/*! ... */) are not allowed")
        if token.comments:
            raise _Reject(COMMENT, "comments are not allowed in the query")
        # Double quotes mean a string or an identifier depending on ANSI_QUOTES. (SQLGlot also uses
        # STRING tokens for the raw text of commands it does not parse; the parser rejects those.)
        if token.token_type == TokenType.STRING and sql[token.start] == '"':
            raise _Reject(QUOTING, "string literals must use single quotes")


def _parse_single_select(sql: str) -> exp.Select:
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except SqlglotError as exc:
        raise _Reject(PARSE_ERROR, f"not valid MySQL in the supported subset: {_first_line(exc)}") from None
    if not statements:
        raise _Reject(EMPTY, "the query contains no statement")
    if len(statements) > 1:
        raise _Reject(MULTIPLE_STATEMENTS, f"exactly one statement is allowed, found {len(statements)}")
    root = statements[0]
    if isinstance(root, exp.SetOperation):
        raise _Reject(UNSUPPORTED, f"{root.key.upper()} is not supported in this version")
    if not isinstance(root, exp.Select):
        kind = root.key.upper() if not isinstance(root, exp.Command) else str(root.this).upper()
        raise _Reject(NOT_SELECT, f"only SELECT statements are allowed, found {kind}")
    return root


def _check_nodes(select: exp.Select) -> None:
    """Walk the entire tree; every node must be explicitly allowed."""
    for node in select.walk():
        cls = type(node)
        if isinstance(node, exp.Into):
            raise _Reject(INTO, "SELECT ... INTO (files or variables) is not allowed")
        if isinstance(node, exp.Lock):
            raise _Reject(LOCKING, "locking clauses (FOR UPDATE, LOCK IN SHARE MODE, ...) are not allowed")
        if isinstance(node, (exp.Parameter, exp.SessionParameter, exp.Placeholder, exp.PropertyEQ)):
            raise _Reject(VARIABLE, "variables, assignments and placeholders are not allowed")
        if isinstance(node, (exp.With, exp.CTE)):
            raise _Reject(UNSUPPORTED, "CTEs (WITH ...) are not supported in this version")
        if isinstance(node, exp.SetOperation):
            raise _Reject(UNSUPPORTED, f"{node.key.upper()} is not supported in this version")
        if isinstance(node, (exp.Subquery, exp.Exists)) or (isinstance(node, exp.Select) and node is not select):
            raise _Reject(UNSUPPORTED, "subqueries are not supported in this version")
        if isinstance(node, exp.Window):
            raise _Reject(UNSUPPORTED, "window functions (OVER ...) are not supported in this version")
        if isinstance(node, exp.Anonymous):
            name = str(node.this).upper()
            if name in _DANGEROUS_FUNCTIONS:
                raise _Reject(DANGEROUS_FUNCTION, f"{name}() is not allowed")
            raise _Reject(FUNCTION_NOT_ALLOWED, f"function {name}() is not on the allowlist")
        if cls in ALLOWED_FUNCTIONS or cls in _ALLOWED_NODES:
            continue
        if cls in _IMPLICIT_HELPERS and isinstance(node.parent, _IMPLICIT_HELPERS[cls]):
            continue
        if isinstance(node, exp.Var) and node.arg_key == "unit" and node.name.upper() in _DATE_UNITS:
            continue
        if isinstance(node, exp.Func):
            raise _Reject(FUNCTION_NOT_ALLOWED, f"function {node.sql_name()}() is not on the allowlist")
        raise _Reject(UNSUPPORTED, f"unsupported SQL construct: {_describe(node)}")


def _check_select_shape(select: exp.Select) -> None:
    """Reject SELECT options and table/join forms that are not node types (e.g. SQL_CALC_FOUND_ROWS)."""
    for key, value in select.args.items():
        if value and key not in _SELECT_ARGS:
            raise _Reject(UNSUPPORTED, f"unsupported SELECT clause or modifier: {key}")
    for table in select.find_all(exp.Table):
        for key, value in table.args.items():
            if value and key not in _TABLE_ARGS:
                raise _Reject(UNSUPPORTED, f"unsupported table option on {table.name!r}: {key}")
    for join in select.args.get("joins") or []:
        for key, value in join.args.items():
            if value and key not in _JOIN_ARGS:
                raise _Reject(UNSUPPORTED_JOIN, f"JOIN with {key.upper()} is not supported; use JOIN ... ON ...")
        kind = (join.args.get("kind") or "").upper()
        if kind not in ("", "INNER", "OUTER") or (kind == "OUTER" and not join.args.get("side")):
            raise _Reject(UNSUPPORTED_JOIN, f"{kind} JOIN is not supported; use [INNER|LEFT|RIGHT] JOIN ... ON ...")
        if not join.args.get("on"):
            raise _Reject(UNSUPPORTED_JOIN, "every JOIN needs an ON condition (comma and CROSS joins are not supported)")
        if not isinstance(join.this, exp.Table):
            raise _Reject(UNSUPPORTED_JOIN, "only tables can be joined")
    if not isinstance(select.args.get("from_"), exp.From) and select.args.get("joins"):
        raise _Reject(UNSUPPORTED, "JOIN without FROM")


def _resolve_sources(select: exp.Select) -> dict[str, tuple[str, int]]:
    """{reference name: (schema table, position)} for FROM and JOIN tables, in order."""
    from_ = select.args.get("from_")
    tables = []
    if isinstance(from_, exp.From):
        if not isinstance(from_.this, exp.Table):
            raise _Reject(UNSUPPORTED, "FROM must name a table")
        tables.append(from_.this)
    tables += [j.this for j in select.args.get("joins") or []]

    schema = schema_columns()
    sources: dict[str, tuple[str, int]] = {}
    for position, table in enumerate(tables):
        if table.args.get("catalog"):
            raise _Reject(CROSS_DATABASE, f"catalog-qualified table {table.sql(dialect=DIALECT)} is not allowed")
        db = table.db
        if db and db.lower() != DATABASE:
            raise _Reject(CROSS_DATABASE, f"only the {DATABASE} database is allowed, found {db}.{table.name}")
        if table.name not in schema:
            raise _Reject(UNKNOWN_TABLE, f"unknown table {table.name!r}; allowed tables: {', '.join(schema)}")
        reference = table.alias or table.name
        if reference in sources:
            raise _Reject(DUPLICATE_TABLE_ALIAS, f"table name or alias {reference!r} is used more than once")
        sources[reference] = (table.name, position)
    return sources


def _check_columns(select: exp.Select, sources: dict[str, tuple[str, int]]) -> None:
    """Every column must resolve to exactly one table column (or, where MySQL allows, a SELECT alias)."""
    schema = schema_columns()
    aliases = {a.alias.lower() for a in select.expressions if isinstance(a, exp.Alias)}

    def table_matches(name: str, visible: dict[str, tuple[str, int]]) -> list[str]:
        return [ref for ref, (table, _) in visible.items() if name in schema[table]]

    def resolve(column: exp.Column, clause: str, visible: dict[str, tuple[str, int]]) -> None:
        if column.args.get("db") or column.args.get("catalog"):
            raise _Reject(UNSUPPORTED, f"three-part column names are not supported: {column.sql(dialect=DIALECT)}")
        qualifier = column.table
        is_star = isinstance(column.this, exp.Star)
        name = column.name.lower()
        if qualifier:
            if qualifier not in sources:
                raise _Reject(UNKNOWN_ALIAS, f"{qualifier!r} in {column.sql(dialect=DIALECT)} is not a table or alias in FROM/JOIN")
            if qualifier not in visible:
                raise _Reject(UNKNOWN_COLUMN, f"{column.sql(dialect=DIALECT)} refers to a table joined later")
            if not is_star and name not in schema[sources[qualifier][0]]:
                raise _Reject(UNKNOWN_COLUMN, f"table {sources[qualifier][0]!r} has no column {column.name!r}")
            return

        standalone = isinstance(column.parent, (exp.Ordered, exp.Group))
        alias_ok = (clause == "order" and standalone) or (clause == "group" and standalone) or clause == "having"
        if clause == "order" and standalone and name in aliases:
            return  # MySQL: ORDER BY looks at SELECT aliases first
        matches = table_matches(name, visible)
        if len(matches) == 1:
            return
        if len(matches) > 1:
            tables = ", ".join(sorted(sources[m][0] + (f" AS {m}" if m != sources[m][0] else "") for m in matches))
            raise _Reject(AMBIGUOUS_COLUMN, f"column {column.name!r} is ambiguous (in {tables}); qualify it")
        if alias_ok and name in aliases:
            return  # GROUP BY / HAVING: table columns first, then SELECT aliases
        raise _Reject(UNKNOWN_COLUMN, f"unknown column {column.name!r}" + (
            "; SELECT aliases are only allowed alone in ORDER BY/GROUP BY, or in HAVING" if name in aliases else ""))

    def walk(node: exp.Expression | None, clause: str, visible: dict[str, tuple[str, int]]) -> None:
        if node is not None:
            for column in node.find_all(exp.Column):
                resolve(column, clause, visible)

    for projection in select.expressions:
        walk(projection, "select", sources)
    for position, join in enumerate(select.args.get("joins") or [], start=1):
        visible = {ref: v for ref, v in sources.items() if v[1] <= position}
        walk(join.args.get("on"), "on", visible)
    for clause in ("where", "group", "having", "order"):
        walk(select.args.get(clause), clause, sources)


def _column_key(column: exp.Column, sources: dict[str, tuple[str, int]]) -> tuple[str, tuple[str, str] | None]:
    """(table reference, canonical key or None) of a column already resolved by _check_columns."""
    schema = schema_columns()
    name = column.name.lower()
    reference = column.table or next((ref for ref, (table, _) in sources.items() if name in schema[table]), "")
    table = sources[reference][0] if reference in sources else ""
    return reference, schema_keys().get((table, name))


def _conjuncts(node: exp.Expression) -> list[exp.Expression]:
    node = node.unnest() if isinstance(node, exp.Paren) else node
    if isinstance(node, exp.And):
        return _conjuncts(node.this) + _conjuncts(node.expression)
    return [node]


def _check_join_keys(select: exp.Select, sources: dict[str, tuple[str, int]]) -> None:
    """Column-to-column equalities across tables must compare the same key (see the module docstring)."""
    for clause in [j.args.get("on") for j in select.args.get("joins") or []] + [select.args.get("where"), select.args.get("having")]:
        if clause is None:
            continue
        for eq in clause.find_all(exp.EQ):
            left, right = eq.this, eq.expression
            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue
            (lref, lkey), (rref, rkey) = _column_key(left, sources), _column_key(right, sources)
            if lref == rref or (lkey is None and rkey is None):
                continue
            if lkey != rkey:
                l_text, r_text = left.sql(dialect=DIALECT), right.sql(dialect=DIALECT)
                raise _Reject(INVALID_JOIN_KEY, f"{l_text} and {r_text} identify different things, so they cannot be "
                                                f"compared; valid join keys: {join_paths()}")
    for position, join in enumerate(select.args.get("joins") or [], start=1):
        joined = next(ref for ref, (_, pos) in sources.items() if pos == position)
        linked = False
        for node in _conjuncts(join.args["on"]):
            if isinstance(node, exp.EQ) and isinstance(node.this, exp.Column) and isinstance(node.expression, exp.Column):
                (lref, lkey), (rref, rkey) = _column_key(node.this, sources), _column_key(node.expression, sources)
                refs = {lref, rref}
                if lkey is not None and lkey == rkey and joined in refs and len(refs) == 2:
                    linked = True
        if not linked:
            raise _Reject(JOIN_WITHOUT_KEY, f"the JOIN of {sources[joined][0]!r} is not linked by a key column; "
                                             f"valid join keys: {join_paths()}")


def _check_group_by(select: exp.Select, sources: dict[str, tuple[str, int]]) -> None:
    """A query on ONE table that aggregates but groups by that table's primary key makes every group a
    single row, so SUM/COUNT/AVG summarize nothing (e.g. GROUP BY bill_id under SUM(amount)). Rejected so
    the bounded correction can fix it; grouping on other columns, and joins, are not affected."""
    group = select.args.get("group")
    if group is None or select.args.get("joins") or not any(select.find_all(exp.AggFunc)):
        return
    (table, _), = sources.values()
    keys = schema_keys()
    for node in group.expressions:
        if isinstance(node, exp.Column) and keys.get((table, node.name.lower())) == (table, node.name.lower()):
            raise _Reject(ROW_LEVEL_GROUP_BY, f"GROUP BY {node.name} (the primary key of {table}) makes every group a "
                                              "single row, so the aggregate summarizes nothing; group by what the "
                                              "question asks for, or remove GROUP BY")


def _check_limit(select: exp.Select) -> None:
    for key in ("limit", "offset"):
        node = select.args.get(key)
        if node is None:
            continue
        value = node.expression
        if not (isinstance(value, exp.Literal) and value.is_int and int(value.this) >= 0):
            raise _Reject(INVALID_LIMIT, f"{key.upper()} must be a non-negative integer literal")


def _describe(node: exp.Expression) -> str:
    text = node.sql(dialect=DIALECT)
    return f"{node.key.upper()} ({text[:60]})" if text else node.key.upper()


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0][:200]
