"""A compact description of what the user asked for, and checks that compare a generated query with it.

The description (RequestSpec) is derived from the resolved question, the user's latest message, the
previous turn's description and the documented schema, never from retrieved example SQL. It records:
    - the requested entity, and which table must keep all its rows (question_checks.requested_table);
    - the grain of amounts: individual invoice amounts or totals;
    - whether zero for missing values was asked for;
    - the grouping, and which groupings a follow-up replaced or removed;
    - each explicit period and the date column it describes (appointment_date, bill_date, ...);
    - the request's own words, which are the only allowed source of filter values.
It goes into the SQL prompt's facts block, and mismatches() checks the generated SQL against it
before execution. A mismatch goes through the existing single correction; if it remains, nothing is run.

These are listed, conservative rules: whatever cannot be established is not checked, and passing
them does not mean the SQL is right.
"""

import datetime as dt
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.app.services import clock, question_checks
from backend.app.services.sql_validator import DIALECT, key_path, schema_keys

# --- grouping dimensions -------------------------------------------------------------------------

# (name, words, SQL columns that express it). Order matters: longer phrases first.
DIMENSIONS = [
    ("payment method", r"payment\s+methods?", {"payment_method"}),
    ("payment status", r"payment\s+status(?:es)?", {"payment_status"}),
    ("treatment type", r"treatment\s+types?|types?\s+of\s+treatments?", {"treatment_type"}),
    ("reason for visit", r"reasons?\s+for\s+(?:the\s+)?visits?|visit\s+reasons?", {"reason_for_visit"}),
    ("insurance provider", r"insurance\s+providers?|insurers?", {"insurance_provider"}),
    ("hospital branch", r"(?:hospital\s+)?branch(?:es)?|hospitals?", {"hospital_branch"}),
    ("specialization", r"speciali[sz]ations?|specialt(?:y|ies)", {"specialization"}),
    ("doctor", r"doctors?|physicians?", {"doctor_id"}),
    ("patient", r"patients?", {"patient_id"}),
    ("status", r"(?:appointment\s+)?status(?:es)?", {"status"}),
    ("gender", r"genders?", {"gender"}),
    ("month", r"(?:calendar\s+)?months?", set()),
    ("year", r"(?:calendar\s+)?years?", set()),
    # after "status", so "appointment status" stays one dimension; checked only when the query aggregates
    ("appointment", r"appointments?", {"appointment_id"}),
]
_DIM_WORDS = "|".join(f"(?P<d{i}>{words})" for i, (_, words, _) in enumerate(DIMENSIONS))
# "for each X", "per X", "by X", "grouped by X", ... A capitalised word or an ID after the dimension is a
# person ("by Doctor Linda Brown"), not a grouping; "sorted by" and "average ... per" are not groupings.
_GROUPING = re.compile(
    r"(?<!sorted )(?<!ordered )(?<!order )(?<!sort )(?<!counting )(?<!including )(?<!not )(?<!than )(?<!of )"
    r"(?<!weighting )(?<!treating )"
    r"\b(?i:for\s+each|for\s+every|grouped\s+by|broken\s+down\s+by|split\s+by|in\s+each|per|each|every|by)\s+"
    r"(?:(?i:the|single|individual)\s+)?(?i:" + _DIM_WORDS + r")\b(?!\s*(?:[A-Z][a-z]|[A-Z]?\d))"
    # "each doctor specialization": the dimension word is an adjective of the next one
    r"(?!\s+(?i:speciali[sz]ations?|specialt\w*|branch\w*|hospital\w*|status\w*|genders?|insurance|payment|types?)\b)")
_RATIO_BEFORE = re.compile(r"\b(?:average|mean|ratio|rate|on average)\b(?:\s+\S+){0,4}\s*$", re.I)


def _dimension(match: re.Match) -> str:
    return next(DIMENSIONS[int(k[1:])][0] for k, v in match.groupdict().items() if v)


