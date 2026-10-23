# Data catalog, system administration and chart fixes

## Data catalog (`frontend/src/pages/CatalogPage.tsx`)

- **Table list** (`GET /catalog/tables`):
  - five cards, each with the display name, table name, description, grain and column count;
  - a filter over name, description and grain.
- **Table detail** (`GET /catalog/tables/{name}`):
  - **Columns:** description, declared type, ENUM values, nullable and key roles (primary key, unique, part of
    a unique key, foreign key with its target, indexed).
  - **Relationships:** foreign keys out and references in, both clickable.
  - **Unique constraints and non-unique indexes:** shown separately, each with what it means.
- **States:** loading; unavailable (with "Try again"); unknown table (404, with a way back); no tables; no
  filter match.
- **Missing descriptions:** shown as "No description available".
- **Not shown:** row counts, sample rows or freshness dates; the API has none.
- **Isolation:** browsing never calls `/chat`. The selected table and the chat state live in the workspace,
  so moving between catalog, administration and chat keeps both.

## System administration (`frontend/src/pages/AdminPage.tsx`)

- **Overview:** `GET /admin/metrics?window=` plus `GET /admin/system`.
  - Request count; answered data requests as numerator / denominator, labelled operational: it does not
    measure whether the answer is correct.
  - Median and p95 latency with their sample count; validation rejections, corrections, timeouts, busy and
    errors.
  - Outcomes by status (statuses with zero requests on one line); the reporting period in local time;
    component health.
- **Models & RAG:** `GET /admin/system`.
  - Models: installed, loaded ("No — loads on first use" is shown as normal) and inference ("Not checked",
    since the status check never runs a model); parameters and quantization.
  - Ollama version and the models loaded now.
  - Index: points, counts by type, vector dimensions and embedding-model match, and schema freshness, with
    the note that documentation and example freshness are not checked.
  - `checked_at` and the cache TTL.
- **Query Activity:** `GET /admin/activity?window=&limit=&offset=`.
  - The returned metadata only: time, route, outcome, code, total/model/database time, attempts, whether
    SQL ran, rows, HTTP status and a short request ID.
  - Newer/older paging; 25, 50 or 100 rows per page.
  - No question text, SQL or rows exist in the API, so the Figma "Question preview" column is omitted.
- **Configuration:** `GET /admin/config`, read-only and grouped as the server groups it. No edit, save,
  download, re-index or restart controls.
- **Loading and failures:** a tab loads when opened and on "Refresh"; nothing polls. A failing endpoint
  only affects its own section. The page says plainly that it has no authentication or role checks.
- **Not shown:** GPU or hardware telemetry; there is no endpoint for it.

**Dev proxy:** `vite.config.ts` forwards `/catalog` and `/admin` to the API, which stays bound to 127.0.0.1.

## Charts

- **Colors:**
  - Category bar charts with at most five categories use the categorical palette (emerald, blue, amber,
    teal, purple) in row order, whether or not a donut is offered.
  - The same category keeps its color in vertical bars, horizontal bars and the donut.
  - More than five categories, and every date or month series, use one series color.
- **Labels:** readable labels ("Patient count") in axis titles, tooltips and the chart description. Column
  names, rows, SQL and CSV exports are unchanged; no units are added.
- **Donut for distinct counts:** `COUNT(DISTINCT <primary key of the FROM table>)` with only many-to-one
  joins can now be a donut (`chart_builder._distinct_rows_of_base`), because each row is counted once, in
  one group. The age-band result (`COUNT(DISTINCT patient_id) FROM patients GROUP BY age_group`) was
  refused before. Distinct patients per branch, child joins, averages, and filtered or top-N results still
  never get a donut.

## Patient percentages by hospital branch (`backend/app/services/branch_share.py`)

- **Root cause:** for "the percentage of patients at each hospital", the SQL model returned `cannot_answer`
  because patients have no branch column. It did not use the appointments → doctors path. Even with that
  path, one supported query cannot return both a per-branch count and its denominator.
- **Clarification:** MedLens asks which percentage is meant:
  1. each branch's distinct patients with appointments ÷ all distinct patients with appointments in the
     same scope (can exceed 100% in total);
  2. each branch's share of appointments.

  The pending question stays in the conversation; "1", "first", "patients", "2" or "share of appointments"
  completes it.
- **The model writes only the per-branch count.** It gets a count question that keeps the period and
  status wording and never mentions a percentage, plus a fixed shape that is checked before execution (one
  correction, otherwise no answer).
- **Patients denominator:** derived from the validated SQL (same FROM, JOINs and WHERE;
  `COUNT(DISTINCT a.patient_id)`, no GROUP BY) and run read-only.
- **Appointments denominator:** the sum of the branch counts, which partition the appointments.
- **Arithmetic:** percentages use Decimal, rounded half-up to one decimal. The answer shows the
  numerator, the denominator, the derivation and the denominator query.
- **Charts:** patient percentages are bars only, with the overlap explained; appointment shares may be a
  donut.
- **Real data:** 48 distinct patients with appointments. Central Hospital 39 (81.3%), Eastside Clinic 34
  (70.8%), Westside Clinic 36 (75.0%), which add up to 227.1%.
