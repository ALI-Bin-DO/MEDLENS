# Semantic guards: routing, names, joins, context and dates

This step fixed observed failures without changing the models, the read-only account, timeouts,
row limits, monitoring privacy or the one-correction limit. Every guard is either structural (from
`database/schema.sql`) or an explicit, listed rule. None depends on names or IDs in the current data.

## 1. Routing: data requests with chart words

- **Intent by request, not by keywords.** The classifier decides by what the message asks for. A
  message that names a measure, filter, grouping, period or person is a data question, even if it
  starts with "create a donut chart".
- **Structured output.** The classifier's JSON now also carries:
  - `chart_request` (bar_vertical, bar_horizontal, line, pie or none);
  - `doctor_names` and `patient_names`, without titles.
- **Data questions are asked without chart words.** The resolved question asks for the data only; the
  application draws the chart. If the requested view fits the result, it becomes the default view.
  Otherwise the answer explains why, using `chart.notes`. For example: distinct counts overlap across
  groups, so their slices would not add up to a whole.
- **Redrawing the previous result.** If a request only changes the chart of the previous result, the
  answer uses `view_request` and the browser redraws the rows it already has; no query runs. This
  covers two cases:
  - topic `change_view`;
  - a chart request whose resolved question equals the previous one.

  The server keeps only the previous result's chart views and notes in conversation memory, never
  its rows. An unsupported view is explained, with the views that would work.
