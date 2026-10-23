"""The application's authoritative current date, and the calendar periods derived from it.

MedLens uses the local time zone of the machine running the API (the MySQL server on the same
machine uses time_zone=SYSTEM, so CURDATE() agrees). The date is read once per request and given to
both the follow-up resolver and SQL generation as explicit periods, so "this month" or "last year"
become literal start-inclusive / end-exclusive date ranges instead of month-only filters.

Explicit periods named in a question ("March 2023", "Q2 2023", "15 March 2023", "the first half of
2022", "2023-03") are also turned into exact ranges here (period_ranges), so the SQL model copies the
dates instead of computing them, and the follow-up resolver is given the neighbours of the periods
already in the conversation (period_neighbours), so "the month before" January 2023 is December 2022.

Evaluation passes a fixed `now` for repeatable tests; nothing here is cached or hard-coded.
"""

import datetime as dt
import re


def current_time() -> dt.datetime:
    """Now, as an aware datetime in the server's local time zone."""
    return dt.datetime.now().astimezone()


def _month_start(year: int, month: int) -> dt.date:
    while month < 1:
        year, month = year - 1, month + 12
    while month > 12:
        year, month = year + 1, month - 12
    return dt.date(year, month, 1)


def _period(label: str, name: str, start: dt.date, end: dt.date) -> str:
    return f"- {label}: {name} = dates from {start.isoformat()} up to but not including {end.isoformat()}"


