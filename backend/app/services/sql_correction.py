"""Policy for the single SQL correction attempt: which errors may be retried, what the model
is told about them, and a guard against corrections that drop what the question asked for.

Default is NO retry. Only errors on these explicit allowlists get one correction attempt:
    - validator codes for column/alias resolution and fixable syntax/shape
    - MySQL error numbers for syntax, column resolution and GROUP BY errors
Policy violations (writes, multiple statements, forbidden functions, comments, cross-database,
unsupported constructs, ...), timeouts, connection/availability failures and successful (even
empty) results are never retried.
"""

import re

import sqlglot
from mysql.connector import errorcode
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.app.services import sql_validator as v

MAX_CORRECTIONS = 1
MAX_DIAGNOSTIC_CHARS = 600  # room for the list of valid join keys

RETRYABLE_VALIDATION_CODES: frozenset[str] = frozenset({
    v.UNKNOWN_COLUMN,         # column not in the referenced table
    v.UNKNOWN_ALIAS,          # qualifier that is not a table/alias in FROM/JOIN
    v.AMBIGUOUS_COLUMN,       # unqualified column present in several joined tables
    v.DUPLICATE_TABLE_ALIAS,  # the same table name/alias used twice
    v.PARSE_ERROR,            # SQL syntax the parser cannot read
    v.INVALID_LIMIT,          # LIMIT/OFFSET that is not a non-negative integer literal
    v.INVALID_JOIN_KEY,       # tables joined on columns that identify different things
    v.JOIN_WITHOUT_KEY,       # a JOIN with no key equality to the earlier tables
    v.ROW_LEVEL_GROUP_BY,     # aggregate grouped by the only table's primary key
})

RETRYABLE_MYSQL_ERRORS: dict[int, str] = {
    errorcode.ER_BAD_FIELD_ERROR: "unknown column",
    errorcode.ER_NON_UNIQ_ERROR: "ambiguous column",
    errorcode.ER_NONUNIQ_TABLE: "table name or alias used twice",
    errorcode.ER_ILLEGAL_REFERENCE: "illegal column reference",
    errorcode.ER_PARSE_ERROR: "SQL syntax error",
    errorcode.ER_WRONG_FIELD_WITH_GROUP: "column not in GROUP BY (ONLY_FULL_GROUP_BY)",
    errorcode.ER_WRONG_GROUP_FIELD: "cannot group on this expression",
    errorcode.ER_INVALID_GROUP_FUNC_USE: "invalid use of an aggregate function",
    errorcode.ER_MIX_OF_GROUP_FUNC_AND_FIELDS: "aggregate mixed with non-grouped columns",
    errorcode.ER_FIELD_IN_ORDER_NOT_SELECT: "ORDER BY column not in the SELECT list with DISTINCT",
}


def mysql_error_code(errno: int | None) -> str:
    """Safe error code for API responses, e.g. "mysql_1054"."""
    return f"mysql_{errno}" if errno is not None else "mysql_error"


def is_retryable_validation(code: str | None) -> bool:
    return code in RETRYABLE_VALIDATION_CODES


def is_retryable_mysql(errno: int | None) -> bool:
    return errno in RETRYABLE_MYSQL_ERRORS


def sanitize(text: str) -> str:
    """One line, no control characters, at most MAX_DIAGNOSTIC_CHARS."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = " ".join(text.split())
    return text if len(text) <= MAX_DIAGNOSTIC_CHARS else text[: MAX_DIAGNOSTIC_CHARS - 3] + "..."


def validation_diagnostic(code: str, reason: str, sql: str | None = None) -> str:
    """Validator codes and reasons name only SQL identifiers, never data or credentials. For an unknown
    column, also name the schema table(s) that have it and the join keys from the query's tables."""
    hint = column_location(reason, sql) if code in (v.UNKNOWN_COLUMN, v.UNKNOWN_ALIAS) else ""
    return sanitize(f"The query failed validation ({code}): {reason}" + (f". {hint}" if hint else ""))


_COLUMN_IN_REASON = re.compile(r"column '([A-Za-z_]+)'")