def grouping_phrases(text: str) -> list[tuple[str, re.Match]]:
    """(dimension, match) for each grouping phrase in text."""
    found = []
    for m in _GROUPING.finditer(text or ""):
        if m.group(0).lower().startswith("per") and _RATIO_BEFORE.search(text[: m.start()]):
            continue
        found.append((_dimension(m), m))
    return found


def grouping_dims(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(d for d, _ in grouping_phrases(text)))


# --- grouping changes in a follow-up ------------------------------------------------------------

_ADJECTIVE = r"(?!\s+(?:speciali[sz]ations?|specialt\w*|branch\w*|hospital\w*|status\w*|genders?|insurance|payment|types?)\b)"
_DIM_ANY = re.compile(r"\b(?:the\s+)?(?:" + _DIM_WORDS + r")\b" + _ADJECTIVE, re.I)  # "patient gender" is one dimension
_CUED_DIM = re.compile(r"\b(?:by|per|for\s+each|each|across|into)\s+(?:the\s+)?(?:\w+\s+)?(?:" + _DIM_WORDS + r")\b"
                       + _ADJECTIVE, re.I)
# "the month before", "previous year", "next month": a period follow-up (clock.shift_from_history), not a grouping
_RELATIVE_PERIOD = re.compile(r"\b(?:month|year)s?\s+(?:before|after|earlier|later|prior)\b|"
                              r"\b(?:previous|prior|preceding|next|following|last|same|this)\s+(?:month|year)\b", re.I)
_ADD = re.compile(r"\b(?:also|as\s+well|too|additionally|further|in\s+addition)\b|^\s*and\b|\bbreak\s+(?:it|that|this|them)?\s*down\b",
                  re.I)
_REMOVE = re.compile(r"\b(?:without|drop|remove|no\s+longer|don'?t|do\s+not|not|stop)\b", re.I)
_TOTAL = re.compile(r"\b(?:overall|in\s+total|all\s+together|altogether|combined|one\s+total)\b", re.I)
_REPLACE = re.compile(r"\b(?:by|per|to|group(?:ed)?|instead|rather)\b", re.I)
_INSTEAD_OF = re.compile(r"\b(?:instead\s+of|rather\s+than)\s+(?:by\s+|per\s+)?(?:the\s+)?(?:" + _DIM_WORDS + r")", re.I)
FOLLOW_UP_MAX_WORDS = 10
# A message that names its own measure or subject is a new question, not a change of the previous grouping.
_NEW_QUESTION = re.compile(r"\b(?:how\s+many|how\s+much|what|which|who|list|total|average|count|sum|number\s+of|"
                           r"bills?|invoices?|appointments?|visits?|treatments?|amounts?|costs?)\b", re.I)


@dataclass(frozen=True)
class GroupingChange:
    operation: str  # "replace", "add" or "remove"
    dims: tuple[str, ...]  # the grouping after the change
    removed: tuple[str, ...]  # groupings the change took away


def grouping_change(message: str, previous: tuple[str, ...]) -> GroupingChange | None:
    """A short follow-up that only changes the grouping of the previous question ("Do it by hospital
    branch", "Also break it down by doctor", "without the status split", "overall"); None otherwise."""
    if not message or len(message.split()) > FOLLOW_UP_MAX_WORDS or _RELATIVE_PERIOD.search(message) \
            or _NEW_QUESTION.search(_DIM_ANY.sub(" ", message)):
        return None  # ("appointment status" names a dimension, not a new subject)
    named = [_dimension(m) for m in _DIM_ANY.finditer(message)]
    old = [_dimension(m) for m in _INSTEAD_OF.finditer(message)]
    new = [d for d in dict.fromkeys(_dimension(m) for m in _CUED_DIM.finditer(message)) if d not in old]
    if _TOTAL.search(message) and not new:
        return GroupingChange("remove", (), tuple(previous))
    if not named:
        return None
    if _REMOVE.search(message) and not _ADD.search(message) and not new:
        gone = tuple(d for d in previous if d in named)
        return GroupingChange("remove", tuple(d for d in previous if d not in gone), gone) if gone else None
    if not new:
        return None
    if _ADD.search(message) and not old:
        return GroupingChange("add", tuple(dict.fromkeys([*previous, *new])), ())
    if _REPLACE.search(message) and new:
        gone = tuple(d for d in previous if d in old) if old else tuple(d for d in previous if d not in new)
        kept = tuple(d for d in previous if d not in gone and d not in new)
        return GroupingChange("replace", tuple(dict.fromkeys([*kept, *new])), gone)
    return None


