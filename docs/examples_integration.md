# Integrating the 250-example knowledge base

This step replaced `knowledge_base/examples.sql` with a revised set of 250 question–SQL examples. It
also fixed the remaining "all appointments" failure. Models, `num_ctx`, `top_k`, schema inclusion,
the read-only account, timeouts, row caps and the one-correction limit are unchanged.

## The example set

- **Structure:** exactly 250 examples, numbered 1–250 with no gaps or duplicates. Every block has one
  `-- Question:` line and one SQL statement ending in `;`. Twelve examples carry an explanatory
  comment line after the question.
- **Chunking:** each block, comments included, is one retrieval chunk (`example:N`) and is embedded
  as a whole. Validation and execution use the SQL with every `--` line removed
  (`check_examples.parse`; the same rule as `tests/test_sql_validator.py`).
- **Validation and execution:** 250/250 pass the unchanged validator and run through the read-only
  `MySQLRepository`, with its timeouts and row cap. The validator already supports NULLIF,
  COUNT(CASE …), DISTINCT aggregates, LEFT JOIN conditions in ON, DATE_FORMAT `%x-W%v`, DATEDIFF and
  TIMESTAMPDIFF. No rule was relaxed.
- **Meaning:** every pair was read against its SQL. Checked distinctions: all appointments vs. only
  invoiced ones, missing vs. zero-amount invoices, individual invoice rows vs. one row per appointment,
  multiple treatments and bills, billed vs. appointment patients, Completed vs. any status, issue dates
  vs. payment dates, inclusive and exclusive date bounds, and zero periods.
  - No example needed a correction, so no IDs were changed.
  - Example 225 filters `amount > 30`. That matches its question, but every real bill is above 534,
    so the filter excludes nothing. It was kept as written: the threshold is illustrative.
- **Real data limits:** it has exactly one treatment and one bill per appointment and no zero-amount
  bills. Examples 145, 146, 159 and 160 therefore return 0 rows. Their meaning is verified on the
  fixture (see Tests).
- **Inventory:** `docs/examples_inventory.md`. It lists 20 near-duplicate pairs, all reviewed: they
  share an SQL shape but teach different periods or values. It also lists 7 word-level overlaps with
  evaluation questions.

## Overlap with evaluation

Several examples restate earlier evaluation scenarios, so those scenarios are no longer held out:

- **Word-level overlaps:** example 41 ~ f03, 42 ~ r11, 105 ~ dev-02/f04, 141 ~ f05, 147 ~ dev-01 and
  187 ~ dev-05.
- **Same scenario in different words:**
  - 225 ~ r01;
  - 201/203/243/40 ~ r03–r06 and fx03/fx04;
  - 162 ~ r08, fx06 and g07;
  - 93 ~ r09;
  - 101/63 ~ r10;
  - 148 ~ g01;
  - 197/116 ~ g06;
  - 109 ~ g10.

New evaluation questions are in `evaluation/examples_eval_set.json` (split `new`). They are not in
the knowledge base, but several deliberately follow an example's pattern with other values; the set
notes which.

## The remaining "all appointments" failure

**Observed:** the question "all appointments with Dr. David Taylor for this month, including the
patient's name, the invoice amount, and the hospital branch" produced a query that started FROM
billing with INNER JOINs. The old check only looked at queries that started FROM appointments, so
this one passed.

**Retrieval before:** the 200-example index retrieved no all-appointments example for this
question, and the budget kept 3 of 5 chunks.

**Retrieval after:** it retrieves 203, 243, 204, 202 and 201. The budget keeps example 203, which
lists all appointments for one doctor and one month with LEFT JOINs to treatments and billing.

**Changes:**

- `question_checks.dropped_rows` now works whatever table comes first in FROM. Its rules come from the
  schema's NOT NULL foreign keys (see `docs/semantic_guards.md` §3). It catches:
  - a FROM on a table T may have no row in;
  - INNER JOINs to optional tables;
  - WHERE conditions that remove NULL-extended rows.
- It does not flag joins to parents (patients, doctors), RIGHT JOIN T, or conditions that keep NULLs.
- "Only …" requests are excluded, and so are billing-first aggregates without "all …" wording.
- The facts block states the requirement before generation (`keep_all_hint`).
- If the rewrite drops the user's "all …", the user's own message is checked.
- A violating attempt is never executed. The first gets the existing single correction. If the
  correction still violates the requirement, the reply is a non-answer: status `rejected`, code
  `drops_unmatched_rows`. The earlier "answer with a warning" path was removed.
- The resolver prompt asks to keep "all / every / only / including … without" verbatim.
- `data_catalog.md` adds the distinctions above without new business definitions, plus a "Counting
  after joins" section.
- The SQL prompt says the facts name the table that must keep all rows.
- Date facts: a "from … to …" day range is no longer also listed as two one-day periods. Listing
  them made the model write `< last day` and lose that day (r10).

**Retrieval selection was not changed.** For the contrasting requests, the most relevant example is
ranked first and always survives the budget. With top_k 5, only 1–3 retrieved chunks fit; this is a
known limit of the current context size.

## Index

- **Build:** `python -m backend.scripts.build_index`, run with the API stopped.
- **Counts:** schema 5, documentation 13, examples 250; 268 points in total.
- **Rebuild:** a second build upserted the same 268 points, removed 0, and left 268 unique point IDs.
- **Content check:** stored chunk content equals the files.
- **Obsolete removal:** checked in a temporary Qdrant store, not the real one. Syncing 220 chunks,
  then the current 267, removed the 3 obsolete ones; a repeat sync removed 0.

## Tests and evaluation

**Offline:**

- **Fixture tests:** `tests/test_join_fixtures.py` runs examples on `fixture_db.JOIN_ROWS`. The
  fixture has:
  - an appointment without treatments;
  - a treatment without bills;
  - a zero-amount bill;
  - two treatments with three bills;
  - equal-cost treatments;
  - a partly billed appointment;
  - a doctor without appointments.

  The tests check:
  - results: missing invoices stay NULL; zero appears only when asked; counts and costs are not
    duplicated; branch-only vs. doctor grouping;
  - SQL semantics: the check flags the billing-first query and a WHERE filter on billing, and never an
    example;
  - the correction path: one correction; a still-violating correction is not executed.
- **Unit tests:** `tests/test_semantic_guards.py` covers the wording rules and join shapes.

**Real model** (reference time 2023-11-15 for the new set):

- **Examples set:** x01 (the observed failure) passes all checks. The new split routed 11/11, matched
  9/10 results and passed 1/1 reply check. n02 fails: it says "visits", which the check does not
  recognise, and the model returned per-visit totals with 0 for unbilled visits instead of individual
  NULL amounts.
- **Regression set:** 34/34 on every axis. fx04 and g05, which failed before, now pass; these are
  regression cases, not generalization.
- **Earlier quality set:** routing 33/33, results 20/22, resolved 9/10.
  - q09: the model joined billing for insurance_provider and was rejected twice.
  - f01: "Do it by hospital branch" was not applied. The first-turn SQL now copies example 147, and
    that SQL is part of the resolver's history. Removing the new resolver sentence did not change it.
