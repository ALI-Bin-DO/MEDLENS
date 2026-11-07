"""RQ2 (retrieval benefit): does retrieved context help the model write correct SQL?

Phase 2 planned to run the same questions with three inputs and compare execution accuracy:

    A. schema_only             - all five CREATE TABLE chunks, nothing retrieved
    B. examples_only           - the top_k retrieved question-SQL examples, no schema
    C. schema_plus_examples    - A + B
    D. production (extra)      - the context the app uses today: schema + retrieved documentation/examples

Only the input changes. Every configuration uses the same questions, model, prompt template, model
settings, calendar facts, validator, read-only database and hardware. To isolate the effect of the
context, this script calls the SQL generation step directly: no intent routing, no semantic guards,
no correction attempt (those are the same for every configuration and would hide the difference).

Questions: single-turn data questions with a reference query from the evaluation sets, that run on the
real MySQL data (no fixture DB) and do not restate a knowledge-base example (docs/examples_integration.md).
Dates are pinned to the sets' reference time (2024-01-15) in both the prompt and MySQL's session clock.

Run from the project root with Ollama and MySQL running and the API STOPPED (it locks the index):

    python -m evaluation.run_rq2_eval                # all configurations
    python -m evaluation.run_rq2_eval --dry-run      # list the questions only (no Ollama/MySQL)
    python -m evaluation.run_rq2_eval --configs schema_only schema_plus_examples

Writes evaluation/results/rq2_<timestamp>.json and prints a summary table. Changes nothing in MySQL
or the index.
"""

import argparse
import datetime as dt
import json
import statistics
import time
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
REFERENCE_TIME = "2024-01-15T10:00:00"

SETS = ["dev_set", "quality_set", "regression_set", "intent_eval_set", "examples_eval_set"]
# Scenarios restated by knowledge-base examples (docs/examples_integration.md, "Overlap with evaluation").
OVERLAP_IDS = {"dev-01", "dev-02", "dev-05", "f03", "f04", "f05", "r01", "r03", "r04", "r05", "r06", "r08",
               "r09", "r10", "r11", "g01", "g06", "g07", "g10", "fx03", "fx04", "fx06"}
CONFIGS = ["schema_only", "examples_only", "schema_plus_examples", "production"]


def load_questions() -> list[dict]:
    """Single-turn data questions with a reference query on the real data, held out from the examples."""
    questions, seen = [], set()
    for name in SETS:
        data = json.loads((EVAL_DIR / f"{name}.json").read_text(encoding="utf-8"))
        if "items" in data:  # dev_set format
            items = [{"id": i["id"], "split": "dev-v1", "tags": [], "question": i["question"],
                      "reference_sql": i["reference_sql"], "order_matters": i["order_matters"]} for i in data["items"]]
        else:
            items = []
            for s in data["scenarios"]:
                e = s["expect"]
                if (len(s["turns"]) != 1 or e.get("route") != "data" or not e.get("reference_sql")
                        or s.get("fixture") or "overlap" in s.get("tags", [])):
                    continue
                items.append({"id": s["id"], "split": s.get("split"), "tags": s.get("tags", []), "question": s["turns"][0],
                              "reference_sql": e["reference_sql"], "order_matters": e.get("order_matters", False)})
        for item in items:
            if item["id"] in OVERLAP_IDS or item["question"] in seen:
                continue
            seen.add(item["question"])
            questions.append({**item, "set": name})
    return questions


def build_context(config: str, question: str, schema, retriever, builder, top_k: int) -> tuple[str, list[str]]:
    """Return (context text, chunk ids) for one configuration."""
    from backend.app.services.rag.context_builder import _section

    if config == "production":
        ctx = builder.build(question)
        return ctx.text, ctx.chunk_ids
    examples = []
    if config in ("examples_only", "schema_plus_examples"):
        examples = [r.chunk for r in retriever.retrieve(question, top_k=top_k, types=["example"])]
    sections = []
    if config in ("schema_only", "schema_plus_examples"):
        sections.append(_section("SCHEMA (all tables, always included)", schema))
    if examples:
        sections.append(_section("EXAMPLES (retrieved for this question)", examples))
    used = (schema if config != "examples_only" else []) + examples
    return "\n\n".join(sections), [c.id for c in used]