def apply_grouping_change(previous_question: str, change: GroupingChange, resolved: str) -> str:
    """The resolver's question if its grouping matches the change; otherwise the previous question with
    the change applied in code (the resolver is a small model and does not always apply it)."""
    if set(grouping_dims(resolved)) == set(change.dims) and not set(grouping_dims(resolved)) & set(change.removed):
        return resolved
    text = previous_question.rstrip()
    end = text[-1] if text and text[-1] in "?." else ""
    body = text[:-1] if end else text
    phrases = grouping_phrases(body)
    if change.operation == "add":
        added = [d for d in change.dims if d not in grouping_dims(body)]
        return f"{body}, broken down by {' and '.join(added)}{end or '?'}" if added else resolved
    # replace / remove: rewrite the phrases of the removed dimensions, right to left
    replacement = [d for d in change.dims if d not in grouping_dims(body)]
    for dim, m in sorted(phrases, key=lambda p: -p[1].start()):
        if dim not in change.removed:
            continue
        span = next(m.span(k) for k, v in m.groupdict().items() if v)
        if replacement:
            body = body[: span[0]] + replacement.pop(0) + body[span[1]:]
        elif m.group(0).split()[0].lower() in ("each", "every"):
            plural = dim + ("es" if dim.endswith(("ch", "s")) else "s")
            body = body[: m.start()] + f"all {plural} together" + body[m.end():]
        else:
            body = (body[: m.start()].rstrip(" ,") + body[m.end():]).strip()
    if replacement:  # nothing to rewrite: the previous question had no grouping phrase
        body += f" for each {' and '.join(replacement)}"
    return body + (end or "?")


# --- the description ---------------------------------------------------------------------------

DATE_COLUMNS = ("appointment_date", "treatment_date", "bill_date", "registration_date", "date_of_birth")
_DATE_CUES = [  # words that say which date a period describes
    ("bill_date", r"issued|issue\s+dates?|billed|invoiced|bills?|invoices?|billing|bill\s+dates?"),
    ("appointment_date", r"appointments?|visits?|consultations?|bookings?|booked|scheduled|seen|saw|visited"),
    ("treatment_date", r"treatments?|treated|procedures?|performed"),
    ("registration_date", r"registered|registrations?|signed\s+up|joined"),
    ("date_of_birth", r"born|births?"),
]
_CUE = re.compile("|".join(f"(?P<{col}>\\b(?:{words})\\b)" for col, words in _DATE_CUES), re.I)
_INDIVIDUAL = re.compile(r"\b(?:each|every|individual|separate|any)\s+(?:invoice|bill)s?\b"
                         r"(?!\s+(?:issue|date|month|year|status|payment|count))|\bone\s+row\s+per\s+(?:invoice|bill)\b"
                         r"|\b(?:invoice|bill)\s+amounts?\b|\bamounts?\s+of\s+(?:each|its|their)\s+(?:invoice|bill)s?\b", re.I)
_AGGREGATE = re.compile(r"\b(?:total|totals|sum|summed|overall|combined|aggregate|average|avg|mean|how\s+much|per\s+appointment"
                        r"|per\s+patient|per\s+doctor)\b", re.I)
_MONEY = re.compile(r"\b(?:amounts?|billed|costs?|totals?|sums?)\b", re.I)
_ZERO = re.compile(r"(?<![\d.,])\b0\b(?![.,]?\d)|\bzero(?:s|es)?\b|\binstead\s+of\s+(?:null|blank|empty|missing)\b", re.I)


