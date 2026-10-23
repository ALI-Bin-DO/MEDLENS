"""Check knowledge_base/examples.sql and write a coverage inventory. Run from the project root:

    python -m backend.scripts.check_examples [--no-execute]

For every "-- Example N" chunk (parsed with the same chunker as the index):
    1. the SQL (comment lines removed) must pass sql_validator;
    2. it is executed with the read-only MySQLRepository (its timeouts and row cap apply);
    3. it is tagged with the tables and SQL features it uses.
Then near-duplicates are listed: pairs whose masked SQL shape is identical, or whose questions and SQL
are both very similar. Pairs are listed for review, not removed automatically. Finally every example
is compared with the evaluation questions (evaluation/*.json) to keep evaluation data out of the
knowledge base.

Writes docs/examples_inventory.md. Exits with status 1 if any example fails validation or execution.
Execution success only proves the SQL runs; whether it answers its question needs human review.
"""

import argparse
import json
import re
import sys
from collections import Counter
from itertools import combinations

from backend.app.config import EXAMPLES_FILE, PROJECT_ROOT
from backend.app.repositories.mysql_repository import MySQLRepository
from backend.app.services.rag.chunker import chunk_documents
from backend.app.services.rag.document_loader import load_document
from backend.app.services.sql_validator import validate

INVENTORY_FILE = PROJECT_ROOT / "docs" / "examples_inventory.md"
EVAL_FILES = [PROJECT_ROOT / "evaluation" / name
              for name in ("dev_set.json", "quality_set.json", "regression_set.json", "examples_eval_set.json",
                           "intent_eval_set.json")]
TABLES = ("patients", "doctors", "appointments", "treatments", "billing")
# Flagged pairs that were reviewed and kept on purpose, with the reason (shown in the inventory).
REVIEWED = {
    ("example:77", "example:178"): "monthly totals vs. the single month with the highest total (ORDER BY ... LIMIT 1)",
    ("example:151", "example:161"): "anti-join vs. anti-join with the status filter in the ON clause",
    ("example:2", "f03"): "original example (also in the baseline); matches only the unscored first turn of f03",
    ("example:4", "f05"): "original example (also in the baseline); matches only the unscored first turn of f05",
    ("example:9", "q07"): "per-year registration counts vs. listing last year's registrations: different question and SQL",
    ("example:141", "f04"): "4-table paid totals by specialization and method vs. f04's unscored first turn",
    # 250-example set: the same SQL shape with different literals teaches a different value or period.
    ("example:40", "example:241"): "doctors named David vs. doctors named Jane: same lookup, different name",
    ("example:62", "example:161"): "July vs. March 2023: monthly range with an exclusive upper bound",
    ("example:62", "example:162"): "a plain month vs. 'the month immediately before January 2023' (year transition)",
    ("example:62", "example:163"): "a plain month vs. February 2024 including the leap day",
    ("example:62", "example:164"): "a plain month vs. December 2023 (upper bound in the next year)",
    ("example:62", "example:166"): "a calendar month vs. a 30-day window ending on a date, both days included",
    ("example:161", "example:162"): "March 2023 vs. the month before January 2023",
    ("example:161", "example:163"): "March 2023 vs. leap-year February 2024",
    ("example:161", "example:164"): "March 2023 vs. December 2023 (year transition)",
    ("example:161", "example:166"): "a calendar month vs. a 30-day window",
    ("example:162", "example:163"): "year transition vs. leap day",
    ("example:162", "example:164"): "the month before January 2023 vs. December 2023",
    ("example:162", "example:166"): "relative month vs. 30-day window",
    ("example:163", "example:164"): "leap-year February vs. December",
    ("example:163", "example:166"): "leap-year February vs. 30-day window",
    ("example:164", "example:166"): "December 2023 vs. the 30 days ending 31 December 2023",
    ("example:67", "example:81"): "total cost per type vs. count and total cost per type",
    ("example:113", "example:129"): "appointments per specialization vs. Emergency appointments per specialization",
    ("example:141", "example:147"): "all appointments per doctor vs. Completed appointments with the filter in ON",
    ("example:218", "example:219"): "Paid total grouped by branch only vs. per doctor with the branch shown",
    # Overlaps the 250-example set brought in: kept (they are valid examples), and the evaluation item is no
    # longer held out; results for it measure recall of an example, not generalization.
    ("example:41", "f03"): "same question as f03's unscored first turn; f03 is no longer held out",
    ("example:42", "r11"): "same question as r11's first turn (r11 scores the chart follow-up); not held out",
    ("example:105", "dev-02"): "pending amount per method; dev-02 is no longer held out",
    ("example:105", "f04"): "pending vs. paid amount per method (f04's unscored first turn)",
    ("example:141", "f05"): "same question as f05's first turn; f05 is no longer held out",
    ("example:147", "dev-01"): "same question as dev-01; dev-01 is no longer held out",
    ("example:187", "dev-05"): "same question as dev-05; dev-05 is no longer held out",
}
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "for", "each", "is", "are", "was", "were", "how", "many", "what", "which", "in", "and",
         "to", "with", "by", "do", "does", "did", "have", "has", "there", "show", "list", "their", "it", "that", "on"}


