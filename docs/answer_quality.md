# Answer quality: dates, follow-ups, missing data, capabilities and charts

This milestone improved how MedLens answers, without changing the hospital data, the validator's
rules, the read-only account, the model or the hardware requirements.

## Current date and relative periods

- **Authoritative clock:** `backend/app/services/clock.py`. MedLens uses the local time zone of the
  machine running the API. MySQL runs on the same machine with `time_zone=SYSTEM`, so `CURDATE()`
  agrees with it.
- **One reading per request:** the date is read once per request, and the same date reaches both
  the follow-up resolver and SQL generation.
  - The resolver gets the full calendar: today, yesterday, this and last week, this and last month,
    this and last year.
  - SQL generation and correction get a compact version: this and last month, this and last year.
  - Every period is written as a start-inclusive, end-exclusive date range.
- **Resolver rule:** today-relative phrases become explicit periods. "This month" becomes
  "October 2026". Explicit periods are copied unchanged.
- **History-relative phrases:** "the previous month" after a question about a month counts back from
  that month, not from today.
- **SQL rule:** filter periods with full-date ranges, never `MONTH(col)` alone. Group months with
  `DATE_FORMAT(col, '%Y-%m')`.
- **No hard-coded dates:** permanent examples use explicit periods (e.g. "March 2023") or `CURDATE()`
  for ages. No example contains today's date.
- **Repeatable evaluation:** evaluation passes a fixed time to `ChatService.answer(..., today=...)`
  and to `MySQLRepository(reference_time=...)`. The repository then runs a fixed
  `SET SESSION timestamp = <seconds>`, which is read-only and only affects that session. So
  `CURDATE()` and the prompt agree. The API never sets this.

## Follow-up grouping

The resolver prompt distinguishes three cases:

- **Replace:** "now group it by payment method", "per month rather than per patient". The new
  question keeps measures and filters but drops the old grouping and its columns, such as doctor
  names.
- **Add:** "also", "as well", "and by gender", "further by status". The old grouping is kept and the
  new one is added.
- **Ask:** a genuinely ambiguous change ("the other type" when several exist) is answered with a
  clarifying question.

The prompt's example phrases differ from the evaluation questions on purpose.

## Information that is not stored

- **Documentation:** `knowledge_base/data_catalog.md` lists what is not stored. Examples: payment
  dates, diagnoses, schedules, appointment end times. It also says `bill_date` is the issue date.
- **SQL prompt rule:** never substitute a column silently. If a related column exists, answer
  `NEEDS_CLARIFICATION` and ask whether it is acceptable.
- **A `CANNOT_ANSWER` that ends with a question counts as a clarification.** The model sometimes uses
  that label for a clarifying question, so `llm_service._classify` treats it as one.
- **Clarification memory:** a clarification from the SQL model is kept in conversation memory.
  `Turn.clarification` holds the server-generated question; no SQL and no rows. The user's answer
  ("Yes, use the bill date") is then resolved into a full question. Memory stays bounded by
  `CONVERSATION_MAX_TURNS` and is never persisted.

## Capability replies

- **Single source:** `backend/app/services/capabilities.py` is the only description of implemented
  features. It covers the chart views and when each is offered, CSV export, the five tables, the
  temporary conversation memory (built from the real settings) and the read-only limits.
- **Topic classification:** the intent classifier's structured JSON output now includes a `topic`,
  constrained to an enum: charts, change_view, export, data, memory, limitations or overview.
  Each topic has one fixed reply. No SQL, index or database is used.
- **Fast replies kept:** obvious standalone questions such as "what can you do?" still get the rule
  reply without the model.
- **Three cases kept apart:**
  - Asking which charts exist gets the charts topic.
  - Asking to see the current result differently ("can I see this as a donut?") gets change_view or
    charts. It explains the chart-type buttons and runs no query.
  - A new data question that mentions a chart is a data question.
- **Write requests:** asking to add, change or delete records gets the read-only explanation. The
  validator, the read-only account and the function allowlist are unchanged.

