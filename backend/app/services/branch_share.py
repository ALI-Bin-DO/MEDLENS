"""Percentages per hospital branch, computed by the application from validated query results.

Patients are not assigned to a hospital branch in the data: patients reach a branch only through their
appointments' doctors (appointments.doctor_id -> doctors.hospital_branch). "The percentage of patients at
each hospital" therefore has two defensible meanings, and MedLens asks which one is meant:

    patients      each branch's distinct patients with appointments, as a percentage of all distinct patients
                  with appointments in the same scope. A patient seen at several branches counts in each, so
                  the percentages can add up to more than 100% (not parts of one whole: bars, no donut).
    appointments  each branch's share of all appointments in the same scope. Every appointment has exactly
                  one doctor and so one branch: the shares form a whole (a donut is fine).

Registered patients are not used as a denominator: they have no branch.

A percentage needs a second aggregate (the denominator) that one supported query cannot return (no
subqueries or window functions). So the SQL model only writes the per-branch count, in a checked shape
(numerator_problem), and this module:
    - patients: derives the denominator query from that validated SQL (same FROM, JOINs and WHERE; the
      SELECT becomes COUNT(DISTINCT <the same patient column>); GROUP BY and ORDER BY removed), which runs
      through the same validator and read-only repository;
    - appointments: uses the sum of the branch counts (exact, since branches partition appointments);
    - computes percent = count * 100 / denominator with Decimal, rounded half-up to one decimal.
The model never does the arithmetic. The answer states the numerator, the denominator and the derivation.
"""

import re
from decimal import ROUND_HALF_UP, Decimal

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.app.repositories.mysql_repository import QueryResult
from backend.app.services.sql_validator import DIALECT, _column_key, _resolve_sources

PATIENTS, APPOINTMENTS = "patients", "appointments"
CLARIFICATION_PREFIX = "Patients are not assigned to a hospital branch in the data"
MARKERS = {
    PATIENTS: "(measure: distinct patients with appointments at each branch as a percentage of all distinct "
              "patients with appointments)",
    APPOINTMENTS: "(measure: each branch's share of all appointments)",
}
_PERCENT = re.compile(r"\b(?:percent(?:age)?s?|share|proportion|fraction)\b|%", re.I)
_PATIENT = re.compile(r"\bpatients?\b", re.I)
_BRANCH = re.compile(r"\b(?:hospitals?|branch(?:es)?)\b", re.I)
_ALLOWED_TABLES = {"appointments", "doctors", "patients"}  # joins that keep one row per appointment


def asks_patient_share(question: str) -> bool:
    """A percentage/share of patients per hospital branch, whose measure has not been chosen yet."""
    return bool(_PERCENT.search(question) and _PATIENT.search(question) and _BRANCH.search(question)) \
        and chosen(question) is None


def chosen(question: str | None) -> str | None:
    """The measure a clarified question carries (its marker), or None."""
    return next((kind for kind, marker in MARKERS.items() if question and marker in question), None)


def clarification() -> str:
    return (f"{CLARIFICATION_PREFIX}; they are linked to a branch through their appointments' doctors. "
            "Which percentage do you mean? 1. Patients: each branch's distinct patients with appointments as a "
            "percentage of all distinct patients with appointments in the same scope (a patient seen at several "
            "branches counts in each, so the total can exceed 100%). 2. Appointments: each branch's share of all "
            "appointments in the same scope. Period and status filters in your question are kept.")


def choice(reply: str) -> str | None:
    """Which measure a reply to the clarification picks ("1", "the patients one", "share of appointments")."""
    text = reply.strip().lower()
    patients = re.search(r"\b(?:1|first)\b|^\s*one\b|\bpatients?\b", text)
    appointments = re.search(r"\b(?:2|second)\b|^\s*two\b|\bappointments?\b", text)
    if bool(patients) == bool(appointments):
        return None
    return PATIENTS if patients else APPOINTMENTS


_PERCENT_OF = re.compile(r"\b(?:the\s+)?(?:percent(?:age)?s?|share|proportion|fraction)\s+of\s+(?:the\s+)?(?:distinct\s+)?patients\b|%\s*of\s+patients\b", re.I)


def numerator_question(kind: str, question: str) -> str:
    """The question the SQL model answers: the per-branch count, with the scope (period, status, people) of
    the clarified question kept word for word. It never mentions a percentage, so the model is not tempted to
    compute one (window functions and subqueries are not supported, and the arithmetic is done here)."""
    base = question.replace(MARKERS[kind], "").strip().rstrip("?. ")
    count = "the number of distinct patients with appointments" if kind == PATIENTS else "the number of appointments"
    rewritten, n = _PERCENT_OF.subn(count, base, count=1)
    if not n:
        rewritten = _PERCENT.sub("number", base, count=1)
    return f"{rewritten} (one row per hospital branch)?"


def facts(kind: str) -> str:
    """Instructions for the SQL model: only the per-branch count, never the percentage."""
    if kind == PATIENTS:
        measure = "COUNT(DISTINCT a.patient_id) AS patient_count"
    else:
        measure = "COUNT(*) AS appointment_count"
    return ("Branch percentage: return one row per branch with exactly d.hospital_branch and "
            f"{measure}, FROM appointments AS a JOIN doctors AS d ON a.doctor_id = d.doctor_id, with the "
            "question's filters, GROUP BY d.hospital_branch. Do not compute percentages or totals; MedLens "
            "computes them from this result.")