def calendar_context(now: dt.datetime, compact: bool = False) -> str:
    """Plain-text calendar facts for the prompts (no instructions; the prompts say how to use them).
    compact keeps the current date, this/last month and this/last year (for the SQL prompts, whose
    questions the resolver has already made explicit); the full form adds days and weeks."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = now.date()
    offset = now.strftime("%z")
    zone = f"UTC{offset[:3]}:{offset[3:]}" if offset else "local time"
    week_start = today - dt.timedelta(days=today.weekday())  # Monday
    month = _month_start(today.year, today.month)
    last_month = _month_start(today.year, today.month - 1)
    lines = [
        f"Current date: {today.isoformat()} ({today.strftime('%A')}), time zone {zone}. "
        "The periods below are relative to today only.",
        _period("Today", today.isoformat(), today, today + dt.timedelta(days=1)),
        _period("Yesterday", (today - dt.timedelta(days=1)).isoformat(), today - dt.timedelta(days=1), today),
        _period("This week (Monday to Sunday)", f"week of {week_start.isoformat()}", week_start, week_start + dt.timedelta(days=7)),
        _period("Last week", f"week of {(week_start - dt.timedelta(days=7)).isoformat()}", week_start - dt.timedelta(days=7), week_start),
        _period("This month", month.strftime("%B %Y"), month, _month_start(today.year, today.month + 1)),
        _period("Last month", last_month.strftime("%B %Y"), last_month, month),
        _period("This year", str(today.year), dt.date(today.year, 1, 1), dt.date(today.year + 1, 1, 1)),
        _period("Last year", str(today.year - 1), dt.date(today.year - 1, 1, 1), dt.date(today.year, 1, 1)),
    ]
    if compact:
        lines = [lines[0], lines[5], lines[6], lines[7], lines[8]]
    return "\n".join(lines)


# --- explicit periods ------------------------------------------------------------------------

_MONTHS = {name: n for n, names in enumerate(
    [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
     ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"),
     ("november", "nov"), ("december", "dec")], start=1) for name in names}
_MONTH_RE = "(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_YEAR_RE = r"((?:19|20)\d{2})"
_ORDINAL = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "last": 4}
_PATTERNS = [  # most specific first; matched text is removed before the next pattern runs
    ("day", re.compile(rf"\b{_YEAR_RE}-(\d{{2}})-(\d{{2}})\b")),
    ("day_dmy", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_RE}\s*,?\s+{_YEAR_RE}\b", re.I)),
    ("day_mdy", re.compile(rf"\b{_MONTH_RE}\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s+{_YEAR_RE}\b", re.I)),
    ("quarter", re.compile(rf"\bQ([1-4])\s*(?:of\s+)?{_YEAR_RE}\b", re.I)),
    ("quarter_words", re.compile(rf"\b(first|second|third|fourth|1st|2nd|3rd|4th|last)\s+quarter\s+(?:of\s+)?{_YEAR_RE}\b", re.I)),
    ("half", re.compile(rf"\b(first|second|1st|2nd)\s+half\s+(?:of\s+)?{_YEAR_RE}\b", re.I)),
    ("month", re.compile(rf"\b{_MONTH_RE}\s*(?:of\s+|,\s*)?{_YEAR_RE}\b", re.I)),
    ("month_iso", re.compile(rf"\b{_YEAR_RE}-(0[1-9]|1[0-2])\b")),
    ("year", re.compile(rf"\b(?:in|during|of|for|year|since|from|to|through|until|before|after)\s+{_YEAR_RE}\b", re.I)),
]


def _parse(kind: str, m: re.Match) -> tuple[str, dt.date, dt.date] | None:
    g = m.groups()
    try:
        if kind == "day":
            start = dt.date(int(g[0]), int(g[1]), int(g[2]))
        elif kind == "day_dmy":
            start = dt.date(int(g[2]), _MONTHS[g[1].lower()], int(g[0]))
        elif kind == "day_mdy":
            start = dt.date(int(g[2]), _MONTHS[g[0].lower()], int(g[1]))
        elif kind in ("quarter", "quarter_words"):
            q = int(g[0]) if kind == "quarter" else _ORDINAL[g[0].lower()]
            start = dt.date(int(g[1]), 3 * q - 2, 1)
            return f"Q{q} {g[1]}", start, _month_start(start.year, start.month + 3)
        elif kind == "half":
            h = 1 if g[0].lower() in ("first", "1st") else 2
            start = dt.date(int(g[1]), 1 if h == 1 else 7, 1)
            return f"{'first' if h == 1 else 'second'} half of {g[1]}", start, _month_start(start.year, start.month + 6)
        elif kind in ("month", "month_iso"):
            year, month = (int(g[1]), _MONTHS[g[0].lower()]) if kind == "month" else (int(g[0]), int(g[1]))
            start = dt.date(year, month, 1)
            return start.strftime("%B %Y"), start, _month_start(year, month + 1)
        else:
            start = dt.date(int(g[0]), 1, 1)
            return g[0], start, dt.date(start.year + 1, 1, 1)
    except (ValueError, KeyError):
        return None
    return start.isoformat(), start, start + dt.timedelta(days=1)


def explicit_period_spans(text: str) -> list[tuple[int, int, str, dt.date, dt.date]]:
    """Explicit calendar periods named in text, in order of appearance: (start position, end position,
    label, start date, end date exclusive)."""
    found: list[tuple[int, int, str, dt.date, dt.date]] = []
    remaining = text
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(remaining):
            parsed = _parse(kind, m)
            if parsed and not any(p[2] == parsed[0] for p in found):
                found.append((m.start(), m.end(), *parsed))
        remaining = pattern.sub(lambda m: " " * len(m.group(0)), remaining)
    return sorted(found)


def explicit_periods(text: str) -> list[tuple[str, dt.date, dt.date]]:
    """Explicit calendar periods named in text, in order of appearance: (label, start, end exclusive)."""
    return [p[2:] for p in explicit_period_spans(text)]


_DAY_RANGE = re.compile(r"\b(?:from|between|since)\b.+\b(?:to|and|through|thru|until|till)\b|\bthrough\b|\buntil\b", re.I)


def period_spans(text: str) -> list[tuple[int, int, str, dt.date, dt.date]]:
    """explicit_period_spans, with the two days of a "from ... to ..." range merged into one period with both
    days included. The days are the ends of one range, not two periods: listing them separately invites
    "col < <last day>", which loses the last day."""
    spans = explicit_period_spans(text)
    days = [s for s in spans if s[4] - s[3] == dt.timedelta(days=1)]
    if len(days) == 2 and days[0][3] < days[1][3] and _DAY_RANGE.search(text):
        (p1, _, a, s1, _), (_, p2, b, s2, _) = days
        merged = (p1, p2, f"from {a} to {b}, both days included", s1, s2 + dt.timedelta(days=1))
        spans = sorted([s for s in spans if s not in days] + [merged])
    return spans


def period_ranges(text: str) -> str:
    """Exact SQL ranges for the explicit periods in text ("" if none), for the SQL prompts."""
    lines = [f"- {label}: col >= '{start.isoformat()}' AND col < '{end.isoformat()}'"
             for _, _, label, start, end in period_spans(text)]
    if not lines:
        return ""
    return "Exact date ranges for the periods named in the question (start inclusive, end exclusive):\n" + "\n".join(lines)


def period_neighbours(texts) -> str:
    """Previous/next months and years of the explicit periods in earlier questions ("" if none),
    so a follow-up such as "the month before" is resolved from that period, across year boundaries."""
    lines = []
    for text in texts:
        for label, start, end in explicit_periods(text or ""):
            if end == _month_start(start.year, start.month + 1) and start.day == 1:
                before, after = _month_start(start.year, start.month - 1), _month_start(start.year, start.month + 1)
                lines.append(f"- {label}: the month before is {before.strftime('%B %Y')}; the month after is "
                             f"{after.strftime('%B %Y')}; the same month a year earlier is {start.replace(year=start.year - 1).strftime('%B %Y')}")
            elif end == dt.date(start.year + 1, 1, 1) and start == dt.date(start.year, 1, 1):
                lines.append(f"- {label}: the year before is {start.year - 1}; the year after is {start.year + 1}")
    unique = list(dict.fromkeys(lines))
    if not unique:
        return ""
    return "Periods mentioned earlier in this conversation and their neighbours:\n" + "\n".join(unique)


# --- periods relative to the previous question -------------------------------------------------

_SHIFT_MONTH = [
    (re.compile(r"\bsame\s+month\s+(?:of\s+)?(?:the\s+)?(?:previous|prior|last)\s+year\b|\ba\s+year\s+(?:earlier|before)\b", re.I), -12),
    (re.compile(r"\b(?:previous|prior|preceding)\s+month\b|\bmonth\s+(?:before|earlier|prior)\b", re.I), -1),
    (re.compile(r"\b(?:next|following|subsequent)\s+month\b|\bmonth\s+(?:after|later)\b", re.I), 1),
]
_SHIFT_YEAR = [
    (re.compile(r"\b(?:previous|prior|preceding)\s+year\b|\byear\s+(?:before|earlier|prior)\b", re.I), -1),
    (re.compile(r"\b(?:next|following)\s+year\b|\byear\s+after\b", re.I), 1),
]
_MONTH_TEXT = re.compile(rf"\b{_MONTH_RE}\s*(?:of\s+|,\s*)?{_YEAR_RE}\b|\b{_YEAR_RE}-(?:0[1-9]|1[0-2])\b(?!-)", re.I)


def shift_from_history(message: str, previous_question: str | None, resolved: str) -> str:
    """Apply "the month before", "the following month", "a year earlier", "the previous year" in the
    user's message to the single month (or year) of the previous question, with calendar arithmetic,
    and put the result into the resolved question. Returns resolved unchanged when the message has
    no such phrase or the previous question does not name exactly one month (or year)."""
    if not previous_question:
        return resolved
    periods = explicit_periods(previous_question)
    months = [p for p in periods if p[1].day == 1 and p[2] == _month_start(p[1].year, p[1].month + 1)]
    years = [p for p in periods if p[1] == dt.date(p[1].year, 1, 1) and p[2] == dt.date(p[1].year + 1, 1, 1)]
    for pattern, delta in _SHIFT_MONTH:
        if pattern.search(message) and len(months) == 1:
            start = months[0][1]
            label = _month_start(start.year, start.month + delta).strftime("%B %Y")
            replaced, count = _MONTH_TEXT.subn(label, resolved)
            return replaced if count else f"{resolved.rstrip(' ?.')} in {label}?"
    for pattern, delta in _SHIFT_YEAR:
        if pattern.search(message) and len(years) == 1 and not months:
            old, new = str(years[0][1].year), str(years[0][1].year + delta)
            return re.sub(rf"\b{old}\b", new, resolved) if re.search(rf"\b(?:{old}|{new})\b", resolved) \
                else f"{resolved.rstrip(' ?.')} in {new}?"
    return resolved


_RELATIVE = re.compile(r"\b(today|yesterday|tomorrow|this|last|current|previous|next|recent|recently|past|ago|"
                       r"so far|to date|year-to-date|ytd|age|aged|old|older|younger)\b", re.I)


def mentions_relative_period(text: str) -> bool:
    """Whether text uses wording that depends on the current date (or on ages, which use CURDATE())."""
    return bool(_RELATIVE.search(text or ""))