_COUNTED = {**question_checks.PARAPHRASES, "billing": r"bills?|invoices?"}
_COUNT_OF = re.compile(r"\b(?:how\s+many|number\s+of|count\s+(?:of\s+)?(?:the\s+)?)\s+(?:[\w-]+\s+){0,2}?"
                       r"(" + "|".join(_COUNTED.values()) + r")\b", re.I)  # "how many completed appointments"


def counted_table(question: str) -> str | None:
    """The table whose rows "how many X" / "number of X" counts, or None."""
    match = _COUNT_OF.search(question or "")
    if not match:
        return None
    return next(t for t, words in _COUNTED.items() if re.fullmatch(words, match.group(1).lower()))


# Attributes a question may name by a word, and the schema column that stores them (database/schema.sql).
_ATTRIBUTES = [
    (r"\binsur(?:ed|ance\s+providers?|ers?)\b", "patients", "insurance_provider"),
    (r"\bspeciali[sz]\w*|\bspecialt\w*", "doctors", "specialization"),
    (r"\bbranch\w*|\bhospitals?\b", "doctors", "hospital_branch"),
    (r"\b(?:gender|female|male|wom[ae]n|men)\b", "patients", "gender"),
    (r"\b(?:age|aged|born|older|younger)\b", "patients", "date_of_birth"),
    (r"\bregist\w*", "patients", "registration_date"),
    (r"\btreatment\s+types?\b|\btypes?\s+of\s+treatment", "treatments", "treatment_type"),
    (r"\breasons?\s+for\s+(?:the\s+)?visits?\b", "appointments", "reason_for_visit"),
]


def attribute_paths(question: str, base: str | None) -> list[str]:
    """For attributes the question names that are stored outside the base table: the column and the key path
    to it, preferring parents ("insured" from treatments: patients.insurance_provider via appointments)."""
    if not base:
        return []
    lines = []
    for pattern, table, column in _ATTRIBUTES:
        if table == base or not re.search(pattern, question or "", re.I):
            continue
        path = key_path(base, table, upward_only=True) or key_path(base, table)
        if path:
            lines.append(f"- {column} is {table}.{column}; from {base} join with {'; '.join(path)}.")
    return lines


@dataclass(frozen=True)
class Period:
    label: str
    start: dt.date
    end: dt.date  # exclusive
    column: str | None  # the date column the period describes, or None if that cannot be told


@dataclass(frozen=True)
class RequestSpec:
    question: str  # the resolved question
    message: str  # the user's latest message
    keep_all: str | None = None  # table whose rows must all remain
    counts: str | None = None  # "how many X": the table whose rows are counted
    individual_amounts: bool = False  # individual invoice amounts were asked for, not totals
    zero_fill: bool = False  # zero for missing values was asked for
    grouping: tuple[str, ...] = ()
    removed_grouping: tuple[str, ...] = ()  # replaced or removed by a follow-up
    periods: tuple[Period, ...] = ()
    relative_dates: bool = False  # wording such as "last 30 days" that is not an explicit period
    grounded: str = ""  # facts that may supply filter values (matched people's IDs)

    @property
    def words(self) -> str:
        return f"{self.question} {self.message}"

    def describe(self) -> str:
        """The facts-block text: only lines that apply, plus the rule about filter sources.
        Kept short: every character here is taken from the retrieved examples' budget. Date columns go into
        the range lines instead (label_ranges)."""
        lines = []
        paths = attribute_paths(self.question, self.counts or self.keep_all)
        if paths and self.counts:
            lines.append(f"- Count {self.counts} rows, not another table's IDs.")
        lines += paths
        if self.individual_amounts:
            lines.append("- One row per invoice amount; no totals.")
        if not self.zero_fill and not self.keep_all and _MONEY.search(self.question):  # keep_all hint says NULL
            lines.append("- Missing amounts stay NULL, not 0.")
        if self.grouping and self.removed_grouping:
            lines.append(f"- Group by {', '.join(self.grouping)} only, not {', '.join(self.removed_grouping)}.")
        elif self.removed_grouping:
            lines.append(f"- No grouping by {', '.join(self.removed_grouping)}.")
        lines.append("- Filter only on values from the question or these facts.")
        return "Request:\n" + "\n".join(lines)

    def label_ranges(self, ranges: str) -> str:
        """clock.period_ranges text with "col" replaced by the date column each period describes."""
        for p in self.periods:
            if p.column:
                ranges = ranges.replace(f"- {p.label}: col >= '{p.start.isoformat()}' AND col <",
                                        f"- {p.label}: {p.column} >= '{p.start.isoformat()}' AND {p.column} <")
        return ranges


