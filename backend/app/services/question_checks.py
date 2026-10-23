"""Deterministic checks on the resolved question before SQL generation, for meanings the data
cannot supply. Each check returns a focused clarifying question, or None.

1. Payment timing. billing has bill_date (when a bill was issued) and the current payment_status, but
   no payment date. A question about WHEN bills were paid ("paid in March", "payment date", "when
   were they paid") is answered only after the user accepts bill_date. Questions that already
   mention the bill/issue date are not asked again.
2. Business measures that the data does not define (GLOSSARY): a ratio, volume, revenue or
   profitability term with no definition in the question gets a clarification listing measures that
   do exist. A volume or revenue term the user defines ("based on the number of patients ...",
   "measured by ...") is accepted. Ratios always need an explicit definition, and profit or margin
   cannot be computed because no costs or expenses are stored for the hospital.

3. Keeping every row (dropped_rows, checked on generated SQL before it runs). When the question asks
   for ALL appointments/patients/doctors/treatments ("all appointments", "every patient", "including
   doctors without ..."), and not only those with a match ("only appointments that have invoices"),
   every row of that table must survive the joins and WHERE, whichever table comes first in FROM. The
   rules come from the schema's NOT NULL foreign keys (see dropped_rows). The first SQL attempt then goes
   through the existing single correction; a correction that still drops rows is not executed.

These are explicit rules with listed terms, not model guesses; terms not listed are left to the SQL
model's own NEEDS_CLARIFICATION rule. They never run SQL and never change the question.
"""

import re
from functools import lru_cache

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.app.services.sql_validator import (
    DIALECT, _column_key, _conjuncts, _Reject, _resolve_sources, required_parents, schema_keys,
)

_ACCEPTS_BILL_DATE = re.compile(r"bill[_ ]date|\bissued?\b|issue date|billing date|date of the bill|\bdated\b", re.I)
_PAYMENT_TIMING = [
    re.compile(r"\bwhen\b[^?.]*\b(paid|settled|pay|pays|paying|settle|settles)\b", re.I),
    re.compile(r"\b(payment|paid|settlement)\s+(date|time|day)s?\b", re.I),
    re.compile(r"\b(paid|settled)\s+(in|during|on|between|before|after|since|within|by|over|last|this|yesterday|today)\b", re.I),
    re.compile(r"\b(pay|pays|paying|settle|settles)\b[^?.]*\b(in|during|on|between|before|after|since|last|this)\s+"
               r"(\d|january|february|march|april|may|june|july|august|september|october|november|december|week|month|year|quarter)", re.I),
    re.compile(r"\bdate\s+(it|they|each bill|the bills?)\s+(was|were)\s+paid\b", re.I),
    re.compile(r"\b(what|which)\s+(date|day|time|month)\b[^?.]*\b(paid|settled)\b", re.I),
]
PAYMENT_CLARIFICATION = ("The data does not record when a bill was paid: billing has bill_date, the date the bill was "
                         "issued, and the current payment status. Should I use bill_date instead?")

_DEFINED = re.compile(r"\b(based on|measured (by|as)|defined as|calculated as|meaning|i\.e\.|as the (number|count|total|sum) of"
                      r"|in terms of|by (the )?(number|count|total|sum) of|counting)\b", re.I)
_RATIO_DEFINED = re.compile(r"\b(defined as|calculated as|divided by|out of|per\s+\w+|over (the )?total|share of|"
                            r"percentage of|proportion of)\b", re.I)

# term pattern -> (kind, plain name, measures that do exist)
GLOSSARY = [
    (re.compile(r"\b(operating|operational|efficiency|utili[sz]ation|performance|occupancy)\s+(ratio|rate|index|score)\b", re.I),
     "ratio", ["the share of the total billed amount", "the number of appointments",
               "the share of appointments that were completed"]),
    (re.compile(r"\b(operational|operating)\s+(volume|activity|load)\b|\bworkload\b|\bthroughput\b", re.I),
     "volume", ["the number of appointments", "the number of distinct patients", "the number of treatments"]),
    (re.compile(r"\b(revenue|income|earnings|turnover|sales)\b", re.I),
     "revenue", ["the total billed amount (all bills)", "the total amount of bills marked Paid"]),
    (re.compile(r"\b(profit|profitability|margin|net income|expenses?)\b", re.I),
     "profit", []),
]


def payment_timing(question: str) -> str | None:
    """Clarification when the question asks when bills were paid and does not already accept bill_date."""
    if _ACCEPTS_BILL_DATE.search(question):
        return None
    return PAYMENT_CLARIFICATION if any(p.search(question) for p in _PAYMENT_TIMING) else None


