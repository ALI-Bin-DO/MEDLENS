"""Deterministic chart specifications for successful answers (presentation only).

Built from the returned columns, rows, MySQL's column type metadata and the structure of the
executed SQL. No model call, no extra query, no change to the SQL or the rows. Anything not clearly
chartable gives None.

Data shapes:
    category  exactly one numeric metric + one or more text columns that together name each row
              (e.g. doctor_id, first_name, last_name + appointment_count); points keep the result's order
    time      exactly one numeric metric + exactly one DATE/DATETIME column, or one text column built
              with DATE_FORMAT(col, '%Y-%m') (a month); points sorted by date

None when: no type metadata, fewer than 2 rows, truncated result, more than CHART_MAX_POINTS rows,
zero or several numeric metrics, any other column kind (time, year, mixed date + text, ...),
any NULL in a plotted column, repeated categories or dates (charting them would need aggregation).

Views (ChartSpec.views, in the order offered; ChartSpec.default_view is chosen from them):
    bar_vertical    categories (default for at most 8 categories whose labels are at most 16 characters)
                    and time series
    bar_horizontal  categories (default for long labels or more categories)
    line            time series only (the default for them)
    pie             categories only, and only for a part-of-whole: 2 to 5 categories, every value >= 0,
                    a positive total, and a measure that adds up to a whole. That is decided from the
                    executed SQL, never from the values: the metric must be COUNT(...) or SUM(...)
                    (optionally inside ROUND or COALESCE) without DISTINCT, in a query with GROUP BY and
                    without HAVING, LIMIT or SELECT DISTINCT. Averages, rates, minimums, maximums,
                    distinct counts (groups may overlap) and filtered or top-N subsets never get a pie.
                    One exception: COUNT(DISTINCT <primary key of the FROM table>) when every JOIN adds a
                    parent along a NOT NULL foreign key. Each row is then counted once, in one group (age
                    bands of patients, appointments per branch), so the groups form a whole.

Numeric metric = MySQL integer/decimal/float column whose name is not an identifier (id, *_id).
Text that merely looks numeric (IDs, phone numbers, DATE_FORMAT output) is never a metric.
Each point carries `y` (number for plotting) and `y_exact` (the original value from rows, e.g.
the exact Decimal string "2663.415600"). No units, currencies or labels beyond column names.
"""

import datetime as dt
import math
import re
from decimal import Decimal, InvalidOperation

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.app.models.chat_models import ChartAxis, ChartPoint, ChartSpec
from backend.app.repositories.mysql_repository import QueryResult

NUMERIC_KINDS = {"integer", "decimal", "float"}
DATE_KINDS = {"date", "datetime"}
LABEL_SEPARATOR = " · "
PIE_MAX_CATEGORIES = 5  # one distinct palette color per slice, never reused
VERTICAL_MAX_CATEGORIES = 8
VERTICAL_MAX_LABEL_CHARS = 16
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def is_identifier(column: str) -> bool:
    name = column.lower()
    return name == "id" or name.endswith("_id")


def build_chart(result: QueryResult | None, max_points: int) -> ChartSpec | None:
    """Return a chart spec for the result, or None if it is not clearly chartable."""
    if result is None or result.column_types is None or len(result.column_types) != len(result.columns):
        return None
    if result.truncated or result.row_count < 2 or result.row_count > max_points or len(result.rows) != result.row_count:
        return None

    kinds = result.column_types
    metrics = [i for i, k in enumerate(kinds) if k in NUMERIC_KINDS and not is_identifier(result.columns[i])]
    if len(metrics) != 1:
        return None
    metric = metrics[0]
    dims = [i for i in range(len(kinds)) if i != metric]
    if not dims:
        return None

    plotted = dims + [metric]
    if any(row[i] is None for row in result.rows for i in plotted):
        return None
    ys = [_number(row[metric], kinds[metric]) for row in result.rows]
    if any(y is None for y in ys):
        return None

    select = _parse_select(result.sql)
    y_axis = ChartAxis(label=result.columns[metric], fields=[result.columns[metric]], kind="number")
    if len(dims) == 1 and kinds[dims[0]] in DATE_KINDS:
        return _time(result, dims[0], metric, ys, y_axis, "date")
    if len(dims) == 1 and kinds[dims[0]] == "string" and _is_month_column(select, dims[0], result.rows):
        return _time(result, dims[0], metric, ys, y_axis, "month")
    if all(kinds[i] == "string" or (kinds[i] == "integer" and is_identifier(result.columns[i])) for i in dims):
        return _categories(result, dims, metric, ys, y_axis, _pie_problem(select, metric))
    return None