_SENTENCE_END = re.compile(r"(?<!\bDr)(?<!\bMr)(?<!\bMs)(?<!\bMrs)(?<!\bProf)(?<!\bSt)[.?!;](?=\s|$)")


def _period_column(text: str, start: int, end: int) -> str | None:
    """The date column a period at text[start:end] describes: the nearest cue word before it in the same
    sentence, else the nearest one shortly after it."""
    ends = [m.end() for m in _SENTENCE_END.finditer(text, 0, start)]
    sentence_start = ends[-1] if ends else 0
    before = [m for m in _CUE.finditer(text, sentence_start, start)]
    if before:
        return before[-1].lastgroup
    after = _CUE.search(text, end, min(len(text), end + 40))
    return after.lastgroup if after else None


def build(question: str, message: str = "", grounded: str = "", change: GroupingChange | None = None,
          keep_all: str | None = None) -> RequestSpec:
    """The description of one request (see the module docstring)."""
    periods = tuple(Period(label, s, e, _period_column(question, pos, pos_end))
                    for pos, pos_end, label, s, e in clock.period_spans(question))
    individual = bool(_INDIVIDUAL.search(question)) and not _AGGREGATE.search(question)
    return RequestSpec(
        question=question, message=message, keep_all=keep_all or question_checks.requested_table(question),
        counts=counted_table(question),
        individual_amounts=individual, zero_fill=bool(_ZERO.search(f"{question} {message}")),
        grouping=change.dims if change else grouping_dims(question),
        removed_grouping=change.removed if change else (),
        periods=periods, relative_dates=clock.mentions_relative_period(question), grounded=grounded)


# --- checks on the generated SQL ------------------------------------------------------------------

_STATUS_WORDS = {  # documented ENUM values (database/schema.sql) and the words that name them
    "completed": r"complet|attended|took\s+place|happened",
    "cancelled": r"cancel",
    "scheduled": r"schedul|upcoming|booked|planned|future",
    "no-show": r"no-?shows?|missed|did\s*n[o']t\s+(?:show|attend)",
    "paid": r"\bpaid\b|settled",
    "pending": r"pending|outstanding|unpaid|open|due",
    "failed": r"fail|outstanding|unpaid|declined|rejected",
    "cash": r"cash",
    "credit card": r"credit|card",
    "insurance": r"insur",
    "f": r"\bfemales?\b|\bwom[ae]n\b|\bgirls?\b",
    "m": r"\bmales?\b|\bmen\b|\bboys?\b",
}
_NUMBER_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                            "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_NUMBER_WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
                      "ninety": 90, "hundred": 100, "dozen": 12})
_AMOUNT_COLUMNS = {"amount", "cost"}


