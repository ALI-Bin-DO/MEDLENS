"""Before/after quality evaluation (evaluation/quality_set.json). Run from the project root, with the
API stopped (the local Qdrant index allows one client):

    python -m evaluation.run_quality_eval --label baseline [--split dev|heldout|all]
    python -m evaluation.run_quality_eval --set evaluation/regression_set.json --label regression

Scenarios marked "fixture": true run on evaluation/fixture_db.py (isolated SQLite copy of the
schema with synthetic rows; "fixture": "joins" / "months" use JOIN_ROWS / MONTH_ROWS), for the model,
the name lookups and the reference query alike.
Extra checks: sql_must_match / sql_must_not_match (regular expressions on the final SQL),
view_request, default_view, chart_views_exclude and reply_must_exclude.

Each scenario replays its turns through ChatService with conversation history built exactly as
POST /chat builds it (chat_controller._turn_to_keep), and scores only the LAST turn, on separate axes:
    routing      did it take the expected path (data / clarification / conversation / out_of_scope / no_sql)?
    resolved     for follow-ups and relative dates: required / forbidden phrases in the resolved question
    result       the executed result agrees with the reference query (rows compared as in run_dev_eval)
    reply        required phrases in the message the user would see
    latency      seconds for the scored turn (and for the whole scenario)
A fixed reference time pins both the date given to the model (when the code supports it) and
MySQL's session timestamp, so CURDATE() and the prompt agree and runs are repeatable.
Result agreement on this small dataset does not prove the SQL is right: read the SQL in the output.

Writes evaluation/results/quality_<label>_<timestamp>.json. Changes nothing in MySQL or the index.
"""

import argparse
import datetime as dt
import inspect
import json
import re
from pathlib import Path

from backend.app.config import get_settings
from backend.app.controllers.chat_controller import _turn_to_keep, to_response
from backend.app.repositories.mysql_repository import MySQLRepository
from backend.app.repositories.vector_repository import VectorRepository
from backend.app.services.chat_service import ChatService
from evaluation.fixture_db import JOIN_ROWS, MONTH_ROWS, FixtureRepository
from evaluation.run_dev_eval import compare

EVAL_DIR = Path(__file__).resolve().parent
QUALITY_SET = EVAL_DIR / "quality_set.json"
RESULTS_DIR = EVAL_DIR / "results"


def _route_ok(expected: str, trace) -> bool:
    executed = any(a.execution != "not_run" for a in trace.attempts)
    if expected == "data":
        return trace.data_pipeline and trace.stage not in ("needs_clarification", "cannot_answer")
    if expected == "clarification":
        return trace.stage == "needs_clarification" and not executed
    if expected == "conversation":
        return trace.stage == "conversation" and not trace.attempts
    if expected == "out_of_scope":
        return trace.stage == "cannot_answer" and trace.route == "out_of_scope" and not trace.attempts
    if expected == "no_sql":
        return not executed and trace.stage != "answered"
    raise ValueError(f"unknown expected route {expected!r}")


def _phrases_ok(text: str | None, include: list[list[str]], exclude: list[str]) -> tuple[bool, list[str]]:
    text = (text or "").lower()
    problems = [f"missing one of {group}" for group in include if not any(p.lower() in text for p in group)]
    problems += [f"contains {p!r}" for p in exclude if p.lower() in text]
    return not problems, problems


