"""Run the development set through the real pipeline and compare with reference queries.
Run from the project root:

    python -m evaluation.run_dev_eval

For each item: the question goes through backend.app.services.chat_service.ChatService - the same
pipeline as POST /chat (context -> LLM -> validation -> read-only execution, no retries) - and the reference SQL is
validated and executed with the same MySQLRepository. Results are compared as row multisets
(or sequences when order matters), ignoring column names; extra model columns are allowed if
some subset of them reproduces the reference exactly. Matching results on this small dataset
do not prove the queries are equivalent - read the SQL too.

Writes evaluation/results/dev_eval_<timestamp>.json. Changes nothing in MySQL or the index.
"""

import itertools
import json
import sys
from datetime import datetime
from pathlib import Path

from backend.app.config import get_settings
from backend.app.repositories.mysql_repository import MySQLRepository, QueryResult
from backend.app.repositories.vector_repository import VectorRepository
from backend.app.services.chat_service import ChatService

EVAL_DIR = Path(__file__).resolve().parent
DEV_SET = EVAL_DIR / "dev_set.json"
RESULTS_DIR = EVAL_DIR / "results"


def _rows(result: QueryResult, columns: tuple[int, ...]) -> list[tuple]:
    return [tuple(json.dumps(row[i]) for i in columns) for row in result.rows]


def compare(model: QueryResult, reference: QueryResult, order_matters: bool) -> tuple[str, str]:
    """Return (verdict, detail). verdict: match, match_extra_columns, mismatch."""
    if model.truncated or reference.truncated:
        return "mismatch", "a result was truncated by the row cap; cannot compare"
    width = len(reference.columns)
    ref = _rows(reference, tuple(range(width)))
    key = (lambda rows: rows) if order_matters else sorted
    for subset in itertools.permutations(range(len(model.columns)), width):
        if key(_rows(model, subset)) == key(ref):
            if len(model.columns) == width:
                return "match", "same rows" + (" in the same order" if order_matters else " (order ignored)")
            cols = ", ".join(model.columns[i] for i in subset)
            return "match_extra_columns", f"reference rows reproduced by model columns [{cols}]; model returned {len(model.columns)} columns"
    if order_matters:
        for subset in itertools.permutations(range(len(model.columns)), width):
            if sorted(_rows(model, subset)) == sorted(ref):
                return "mismatch", "same rows but in a different order, and the question asks for an order"
    return "mismatch", f"model {model.row_count} row(s) x {len(model.columns)} col(s) vs reference {reference.row_count} x {width}"


def main() -> None:
    dev = json.loads(DEV_SET.read_text(encoding="utf-8"))
    settings = get_settings()
    report = {"dev_set": dev["name"], "model": settings.ollama_sql_model, "started": datetime.now().isoformat(timespec="seconds"),
              "settings": {"rag_context_top_k": settings.rag_context_top_k, "num_ctx": settings.ollama_sql_num_ctx,
                           "row_cap": settings.mysql_row_cap, "max_execution_ms": settings.mysql_max_execution_ms},
              "items": []}

    with VectorRepository(settings) as vectors:  # one client for the whole run (stop the API first)
        service = ChatService(settings, vectors)
        for item in dev["items"]:
            report["items"].append(evaluate_item(item, service, settings))

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"dev_eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved {out.relative_to(EVAL_DIR.parent)}")
    if any(i["verdict"] in ("mismatch", "no_result") for i in report["items"]):
        sys.exit(1)


def evaluate_item(item: dict, service: ChatService, settings) -> dict:
    reference = MySQLRepository(settings).execute(item["reference_sql"])  # validated + read-only, same layer
    trace = service.answer(item["question"])
    g, v = trace.generation, trace.validation
    entry = {
        "id": item["id"], "question": item["question"], "stage": trace.stage,
        "route": trace.route, "route_source": trace.route_source,
        "attempt_count": len(trace.attempts), "correction_attempted": trace.correction_attempted,
        "attempts": [{"purpose": a.purpose, "sql": a.sql, "validation": a.validation, "execution": a.execution,
                      "error_code": a.error_code, "retryable": a.retryable} for a in trace.attempts],
        "elapsed_seconds": round(trace.elapsed_seconds, 2),
        "llm_seconds": round(g.stats.total_seconds, 2) if g and g.stats.total_seconds is not None else None,
        "context_ids": trace.context_ids,
        "generated_kind": g.kind if g else None, "generated_sql": g.text if g else None,
        "validation": None if v is None else {"ok": v.ok, "code": v.code, "reason": v.reason},
        "error": trace.error,
        "reference_sql": item["reference_sql"],
        "reference_result": {"columns": reference.columns, "rows": reference.rows},
        "model_result": None if trace.result is None else {"columns": trace.result.columns, "rows": trace.result.rows,
                                                            "execution_ms": round(trace.result.duration_seconds * 1000, 1)},
    }
    if trace.result is not None:
        entry["verdict"], entry["detail"] = compare(trace.result, reference, item["order_matters"])
    else:
        entry["verdict"], entry["detail"] = "no_result", f"pipeline stopped at stage {trace.stage!r}"
    print(f"{item['id']}: {entry['verdict']:20} stage={trace.stage:10} {entry['elapsed_seconds']:6.2f} s  {entry['detail']}")
    return entry


if __name__ == "__main__":
    main()