def parse(chunk) -> tuple[str, str]:
    """(question, sql) of one example chunk."""
    lines = chunk.content.splitlines()
    question = []
    for line in lines:
        if line.startswith("-- Question:"):
            question.append(line.removeprefix("-- Question:").strip())
        elif question and line.startswith("-- ") and not line.startswith("-- ("):
            question.append(line.removeprefix("-- ").strip())
        elif question:
            break
    sql = "\n".join(line for line in lines if not line.lstrip().startswith("--")).strip()
    return " ".join(question), sql


def features(sql: str) -> list[str]:
    s = " ".join(sql.upper().split())
    found = []
    tables = [t for t in TABLES if re.search(rf"\b{t.upper()}\b", s)]
    joins = s.count(" JOIN ")
    found.append(f"{len(tables)}-table" if joins else "1-table")
    checks = {
        "left_join": " LEFT JOIN " in s, "group_by": " GROUP BY " in s, "having": " HAVING " in s,
        "distinct": "DISTINCT" in s, "order_limit": " LIMIT " in s, "case": "CASE " in s,
        "null_check": " IS NULL" in s or " IS NOT NULL" in s or "COALESCE" in s or "COUNT(INSURANCE_NUMBER)" in s,
        "anti_join": " LEFT JOIN " in s and " IS NULL" in s,
        "filter_in_on": bool(re.search(r" ON [^()]*? AND ", s)),
        "date_range": bool(re.search(r"_DATE (>=|<|BETWEEN|>|<=) '\d{4}-\d{2}-\d{2}'", s)),
        "date_function": any(f in s for f in ("DATE_FORMAT(", "YEAR(", "MONTH(", "DATEDIFF(", "TIMESTAMPDIFF(", "DATE_SUB(")),
        "current_date": "CURDATE()" in s,
        "aggregate": any(f in s for f in ("COUNT(", "SUM(", "AVG(", "MIN(", "MAX(")),
        "conditional_aggregate": "SUM(CASE" in s or "COUNT(CASE" in s,
        "count_case": "COUNT(CASE" in s,
        "nullif": "NULLIF(" in s,
        "distinct_aggregate": bool(re.search(r"(COUNT|SUM|AVG)\(DISTINCT ", s)),
    }
    found += [name for name, hit in checks.items() if hit]
    return found, tables


def shape(sql: str) -> str:
    """SQL with literals and numbers masked, for duplicate detection."""
    s = re.sub(r"'[^']*'", "'?'", sql.upper())
    s = re.sub(r"\b\d+(\.\d+)?\b", "0", s)
    return " ".join(s.split())