def _numbers(text: str) -> set[float]:
    found = {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}
    found |= {float(v) for w, v in _NUMBER_WORDS.items() if re.search(rf"\b{w}\b", text, re.I)}
    return found


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _value_requested(column: str, value: str, spec: RequestSpec) -> bool:
    words, lowered = spec.words, spec.words.lower()
    if not value.strip("% "):
        return True  # '' and '%' are technical (blank checks), not business values
    if _norm(value) and _norm(value) in _norm(words):
        return True
    if column.endswith("_id"):
        return value.lower() in spec.grounded.lower()
    pattern = _STATUS_WORDS.get(value.lower())
    if pattern and column in ("status", "payment_status", "payment_method", "gender"):
        return bool(re.search(pattern, lowered))
    if column in ("first_name", "last_name", "status", "payment_status", "payment_method", "gender"):
        return False
    # free-text columns: every word of the value must share a 5-letter prefix with a word of the request
    request_words = re.findall(r"[a-z0-9]+", lowered)
    return all(any(w.startswith(v[:5]) for w in request_words) for v in re.findall(r"[a-z0-9]+", value.lower()) if len(v) > 2)


def _filter_clauses(select: exp.Select) -> list[exp.Expression]:
    clauses = [select.args.get("where"), select.args.get("having")]
    clauses += [j.args.get("on") for j in select.args.get("joins") or []]
    return [c for c in clauses if c is not None]


def _column_side(node: exp.Expression) -> exp.Column | None:
    for side in (node.this, node.args.get("expression")):
        if isinstance(side, exp.Column):
            return side
    return None


def _literals(node: exp.Expression) -> list[exp.Literal]:
    if isinstance(node, exp.In):
        return [e for e in node.expressions if isinstance(e, exp.Literal)]
    if isinstance(node, exp.Between):
        return [e for e in (node.args.get("low"), node.args.get("high")) if isinstance(e, exp.Literal)]
    return [e for e in (node.this, node.args.get("expression")) if isinstance(e, exp.Literal)]


def _date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _filter_problems(select: exp.Select, spec: RequestSpec) -> list[tuple[str, str]]:
    """(problem, the literal as sql_correction's meaning guard names it) for unrequested filter values."""
    problems: list[tuple[str, str]] = []
    year_words = set(re.findall(r"\b(?:19|20)\d{2}", spec.words))
    decades = [int(d) for d in re.findall(r"\b((?:19|20)\d0)s\b", spec.words)]
    numbers = _numbers(spec.words)
    assigned = {p.column for p in spec.periods if p.column}
    for clause in _filter_clauses(select):
        for node in clause.find_all(exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.In, exp.Between):
            column = _column_side(node)
            if column is None:
                continue
            name = column.name.lower()
            for lit in _literals(node):
                text = node.sql(dialect=DIALECT)
                token = repr(lit.this) if lit.is_string else lit.this
                if name in DATE_COLUMNS and lit.is_string and _date(lit.this):
                    day = _date(lit.this)
                    bounds = [p for p in spec.periods if day in (p.start, p.end, p.end - dt.timedelta(days=1))]
                    describes = [p for p in bounds if p.column and p.column != name]
                    cut = [p for p in bounds if isinstance(node, exp.LT) and day == p.end - dt.timedelta(days=1)
                           and day != p.start]
                    if cut:
                        problems.append((f"{text}: '<' excludes the last day of {cut[0].label}; use < "
                                         f"'{cut[0].end.isoformat()}'", token))
                    elif describes and name not in assigned:
                        problems.append((f"{text}: {describes[0].label} describes {describes[0].column}, not {name}; "
                                         f"do not apply the period to {name}", token))
                    elif not bounds and not spec.relative_dates and str(day.year) not in year_words \
                            and not any(d <= day.year <= d + 10 for d in decades) and not spec.periods:
                        problems.append((f"{text}: the question names no such date", token))
                elif lit.is_string and name not in DATE_COLUMNS:
                    if not _value_requested(name, lit.this, spec):
                        problems.append((f"{text}: the value {lit.this!r} is not in the question", token))
                elif not lit.is_string and name not in DATE_COLUMNS:
                    value = float(lit.this)
                    if value not in (0, 1) and value not in numbers:
                        problems.append((f"{text}: the threshold {lit.this} is not in the question", token))
    return list(dict.fromkeys(problems))