def undefined_measure(question: str) -> str | None:
    """Clarification for a listed business measure the question does not define."""
    for pattern, kind, options in GLOSSARY:
        match = pattern.search(question)
        if not match:
            continue
        term = match.group(0)
        if kind == "profit":
            return (f"\"{term}\" cannot be calculated from this data: it stores billed amounts and treatment costs, "
                    "but no hospital expenses. Would the total billed amount or the total of paid bills help instead?")
        if kind == "ratio" and _RATIO_DEFINED.search(question):
            continue
        if kind in ("volume", "revenue") and _DEFINED.search(question[match.end():]):
            continue
        if kind == "revenue" and re.search(r"\b(billed|paid|bill amount|invoice)\b", question, re.I):
            continue
        choices = ", ".join(options[:-1]) + f" or {options[-1]}"
        return (f"\"{term}\" is not defined in this data. Which measure do you mean: {choices}? "
                "You can also describe how to calculate it.")
    return None


def check(question: str) -> str | None:
    """The first clarification any check asks for, or None."""
    return payment_timing(question) or undefined_measure(question)


# Words for each table's rows, including paraphrases ("visits" are appointments; "reason for visit" is a column).
PARAPHRASES = {
    "appointments": r"appointments?|(?<!reason for )(?<!reasons for )visits?|consultations?|bookings?",
    "patients": r"patients?",
    "doctors": r"doctors?|physicians?",
    "treatments": r"treatments?|procedures?",
}
_ENTITY = "(" + "|".join(PARAPHRASES.values()) + ")"
_ATTRIBUTE = r"(?!\s+(?:status|statuses|types?|reasons?|dates?|times?)\b)"  # "every appointment status" is not rows
# "all/every [of] [the] [June 2023] [Dr. David Taylor's] appointments". Case-sensitive on purpose: only
# capitalised words, years or possessives may stand between "all" and the entity, so "all bills of patients"
# is not "all patients".
_KEEP_ALL = re.compile(r"\b(?i:all|every)\s+(?:(?i:of)\s+)?(?:(?i:the)\s+)?(?:(?:[A-Z][\w.-]*|\w+'s|\d{4})\s+){0,4}?"
                       r"(?i:" + _ENTITY + r")\b" + _ATTRIBUTE)
# "<entities> ... without / with no / nobody ...", with no other entity in between, so "including doctors
# with no appointments" names doctors.
_KEEP_UNMATCHED = re.compile(
    _ENTITY + r"(?:(?!\b" + _ENTITY + r"\b)[^.?;]){0,60}?"
    r"\b(?:without|with no|with none|with zero|that have no|even if|even those|never|nobody|no one|nothing"
    r"|not (?:yet )?(?:billed|invoiced|treated)"
    r"|unbilled|uninvoiced)\b", re.I)


def _table_of(word: str) -> str:
    return next(t for t, words in PARAPHRASES.items() if re.fullmatch(words, word.lower()))
_DETAIL = r"(?!\s+(?:amounts?|totals?|costs?|dates?|counts?|numbers?|details?))"
# Wording that asks only for rows with a match ("only appointments that have invoices"): never "keep all".
_ONLY_MATCHING = re.compile(
    r"\bonly\s+(?:those\s+|the\s+ones\s+|" + _ENTITY + r"\s+)?(?:with|that|which|having|where)\b"
    r"|\bonly\s+(?:invoiced|billed|treated)\b"
    r"|\b(?:that|which)\s+(?:have|has|had)\s+(?!no\b|not\b)(?:an?\s+|at least one\s+|one or more\s+|any\s+|some\s+)?"
    r"(?:\w+\s+)?(?:invoices?|bills?|treatments?)\b" + _DETAIL +
    r"|\bwith\s+(?:at least one|one or more|an?)\s+(?:\w+\s+)?(?:invoice|bill|treatment)\b" + _DETAIL +
    r"|\bwith\s+(?:invoices|bills|treatments)\b" + _DETAIL +
    r"|\bhaving\s+(?:an?\s+|at least one\s+)?(?:invoices?|bills?|treatments?)\b", re.I)


def only_matching(question: str | None) -> bool:
    """The question asks only for rows that have a match ("only appointments that have invoices")."""
    return bool(question and _ONLY_MATCHING.search(question))


def requested_table(question: str | None) -> str | None:
    """The table whose rows the question asks to keep in full ("all appointments", "every doctor",
    "patients without ..."), or None, also when the question asks only for rows that have a match."""
    if not question or only_matching(question):
        return None
    match = _KEEP_ALL.search(question) or _KEEP_UNMATCHED.search(question)
    if not match:
        return None
    return _table_of(match.group(1))


def _ancestors(table: str) -> frozenset[str]:
    """Tables that every row of `table` reaches through NOT NULL foreign keys (its parents, transitively)."""
    found, frontier = set(), {table}
    while frontier:
        frontier = {parent for (child, _), parent in required_parents().items() if child in frontier} - found
        found |= frontier
    return frozenset(found)


def _parent_link(on: exp.Expression | None, child: str, parent: str, sources: dict[str, tuple[str, int]]) -> bool:
    """ON contains child.fk = parent.key for a NOT NULL foreign key from child's table to parent's table."""
    for cond in _conjuncts(on) if on is not None else []:
        if not isinstance(cond, exp.EQ) or not all(isinstance(s, exp.Column) for s in (cond.this, cond.expression)):
            continue
        for a, b in ((cond.this, cond.expression), (cond.expression, cond.this)):
            (ref_a, key_a), (ref_b, key_b) = _column_key(a, sources), _column_key(b, sources)
            if ref_a == child and ref_b == parent and key_a is not None and key_a == key_b \
                    and required_parents().get((sources[child][0], a.name.lower())) == sources[parent][0]:
                return True
    return False


