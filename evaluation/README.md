# التقييم

هذا المجلد يقيس جودة MedLens: هل يكتب SQL صحيحًا، وهل النتيجة صحيحة؟

## قاعدة مهمة: افصل أسئلة التقييم عن أمثلة RAG

الأمثلة في `knowledge_base/examples.sql` تدخل في سياق النموذج اللغوي. إذا استخدمنا الأسئلة نفسها (أو صيغًا قريبة جدًا منها) في التقييم، سيبدو النظام أفضل من حقيقته، لأنه رأى الإجابة مسبقًا.

لذلك:
- أسئلة التقييم تُكتب هنا فقط، ولا تُضاف إلى `knowledge_base/`.
- لا تنسخ سؤالًا من `examples.sql` إلى التقييم، ولا العكس.
- لكل سؤال تقييم: السؤال، واستعلام SQL مرجعي كُتب وجُرّب يدويًا، والنتيجة المتوقعة.
- قارن **نتيجة** الاستعلام وليس نصه، لأن استعلامين مختلفين قد يعطيان النتيجة الصحيحة نفسها.

## المحتوى

- `dev_set.json`: مجموعة تطوير (dev-v1) من 5 أسئلة، لكل سؤال استعلام مرجعي كُتب يدويًا قبل رؤية ناتج النموذج.
  - هذه ليست مجموعة اختبار نهائية مخفية، لأنها قد تؤثر على تعديلات لاحقة.
  - الأسئلة ليست موجودة في `knowledge_base/`، ولا تُضاف إليها.
- `run_dev_eval.py`: يمرر كل سؤال عبر المسار الكامل، وينفذ الاستعلام المرجعي بطبقة التنفيذ نفسها، ثم يقارن النتائج.
  - المقارنة تتجاهل أسماء الأعمدة، وتتجاهل ترتيب الصفوف إلا إذا طلبه السؤال.
- `results/`: نتائج كل تجربة، مع تاريخها والنموذج والإعدادات المستخدمة.

التشغيل من جذر المشروع:

```powershell
python -m evaluation.run_dev_eval
```

## Regression evaluation (regression-v1)

- `regression_set.json`: observed failures and earlier held-out failures (regression cases), fixture cases run on
  `fixture_db.py` (isolated in-memory SQLite copy of the schema, synthetic rows; MySQL is not touched), and a
  `fresh` split for a small generalization check.
- Run with the API stopped: `python -m evaluation.run_quality_eval --set evaluation/regression_set.json --label <name>`.
- Results: `docs/semantic_guards.md`.

## Examples integration (examples-integration-v1)

- `examples_eval_set.json`: the observed all-appointments failure (regression) and a `new` split written before any run,
  outside `knowledge_base/examples.sql`. Scenarios with `"fixture": "joins"` run on `fixture_db.JOIN_ROWS`.
- Several earlier evaluation scenarios now overlap the 250 examples (listed in `docs/examples_integration.md`); they
  are no longer held out.
- Run with the API stopped: `python -m evaluation.run_quality_eval --set evaluation/examples_eval_set.json --label <name>`.

## Intent preservation (intent-v1)

- `intent_eval_set.json`: fresh_round1 (i01-i11; fresh only on its first run, 10/11) and fresh_round2 (j01-j06;
  written after the fixes, first run 6/6). `"fixture": "months"` uses `fixture_db.MONTH_ROWS`, where invoice and
  appointment months differ. Results: `docs/intent_preservation.md`.

## Quality evaluation (quality-v1)

- `quality_set.json`: 33 scenarios (18 dev, 15 held-out): relative and explicit dates, date boundaries,
  3-/4-table joins, zero counts, replace vs. add follow-up grouping, topic changes, unavailable
  information, and capability questions. References were written before any model output was seen.
  Held-out items are only for before/after comparison, never for tuning.
- `run_quality_eval.py`: replays multi-turn scenarios with the same history rules as POST /chat and
  scores routing, resolved question, result agreement, reply topic and latency separately. A fixed
  reference time (2024-01-15) pins both the prompt date and MySQL's session timestamp.
- Run with the API stopped: `python -m evaluation.run_quality_eval --label <name> [--split dev|heldout|all]`.
- Results and the manual SQL review: `docs/answer_quality.md`.

## Retrieval benefit (rq2-v1)

- `run_rq2_eval.py`: RQ2, does retrieved context help the model write correct SQL? Runs the same questions through
  the SQL generation step alone with four inputs: `schema_only`, `examples_only`, `schema_plus_examples` and
  `production`. No intent routing, semantic guards or correction, so only the context changes.
- Questions: single-turn data questions from the sets above with a reference query on the real data, excluding
  fixture scenarios and those that overlap the knowledge-base examples (31 today).
- Run with the API stopped: `python -m evaluation.run_rq2_eval [--configs ...] [--top-k N]`; `--dry-run` lists the
  questions without Ollama or MySQL.

**تحذير:** تطابق النتائج على هذه البيانات الصغيرة لا يثبت أن الاستعلام صحيح.
- في dev-v1، لا تستطيع البيانات التفريق بين الاستعلام الصحيح والخاطئ في 3 من 5 أسئلة:
  - dev-01: لا يوجد طبيب بصفر مواعيد مكتملة، فوضع الفلتر في WHERE يعطي النتيجة نفسها.
  - dev-03: كل المتوسطات أكبر من 2500، فحذف HAVING يعطي النتيجة نفسها.
  - dev-05: لا يوجد فرع فيه طبيبان بالضبط، فاستخدام `>= 2` يعطي النتيجة نفسها.
- لذلك راجع الـ SQL المولّد بنفسك أيضًا.
- واختر أسئلة مستقبلية بحدود تفرّق بين الصحيح والخاطئ، مثل حد يقع بين قيم موجودة فعلًا.