def _categories(result: QueryResult, dims: list[int], metric: int, ys: list, y_axis: ChartAxis,
                pie_problem: str | None) -> ChartSpec | None:
    labels = [LABEL_SEPARATOR.join(str(row[i]) for i in dims) for row in result.rows]
    if len(set(labels)) != len(labels):
        return None  # repeated categories would need aggregation
    points = [ChartPoint(x=label, y=y, y_exact=row[metric], row_index=n)
              for n, (label, y, row) in enumerate(zip(labels, ys, result.rows))]
    x_axis = ChartAxis(label=LABEL_SEPARATOR.join(result.columns[i] for i in dims),
                       fields=[result.columns[i] for i in dims], kind="category")
    compact = len(points) <= VERTICAL_MAX_CATEGORIES and max(len(label) for label in labels) <= VERTICAL_MAX_LABEL_CHARS
    views = ["bar_vertical", "bar_horizontal"] if compact else ["bar_horizontal", "bar_vertical"]
    if pie_problem is None and len(points) > PIE_MAX_CATEGORIES:
        pie_problem = f"it has {len(points)} categories and a donut stays readable only up to {PIE_MAX_CATEGORIES}"
    if pie_problem is None and (any(y < 0 for y in ys) or sum(ys) <= 0):
        pie_problem = "its values include negatives or add up to zero, so they do not form a whole"
    notes = {"line": "a line needs dates or months; this result is grouped by categories"}
    if pie_problem is None:
        views.append("pie")
    else:
        notes["pie"] = pie_problem
    return ChartSpec(type="bar", views=views, default_view=views[0], x=x_axis, y=y_axis, points=points,
                     point_count=len(points), notes=notes)


def share_chart(result: QueryResult, label: int, value: int, pie_problem: str | None) -> ChartSpec | None:
    """Category chart of one computed column (e.g. branch percentages from branch_share.py), whose part-of-whole
    status the caller knows: pie_problem explains why a donut would mislead, None when the values form a whole."""
    if result.row_count < 2 or any(row[value] is None for row in result.rows):
        return None
    ys = [_number(row[value], "decimal") for row in result.rows]
    if any(y is None for y in ys):
        return None
    y_axis = ChartAxis(label=result.columns[value], fields=[result.columns[value]], kind="number")
    return _categories(result, [label], value, ys, y_axis, pie_problem)


def _time(result: QueryResult, date_col: int, metric: int, ys: list, y_axis: ChartAxis, kind: str) -> ChartSpec | None:
    try:
        dates = [_parse_date(row[date_col], kind) for row in result.rows]
    except (TypeError, ValueError):
        return None
    if len(set(dates)) != len(dates):
        return None  # several values per date would need aggregation
    order = sorted(range(len(dates)), key=lambda n: dates[n])
    points = [ChartPoint(x=result.rows[n][date_col], y=ys[n], y_exact=result.rows[n][metric], row_index=n) for n in order]
    x_axis = ChartAxis(label=result.columns[date_col], fields=[result.columns[date_col]], kind=kind)
    notes = {"pie": "the values are a series over time, not parts of one whole",
             "bar_horizontal": "a date series is shown along a horizontal time axis (line or vertical bars)"}
    return ChartSpec(type="line", views=["line", "bar_vertical"], default_view="line", x=x_axis, y=y_axis,
                     points=points, point_count=len(points), notes=notes)


def _parse_select(sql: str | None) -> exp.Select | None:
    if not sql:
        return None
    try:
        tree = sqlglot.parse_one(sql, read="mysql")
    except SqlglotError:
        return None
    return tree if isinstance(tree, exp.Select) else None


def _output_expression(select: exp.Select | None, index: int) -> exp.Expression | None:
    """The SELECT-list expression that produced result column `index` (None if it cannot be known)."""
    if select is None or any(_is_star(e) for e in select.expressions):
        return None  # * or t.* : output positions cannot be mapped to expressions
    if index >= len(select.expressions):
        return None
    node = select.expressions[index]
    return node.this if isinstance(node, exp.Alias) else node


def _is_star(node: exp.Expression) -> bool:
    return isinstance(node, exp.Star) or (isinstance(node, exp.Column) and isinstance(node.this, exp.Star))