def _grouped_dims(select: exp.Select) -> set[str] | None:
    """Dimensions the GROUP BY expresses; None when there is no GROUP BY."""
    group = select.args.get("group")
    if group is None:
        return None
    aliases = {e.alias: e.this for e in select.expressions if isinstance(e, exp.Alias)}
    dims = set()
    for item in group.expressions:
        if isinstance(item, exp.Column) and not item.table and item.name in aliases:
            item = aliases[item.name]
        for col in item.find_all(exp.Column):
            name = col.name.lower()
            if name in DATE_COLUMNS and item is not col:
                fmt = item.sql(dialect=DIALECT).upper()
                dims.add("year" if ("YEAR(" in fmt or "'%Y'" in fmt) else "month")
            dims |= {d for d, _, cols in DIMENSIONS if name in cols}
            if name in ("first_name", "last_name"):
                dims |= {"doctor", "patient"}  # cannot tell which table without resolving aliases; accept both
    return dims


def _grouping_problems(select: exp.Select, spec: RequestSpec) -> list[str]:
    grouped = _grouped_dims(select)
    if grouped is None:
        aggregates = any(item.find(exp.AggFunc) for item in select.expressions)
        if spec.grouping and aggregates:
            return [f"the query returns one overall value, but the question asks per {', '.join(spec.grouping)}"]
        return []
    problems = [f"the question groups by {d}, but GROUP BY does not" for d in spec.grouping if d not in grouped]
    problems += [f"GROUP BY still groups by {d}, which the follow-up replaced or removed"
                 for d in spec.removed_grouping if d in grouped and d not in spec.grouping]
    return problems


def _amount_problems(select: exp.Select, spec: RequestSpec) -> list[str]:
    problems = []
    for item in select.expressions:
        node = item.this if isinstance(item, exp.Alias) else item
        amount_columns = {c.name.lower() for c in node.find_all(exp.Column)} & _AMOUNT_COLUMNS
        if not amount_columns:
            continue
        if spec.individual_amounts and "amount" in amount_columns and any(node.find_all(exp.Sum, exp.Avg)):
            problems.append(f"{item.sql(dialect=DIALECT)} totals the invoice amounts; the question asks for each invoice's "
                            "own amount (one row per invoice)")
        if not spec.zero_fill:
            for fill in node.find_all(exp.Coalesce):
                fallbacks = fill.expressions
                if any(isinstance(f, exp.Literal) and not f.is_string and float(f.this) == 0 for f in fallbacks):
                    problems.append(f"{fill.sql(dialect=DIALECT)} turns a missing amount into 0; the question did not ask "
                                    "for zero, so leave it NULL")
    return problems


def _count_problems(select: exp.Select, spec: RequestSpec) -> list[str]:
    """"How many treatments" answered by counting another table's key (COUNT(DISTINCT p.patient_id)). Only
    when every COUNT in the SELECT list counts a key of a different table; COUNT(*) is never questioned."""
    counts = [c for item in select.expressions for c in item.find_all(exp.Count)]
    group = select.args.get("group")
    grouped = {g.sql(dialect=DIALECT) for g in group.expressions} if group is not None else set()
    for count in counts:  # a distinct count of the grouped column is 1 in every group, whatever was asked
        if isinstance(count.this, exp.Distinct) and len(count.this.expressions) == 1 \
                and count.this.expressions[0].sql(dialect=DIALECT) in grouped:
            return [f"{count.sql(dialect=DIALECT)} is grouped by the same column, so every count is 1"]
    if not spec.counts:
        return []
    keyed = []
    for count in counts:
        column = next(count.find_all(exp.Column), None)
        if column is None:
            return []  # COUNT(*) (or a CASE count) counts rows: fine
        key = schema_keys().get((_table_for(column, select), column.name.lower()))
        if key is None:
            return []
        keyed.append((count, key[0]))
    if keyed and all(table != spec.counts for _, table in keyed):
        count, table = keyed[0]
        return [f"{count.sql(dialect=DIALECT)} counts {table}; the question asks how many {spec.counts}"]
    return []


