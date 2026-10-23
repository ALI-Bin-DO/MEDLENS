"""Grounds doctor and patient names in the actual records before SQL is generated.

A question such as "appointments with Dr. David" must not become last_name = 'Dr.', and must not
silently pick one of several doctors called David. So:

1. Mentions come from the resolver's structured output (doctor_names / patient_names) plus a
   title-based scan of the resolved question ("Dr. Jane", "Doctor Lee"). Honorifics (Dr., Doctor,
   Prof., Mr., Mrs., Ms., ...) are removed; they are never part of a name.
2. Each mention must look like a name (letters, spaces and hyphens, at most three words) or a record
   ID (a letter followed by digits). Anything else is not looked up. Because of this pattern the
   value can be placed in a SQL string literal without escaping problems.
3. One bounded lookup per mention (LIMIT 20) is run through the normal MySQLRepository.execute: the
   same validator, read-only account, session limits and row cap as any query. Only identifying
   columns are read (doctors: ID, name, specialization, branch; patients: ID and name).
4. Exactly one match: the SQL prompt is told which ID to filter on. No match or several matches:
   the caller asks the user, listing only those identifiers and details. Nothing is guessed.

No names, IDs or other dataset values are hard-coded here.
"""

import re
from dataclasses import dataclass, field

HONORIFICS = {"dr", "doctor", "doc", "prof", "professor", "mr", "mrs", "ms", "miss", "mx", "sir", "madam"}
MAX_MENTIONS = 3
LOOKUP_LIMIT = 20
MAX_LISTED = 10
_NAME = re.compile(r"^[A-Za-z][A-Za-z-]*(?: [A-Za-z][A-Za-z-]*){0,2}$")
_ID = re.compile(r"^[A-Za-z]\d{1,6}$")
# "Dr. Jane", "Doctor Lee Smith": a title followed by capitalized words (case-sensitive on the name).
_TITLED = re.compile(r"\b(?:[Dd]r\.?|[Dd]octor|[Pp]rof\.?|[Pp]rofessor)\s+([A-Z][a-zA-Z-]+(?:\s+[A-Z][a-zA-Z-]+)?)")

ROLES = {
    "doctor": {"table": "doctors", "id": "doctor_id",
               "columns": ("doctor_id", "first_name", "last_name", "specialization", "hospital_branch")},
    "patient": {"table": "patients", "id": "patient_id", "columns": ("patient_id", "first_name", "last_name")},
}


@dataclass(frozen=True)
class PersonMatch:
    id: str
    first_name: str
    last_name: str
    details: tuple[str, ...] = ()

    def describe(self) -> str:
        extra = f" ({', '.join(d for d in self.details if d)})" if any(self.details) else ""
        return f"{self.id} {self.first_name} {self.last_name}{extra}"


@dataclass
class Mention:
    role: str
    text: str  # cleaned name or ID as looked up
    matches: list[PersonMatch] = field(default_factory=list)


def clean_name(raw: str) -> str | None:
    """Remove honorifics and punctuation around a name; None if what is left is not a name or an ID."""
    words = [w.strip(".,;:()'\"") for w in str(raw).split()]
    words = [w for w in words if w and w.lower().rstrip(".") not in HONORIFICS]
    text = " ".join(words)
    if _ID.match(text):
        return text.upper()
    return text if _NAME.match(text) else None


def mentions_from(resolved_question: str, doctor_names=(), patient_names=()) -> list[Mention]:
    """Unique cleaned mentions from the resolver's lists and titled names in the question."""
    raw = [("doctor", n) for n in doctor_names or ()] + [("patient", n) for n in patient_names or ()]
    raw += [("doctor", m.group(1)) for m in _TITLED.finditer(resolved_question or "")]
    seen, result = set(), []
    for role, name in raw:
        cleaned = clean_name(name) if isinstance(name, str) else None
        if cleaned and (role, cleaned.lower()) not in seen:
            seen.add((role, cleaned.lower()))
            result.append(Mention(role, cleaned))
    # A titled name that only repeats part of a listed name (e.g. "David" vs "David Jones") is dropped.
    result = [m for m in result if not any(o is not m and o.role == m.role and m.text.lower() in o.text.lower().split()
                                           and len(o.text) > len(m.text) for o in result)]
    return result[:MAX_MENTIONS]


