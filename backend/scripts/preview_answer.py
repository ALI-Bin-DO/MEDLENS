"""Answer an English question end to end, read-only, from the command line. Run from the project root:

    python -m backend.scripts.preview_answer "How many patients are registered?"

Uses the same ChatService as POST /chat. Opens the Qdrant storage for the duration of the run,
so stop the API first. "cannot answer", "needs clarification" and rejected SQL never touch the
database. No retries.
"""

import argparse
import json
import sys

from backend.app.config import get_settings
from backend.app.repositories.mysql_repository import QueryResult
from backend.app.repositories.vector_repository import VectorRepository, VectorStoreError
from backend.app.services.chat_service import ChatService

RULE = "-" * 72


def format_rows(result: QueryResult) -> list[str]:
    lines = [" | ".join(result.columns)]
    for row in result.rows:
        lines.append(" | ".join("NULL" if v is None else json.dumps(v) if isinstance(v, list) else str(v) for v in row))
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Answer a question with read-only SQL (no retries).")
    parser.add_argument("question", help="English question (quote it)")
    args = parser.parse_args()
    settings = get_settings()
    try:
        with VectorRepository(settings) as repo:
            trace = ChatService(settings, repo).answer(args.question)
    except VectorStoreError as exc:  # e.g. storage locked by the running API
        sys.exit(f"Failed: {exc}")

    print(f"Question: {trace.question}")
    print(f"Route: {trace.route} ({trace.route_source}), routing {trace.timings.routing_seconds or 0:.2f} s")
    if trace.reply is not None:
        print(f"\n{trace.stage.upper()} [{trace.reply_code}] (no retrieval, no SQL, database not contacted)\n"
              f"{RULE}\n{trace.reply}\n{RULE}")
        return
    print(f"Context: {', '.join(trace.context_ids) or '(none)'}")
    g = trace.generation
    if g is None:
        sys.exit(f"FAILED at stage {trace.stage} ({trace.failed_component}): {trace.error}")
    print(f"LLM: {g.model}, total {g.stats.total_seconds:.2f} s" if g.stats.total_seconds is not None else f"LLM: {g.model}")

    if g.kind != "sql":
        title = "CANNOT ANSWER" if g.kind == "cannot_answer" else "NEEDS CLARIFICATION"
        print(f"\n{title} (no SQL generated, database not contacted)\n{RULE}\n{g.text}\n{RULE}")
        return

    for a in trace.attempts:
        print(f"Attempt {a.number} ({a.purpose}): outcome={a.outcome} validation={a.validation} "
              f"execution={a.execution} error_code={a.error_code} retryable={a.retryable}")
    if trace.correction_attempted:
        print(f"Correction: one attempt, {trace.timings.correction_seconds or 0:.2f} s; the SQL below is the final attempt.")
    print(f"\nGENERATED SQL\n{RULE}\n{g.text}\n{RULE}")
    v = trace.validation
    if v is not None and not v.ok:
        print(f"VALIDATION REJECTED (database not contacted)\n  code:   {v.code}\n  reason: {v.reason}")
        sys.exit(2)
    print("VALIDATION PASSED")
    if trace.result is None:
        sys.exit(f"EXECUTION FAILED at stage {trace.stage}: {trace.error}")

    r = trace.result
    print(f"EXECUTED (read-only): {r.row_count} row(s) in {r.duration_seconds * 1000:.1f} ms"
          + (f" - TRUNCATED at the {r.row_cap}-row cap; more rows exist" if r.truncated else ""))
    print(f"End-to-end: {trace.elapsed_seconds:.2f} s")
    print(RULE, *format_rows(r), RULE, sep="\n")


if __name__ == "__main__":
    main()