## Chart views

The backend (`chart_builder.py`) decides which views fit a result and returns them in
`chart.views` with a `chart.default_view`. The frontend only offers those views. Switching reuses
the same points, so there is no new SQL, model call or request (checked in the browser and in
tests).

**Data shapes:**

- **Categories:** one numeric metric plus text columns that name each row. Points keep the result's
  order.
- **Time series:** one numeric metric plus a DATE/DATETIME column, or a month column built with
  `DATE_FORMAT(col, '%Y-%m')`, verified from the SQL rather than guessed from the values. Points are
  sorted by date.

**When each view is offered and chosen by default:**

- **Vertical bars:** offered for categories and time series. Default for at most 8 categories whose
  labels are at most 16 characters.
- **Horizontal bars:** offered for categories. Default for longer labels or more categories.
- **Line:** time series only, and the default for them.
- **Pie/donut:** only for a part of a whole. All of these must hold:
  - 2 to 5 categories;
  - every value at least 0, and a positive total;
  - the metric is `COUNT(...)` or `SUM(...)`, optionally inside `ROUND`/`COALESCE`, without
    `DISTINCT`;
  - the query has `GROUP BY` and no `HAVING`, `LIMIT` or `SELECT DISTINCT`.

Averages, rates, minimums and maximums never get a pie. Neither do distinct counts (groups may
overlap), top-N or filtered subsets, or results whose output columns cannot be mapped to the SQL
(`SELECT *`).

**Unchanged safeguards:** no chart for NULLs in plotted columns, truncated results, repeated
categories, or more than `CHART_MAX_POINTS` rows. Nothing is aggregated, dropped or merged into
"Other".

**Colors:**

- **Categorical palette, in fixed order:** emerald `#008755`, blue `#2563EB`, amber `#B7791F`,
  teal `#0891B2`, purple `#7C3AED`.
- **Validated** with the dataviz palette checker against the white surface: lightness band, chroma,
  colour-blind separation, normal-vision separation and contrast. This includes the last-to-first
  pair that meets in a donut.
- **When categories get colors:** results that offer a donut color each category, in row order, the
  same way in every view. All other results use one emerald series, and a line is always one color.
- **No overlap with status colors:** none of these colors is the app's error red, and colors are never
  cycled.
- **Tooltips and motion:** tooltips show the exact value (`y_exact`); donut tooltips also show the
  share. There are no chart animations, and the app's reduced-motion rule still applies.

## Knowledge base

- **Superseded:** the 200-example set described here was later replaced by a revised set of 250
  examples; see `docs/examples_integration.md`. The rest of this section describes the 200-example set.
- **Examples:** `knowledge_base/examples.sql` had exactly 200 examples. That is the 5 original ones,
  reviewed, plus 195 new ones, one question–SQL pair per chunk in the existing format.
- **Checker:** `python -m backend.scripts.check_examples` validates every example with the existing
  validator and runs it through the read-only repository. It then writes `docs/examples_inventory.md`:
  coverage by table and feature, near-duplicates, and overlap with the evaluation questions.
- **Validation and execution:** 200/200 passed validation and executed.
- **Coverage:**
  - by tables joined: 116 single-table, 51 two-table, 21 three-table, 10 four-table and 2 five-table
    examples;
  - by feature: 17 LEFT JOINs (6 anti-joins), 13 filters inside `ON`, 8 HAVING, 15 NULL checks and
    32 date-range filters.
- **Semantic review:** execution success is not semantic correctness. Every question–SQL pair was
  reviewed by hand. Example 192 originally paired a question with SQL that answered a different one,
  so it was replaced. Example 134 had a wrong comment, which was fixed.
- **Duplicates:** three examples were replaced:
  - one changed-constant copy (pending vs paid total);
  - one same-shape date example;
  - one example too close to evaluation question q14.

  Two flagged pairs were kept, with the reason recorded in the inventory.