def _measure(select: exp.Select) -> tuple[exp.Expression | None, exp.Expression | None]:
    """(branch column, count expression) of the SELECT list, in either order."""
    items = [e.this if isinstance(e, exp.Alias) else e for e in select.expressions]
    branch = next((e for e in items if isinstance(e, exp.Column) and e.name.lower() == "hospital_branch"), None)
    count = next((e for e in items if isinstance(e, exp.Count)), None)
    return branch, count


def numerator_problem(kind: str, sql: str) -> str | None:
    """Why the SQL is not the per-branch count this module can turn into percentages (None if it is)."""
    try:
        select = sqlglot.parse_one(sql, read=DIALECT)
        sources = _resolve_sources(select) if isinstance(select, exp.Select) else None
    except (SqlglotError, Exception):
        return None  # validation reports it
    if sources is None:
        return None
    expected = "COUNT(DISTINCT a.patient_id)" if kind == PATIENTS else "COUNT(*)"
    advice = f"return d.hospital_branch and {expected} per branch, GROUP BY d.hospital_branch, no percentages"
    if any(name not in _ALLOWED_TABLES for name, _ in sources.values()) or "appointments" not in {n for n, _ in sources.values()}:
        return f"a branch percentage counts appointments joined to doctors only (no treatments or billing); {advice}"
    group = select.args.get("group")
    branch, count = _measure(select)
    if group is None or len(group.expressions) != 1 or not isinstance(group.expressions[0], exp.Column) \
            or group.expressions[0].name.lower() != "hospital_branch" or branch is None or count is None \
            or len(select.expressions) != 2:
        return f"the query must have exactly two columns, the branch and its count; {advice}"
    if select.args.get("having") or select.args.get("limit") or select.args.get("distinct"):
        return f"every branch is needed for the percentages (no HAVING, LIMIT or DISTINCT); {advice}"
    distinct = count.this if isinstance(count.this, exp.Distinct) else None
    if kind == PATIENTS:
        column = distinct.expressions[0] if distinct is not None and len(distinct.expressions) == 1 else None
        ref, _ = _column_key(column, sources) if isinstance(column, exp.Column) else ("", None)
        if not isinstance(column, exp.Column) or column.name.lower() != "patient_id" or sources.get(ref, ("",))[0] != "appointments":
            return f"the count must be COUNT(DISTINCT a.patient_id) of the appointments table; {advice}"
    elif distinct is not None and not (len(distinct.expressions) == 1 and isinstance(distinct.expressions[0], exp.Column)
                                       and distinct.expressions[0].name.lower() == "appointment_id"):
        return f"the count must count appointments; {advice}"
    return None


def denominator_sql(sql: str) -> str:
    """The same FROM, JOINs and WHERE as the validated numerator, counting all distinct patients once."""
    select = sqlglot.parse_one(sql, read=DIALECT)
    _, count = _measure(select)
    total = exp.alias_(count.copy(), "all_patients")
    select = select.copy()
    select.set("expressions", [total])
    for part in ("group", "order", "having", "limit"):
        select.set(part, None)
    return select.sql(dialect=DIALECT)


def _percent(count: Decimal, total: Decimal) -> str | None:
    if total == 0:
        return None
    return str((count * 100 / total).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def derive(kind: str, result: QueryResult, denominator: int | None) -> tuple[QueryResult, str]:
    """Rows (branch, count, denominator, percent) and the explanation shown with the answer.
    denominator: the distinct-patient total for PATIENTS; ignored for APPOINTMENTS (sum of the counts)."""
    branch_i = next(i for i, c in enumerate(result.columns) if c.lower() == "hospital_branch")
    count_i = 1 - branch_i
    counts = [Decimal(str(row[count_i] or 0)) for row in result.rows]
    total = Decimal(denominator if kind == PATIENTS else sum(counts))
    noun = "patients" if kind == PATIENTS else "appointments"
    rows = [[row[branch_i], row[count_i], int(total), _percent(c, total)] for row, c in zip(result.rows, counts)]
    columns = ["hospital_branch", f"{noun[:-1]}_count", f"all_{noun}", f"{noun[:-1]}_percent"]
    derived = QueryResult(result.sql, columns, rows, len(rows), result.truncated, result.row_cap,
                          result.duration_seconds, ["string", "integer", "integer", "decimal"])
    added = sum((Decimal(r[3]) for r in rows if r[3] is not None), Decimal(0))
    if kind == PATIENTS:
        note = (f"Percentages computed by MedLens, not by the model: {noun[:-1]}_count (distinct patients with "
                f"appointments at the branch) ÷ all_patients ({int(total)} distinct patients with appointments in the "
                "same scope, from a second read-only query with the same joins and filters) × 100, rounded to one "
                f"decimal. A patient seen at several branches counts in each, so the percentages add up to {added}%; "
                "they are not parts of one whole.")
    else:
        note = (f"Percentages computed by MedLens, not by the model: appointment_count ÷ all_appointments "
                f"({int(total)}, the sum of the branch counts; every appointment has one doctor and one branch) × 100, "
                f"rounded to one decimal (they add up to {added}%).")
    if total == 0:
        note = "No appointments match this scope, so no percentage can be computed (the percentages are empty)."
    return derived, note