def _pie_problem(select: exp.Select | None, metric: int) -> str | None:
    """Why the metric is not a part of a whole (None when it is a non-distinct COUNT/SUM per group of a
    GROUP BY without HAVING, LIMIT or SELECT DISTINCT)."""
    if select is None or not select.args.get("group"):
        return "it is not a grouped count or total, so the parts of a whole cannot be established"
    if select.args.get("having") or select.args.get("limit"):
        return "it keeps only some groups (a filter on groups or a top-N limit), so the slices are not the whole"
    if select.args.get("distinct"):
        return "it uses SELECT DISTINCT, so the parts of a whole cannot be established"
    node = _output_expression(select, metric)
    while isinstance(node, (exp.Paren, exp.Round, exp.Coalesce)):
        node = node.this
    if isinstance(node, exp.Count) and isinstance(node.this, exp.Distinct) and _distinct_rows_of_base(select, node.this):
        return None  # COUNT(DISTINCT <primary key of the base table>) with only many-to-one joins: a partition
    if isinstance(node, (exp.Count, exp.Sum)) and isinstance(node.this, exp.Distinct):
        return ("it counts distinct values per group, so the same item can appear in several groups and the "
                "slices would not add up to a whole")
    if not isinstance(node, (exp.Count, exp.Sum)):
        return "the measure is not a count or a total (for example an average, rate, minimum or maximum)"
    return None


def _distinct_rows_of_base(select: exp.Select, distinct: exp.Distinct) -> bool:
    """COUNT(DISTINCT x) counts each row of the FROM table once and in one group only: x is that table's
    primary key, and every JOIN adds a parent along a NOT NULL foreign key (many-to-one), so no row is repeated
    and each row's group values come from a single row. Then the groups partition the rows like COUNT(*).
    Joins to child tables (treatments, billing for appointments) could put one row in several groups."""
    from backend.app.services.sql_validator import _column_key, _resolve_sources, required_parents, schema_keys

    if len(distinct.expressions) != 1 or not isinstance(distinct.expressions[0], exp.Column):
        return False
    try:
        sources = _resolve_sources(select)
    except Exception:  # anything the validator would reject: cannot establish
        return False
    order = sorted(sources, key=lambda ref: sources[ref][1])
    base_ref, base = order[0], sources[order[0]][0]
    ref, key = _column_key(distinct.expressions[0], sources)
    if ref != base_ref or key != (base, distinct.expressions[0].name.lower()):
        return False  # not the primary key of the base table
    joined = {base_ref}
    for join in select.args.get("joins") or []:
        target = join.this.alias or join.this.name
        if (join.args.get("side") or "").upper() == "RIGHT":
            return False
        if not _joins_a_parent(join.args.get("on"), joined, target, sources, required_parents(), schema_keys()):
            return False
        joined.add(target)
    return True


def _joins_a_parent(on, joined: set[str], target: str, sources, parents: dict, keys: dict) -> bool:
    """ON has child.fk = target.pk for a NOT NULL foreign key of an already joined table into target's table."""
    from backend.app.services.sql_validator import _column_key, _conjuncts

    for cond in _conjuncts(on) if on is not None else []:
        if not isinstance(cond, exp.EQ) or not all(isinstance(s, exp.Column) for s in (cond.this, cond.expression)):
            continue
        for a, b in ((cond.this, cond.expression), (cond.expression, cond.this)):
            (ref_a, key_a), (ref_b, key_b) = _column_key(a, sources), _column_key(b, sources)
            if ref_a in joined and ref_b == target and key_a is not None and key_a == key_b \
                    and parents.get((sources[ref_a][0], a.name.lower())) == sources[target][0] \
                    and keys.get((sources[target][0], b.name.lower())) == (sources[target][0], b.name.lower()):
                return True
    return False


def _additive_measure(select: exp.Select | None, metric: int) -> bool:
    """True when the metric is a non-distinct COUNT/SUM per group of a GROUP BY without HAVING/LIMIT."""
    return _pie_problem(select, metric) is None


def _is_month_column(select: exp.Select | None, index: int, rows: list) -> bool:
    """A text column built with DATE_FORMAT(col, '%Y-%m') whose values are all YYYY-MM."""
    node = _output_expression(select, index)
    if not isinstance(node, exp.TimeToStr):
        return False
    fmt = node.args.get("format")
    if not isinstance(fmt, exp.Literal) or fmt.this != "%Y-%m":
        return False
    return all(isinstance(row[index], str) and _MONTH.match(row[index]) for row in rows)


def _number(value: object, kind: str) -> int | float | None:
    """Plotting number for a metric value; the exact original stays in y_exact."""
    if isinstance(value, bool):
        return None
    if kind == "integer":
        return value if isinstance(value, int) else None
    try:
        number = float(Decimal(value)) if isinstance(value, str) else float(value)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_date(value: object, kind: str) -> dt.date | dt.datetime:
    if not isinstance(value, str):
        raise TypeError("date values are ISO strings")
    if kind == "month":
        return dt.date.fromisoformat(value + "-01")
    return dt.datetime.fromisoformat(value) if "T" in value else dt.date.fromisoformat(value)