- **Index:** rebuilt in place (update, not recreate) while the API was stopped. 215 points: 5 schema,
  10 documentation and 200 example chunks.
- **Prompt budget:** with this larger knowledge, the lowest-ranked retrieved chunks are left out when
  the SQL prompt would not fit `num_ctx`. Room is kept for one correction, and the schema is never cut.
  `num_ctx`, the model and the safety ratio of the token estimate are unchanged.

## Evaluation (before and after)

**Setup:**

- Set `evaluation/quality_set.json`: 33 scenarios, 18 dev and 15 held-out. References were written
  before any model output was seen, and none of these questions are in the knowledge base.
- Runner: `python -m evaluation.run_quality_eval --label <name>`. Stop the API first, because the
  local index allows one client.
- Settings for both runs: `qwen3.5:4b`, `RAG_CONTEXT_TOP_K=5`, `num_ctx` 4096, and the fixed reference
  time 2024-01-15 12:00 local. All data is from 2023, so a 2024 date separates year-aware SQL from
  `MONTH()`-only SQL.
- Held-out items were run only for the baseline and the final comparison. Prompts were tuned on dev
  items only.

**Baseline (before any prompt or knowledge change; 5 examples):**
`evaluation/results/quality_baseline_20261022_220320.json`

- Routing: dev 16/18, held-out 12/15.
- Resolved question: dev 6/7, held-out 3/3.
- Result agrees with the reference: dev 11/13, held-out 8/9.
- Reply topic: dev 2/5, held-out 0/3.
- Median latency of the scored turn: 2.33 s.

**After (final code):** `evaluation/results/quality_after_final2_20261022_223142.json`

- Routing: dev 18/18, held-out 14/15.
- Resolved question: dev 6/7, held-out 3/3.
- Result agrees with the reference: dev 12/13, held-out 8/9.
- Reply topic: dev 5/5, held-out 2/3.
- Median latency of the scored turn: 2.13 s.

**SQL semantics, reviewed by hand** (result agreement alone can pass by luck):

- Baseline: 18 of 22 data scenarios correct.
  - q02 used a rolling 30 days for "last month".
  - q03 ended March at `2024-03-01`.
  - q07 used a rolling 12 months for "last year"; its result matched the reference only by luck.
  - q08 added a "Paid" filter nobody asked for.
- After: 20 of 22 correct. q03 and f09 are wrong.

**What improved:**

- "This month" and "last month" now resolve to explicit, year-aware months (q01, q02).
- "Last year" is now the calendar year (q07).
- No invented filter (q08).
- Replacing a grouping now drops the old one in the resolved question (f01).
- Asking when bills were paid gets a clarification instead of a silent substitution (u01). u03 now
  asks before using `reason_for_visit`.
- All capability questions get topic-specific replies (c01–c06), and "show that as a pie chart"
  no longer re-runs SQL (c06).

**Remaining failures, reported as they are:**

- **q03 (held-out, also failed in the baseline):** "March 2023" still ends at `2024-03-01`. The SQL
  model's literal-date arithmetic is unreliable.
- **f09 (dev, regression):** the month before January 2023 is resolved as December 2023. The baseline
  answered December 2022 correctly. Having the calendar in the resolver prompt very likely causes this.
  Two general prompt changes did not fix it.
- **u02 (held-out, also failed in the baseline):** "bills paid during March 2023" still uses
  `bill_date` silently, with the same year error as q03. Direct "when were they paid" questions are
  caught; "paid in <period>" is not.
- **q05 (held-out):** it failed in one final run and passed in the next, with identical code and
  settings. The resolver dropped "including both days" in the failing run. Even at temperature 0,
  results vary between runs.
- **q04 (dev):** correct result, but convoluted SQL (a quarter `CASE` with `HAVING`), copied from an
  example.

**Not tested:** synthetic fixtures were not needed. A 2024 reference date makes the real 2023 data
separate correct and incorrect date SQL. Zero-count cases use the 2 patients without appointments.