def _table_for(column: exp.Column, select: exp.Select) -> str:
    tables = {(t.alias or t.name): t.name for t in select.find_all(exp.Table)}
    if column.table:
        return tables.get(column.table, "")
    from_ = select.args.get("from_")
    return from_.this.name if isinstance(from_, exp.From) and isinstance(from_.this, exp.Table) else ""


def mismatches(spec: RequestSpec, sql: str) -> list[str]:
    """Differences between the generated SQL and the request that can be established ([] otherwise)."""
    try:
        select = sqlglot.parse_one(sql, read=DIALECT)
    except SqlglotError:
        return []
    if not isinstance(select, exp.Select):
        return []
    return (_grouping_problems(select, spec) + _count_problems(select, spec) + _amount_problems(select, spec)
            + [text for text, _ in _filter_problems(select, spec)])


def removable(spec: RequestSpec, sql: str) -> frozenset[str]:
    """What a correction of sql may drop because the request did not ask for it: flagged filter values and,
    when the question asks for individual amounts, the totals (SUM/AVG). Used to narrow the correction's
    meaning guard (sql_correction.meaning_change) to the rest of the query."""
    try:
        select = sqlglot.parse_one(sql, read=DIALECT)
    except SqlglotError:
        return frozenset()
    if not isinstance(select, exp.Select):
        return frozenset()
    allowed = {token for _, token in _filter_problems(select, spec)}
    if spec.individual_amounts and any("totals the invoice amounts" in p for p in _amount_problems(select, spec)):
        allowed |= {"SUM", "AVG"}
    return frozenset(allowed)


# --- "visits" -----------------------------------------------------------------------------------

_VISIT = re.compile(r"(?<!reason for )(?<!reasons for )\bvisit(?:s|ed)?\b", re.I)
_STATUS_CUE = re.compile(r"\b(?:complet\w*|attended|took\s+place|cancel\w*|schedul\w*|booked|no-?shows?|missed|"
                         r"any\s+status|all\s+status(?:es)?|every\s+status|regardless\s+of\s+(?:the\s+)?status|of\s+all\s+statuses|"
                         r"all\s+appointments)\b", re.I)
VISITS_CLARIFICATION_PREFIX = "\"Visits\" is not a status in the data."


def mentions_status(text: str) -> bool:
    return bool(_STATUS_CUE.search(text or ""))


def mentions_visits_without_status(question: str, message: str = "") -> bool:
    """The request says visits but not which appointment statuses count as one."""
    return bool(_VISIT.search(question)) and not _STATUS_CUE.search(f"{question} {message}")


def visits_scope_sql(spec: RequestSpec) -> str:
    """Bounded read-only count of appointments in the request's scope that are not Completed."""
    conditions = ["status <> 'Completed'"]
    for p in spec.periods:
        if p.column in (None, "appointment_date"):
            conditions.append(f"appointment_date >= '{p.start.isoformat()}' AND appointment_date < '{p.end.isoformat()}'")
    for column, value in re.findall(r"(doctor_id|patient_id) = '([A-Za-z0-9]+)'", spec.grounded):
        conditions.append(f"{column} = '{value}'")
    return "SELECT COUNT(*) AS n FROM appointments WHERE " + " AND ".join(conditions)


def visits_clarification(count: int) -> str:
    return (f"{VISITS_CLARIFICATION_PREFIX} In the requested period and people, {count} appointment(s) are Scheduled, "
            "Cancelled or No-show. Should I count only Completed appointments, or all appointments of any status?")


def status_answer(message: str) -> str:
    """The phrase a reply to the visits clarification adds to the pending question ("" if none)."""
    if re.search(r"\bcomplet\w*|\battended\b|\bonly\b.*\bcomplet", message, re.I):
        return "Completed appointments only"
    if re.search(r"\b(?:all|any|every|both|regardless)\b", message, re.I):
        return "appointments of any status"
    return ""