def run_one(item: dict, config: str, ctx_text: str, llm, database, calendar: str, compare) -> dict:
    from backend.app.services.sql_validator import validate

    entry = {"id": item["id"], "config": config, "verdict": "no_result", "detail": None, "sql": None}
    start = time.perf_counter()
    try:
        generation = llm.generate_sql(item["question"], ctx_text, calendar)
        entry["generated_kind"] = generation.kind
        if generation.kind != "sql":
            entry["detail"] = f"model replied {generation.kind}"
        else:
            entry["sql"] = generation.text
            check = validate(generation.text)
            if not check.ok:
                entry["verdict"], entry["detail"] = "rejected", check.code
            else:
                result = database.execute(generation.text)
                entry["verdict"], entry["detail"] = compare(result, database.execute(item["reference_sql"]),
                                                            item["order_matters"])
    except Exception as exc:  # timeouts, MySQL errors, context too long: count as no result, keep going
        entry["detail"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    entry["seconds"] = round(time.perf_counter() - start, 2)
    return entry


def summarise(entries: list[dict], configs: list[str], total: int) -> dict:
    summary = {}
    for config in configs:
        rows = [e for e in entries if e["config"] == config]
        correct = sum(e["verdict"] in ("match", "match_extra_columns") for e in rows)
        times = [e["seconds"] for e in rows]
        summary[config] = {"correct": correct, "total": total,
                           "accuracy": round(correct / total, 3) if total else None,
                           "rejected": sum(e["verdict"] == "rejected" for e in rows),
                           "no_result": sum(e["verdict"] == "no_result" for e in rows),
                           "median_seconds": round(statistics.median(times), 2) if times else None}
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--configs", nargs="+", choices=CONFIGS, default=CONFIGS)
    parser.add_argument("--top-k", type=int, default=None, help="retrieved examples (default: RAG_CONTEXT_TOP_K)")
    parser.add_argument("--dry-run", action="store_true", help="list the questions and stop")
    args = parser.parse_args()

    questions = load_questions()
    print(f"{len(questions)} questions")
    if args.dry_run:
        for q in questions:
            print(f"  {q['set']:18} {q['id']:6} {q['question']}")
        return

    from backend.app.config import get_settings
    from backend.app.repositories.mysql_repository import MySQLRepository
    from backend.app.repositories.vector_repository import VectorRepository
    from backend.app.services.clock import calendar_context
    from backend.app.services.llm_service import LLMService
    from backend.app.services.rag.context_builder import ContextBuilder
    from backend.app.services.rag.embedding_service import EmbeddingService
    from backend.app.services.rag.retriever import Retriever
    from evaluation.run_dev_eval import compare

    settings = get_settings()
    top_k = settings.rag_context_top_k if args.top_k is None else args.top_k
    today = dt.datetime.fromisoformat(REFERENCE_TIME).astimezone()
    calendar = calendar_context(today, compact=True)
    database = MySQLRepository(settings, reference_time=today)
    llm = LLMService(settings)
    report = {"name": "rq2-v1", "model": settings.ollama_sql_model, "top_k": top_k, "reference_time": today.isoformat(),
              "started": dt.datetime.now().isoformat(timespec="seconds"), "configs": args.configs,
              "questions": questions, "items": []}

    with VectorRepository(settings) as vectors:  # one client for the whole run (stop the API first)
        retriever = Retriever(EmbeddingService(settings), vectors)
        builder = ContextBuilder(retriever, vectors, top_k=top_k)
        schema = builder._stored_schema()
        for item in questions:  # configurations interleaved per question, so drift affects all alike
            for config in args.configs:
                ctx_text, ids = build_context(config, item["question"], schema, retriever, builder, top_k)
                entry = run_one(item, config, ctx_text, llm, database, calendar, compare)
                entry["context_ids"] = ids
                report["items"].append(entry)
                print(f"{item['id']:6} {config:22} {entry['verdict']:20} {entry['seconds']:6.2f} s")

    report["summary"] = summarise(report["items"], args.configs, len(questions))
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"rq2_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\n{'configuration':24} {'correct':>9} {'accuracy':>9} {'rejected':>9} {'median s':>9}")
    for config, s in report["summary"].items():
        print(f"{config:24} {s['correct']:>4}/{s['total']:<4} {s['accuracy']:>9.1%} {s['rejected']:>9} {s['median_seconds']:>9}")
    print(f"\nSaved {out.relative_to(EVAL_DIR.parent)}")


if __name__ == "__main__":
    main()