def run_scenario(item: dict, service: ChatService, database: MySQLRepository, today: dt.datetime, supports_today: bool,
                 max_turns: int) -> dict:
    expect = item["expect"]
    history, turns_out, trace = [], [], None
    scenario_seconds = 0.0
    for text in item["turns"]:
        kwargs = {"today": today} if supports_today else {}
        trace = service.answer(text, history, **kwargs)
        body = to_response(trace)
        scenario_seconds += trace.elapsed_seconds
        turns_out.append({"message": text, "stage": trace.stage, "route": trace.route, "route_source": trace.route_source,
                          "resolved_question": trace.resolved_question, "reply": body.message, "code": body.code,
                          "sql": [a.sql for a in trace.attempts], "elapsed_seconds": round(trace.elapsed_seconds, 2)})
        kept = _turn_to_keep(trace)
        if kept is not None:
            history = (history + [kept])[-max_turns:]

    body = to_response(trace)
    entry = {"id": item["id"], "split": item["split"], "tags": item["tags"], "turns": turns_out,
             "expected_route": expect["route"], "scored_seconds": round(trace.elapsed_seconds, 2),
             "scenario_seconds": round(scenario_seconds, 2)}
    entry["routing_ok"] = _route_ok(expect["route"], trace)

    if "resolved_must_include" in expect or "resolved_must_exclude" in expect:
        ok, problems = _phrases_ok(trace.resolved_question, expect.get("resolved_must_include", []),
                                   expect.get("resolved_must_exclude", []))
        entry["resolved_ok"], entry["resolved_problems"] = ok, problems
    if "reply_must_include" in expect or "reply_must_exclude" in expect:
        ok, problems = _phrases_ok(body.message, expect.get("reply_must_include", []), expect.get("reply_must_exclude", []))
        entry["reply_ok"], entry["reply_problems"] = ok, problems
    final_sql = trace.attempts[-1].sql if trace.attempts else None
    if "sql_must_match" in expect or "sql_must_not_match" in expect:
        problems = [f"missing /{p}/" for p in expect.get("sql_must_match", []) if not re.search(p, final_sql or "", re.I)]
        problems += [f"contains /{p}/" for p in expect.get("sql_must_not_match", []) if re.search(p, final_sql or "", re.I)]
        entry["sql_ok"], entry["sql_problems"] = not problems, problems
    presentation = []
    if "view_request" in expect and body.view_request != expect["view_request"]:
        presentation.append(f"view_request {body.view_request!r} != {expect['view_request']!r}")
    if "default_view" in expect and (body.chart is None or body.chart.default_view != expect["default_view"]):
        presentation.append(f"default_view {body.chart and body.chart.default_view!r} != {expect['default_view']!r}")
    for view in expect.get("chart_views_exclude", []):
        if body.chart is not None and view in body.chart.views:
            presentation.append(f"chart offers {view}")
    if any(k in expect for k in ("view_request", "default_view", "chart_views_exclude")):
        entry["presentation_ok"], entry["presentation_problems"] = not presentation, presentation
    if "reference_sql" in expect:
        if trace.result is None:
            entry["result_ok"], entry["result_detail"] = False, f"no result (stage {trace.stage})"
        else:
            verdicts = []
            for ref_sql in [expect["reference_sql"], *expect.get("alternative_reference_sqls", [])]:
                verdict, detail = compare(trace.result, database.execute(ref_sql), expect["order_matters"])
                verdicts.append((verdict, detail))
                if verdict != "mismatch":
                    break
            verdict, detail = verdicts[-1]
            entry["result_ok"], entry["result_detail"] = verdict != "mismatch", f"{verdict}: {detail}"
            entry["model_rows"] = trace.result.rows[:20]
    return entry


CHECKS = ("routing_ok", "resolved_ok", "result_ok", "sql_ok", "reply_ok", "presentation_ok")


def _rate(items: list[dict], key: str) -> str:
    scored = [i[key] for i in items if key in i]
    return f"{sum(scored)}/{len(scored)}" if scored else "n/a"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--split", default="all", help="a split name from the set, or all")
    parser.add_argument("--set", default=str(QUALITY_SET), help="scenario file (default: quality_set.json)")
    parser.add_argument("--only", nargs="*", help="scenario ids to run")
    args = parser.parse_args()

    spec = json.loads(Path(args.set).read_text(encoding="utf-8"))
    today = dt.datetime.fromisoformat(spec["reference_time"]).astimezone()  # local zone, like the app clock
    settings = get_settings()
    items = [s for s in spec["scenarios"] if args.split == "all" or s["split"] == args.split]
    if args.only:
        items = [s for s in items if s["id"] in args.only]

    database = MySQLRepository(settings, reference_time=today)
    report = {"set": spec["name"], "label": args.label, "split": args.split, "model": settings.ollama_sql_model,
              "reference_time": today.isoformat(), "started": dt.datetime.now().isoformat(timespec="seconds"),
              "settings": {"rag_context_top_k": settings.rag_context_top_k, "num_ctx": settings.ollama_sql_num_ctx,
                           "num_predict": settings.ollama_sql_num_predict}, "items": []}
    with VectorRepository(settings) as vectors:
        service = ChatService(settings, vectors, database=database)
        supports_today = "today" in inspect.signature(service.answer).parameters
        report["date_given_to_model"] = supports_today
        for item in items:
            if item.get("fixture"):  # isolated SQLite copy: model SQL, name lookups and references
                fixture = FixtureRepository(reference_time=today, rows={"joins": JOIN_ROWS, "months": MONTH_ROWS}.get(item["fixture"]))
                entry = run_scenario(item, ChatService(settings, vectors, database=fixture), fixture, today,
                                     supports_today, settings.conversation_max_turns)
            else:
                entry = run_scenario(item, service, database, today, supports_today, settings.conversation_max_turns)
            report["items"].append(entry)
            flags = " ".join(f"{k[:-3]}={'ok' if entry[k] else 'FAIL'}"
                             for k in CHECKS if k in entry)
            print(f"{entry['id']:4} {entry['split']:7} {flags:55} {entry['scored_seconds']:6.1f} s  "
                  f"stage={entry['turns'][-1]['stage']}")

    summary = {}
    for split in sorted({i["split"] for i in report["items"]}) + ["all"]:
        group = [i for i in report["items"] if split == "all" or i["split"] == split]
        if group:
            summary[split] = {k: _rate(group, k) for k in CHECKS}
            times = sorted(i["scored_seconds"] for i in group)
            summary[split]["median_scored_seconds"] = times[len(times) // 2]
    report["summary"] = summary
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"quality_{args.label}_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved {out.relative_to(EVAL_DIR.parent)}")


if __name__ == "__main__":
    main()