- **Undefined measures** (`question_checks.py`, a listed glossary) get a targeted clarification:
  - operating/efficiency/utilization ratios;
  - operational volume and workload;
  - revenue;
  - profit and margin.

  The clarification lists measures that exist. A volume or revenue term that the user defines ("based
  on the number of patients …") is accepted. Ratios need an explicit definition. Profit cannot be
  computed, because no expenses are stored.

## 2. Names and clarification (`entity_grounding.py`)

- **Honorifics are removed.** Dr., Doctor, Prof., Mr., Mrs., Ms. and similar are never part of a name.
- **Where names come from:** the classifier's lists, plus titled names in the resolved question.
- **Bounded lookups.** Each name is looked up with `LIMIT 20` through `MySQLRepository.execute`: the
  same validator, read-only account, session limits and row cap as any other query. Only these values
  are allowed: letters, spaces and hyphens, up to three words, or an ID such as `D004`.
- **Exactly one match:** the SQL prompt gets the record's ID to filter on.
- **No match:** MedLens says no such person was found. It never substitutes someone else.
- **Several matches:** MedLens lists them with the identifying details only. Doctors are shown with
  ID, name, specialization and branch; patients with ID and name. The offered candidates are stored
  in the conversation turn.
- **Who decides.** The user's own reply must pick exactly one candidate: an ID, a surname, a full name,
  or a first name only one candidate has. The model's rewrite is not trusted for this. If the reply
  picks exactly one, it answers the pending question even when the classifier labelled it something
  else. "David" against two Davids asks again.
- **Short replies keep their context.** A short reply (4 words or fewer) while a clarification is
  pending is never treated as a greeting, thanks or an unrelated topic; the pending question is
  repeated instead. A standalone "Hello" still gets the normal greeting.

## 3. Relationship correctness (`sql_validator.py`)

**Join keys:**

- **Canonical keys from the schema.** A primary-key column is its own key. A foreign-key column takes
  the key it references, followed to the end. For example, `billing.treatment_id` resolves to
  `treatments.treatment_id`.
- **Equalities across tables** (in ON, WHERE or HAVING) are checked as follows:
  - Both sides are key columns: they must share the same canonical key. Otherwise the query is
    rejected as `invalid_join_key`; `appointments.appointment_id = billing.treatment_id` is the
    observed case. `billing.patient_id = appointments.patient_id` is allowed, because both resolve to
    `patients.patient_id`.
  - Only one side is a key column: rejected.
  - Neither side is a key column: allowed, because nothing can be established.
- **Every JOIN needs a key link.** Its ON clause must contain a key equality to an earlier table,
  combined with AND. Joins on dates, `1 = 1` or OR are therefore rejected as `join_without_key`; the
  check fails safely when it cannot establish a relationship.
- **Pointless grouping.** An aggregate over one table, grouped by that table's own primary key, gives
  one group per row (`row_level_group_by`).

**Dropped rows (`question_checks.dropped_rows`):**

- **When it applies.** The question asks for all or every appointments, patients, doctors or
  treatments ("all of Dr. David Taylor's appointments" included), or says "X … without / with no …".
  It does not apply when the question asks only for rows with a match ("only appointments that have
  invoices", "appointments having a bill"). The resolved question decides; if the rewrite lost the
  user's "all …", the user's own message is used.
- **What is checked, whatever table comes first in FROM.** Rules come from the schema's NOT NULL
  foreign keys, not from table names:
  - FROM must be the requested table T, or a table every T row reaches through NOT NULL foreign keys
    (doctors or patients for appointments). FROM billing therefore fails for "all appointments".
  - T joins along its own foreign key to such a complete table, or is RIGHT JOINed.
  - Every other INNER JOIN must follow a NOT NULL foreign key to a parent (appointments → doctors,
    patients). Tables T may have no row in (treatments, billing) must be LEFT JOINed.
  - A WHERE condition on a LEFT-joined table removes the NULL-extended rows unless it keeps NULLs
    (IS NULL, COALESCE). Conditions on T and its parents (doctor, dates, status) are the user's own
    restrictions and are fine.
  - If T is absent or joined twice, nothing is established and the check passes.
- **Before generation.** The facts block states which table must keep all rows and how
  (`keep_all_hint`).
- **Outcome.** A violating first attempt is not executed; it goes through the existing single
  correction with the diagnostic. If the correction still violates the requirement, it is not executed
  either, and the reply is a clear non-answer (status `rejected`, code `drops_unmatched_rows`): no
  partial list is shown.
- **Never flagged:** billing-first financial aggregates (no "all …" wording), "only …" requests, and
  all 250 knowledge-base examples (a unit test runs the check over every example).

**Prompt and documentation** state the key path:

- `appointments` → `treatments` (`appointment_id`) → `billing` (`treatment_id`);
- `appointments` → `doctors` and `patients`.

They also state the LEFT JOIN and ON-clause rule, and that an appointment can have several treatments
and bills.

**Correction allowlist.** All new rejection codes use the existing one-correction path. The
diagnostic lists the valid join keys and now allows up to 600 characters.

## 4. Dates (`clock.py`)

- **Exact ranges in application code.** `period_ranges()` computes ranges for explicit periods in the
  resolved question: months, ISO months, days, quarters, halves, years, and "from … to …" with both
  days included. March 2023 is `>= '2023-03-01' AND < '2023-04-01'`. The SQL prompt receives them.
- **Shifts relative to the previous question.** `shift_from_history()` applies "the month before",
  "the following month", "a year earlier" and "the previous year" to the single month or year of the
  previous question, in code. The month before January 2023 is December 2022.
- **Relative to today.** "This month" is resolved from the application calendar, including the year.
  The calendar reaches the SQL prompt only for relative wording, so it cannot inspire unrequested
  date filters.
- **Payment timing.** A question about when bills were paid asks whether `bill_date` is acceptable,
  unless the question already accepts the bill or issue date.
- **No silent changes.** An empty result never changes the date or the person.

## Evaluation

**`evaluation/regression_set.json`**, run with
`python -m evaluation.run_quality_eval --set evaluation/regression_set.json --label <name>`.

- `regression` (16 cases): the observed failures and the earlier held-out failures. These items have
  been inspected, so they are regression tests, not a generalization measure.
- `fixture` (8 cases): run on `evaluation/fixture_db.py`, an isolated in-memory SQLite copy of the
  schema with synthetic rows. It covers two doctors sharing a first name, an appointment without a
  bill, a treatment with two bills, and the year boundary. The real validator checks every query
  first. MySQL is not touched.
- `fresh` (10 cases): written before they were run, for a small generalization check.

**Final run** (`evaluation/results/quality_regression_final5_*.json`):

- **Regression:** routing 16/16, resolved 3/3, results 8/8, SQL checks 4/4, replies 8/8,
  presentation 4/4.
- **Fixture:** routing 8/8, results 5/6.
  - fx04 keeps the appointment without a bill only after the correction.
  - fx04 still fails strict comparison: it shows that appointment's invoice as 0 via
    `COALESCE(SUM(...), 0)`, not as missing.
- **Fresh:** routing 10/10, results 5/6, replies 3/3.
  - g05 picks the right doctor, but the SQL model wrote `SELECT DISTINCT ... ORDER BY` on a column
    outside the select list; MySQL refused it and the one correction repeated the mistake.
  - g10 ("When did patient P005 pay …") failed on its first run because the payment check did not
    know the verb "pay". It was fixed and passes now, so g10 is no longer unseen.

**Earlier quality set rerun:** all 33/33 scenarios pass every check. Its held-out items had been
inspected before, so this shows the absence of regressions, not generalization.
