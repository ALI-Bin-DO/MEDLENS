"""What MedLens can actually do: the single source for capability replies.

The intent classifier labels questions about MedLens itself as "capabilities" and adds a topic
(structured output, not phrase matching); obvious standalone questions such as "what can you do?"
are routed by rules to the overview. Each topic has one fixed reply built from the definitions
below, so replies describe implemented features only. No SQL, model or database is used to answer.

Keep this file in step with the features: chart views (chart_builder.py and the frontend chart
selector), CSV export (POST /export/csv), conversation memory (conversation_store.py) and the
read-only pipeline (sql_validator.py, mysql_repository.py).
"""

from backend.app.config import Settings, get_settings

OVERVIEW = "overview"
CHARTS = "charts"
EXPORT = "export"
DATA = "data"
MEMORY = "memory"
LIMITATIONS = "limitations"
CHANGE_VIEW = "change_view"
TOPICS = (OVERVIEW, CHARTS, EXPORT, DATA, MEMORY, LIMITATIONS, CHANGE_VIEW)

# Chart views the result card can show, with when MedLens offers each one (chart_builder.py).
CHART_VIEWS = (
    ("vertical bars", "a few categories with short names"),
    ("horizontal bars", "long category names or many categories"),
    ("a line", "a trend over dates or months, in date order"),
    ("a pie/donut", "up to 5 categories whose counts or totals add up to a meaningful whole "
                    "(never for averages, rates or groups that overlap)"),
)

# How chart views are named in replies.
VIEW_NAMES = {"bar_vertical": "vertical bars", "bar_horizontal": "horizontal bars", "line": "a line", "pie": "a donut"}

# Tables in database/schema.sql and what they hold (data_catalog.md).
TABLES = (
    ("patients", "registration, gender, date of birth, insurance provider and contact details"),
    ("doctors", "specialization, hospital branch and years of experience"),
    ("appointments", "date, time, reason for visit and status, linking patients and doctors"),
    ("treatments", "treatment type, description, cost and date, linked to appointments"),
    ("billing", "bill date, amount, payment method and payment status"),
)

_EXAMPLE = '"How many patients are registered?"'


def reply(topic: str, settings: Settings | None = None) -> str:
    """The fixed English reply for a capability topic (unknown topics get the overview)."""
    settings = settings or get_settings()
    charts = "; ".join(f"{name} for {when}" for name, when in CHART_VIEWS)
    if topic == CHARTS:
        return ("Every answer includes a table. When a result has one numeric measure per category or per date, "
                f"MedLens also offers a chart: {charts}. The chart-type buttons above the chart switch between "
                "the views that fit the result; switching reuses the same rows and runs no new query. "
                "Hover a bar, point or slice to see its exact value.")
    if topic == CHANGE_VIEW:
        return ("To see the latest result another way, use the Table and Chart buttons on that result, then the "
                "chart-type buttons above the chart. Switching reuses the rows you already have and runs no new "
                "query. Only views that fit the result are offered; for example, a pie/donut appears only for up to "
                "5 categories whose counts or totals form a whole. To change the data itself, ask a new question.")
    if topic == EXPORT:
        return ("Each result table has an Export CSV button. It downloads exactly the rows shown, with exact values, "
                "as a UTF-8 CSV file, without running the question again. If the result was cut at the row limit, "
                "the file is marked as a partial export.")
    if topic == DATA:
        tables = "; ".join(f"{name} ({what})" for name, what in TABLES)
        return (f"MedLens reads five tables of this hospital's database: {tables}. Monetary amounts have no "
                "stated currency, and bills record the date they were issued (bill_date), not the date they were "
                f"paid. Ask in English, for example: {_EXAMPLE}")
    if topic == MEMORY:
        minutes = max(1, settings.conversation_ttl_seconds // 60)
        return ("Within one conversation you can ask follow-ups such as \"Only completed ones\" or \"Do it by "
                f"hospital branch\". The server keeps the last {settings.conversation_max_turns} answered questions "
                f"of a conversation in memory only: it expires after {minutes} minutes without use, is lost when the "
                "server restarts and is never saved. A new conversation starts with no memory.")
    if topic == LIMITATIONS:
        return ("MedLens is read-only: it only runs validated SELECT queries with a read-only database account, so "
                "it cannot add, change or delete records. It answers English questions about the five hospital "
                "tables only, and it cannot answer from information that is not stored, such as diagnoses or the "
                "date a bill was paid.")
    return ("MedLens answers English questions about this hospital's data (patients, doctors, appointments, "
            "treatments and billing) by generating a read-only SQL query, validating it and running it. Each answer "
            "shows the SQL and the result table, with a chart when the result suits one, and you can export it "
            f"as CSV. Try {_EXAMPLE} Within one conversation you can ask follow-ups; that memory is temporary "
            "and is not saved.")