def _keeps_nulls(cond: exp.Expression) -> bool:
    """A WHERE condition that can be true for a NULL-extended row (IS NULL, COALESCE/IFNULL)."""
    if any(isinstance(n.expression, exp.Null) and not isinstance(n.parent, exp.Not) for n in cond.find_all(exp.Is)):
        return True
    return any(True for _ in cond.find_all(exp.Coalesce))


def dropped_rows(question: str, sql: str) -> str | None:
    """A diagnostic when the question asks for all rows of a table T but the SQL can leave some of them out;
    None when every row is kept, or when that cannot be established (T absent or joined twice).

    It does not matter which table comes first. In join order, T's rows are kept when:
      - FROM is T, or a table every T row reaches through NOT NULL foreign keys (doctors for appointments);
      - T joins (INNER or LEFT) along its own NOT NULL foreign key to such a complete table, or is RIGHT JOINed;
      - every other INNER JOIN follows a NOT NULL foreign key from a complete table to its parent
        (appointments -> doctors, patients); tables T may have no row in (treatments, billing) are LEFT JOINed;
      - WHERE does not filter such an optional table unless the condition keeps NULLs (IS NULL, COALESCE).
        Conditions on T and its parents are the user's own restrictions and are fine.
    """
    table = requested_table(question)
    if not table:
        return None
    try:
        select = sqlglot.parse_one(sql, read=DIALECT)
        sources = _resolve_sources(select) if isinstance(select, exp.Select) else {}
    except (SqlglotError, _Reject):
        return None
    targets = [ref for ref, (name, _) in sources.items() if name == table]
    if len(targets) != 1:
        return None
    target = targets[0]
    order = sorted(sources, key=lambda ref: sources[ref][1])
    ancestors = _ancestors(table)
    first = sources[order[0]][0]
    complete = {order[0]} if first == table or first in ancestors else set()
    problems = [] if complete else [f"the query starts FROM {first}, which leaves out {table} without a {first} row"]
    for join in select.args.get("joins") or []:
        ref = join.this.alias or join.this.name
        name, side, on = sources[ref][0], (join.args.get("side") or "").upper(), join.args.get("on")
        if side == "RIGHT":
            if ref == target:
                complete, problems = {target}, []  # every T row is kept; the tables before it become optional
            elif target in complete:
                problems.append(f"RIGHT JOIN {name} keeps only the rows that match {name}")
            else:
                complete = {ref} if name in ancestors else set()
            continue
        if ref == target:
            if any(_parent_link(on, target, c, sources) for c in complete):
                complete.add(target)
            elif not problems:
                problems.append(f"{table} are joined to a table they may have no row in")
        elif any(_parent_link(on, c, ref, sources) for c in complete):
            complete.add(ref)
        elif side == "LEFT":
            continue
        elif target in complete:
            problems.append(f"INNER JOIN with {name} drops {table} that have no {name} row")
        else:
            complete = set()  # an inner join before T restricts the rows T would attach to
    if target not in complete and not problems:
        problems.append(f"{table} are joined to a table they may have no row in")
    where = select.args.get("where")
    for cond in _conjuncts(where.this) if where is not None and target in complete else []:
        optional = sorted({sources[r][0] for r, _ in (_column_key(c, sources) for c in cond.find_all(exp.Column))
                           if r in sources and r not in complete})
        if optional and not _keeps_nulls(cond):
            problems.append(f"the WHERE condition {cond.sql(dialect=DIALECT)} removes {table} that have no "
                            f"{' or '.join(optional)} row (put it in that table's ON clause)")
    if not problems:
        return None
    return f"The question asks for all {table}, but " + "; ".join(problems) + "."


def keep_all_advice(table: str) -> str:
    """How to keep every row of `table` (for the correction diagnostic and the SQL prompt)."""
    optional = sorted(_dependents().get(table, ()))
    joins = f", LEFT JOIN {' and '.join(optional)}" if optional else ""
    return f"Start FROM {table}{joins}; conditions on those go in ON, so unmatched rows stay (NULL values)."


def keep_all_hint(question: str | None) -> str:
    """A fact for the SQL prompt when the question asks for every row of a table ("" otherwise)."""
    table = requested_table(question)
    return f"The question asks for all {table}. {keep_all_advice(table)}" if table else ""


@lru_cache
def _dependents() -> dict[str, frozenset[str]]:
    """{table: tables that reference it directly or through other tables}, from the schema's foreign keys."""
    parents: dict[str, set[str]] = {}
    for (table, _), (target, _) in schema_keys().items():
        if target != table:
            parents.setdefault(table, set()).add(target)
    result = {}
    for root in {t for (t, _) in schema_keys()}:
        found, frontier = set(), {root}
        while frontier:
            frontier = {child for child, ps in parents.items() if ps & frontier} - found
            found |= frontier
        result[root] = frozenset(found)
    return result