def lookup_sql(mention: Mention) -> str:
    """The bounded read-only lookup for one mention (values already restricted by clean_name)."""
    spec = ROLES[mention.role]
    if _ID.match(mention.text):
        condition = f"{spec['id']} = '{mention.text}'"
    else:
        words = mention.text.lower().split()
        if len(words) == 1:
            condition = f"LOWER(first_name) = '{words[0]}' OR LOWER(last_name) = '{words[0]}'"
        else:
            condition = (f"(LOWER(first_name) = '{words[0]}' AND LOWER(last_name) = '{' '.join(words[1:])}') "
                         f"OR LOWER(CONCAT(first_name, ' ', last_name)) = '{' '.join(words)}'")
    return (f"SELECT {', '.join(spec['columns'])} FROM {spec['table']} WHERE {condition} "
            f"ORDER BY {spec['id']} LIMIT {LOOKUP_LIMIT}")


def ground(mentions: list[Mention], database) -> list[Mention]:
    """Run one lookup per mention through database.execute (validated, read-only, limited)."""
    for mention in mentions:
        result = database.execute(lookup_sql(mention))
        mention.matches = [PersonMatch(str(r[0]), str(r[1]), str(r[2]), tuple(str(v) for v in r[3:]))
                           for r in result.rows]
    return mentions


def clarification(mentions: list[Mention], repeated: bool = False) -> str | None:
    """A focused question when a mention matches no record or several; None when all are unique."""
    for m in mentions:
        if not m.matches:
            return (f"I couldn't find a {m.role} named \"{m.text}\" in the data. Please check the name, or give the "
                    f"full name or the {m.role} ID.")
        if len(m.matches) > 1:
            listed = "; ".join(p.describe() for p in m.matches[:MAX_LISTED])
            more = f" (and {len(m.matches) - MAX_LISTED} more)" if len(m.matches) > MAX_LISTED else ""
            lead = f"\"{m.text}\" still matches more than one {m.role}." if repeated else \
                f"More than one {m.role} matches \"{m.text}\"."
            return f"{lead} Which one do you mean: {listed}{more}? Reply with the full name or the ID."
    return None


def pick(reply: str, candidates: list[tuple[str, PersonMatch]]) -> list[tuple[str, PersonMatch]]:
    """The candidates the user's own reply identifies: by ID, full name, last name, or a first name only
    one candidate has. The model's rewrite is not trusted for this; only the words the user typed count."""
    words = {w.lower() for w in (clean_name(reply) or "").split()}
    if not words:
        words = {w.strip(".,;:!?'\"").lower() for w in reply.split()} - {h for h in HONORIFICS}
    chosen = []
    for role, p in candidates:
        if p.id.lower() in words or {p.first_name.lower(), p.last_name.lower()} <= words or p.last_name.lower() in words:
            chosen.append((role, p))
    if not chosen:  # a first name counts only when it identifies exactly one candidate
        chosen = [(role, p) for role, p in candidates if p.first_name.lower() in words]
    return chosen


def reask(reply: str, candidates: list[tuple[str, PersonMatch]], chosen: list) -> str:
    """Ask again when the reply did not pick exactly one of the listed people."""
    role = candidates[0][0]
    listed = "; ".join(p.describe() for _, p in candidates[:MAX_LISTED])
    lead = (f"\"{reply.strip()}\" still matches more than one {role}." if len(chosen) > 1
            else f"I couldn't match \"{reply.strip()}\" to one of the {role}s I listed.")
    return f"{lead} Which one do you mean: {listed}? Reply with the full name or the ID."


def grounding_hint(mentions: list[Mention]) -> str:
    """Text for the SQL prompt naming the matched IDs ("" if nothing to add)."""
    lines = [f"- {m.role} \"{m.text}\" is {ROLES[m.role]['table']}.{ROLES[m.role]['id']} = '{m.matches[0].id}' "
             f"({m.matches[0].first_name} {m.matches[0].last_name}); filter by this ID, not by name"
             for m in mentions if len(m.matches) == 1]
    return "People named in the question, matched in the data:\n" + "\n".join(lines) if lines else ""
