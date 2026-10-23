# Preserving user intent

The user's question and the conversation decide the requested output, grouping, filters and dates.
Retrieved examples only demonstrate SQL patterns. This step added a compact request description and
checks before execution. Models, the 250 examples, the context size, read-only controls, timeouts, row
caps, conversation isolation, metadata-only monitoring and the one-correction limit are unchanged.
The description and its checks catch listed mismatches; they do not guarantee that a query is correct.

## Root causes (from traces)

- **n02, "visits":** "visits" was not recognised as appointments, so the keep-all rule did not apply.
  The retrieved examples were 229 (daily totals) and 206 (zero-filled totals per appointment), and the
  SQL copied 206: `COALESCE(SUM(b.amount), 0)` per appointment instead of individual amounts with
  NULL.
- **f01, "Do it by hospital branch":** the resolver returned the previous question unchanged. With
  the same history the 4B resolver applied grouping changes inconsistently, with or without the
  previous SQL; "Also break it down by status" was once turned into a replacement.
- **Extra bill_date filter:** the facts gave "November 2023: col >= ..." without naming a column.
  Example 249 filters bill_date, and the model applied the period to both columns.
- **q09 (insurance):**
  - Both attempts looked for `insurance_provider` on billing.
  - The validator reports only the first unknown column, and the diagnostic said where the column
    was not, never where it is.
  - The retrieved examples (72, 106, 76, 79) were single-table treatment examples, so nothing showed
    the treatments → appointments → patients path.
  - A later run also counted `COUNT(DISTINCT p.patient_id)` for "how many treatments".

## The request description (`backend/app/services/request_spec.py`)

Built from the resolved question, the latest message, the previous turn and the schema:

- **keep_all:** the table whose rows must all remain (`question_checks.requested_table`). Paraphrases
  count: visits, consultations and bookings are appointments; "reason for visit" is a column.
- **counts:** the table "how many X" counts.
- **individual_amounts / zero_fill:** individual invoice amounts vs. totals, and whether 0 for
  missing values was asked for.
- **grouping / removed_grouping:** from the question, or from a follow-up's change.
- **periods:** each explicit period, with the date column it describes. The nearest cue word decides:
  "appointments in November" → appointment_date, "invoices issued in June" → bill_date, "registered in
  2022" → registration_date. A "from … to …" day range is one period with both days included.
- **grounded:** matched people's IDs (the only source of IDs besides the question).

The facts block gets the period's column in its range line and a few short "Request" lines. It also
names the table and key path of an attribute stored elsewhere: "insurance_provider is
patients.insurance_provider; from treatments join with …". These are advisory lines. If they would
leave no room for any retrieved example, they are left out of that prompt. This happened for r05,
whose only kept example is 203. The checks still run.

## Follow-ups

`grouping_change` handles short follow-ups that only change the grouping, in code:

- **replace:** "Do it by hospital branch", "Now per specialization instead", "by month instead of by
  doctor";
- **add:** "Also break it down by status", "and by hospital branch too", "Split that further by
  appointment status";
- **remove:** "Drop the payment status split", "overall".

A message that names its own measure or subject is a new question. A relative period ("And the
month before?") is not a grouping. If the resolver's question does not match the change, the
previous question is rewritten in code. The previous turn's grouping is kept in the conversation
turn (`Turn.grouping`, never rows).

## "Visits"

"Visits" is not a status in the data. If a question says visits but no status, a bounded, validated,
read-only count checks the requested scope (period, matched people) for appointments that are not
Completed:

- **None:** the question is answered.
- **Some:** MedLens asks whether to count only Completed appointments or every status.

The reply ("all of them", "only completed") is added to the pending question even if the resolver
drops it.

## Checks before execution (`request_spec.mismatches`)

| Check | Catches |
|---|---|
| Grouping | GROUP BY missing a requested dimension, still using a replaced one, or one overall value when the question asks "per X" |
| Count target | every COUNT counts another table's key ("how many treatments" → COUNT(DISTINCT patient_id)); COUNT(DISTINCT x) grouped by x (always 1) |
| Amounts | SUM/AVG of amounts when individual invoice amounts were asked for; COALESCE(…, 0) on amounts when zero was not asked for |
| Date column | a question's period applied to another date column (the November bill_date case) |
| Range end | `< last day` that drops the last day of an inclusive range |
| Filter values | status/method/gender values, IDs, names and thresholds in WHERE/HAVING/ON that are not in the question, its documented synonyms or the grounded IDs; dates the question does not name |

Join keys and technical constants are not treated as business filters: IS NULL, '' blanks, 0 and 1,
and dates that are period bounds. A mismatch is never executed:

- **First attempt:** it goes through the existing single correction (`request_mismatch`), and the
  correction prompt gets the problems plus the request lines.
- **Correction still mismatched:** the reply is a non-answer ("does not match what was asked").
- **Meaning guard:** the correction's meaning guard lets the correction drop exactly what was flagged
  (`request_spec.removable`). A filter moved into `SUM(CASE WHEN …)` also counts as kept.

**Unknown column or alias:** the diagnostic now covers every misplaced column, not only the first.
For each, it names where the column is and how to reach it. A parent that is already joined comes
first ("use a.patient_id"); otherwise it gives the key path, and it names the table for an alias
that was never joined.

**False positives:** none of the 250 examples is flagged (unit test), and neither is any evaluation
reference query, apart from IDs that name grounding supplies.

## Evaluation (real model, reference time 2023-11-15)

- **`intent_eval_set.json`, fresh_round1 (i01–i11):** first run 10/11 results.
  - i11 failed: the resolver dropped "leave it empty …", and the SQL returned one overall total
    instead of one per appointment.
  - The "per X" rule followed, so later runs (11/11) are not held out.
- **`intent_eval_set.json`, fresh_round2 (j01–j06):** written after all fixes, first and only run 6/6.
- **`examples_eval_set.json`:** 13/13 routing, 12/12 results. n02 and x02 (the November case) are now
  regression cases.
- **`regression_set.json`:** 34/34 on every axis.
- **`quality_set.json`:** 33/33 routing, 22/22 results, 10/10 resolved. q09, f01, q05, q12, q14 and
  f04 were inspected during this step, so they are regression cases now.
- **Intermediate runs:** these failures were found, traced and fixed:
  - r10/q05: range end;
  - fx06/g07: "the month before" misread as a grouping;
  - r05: example squeezed out by the facts;
  - q12: "with none";
  - q14: always-1 counts;
  - f04: "patient gender", and the guard rejecting a filter moved into CASE.

## Limitations

- **Listed rules:** the rules are listed words and shapes. Other paraphrases, measures or groupings
  are left to the model.
- **Period columns:** the column a period describes comes from the nearest cue word. A question with
  no cue gets no date-column check.
- **Filter values:** checked by text (with a 5-letter prefix match for free-text columns). A copied
  value that happens to appear in the question is not caught; thresholds compared to functions
  (HAVING COUNT(*) > n) are not checked.
- **Group-by check:** it cannot tell doctors' from patients' name columns.
- **"Visits" count:** it covers explicit periods and matched people, not other filters.
- **Rewritten follow-ups:** grouping changes rewritten in code can be clumsy English, for example
  "… does each hospital branch have?".
- **Removal follow-up:** "without the doctor breakdown" after "each doctor" becomes "all doctors
  together".
- **Prompt budget:** advisory lines compete with retrieved examples; when only one fits, the example
  wins.