def words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-execute", action="store_true", help="validate only")
    args = parser.parse_args()

    chunks = chunk_documents([load_document(EXAMPLES_FILE, "example")])
    database = None if args.no_execute else MySQLRepository()
    rows, failures = [], []
    for chunk in chunks:
        question, sql = parse(chunk)
        v = validate(sql)
        entry = {"id": chunk.id, "question": question, "sql": sql, "valid": v.ok, "error": None, "row_count": None}
        entry["features"], entry["tables"] = features(sql)
        if not v.ok:
            entry["error"] = f"validation {v.code}: {v.reason}"
        elif database is not None:
            try:
                result = database.execute(sql)
                entry["row_count"] = result.row_count
                entry["truncated"] = result.truncated
            except Exception as exc:  # report every failure, keep going
                entry["error"] = f"execution {type(exc).__name__}: {exc}"
        if entry["error"]:
            failures.append(entry)
        rows.append(entry)

    # Near-duplicates: same masked SQL shape, or similar question AND similar SQL tokens.
    duplicates = []
    for a, b in combinations(rows, 2):
        q = jaccard(words(a["question"]), words(b["question"]))
        s = jaccard(set(shape(a["sql"]).split()), set(shape(b["sql"]).split()))
        if shape(a["sql"]) == shape(b["sql"]) or (q >= 0.6 and s >= 0.8):
            duplicates.append((a["id"], b["id"], round(q, 2), round(s, 2)))

    # Overlap with evaluation questions (must stay out of the knowledge base).
    eval_questions = []
    for path in EVAL_FILES:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data.get("items", []) + data.get("scenarios", []):
                for text in item.get("turns", [item.get("question", "")]):
                    eval_questions.append((item["id"], text))
    overlaps = []
    for entry in rows:
        for eval_id, text in eval_questions:
            q = jaccard(words(entry["question"]), words(text))
            if q >= 0.7:
                overlaps.append((entry["id"], eval_id, round(q, 2), text))

    write_inventory(rows, failures, duplicates, overlaps, executed=database is not None)
    print(f"{len(rows)} examples, {len(failures)} failed, {len(duplicates)} near-duplicate pairs, "
          f"{len(overlaps)} evaluation overlaps. Inventory: {INVENTORY_FILE.relative_to(PROJECT_ROOT)}")
    for f in failures:
        print(f"  FAIL {f['id']}: {f['error']}")
    for d in duplicates:
        print(f"  near-duplicate {d[0]} ~ {d[1]} (question {d[2]}, sql {d[3]})")
    for o in overlaps:
        print(f"  eval overlap {o[0]} ~ {o[1]} ({o[2]}): {o[3]}")
    if failures:
        sys.exit(1)


def write_inventory(rows, failures, duplicates, overlaps, executed: bool) -> None:
    feature_counts = Counter(f for r in rows for f in r["features"])
    table_counts = Counter(t for r in rows for t in r["tables"])
    lines = [
        "# Knowledge-base example inventory",
        "",
        "Generated by `python -m backend.scripts.check_examples`. Do not edit by hand.",
        "",
        f"- Examples: {len(rows)}",
        f"- Validation/execution failures: {len(failures)}" + ("" if executed else " (validation only; not executed)"),
        f"- Near-duplicate pairs flagged for review: {len(duplicates)}",
        f"- Overlaps with evaluation questions: {len(overlaps)}",
        "",
        "## Tables used (examples that read each table)",
        "",
        *[f"- {t}: {table_counts[t]}" for t in TABLES],
        "",
        "## Features",
        "",
        *[f"- {name}: {count}" for name, count in sorted(feature_counts.items())],
        "",
        "## Near-duplicates",
        "",
        *([f"- {a} ~ {b} (question similarity {q}, SQL similarity {s}): "
           + REVIEWED.get((a, b), "NOT REVIEWED") for a, b, q, s in duplicates] or ["- none"]),
        "",
        "## Overlaps with evaluation questions",
        "",
        *([f"- {e} ~ {i} ({q}): {t} - " + REVIEWED.get((e, i), "NOT REVIEWED") for e, i, q, t in overlaps] or ["- none"]),
        "",
        "## Examples",
        "",
    ]
    for r in rows:
        status = "FAIL " + r["error"] if r["error"] else (f"ok, {r['row_count']} rows" if r["row_count"] is not None else "valid")
        lines.append(f"- {r['id']} [{', '.join(r['features'])}] {status}: {r['question']}")
    INVENTORY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