def column_location(reason: str, sql: str | None) -> str:
    """Where misplaced columns really are, from database/schema.sql: for every qualified column of the query
    that its table does not have (the validator reports only the first), "t.x: x is a column of A, B; from t,
    join A with t.k = A.k" along the nearest key path. "" if nothing can be said."""
    columns = v.schema_columns()
    hints = []
    try:
        select = sqlglot.parse_one(sql, read=v.DIALECT) if sql else None
    except SqlglotError:
        select = None
    tables = {(t.alias or t.name): t.name for t in select.find_all(exp.Table)} if select is not None else {}
    found = list(select.find_all(exp.Column)) if select is not None else []
    misplaced = [(c.table, tables[c.table], c.name.lower()) for c in found
                 if c.table in tables and tables[c.table] in columns and c.name.lower() not in columns[tables[c.table]]]
    first = next(iter(tables.values()), "")
    for qualifier in dict.fromkeys(c.table for c in found if c.table and c.table not in tables):
        # a qualifier that is never joined ("p.first_name" without "JOIN patients AS p")
        names = {c.name.lower() for c in found if c.table == qualifier}
        owners = sorted(t for t, cols in columns.items() if names <= cols)
        named = [t for t in owners if t == qualifier or t.startswith(qualifier.lower())]
        target = named[0] if len(named) == 1 else (owners[0] if len(owners) == 1 else "")
        path = v.key_path(first, target) if first and target else []
        alias = "" if target == qualifier else f" AS {qualifier}"
        hints.append(f"{qualifier} is not in FROM/JOIN"
                     + (f"; join {target}{alias} with {'; '.join(path)}" if path else
                        f"; its columns are in {', '.join(owners)}" if owners else ""))
    if not misplaced and hints:
        return ". ".join(hints)
    if not misplaced:
        match = _COLUMN_IN_REASON.search(reason)
        misplaced = [("", "", match.group(1).lower())] if match else []
    for ref, table, column in dict.fromkeys(misplaced):
        owners = sorted(t for t, cols in columns.items() if column in cols)
        if not owners:
            hints.append(f"{column} is not a column of any table")
            continue
        text = f"{ref + '.' if ref else ''}{column}: {column} is a column of {', '.join(owners)}"
        joined = [alias for alias, name in tables.items()
                  if name in owners and table and v.key_path(table, name, upward_only=True)]
        if joined:  # a parent that has the column is already in the query: name the column to use instead
            hints.append(text + f"; use {' or '.join(f'{a}.{column}' for a in joined)}")
            continue
        candidates = [(not bool(v.key_path(table, o, upward_only=True)), len(v.key_path(table, o)), o)
                      for o in owners if table and v.key_path(table, o)]
        owner = min(candidates)[2] if candidates else None  # a parent first: every row has exactly one
        path = v.key_path(table, owner) if owner else []
        if path:
            text += f"; from {table}, join {owner} with {'; '.join(path)}"
        hints.append(text)
    return ". ".join(hints)


def mysql_diagnostic(errno: int, mysql_message: str | None) -> str:
    """Only called for allowlisted error numbers, whose MySQL messages name SQL identifiers,
    not row values, credentials or connection details."""
    label = RETRYABLE_MYSQL_ERRORS[errno]
    return sanitize(f"MySQL error {errno} ({label}): {mysql_message or ''}")


def meaning_change(original_sql: str, corrected_sql: str, removable: frozenset[str] = frozenset()) -> str | None:
    """Return a reason if the correction dropped a filter value or an aggregate the original used.
    removable: values and aggregate names the request did not ask for (request_spec.removable); dropping
    those is the point of the correction.

    Filter values are the literals in WHERE, HAVING and JOIN ... ON (moving a filter between
    those clauses is fine; deleting it is not). If the original cannot be parsed there is
    nothing reliable to compare, so the check is skipped.
    """
    try:
        before = sqlglot.parse_one(original_sql, read=v.DIALECT)
        after = sqlglot.parse_one(corrected_sql, read=v.DIALECT)
    except SqlglotError:
        return None
    if not isinstance(before, exp.Select) or not isinstance(after, exp.Select):
        return None
    # a filter may move into conditional aggregation (SUM(CASE WHEN status = 'Paid' ...)); that keeps it
    lost_values = _filter_literals(before) - _filter_literals(after) - _case_condition_literals(after) - removable
    if lost_values:
        return f"the correction removed filter value(s) {', '.join(sorted(lost_values))}"
    lost_aggregates = _aggregates(before) - _aggregates(after) - removable
    if lost_aggregates:
        return f"the correction removed aggregate function(s) {', '.join(sorted(lost_aggregates))}"
    return None


def _filter_literals(select: exp.Select) -> set[str]:
    clauses = [select.args.get("where"), select.args.get("having")]
    clauses += [j.args.get("on") for j in select.args.get("joins") or []]
    values = set()
    for clause in clauses:
        if clause is not None:
            values |= {repr(l.this) if l.is_string else l.this for l in clause.find_all(exp.Literal)}
    return values


def _case_condition_literals(select: exp.Select) -> set[str]:
    """Literals in CASE WHEN conditions (not in their results), as _filter_literals names them."""
    return {repr(l.this) if l.is_string else l.this
            for case in select.find_all(exp.If) for l in case.this.find_all(exp.Literal)}


def _aggregates(select: exp.Select) -> set[str]:
    return {type(node).__name__.upper() for node in select.find_all(exp.AggFunc)}
