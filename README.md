# MedLens

MedLens مشروع جامعي يجيب على أسئلة باللغة الطبيعية عن بيانات مستشفى (بيانات اصطناعية من Kaggle).
الفكرة: المستخدم يكتب سؤالًا، والنظام يسترجع معلومات عن الجداول (RAG)، ثم يطلب من نموذج لغوي محلي عبر Ollama أن يكتب استعلام SQL، ثم يتحقق من الاستعلام وينفذه على MySQL بحساب للقراءة فقط.

التقنيات: FastAPI و MySQL 8 و Qdrant و Ollama.

## Team setup (English)

MedLens answers English questions about a synthetic hospital database. A local LLM (Ollama) writes
read-only SQL with retrieval over a local Qdrant index, and a React frontend shows the answers.
Everything runs locally on `127.0.0.1`.

- **Run it on your machine:** follow [`docs/team_setup.md`](docs/team_setup.md). It covers
  prerequisites, `.env`, the MySQL database and read-only account, Ollama models, the index, starting
  both servers, tests and the branch → pull request workflow.
- **Architecture:** [`docs/architecture.md`](docs/architecture.md). **Frontend:**
  [`frontend/README.md`](frontend/README.md). **Evaluation:** [`evaluation/README.md`](evaluation/README.md).
- **Data:** the [Hospital Management Dataset](https://www.kaggle.com/datasets/kanakbaghel/hospital-management-dataset)
  by Kanak Baghel (synthetic, CC BY-SA 4.0), included in `Data/raw/` (see `Data/raw/README.md`).
- **No real authentication:** the login screen is a demo, and the administration pages have no access
  control. Keep both servers bound to `127.0.0.1`.
- **Secrets stay local:** each teammate uses their own `.env` (ignored by Git), MySQL accounts and index.
  `database/readonly_user.sql` only holds the placeholder `CHANGE_ME_READONLY_PASSWORD`.

## الحالة الآن

**يعمل الآن:**
- قاعدة البيانات `medlens` فيها 5 جداول: `patients` (50 صفًا)، `doctors` (10)، `appointments` (200)، `treatments` (200)، `billing` (200).
- الحساب `medlens_readonly` يملك صلاحية القراءة فقط.
- تطبيق FastAPI يشتغل، وفيه `GET /health`. هذا المسار يثبت أن التطبيق شغال فقط، ولا يفحص MySQL أو Ollama أو Qdrant.
- قراءة المعرفة وتقسيمها إلى 215 جزءًا: 5 جداول، و10 أقسام من الكتالوج، و200 مثال (سؤال + SQL). لمراجعتها:
  `python -m backend.scripts.preview_chunks`، ولعرض جزء كامل:
  `python -m backend.scripts.preview_chunks --show schema:billing`.

- إنتاج الـ Embeddings محليًا عبر Ollama بالنموذج `nomic-embed-text:v1.5`، بطول 768 لكل vector. يتطلب تثبيت Ollama وتنزيل النموذج (`ollama pull nomic-embed-text:v1.5`). للتجربة دون حفظ شيء:
  `python -m backend.scripts.preview_embeddings`.

- تخزين الـ vectors محليًا في Qdrant (`storage/qdrant/`)، والبحث عن أقرب الأجزاء لأي سؤال. راجع قسم "فهرس المعرفة والبحث".

- توليد SQL محليًا بالنموذج `qwen3.5:4b` عبر Ollama، للمعاينة فقط: الناتج **غير مُتحقق منه ولا يُنفذ**. للتجربة:
  `python -m backend.scripts.preview_sql "What is the total amount of paid bills?"`.
  - النموذج يتطلب تنزيله مرة واحدة: `ollama pull qwen3.5:4b`.
  - التعليمات في الـ prompt تطلب SELECT فقط، لكنها ليست حماية أمنية. الحماية هي `sql_validator` وحساب القراءة فقط.

- التحقق من SQL في `sql_validator.py` بـ SQLGlot، دون اتصال بقاعدة البيانات. `preview_sql` يعرض الآن إحدى النتيجتين:
  - `VALIDATION PASSED — NOT EXECUTED`.
  - `VALIDATION REJECTED` مع السبب.
  - التفاصيل وحدود التحقق في `docs/architecture.md`.
- اختبارات التحقق (لا تنفذ أي SQL ولا تتصل بـ MySQL):
  `python -m unittest discover -s tests -v`.

- الإجابة الكاملة من سطر الأوامر: سؤال، ثم SQL، ثم تحقق، ثم تنفيذ للقراءة فقط:
  `python -m backend.scripts.preview_answer "How many patients are registered?"`.
  - يتصل بحساب القراءة فقط، مع مهلة 5 ثوانٍ على الخادم، وحد 200 صف.
  - الإعدادات في `.env.example`، والتفاصيل في `docs/architecture.md`.

- `POST /chat` يشغّل نفس المسار عبر HTTP، وهو نفس `ChatService` الذي يستخدمه سطر الأوامر والتقييم. راجع قسم "واجهة POST /chat".

- **محاولة تصحيح واحدة فقط** عند أخطاء محددة قابلة للإصلاح. التفاصيل في قسم "تصحيح SQL" تحت "واجهة POST /chat".

- كتالوج البيانات: `GET /catalog/tables` و `GET /catalog/tables/{table_name}`. راجع قسم "واجهة كتالوج البيانات".

- تصدير CSV لجدول نتيجة موجودة: `POST /export/csv`. راجع قسم "تصدير CSV".

- وصف رسم بياني (`chart`) لبعض الإجابات الناجحة، يُبنى من النتيجة نفسها بدون نموذج، مع قائمة الأشكال المناسبة (`views`): أعمدة عمودية، أعمدة أفقية، خط، ودائرة (donut). راجع "الرسم البياني" تحت "واجهة POST /chat".

- Answer quality (English details: `docs/answer_quality.md`): one application clock for relative dates, replace-vs-add follow-up grouping, clarification instead of silent substitution for data that is not stored (e.g. payment dates), topic-specific capability replies (`backend/app/services/capabilities.py`), 200 checked knowledge examples (`python -m backend.scripts.check_examples`) and a before/after evaluation (`python -m evaluation.run_quality_eval`).

- Semantic guards (English details: `docs/semantic_guards.md`): chart words never override a data request, undefined measures (e.g. "operating ratio") and unstored payment dates get a clarification, doctor/patient names are grounded in the records with bounded read-only lookups (several matches: MedLens asks, and the user's own reply must pick one), join keys are checked against the schema (`invalid_join_key`, `join_without_key`), explicit date ranges and "the month before" are computed in application code, and a request to redraw the previous result reuses it without a new query. A question that asks for all appointments (or patients, doctors, treatments) must keep every one of them, whichever table the SQL starts from; a query that would drop some is corrected once and otherwise not executed. Knowledge-base examples: 250 (`docs/examples_integration.md`). The request (grouping, follow-up changes, date column per period, individual amounts vs. totals, missing vs. zero, filter sources) is described in code and checked before execution (`docs/intent_preservation.md`). Data catalog and system administration pages, chart colors and branch percentages: `docs/frontend_pages.md`.

- مراقبة طلبات `/chat` محليًا: `GET /admin/metrics` و `GET /admin/activity`. راجع قسم "مراقبة الطلبات".

- حالة المكونات والإعدادات للوحة الإدارة: `GET /admin/system` و `GET /admin/config`. راجع قسم "حالة النظام والإعدادات".

- الواجهة الأمامية (المرحلة الأولى): دخول تجريبي، الهيكل العام، والمحادثة الكاملة مع الجدول والرسم وSQL وتصدير CSV. راجع `frontend/README.md`.

**لم يُنفذ بعد:**
- شاشتا كتالوج البيانات والإدارة في الواجهة (تعرضان "لم يُنفذ بعد").
- ذاكرة محادثة دائمة: المحادثات مؤقتة في الذاكرة فقط، وتضيع عند إعادة تشغيل الخادم. راجع "المحادثات وأسئلة المتابعة".
- تسجيل الدخول والصلاحيات لواجهات `/admin`.
- مراقبة الـ GPU.

## أماكن الأشياء

| ماذا | أين |
|---|---|
| ملفات CSV الأصلية | `Data/raw/` |
| تعريف الجداول (المصدر الوحيد للسكيما) | `database/schema.sql` |
| تحميل البيانات إلى MySQL | `database/load_data.py` |
| إنشاء حساب القراءة فقط | `database/readonly_user.sql` |
| شرح الجداول وقواعد الاستعلام | `knowledge_base/data_catalog.md` |
| أمثلة سؤال + SQL | `knowledge_base/examples.sql` |
| كود الخادم | `backend/app/` |
| تعليمات النموذج اللغوي | `backend/app/prompts/` |
| الإعدادات وكلمات المرور (لا يُرفع إلى Git) | `.env` |
| قالب الإعدادات (يُرفع إلى Git) | `.env.example` |
| ملفات Qdrant المحلية (لا تُرفع إلى Git) | `storage/qdrant/` |
| أسئلة التقييم ونتائجه | `evaluation/` |
| شرح البنية | `docs/architecture.md` |

## الإعداد والتشغيل على Windows

نفّذ كل الأوامر في PowerShell من جذر المشروع `MedLens`:

```powershell
# 1. أنشئ بيئة افتراضية وفعّلها (مرة واحدة)
py -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. ثبّت الاعتماديات
python -m pip install -r requirements.txt

# 3. الإعدادات: إذا لم يكن عندك .env بعد، انسخ القالب واكتب كلمات المرور فيه
Copy-Item .env.example .env

# 4. شغّل التطبيق: عامل (worker) واحد، على 127.0.0.1 فقط
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

بعدها افتح http://127.0.0.1:8000/health، ويجب أن ترى `{"status":"ok"}`.
- هذا يعني أن العملية شغالة فقط، ولا يفحص Qdrant أو Ollama أو MySQL.
- في http://127.0.0.1:8000/docs تجد صفحة توثيق تلقائية للـ API.

## واجهة POST /chat

إرسال سؤال من PowerShell (والخادم شغال):

```powershell
$body = @{ question = "What is the total amount of paid bills?" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/chat -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 5
```

ملاحظة: `Invoke-RestMethod` يرمي خطأ عند الردود غير 200، مثل 503 و 504. لرؤية جسم الرد في هذه الحالة، استخدم `try { ... } catch { $_.ErrorDetails.Message }`.

**رسائل بالعربي من PowerShell 5.1:** الأمر السابق لا يرسل النص بترميز UTF-8، فتتشوه الحروف العربية. استخدم هذا الأمر بدلًا منه:

```powershell
$bytes = [System.Text.Encoding]::UTF8.GetBytes((@{ question = "السلام عليكم" } | ConvertTo-Json))
$resp = Invoke-WebRequest -Method Post -Uri http://127.0.0.1:8000/chat -ContentType "application/json; charset=utf-8" -Body $bytes -UseBasicParsing
[System.Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())
```

**توجيه الرسائل (قبل أي بحث أو SQL):**
- **الرسائل الواضحة:** التحية والشكر والوداع وسؤال "وش تقدر تسوي؟" ترد مباشرة بنص ثابت.
  - التطابق يكون للرسالة كاملة فقط، بالعربي أو الإنجليزي، بدون نموذج ولا فهرس ولا قاعدة بيانات.
  - هذه الرسائل تعمل حتى لو كان Ollama أو الفهرس غير متاح، ولا تنتظر دورها خلف سؤال آخر.
- **الكلمة داخل جملة أطول لا تكفي:** مثلًا `Hello, how many patients are registered?` يذهب لمسار البيانات.
- **أي رسالة أخرى:** يصنّفها النموذج المحلي (qwen3.5:4b) بمطالبة منفصلة (`prompts/intent_classification.txt`).
  - يرد بأحد التصنيفات المسموحة فقط، بصيغة JSON مقيدة.
  - لا يستخدم الفهرس ولا قاعدة البيانات.
  - التصنيف وتوليد SQL يعملان داخل نفس دور الانتظار.
  - إذا كان رد المصنِّف غير صالح، تذهب الرسالة لمسار البيانات، وهو آمن بنفسه.
- **نتائج التصنيف:**
  - السؤال الناقص، مثل `How many of them?`، يطلب توضيحًا، لأنه لا توجد ذاكرة محادثة.
  - الطلب خارج النطاق يرد بشرح قصير لنطاق المشروع.
- **أسئلة البيانات بالإنجليزي فقط حاليًا:**
  - السؤال العربي عن البيانات، مثل `كم عدد المرضى؟`، يرد برسالة أن الاستعلام يدعم الإنجليزي.
  - التعرف على التحية العربية لا يعني دعم الاستعلام بالعربي.

**الطلب:** `{"question": "...", "conversation_id": "..."}`، و `conversation_id` اختياري. راجع "المحادثات وأسئلة المتابعة".
- السؤال الفارغ يُرفض.
- السؤال الأطول من `CHAT_MAX_QUESTION_CHARS` (الافتراضي 500 حرف) يُرفض.

**الرد له شكل واحد دائمًا.** اقرأ `status` أولًا:

```json
{
  "status": "answered",
  "message": "Answered with a read-only query (1 row).",
  "question": "What is the total amount of paid bills?",
  "code": null,
  "sql": "SELECT SUM(amount) AS total_paid_amount\nFROM billing\nWHERE payment_status = 'Paid';",
  "sql_executed": true,
  "sql_reached_database": true,
  "attempt_count": 1,
  "correction_attempted": false,
  "sql_attempts": [{"attempt": 1, "purpose": "initial", "outcome": "sql", "sql": "SELECT ...",
                    "validation": "passed", "execution": "succeeded", "error_code": null, "retryable": false,
                    "llm_ms": 1114.2, "validation_ms": 23.1, "database_ms": 175.5}],
  "columns": ["total_paid_amount"],
  "rows": [["173424.90"]],
  "row_count": 1,
  "truncated": false,
  "timings_ms": {"routing_ms": 676.2, "context_ms": 35.8, "llm_ms": 1075.1, "validation_ms": 20.7, "database_ms": 101.1, "correction_ms": null, "total_ms": 1908.9},
  "source_chunk_ids": ["schema:patients", "...", "example:3", "documentation:billing"],
  "chart": null
}
```

- **قيم `rows`:**
  - الأرقام العشرية نصوص دقيقة، مثل `"173424.90"`.
  - التواريخ بصيغة ISO، و TIME بصيغة `"HH:MM:SS"`.
  - NULL تبقى `null`.
- **الحقول الأخرى:**
  - `timings_ms` كل قيمها بالميلي ثانية:
    - `routing_ms` هو وقت التوجيه، أي القواعد وتصنيف النموذج إن احتاجه.
    - `total_ms` يشمل `routing_ms`.
    - الخطوات التي لم تحدث تكون `null`.
  - `sql` هو SQL آخر محاولة، إن أنتجت SQL.
  - `sql_executed` تكون `true` فقط إذا نُفذ SQL الموجود في `sql` بنجاح، والنتيجة في `rows`. الاستعلام الذي رفضه MySQL لا يُعرض أبدًا كنتيجة ناجحة.
  - `sql_reached_database` تكون `true` إذا وصل SQL أي محاولة إلى MySQL، حتى لو رفضه MySQL.
  - `attempt_count` هو عدد مرات توليد SQL:
    - `0`: لم يحدث توليد، مثل رد محادثة أو فشل قبل التوليد.
    - `1`: المحاولة الأولى فقط.
    - `2`: المحاولة الأولى ومحاولة تصحيح واحدة.
  - `sql_attempts` فيه تفاصيل كل محاولة:
    - الـ SQL، ونتيجة التحقق، ونتيجة التنفيذ.
    - رمز الخطأ الآمن (`error_code`)، وهل الخطأ قابل للتصحيح (`retryable`).
  - `timings_ms.correction_ms` هو زمن محاولة التصحيح كاملة. أما `llm_ms` و `validation_ms` و `database_ms` فهي مجموع كل المحاولات.

**الحالات ورموز HTTP:**

- `200`: الطلب عولج، والنتيجة في `status`:
  - `conversation`: رد محادثة ثابت. `code` يكون `greeting` أو `thanks_or_goodbye` أو `capabilities`.
    - في هذه الحالة: `sql=null` و `sql_executed=false`.
    - و `columns` و `rows` و `source_chunk_ids` فارغة، و `row_count=0`.
  - `answered`: نُفذ الاستعلام، والنتيجة في `rows`.
  - `cannot_answer`: لا SQL ولا اتصال بقاعدة البيانات. `code` يوضح السبب:
    - `out_of_scope`: الطلب خارج النطاق.
    - `unsupported_language`: سؤال بيانات بالعربي.
    - بدون `code`: نموذج SQL نفسه قال إن البيانات لا تجيب على السؤال.
  - `classification_failed`: رد مصنّف النية غير صالح. الرسالة تطلب إعادة الصياغة، ولا يحدث بحث ولا SQL ولا اتصال بقاعدة البيانات.
  - `needs_clarification`: لا SQL ولا اتصال بقاعدة البيانات.
    - مع `code=ambiguous`: السؤال غير مفهوم وحده، مثل `How many of them?`.
    - بدون `code`: نموذج SQL طلب التوضيح.
  - `rejected`: الـ SQL المولّد رُفض في التحقق. يُعرض في `sql` مع `code`، ولم يُنفذ.
  - `generation_failed`: رد النموذج غير صالح، مثل أن يُقطع عند حد `num_predict`.
  - `query_failed`: MySQL رفض الاستعلام بعد أن قبله التحقق، مثل أخطاء `ONLY_FULL_GROUP_BY`.
- `404`: `conversation_expired`. معرّف المحادثة غير معروف، أو انتهت صلاحيته، أو حُذف. ابدأ محادثة جديدة.
- `409`: `conversation_busy`. المحادثة نفسها ما زالت تجيب على سؤال سابق. انتظر ثم أعد المحاولة.
- `422`: `invalid_request`. السؤال فارغ أو طويل، أو الجسم غير صالح، أو صيغة `conversation_id` خاطئة.
- `503`:
  - `busy`: هناك سؤال قيد التنفيذ، وأماكن الانتظار ممتلئة. الرد فيه `Retry-After: 5`.
  - `unavailable`: الفهرس أو Ollama أو MySQL أو الإعدادات غير متاحة. `code` يحدد أيها.
- `504`: `timeout`. انتهت مهلة Ollama أو مهلة تنفيذ MySQL.
- `500`: `error`. خطأ غير متوقع، والتفاصيل في سجل الخادم فقط.

رسائل الأخطاء نصوص ثابتة، ولا تحتوي كلمات مرور أو عناوين اتصال أو أخطاء داخلية.

**المحادثات وأسئلة المتابعة:**

- **بدء محادثة:** أرسل السؤال بدون `conversation_id`. الخادم ينشئ محادثة جديدة ويرجع معرّفها في `conversation_id`.
- **المتابعة:** أرسل نفس `conversation_id` مع السؤال التالي. مثال حقيقي:

```json
// الطلب الأول
{"question": "How many appointments does each doctor have, including doctors with no appointments?"}
// الرد (مختصر)
{"status": "answered", "conversation_id": "<ID>",
 "resolved_question": "How many appointments does each doctor have, including doctors with no appointments?", "...": "..."}

// المتابعة
{"question": "Only completed ones.", "conversation_id": "<ID>"}
// الرد (مختصر)
{"status": "answered", "question": "Only completed ones.", "conversation_id": "<ID>",
 "resolved_question": "How many completed appointments does each doctor have, including doctors with no completed appointments?",
 "sql": "SELECT ... LEFT JOIN appointments AS a ON d.doctor_id = a.doctor_id AND a.status = 'Completed' GROUP BY ...", "...": "..."}
```

- **كيف تُفهم المتابعة:**
  - نفس استدعاء تصنيف النية يرجع أيضًا `resolved_question`، وهو سؤال كامل مستقل. لا يوجد استدعاء إضافي للنموذج.
  - البحث وتوليد SQL والتصحيح كلها تستخدم `resolved_question`. أما `question` في الرد فهو رسالة المستخدم الأصلية.
  - السؤال الجديد المستقل لا يرث شروط الأسئلة السابقة.
  - المرجع الغامض، أو المتابعة بدون محادثة سابقة مثل `Only completed ones` في محادثة جديدة، يرجع `needs_clarification` بدون SQL.
  - إذا كان رد النموذج غير صالح، يرجع `classification_failed`، ولا يحدث بحث ولا SQL.
- **ما يُحفظ:** لكل محادثة، أحدث أسئلة البيانات الناجحة فقط، وهي:
  - السؤال الأصلي.
  - السؤال المستقل.
  - الـ SQL الذي تحقق منه النظام ونُفذ بنجاح.
  - التحية والطلبات الفاشلة أو المرفوضة لا تمسح آخر سياق ناجح.
- **ما لا يُحفظ:** صفوف النتائج، والـ prompts، والأجزاء المسترجعة.
- **سياق العميل:** لا يُقبل سياق أو SQL يرسله العميل على أنه رد سابق للمساعد.
- **التاريخ نص فقط:** التاريخ يُعطى للنموذج كبيانات مهرّبة (escaped)، وليس كتعليمات. أي SQL ناتج يمر بنفس التحقق والتنفيذ للقراءة فقط.
- **ميزانية السياق:** إذا لم يتسع التاريخ في نافذة النموذج، تُحذف أقدم الأسئلة كاملة أولًا، ولا يُقص أي نص.
- **الحدود** (قابلة للتغيير في `.env`):

| الإعداد | الافتراضي | المعنى |
|---|---|---|
| `CONVERSATION_TTL_SECONDS` | 1800 | تنتهي المحادثة بعد هذه المدة بدون استخدام |
| `CONVERSATION_MAX_COUNT` | 200 | أكبر عدد محادثات في الذاكرة، ويُحذف الأقدم استخدامًا |
| `CONVERSATION_MAX_TURNS` | 5 | أكبر عدد أسئلة ناجحة محفوظة لكل محادثة |

- **الحذف:** `DELETE /chat/conversations/{conversation_id}` يرجع 204. إذا كانت المحادثة غير موجودة يرجع `404 {"status": "conversation_expired", ...}`.
- **سؤالان معًا:** لا يُسمح بسؤالين في نفس الوقت لنفس المحادثة. الثاني يرجع `409 conversation_busy`.
- **التخزين:** المحادثات في الذاكرة فقط، وتضيع عند إعادة التشغيل. لا يوجد تخزين دائم للمحادثات.
- **المراقبة:** لا تسجّل معرّف المحادثة ولا نصها.
- **تحذير:** معرّف المحادثة ليس تسجيل دخول. أي شخص يملك المعرّف يستطيع متابعة المحادثة أو حذفها.
- **قاعدة للواجهة الأمامية:** زر "محادثة جديدة" يجب أن يتوقف عن إرسال `conversation_id` القديم، ويرسل السؤال بدونه.
  - عند `conversation_expired`، ابدأ محادثة جديدة، وأخبر المستخدم أن السياق السابق انتهى.

**الرسم البياني (`chart`):** حقل اختياري يكون `null` افتراضيًا.
- **متى يُعبّأ:** فقط مع `status=answered`.
- **كيف يُبنى:** بطريقة ثابتة من الأعمدة والصفوف الراجعة ونوع كل عمود كما يحدده MySQL.
  - لا يستدعي النموذج، ولا ينفذ استعلامًا إضافيًا، ولا يغيّر الـ SQL أو `rows`.
- **ما يرجعه:** وصف محايد للرسم، لا HTML ولا صورة. الواجهة سترسمه لاحقًا، مثلًا بـ Plotly.
- **رسم أعمدة `bar`:**
  - مقياس رقمي واحد، مع عمود نصي أو أكثر يسمّي كل صف.
  - الأعمدة النصية تُدمج بـ ` · `، مثل `D001 · David · Taylor`.
  - النقاط بنفس ترتيب النتيجة.
- **رسم خطي `line`:** مقياس رقمي واحد، مع عمود DATE أو DATETIME واحد، أو عمود شهر من `DATE_FORMAT(col, '%Y-%m')`، والنقاط مرتبة زمنيًا.
- **الأشكال المسموحة (`views`) والافتراضي (`default_view`):** يحددها الخادم، والواجهة لا تعرض غيرها، والتبديل لا ينفذ استعلامًا جديدًا.
  - `bar_vertical` و `bar_horizontal` للفئات؛ العمودي افتراضي لـ 8 فئات أو أقل بأسماء قصيرة (16 حرفًا أو أقل).
  - `line` (افتراضي) و `bar_vertical` للتواريخ والأشهر.
  - `pie` فقط لجزء من كل: 2 إلى 5 فئات، قيم غير سالبة ومجموع موجب، والمقياس `COUNT` أو `SUM` بدون `DISTINCT` في استعلام فيه `GROUP BY` وبدون `HAVING` أو `LIMIT`. لا يُعرض للمتوسطات أو النسب أو الفئات المتداخلة.
  - التفاصيل والألوان في `docs/answer_quality.md`.
- **المقياس الرقمي:** عمود رقمي حسب نوع MySQL، ليس اسمه `id` ولا ينتهي بـ `_id`.
  - النصوص التي تشبه الأرقام لا تُعامل كمقياس أبدًا، مثل المعرّفات وأرقام الهواتف وناتج `DATE_FORMAT`.
- **`chart=null` في هذه الحالات:**
  - أقل من صفين، أو نتيجة فارغة، أو قيمة واحدة.
  - نتيجة مقطوعة (`truncated`)، أو أكثر من `CHART_MAX_POINTS` صف (الافتراضي 50).
  - صفر أو أكثر من مقياس رقمي.
  - أعمدة من نوع آخر، مثل TIME و YEAR، أو تاريخ مع نص.
  - أي `NULL` في عمود مرسوم. لا تُستبدل `NULL` بصفر، ولا تُحذف صفوف.
  - فئات أو تواريخ مكررة، لأن رسمها يحتاج تجميعًا.
- **النقاط:** كل نقطة فيها `y` للرسم، و `y_exact` بالقيمة الأصلية الدقيقة من `rows`، مثل `"2663.415600"`، و `row_index`.
- **التسميات:** أسماء الأعمدة فقط، بدون عملات أو وحدات مخترعة.
- **إذا فشل بناء الرسم:** لا يتحول الرد الناجح إلى خطأ. يكون `chart=null` فقط.

مثال حقيقي (مختصر) لسؤال "How many appointments does each doctor have, including doctors with no appointments?":

```json
"chart": {
  "type": "bar",
  "views": ["bar_horizontal", "bar_vertical"],
  "default_view": "bar_horizontal",
  "x": {"label": "doctor_id · first_name · last_name", "fields": ["doctor_id", "first_name", "last_name"], "kind": "category"},
  "y": {"label": "appointment_count", "fields": ["appointment_count"], "kind": "number"},
  "points": [{"x": "D001 · David · Taylor", "y": 25, "y_exact": 25, "row_index": 0},
             {"x": "D002 · Jane · Davis", "y": 21, "y_exact": 21, "row_index": 1}, "... 10 points ..."],
  "point_count": 10
}
```

**تصحيح SQL (محاولة واحدة فقط):**
- إذا فشلت المحاولة الأولى بخطأ من القائمة التالية، يُطلب من نفس النموذج تصحيح واحد.
  - التصحيح يستخدم نفس السياق، بدون بحث جديد، وداخل نفس دور الانتظار.
  - الـ SQL المصحّح يمر بالتحقق الكامل، ثم بالتحقق مرة ثانية عند التنفيذ.
- **أخطاء التحقق القابلة للتصحيح:**
  - `unknown_column`، `unknown_alias`، `ambiguous_column`.
  - `duplicate_table_alias`، `parse_error`، `invalid_limit`.
- **أخطاء MySQL القابلة للتصحيح:**
  - 1054: عمود غير معروف.
  - 1052: عمود غامض.
  - 1066: اسم جدول أو alias مكرر.
  - 1247: مرجع غير صالح.
  - 1064: خطأ صياغة.
  - 1055 و 1056 و 1111 و 1140: أخطاء GROUP BY والتجميع.
  - 3065: عمود في ORDER BY ليس في SELECT مع DISTINCT.
- **لا تصحيح أبدًا في هذه الحالات:**
  - مخالفات السياسة: الكتابة، أو أكثر من أمر، أو الدوال الممنوعة، أو التعليقات، أو قاعدة بيانات أخرى.
  - الأشياء غير المدعومة، مثل الاستعلام الفرعي و CTE، والجداول غير الموجودة.
  - انتهاء المهلة، أو تعطل الاتصال، أو أي خطأ MySQL خارج القائمة.
  - النتيجة الفارغة الناجحة، و `cannot_answer`، و `needs_clarification`.
- **حماية المعنى:** إذا حذف التصحيح قيمة شرط، مثل `'Paid'` أو `'Completed'`، أو دالة تجميع كانت في الاستعلام الأصلي، يُرفض بالرمز `correction_changed_meaning`.
  - أما نقل الشرط من WHERE إلى ON فمسموح.
- **رسالة الخطأ للنموذج:** مختصرة وآمنة، بحد أقصى 300 حرف. فيها رمز الخطأ واسم العمود فقط، بدون بيانات ولا كلمات مرور ولا عناوين اتصال.
- **إذا لم ينجح التصحيح:** يرجع فشل صريح يوضح أن التصحيح جُرّب، ولا تُعرض نتيجة المحاولة الأولى.

**قيود التشغيل (نموذج أولي على GPU واحد):**
- **سؤال واحد في كل مرة:** يُجاب سؤال واحد فقط، وينتظر حتى `CHAT_MAX_WAITING_REQUESTS` (الافتراضي 1). ما بعد ذلك يحصل على `busy`.
- **عامل واحد:** شغّل الخادم بـ `--workers 1` فقط، لأن عدة عمال يعني عدة عملاء Qdrant على نفس المجلد.
- **قفل الفهرس:** الخادم يفتح `storage/qdrant` عند البدء ويبقيه مقفلًا حتى يتوقف. أوقف الخادم (Ctrl+C) قبل تشغيل أي من هذه الأوامر:
  - `build_index` و `preview_retrieval` و `preview_context`.
  - `preview_sql` و `preview_answer` و `run_dev_eval`.
  - إذا بدأ الخادم والفهرس مقفل، يعمل الخادم لكن `/chat` يرجع 503 حتى تعيد تشغيله.
- **لا شيء تلقائي عند البدء:** لا يُبنى الفهرس ولا تُنزّل النماذج.
- **السؤال الأول بطيء:** أول سؤال بعد البدء، أو بعد 5 دقائق بدون استخدام، يكون أبطأ، لأن Ollama يحمّل النماذج من جديد.
- **العنوان المحلي:** الخادم يستمع على `127.0.0.1` فقط.
- **CORS:** معطّل افتراضيًا، والواجهة لا تحتاجه لأن خادم Vite يمرّر `/chat` و `/export` و `/health` إلى الخادم. إذا شغّلت الواجهة من أصل آخر، ضعه في `CORS_ALLOWED_ORIGINS`، ولا يُقبل `*`.

إذا رفض PowerShell تفعيل البيئة بسبب سياسة السكربتات، نفّذ هذا الأمر مرة واحدة:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## فهرس المعرفة والبحث

**عنوان Ollama:** استخدم `OLLAMA_BASE_URL=http://127.0.0.1:11434` وليس `localhost`، وهذا هو الافتراضي.
- السبب: على Windows، `localhost` يجرّب IPv6 أولًا، ويرفضه Ollama بعد نحو ثانيتين، فتتأخر كل الطلبات.
- قيس هذا في أول تقييم: 2.07 ثانية لكل طلب embedding عبر `localhost`، مقابل 0.04 ثانية عبر `127.0.0.1`.

يحتاج Ollama شغالًا، وتنزيل النموذج مرة واحدة:

```powershell
ollama pull nomic-embed-text:v1.5
```

نفّذ الأوامر من جذر `MedLens` بعد تفعيل البيئة (`.\.venv\Scripts\Activate.ps1`):

```powershell
# بناء الفهرس أو تحديثه: شغّله عند الإعداد، وكلما تغيّر database/schema.sql أو knowledge_base/
python -m backend.scripts.build_index

# البحث الدلالي الخام عن أقرب الأجزاء لسؤال (للمقارنة)
python -m backend.scripts.preview_retrieval "What is the total amount of paid bills?" --top-k 5

# السياق الكامل بالضبط كما سيُرسل إلى النموذج اللغوي
python -m backend.scripts.preview_context "How many visits are booked with each physician?"
python -m backend.scripts.preview_context "How many visits are booked with each physician?" --top-k 3
```

- **الاستراتيجية:** السياق يُبنى بالاستراتيجية **"full schema + retrieved documentation/examples"**. الجداول الخمسة تدخل دائمًا، ومعها أقرب أجزاء التوثيق والأمثلة للسؤال.
- **عدد الأجزاء المسترجعة:** يتحدد بالمتغير `RAG_CONTEXT_TOP_K`، والافتراضي 5. الخيار `--top-k` يغيّره لأمر واحد. التفاصيل في `docs/architecture.md`.

- **بناء الفهرس:** لا يحدث تلقائيًا، لا عند تشغيل التطبيق ولا مع كل سؤال.
- **إعادة البناء:** تحدّث الأجزاء الموجودة دون تكرار، ثم تحذف الأجزاء التي لم تعد موجودة في الملفات.
- **الرقم بجانب كل نتيجة:** هو تشابه cosine بين السؤال والجزء، والأعلى أقرب. ليس نسبة ثقة.
- **عملية واحدة فقط:** التخزين المحلي في `storage/qdrant/` يقبل عملية واحدة فقط في نفس الوقت، مثل `build_index` أو `preview_retrieval` أو التطبيق لاحقًا. إذا ظهرت رسالة `is in use by another process`، أغلق العملية الأخرى ثم أعد المحاولة. لا تحذف ملف `.lock`.
- **إذا تغيّر نموذج الـ Embeddings أو طول الـ vector:** يرفض النظام استخدام الفهرس القديم ولا يحذفه تلقائيًا. أوقف كل العمليات، ثم احذف المجلد `storage/qdrant/collection/medlens_knowledge`، أو غيّر `QDRANT_COLLECTION`، ثم شغّل `build_index` من جديد.

## واجهة كتالوج البيانات

تصف الجداول الخمسة وأعمدتها وعلاقاتها، لتستخدمها الواجهة الأمامية.
- لا تحتاج Ollama ولا Qdrant ولا MySQL، ولا تنفذ أي SQL.
- **مصادرها:**
  - `database/schema.sql` هو المرجع للبنية: الجداول، والأعمدة، والأنواع، و NULL، والمفاتيح، والفهارس، والعلاقات، وقيم ENUM.
  - `knowledge_base/data_catalog.md` يوفر الأوصاف فقط.
- **الحقول غير الموثقة:** إذا لم يكن للحقل وصف في `data_catalog.md`، تكون قيمته `null`. لا تُخترع أوصاف.
- **ما لا تعرضه:** لا عدد صفوف، ولا بيانات مرضى، ولا مسارات ملفات، ولا أوامر SQL.
- **القراءة والتحديث:** تُقرأ الملفات أول طلب، وتبقى في الذاكرة حتى يُعاد تشغيل الخادم. أي تعديل على المصادر يحتاج إعادة تشغيل. إذا فشلت القراءة، يُعاد المحاولة في الطلب التالي.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/catalog/tables | ConvertTo-Json -Depth 5
Invoke-RestMethod http://127.0.0.1:8000/catalog/tables/billing | ConvertTo-Json -Depth 6
```

**`GET /catalog/tables`** يرجع 200 (الرد الفعلي مختصر):

```json
{"tables": [
  {"name": "patients", "display_name": "Patients",
   "description": "Stores patient registration and contact information.", "grain": "One row per patient.", "column_count": 11},
  "... doctors, appointments, treatments ...",
  {"name": "billing", "display_name": "Billing",
   "description": "Stores billing records associated with patients and treatments.", "grain": "One row per bill.", "column_count": 7}
]}
```

**`GET /catalog/tables/billing`** يرجع 200 (الرد الفعلي مختصر):

```json
{
  "name": "billing", "display_name": "Billing",
  "description": "Stores billing records associated with patients and treatments.",
  "grain": "One row per bill.", "column_count": 7, "primary_key": ["bill_id"],
  "columns": [
    {"name": "bill_id", "description": "Unique bill identifier.", "data_type": "VARCHAR(10)",
     "allowed_values": null, "nullable": false, "key_roles": ["primary_key"], "references": null},
    {"name": "patient_id", "description": "References patients.patient_id.", "data_type": "VARCHAR(10)",
     "allowed_values": null, "nullable": false, "key_roles": ["foreign_key"],
     "references": {"table": "patients", "column": "patient_id"}},
    {"name": "payment_status", "description": "Paid, Pending, or Failed.", "data_type": "ENUM",
     "allowed_values": ["Paid", "Pending", "Failed"], "nullable": false, "key_roles": [], "references": null}
  ],
  "foreign_keys": [
    {"name": "fk_billing_patient", "columns": ["patient_id"], "references_table": "patients", "references_columns": ["patient_id"]},
    {"name": "fk_billing_treatment", "columns": ["treatment_id"], "references_table": "treatments", "references_columns": ["treatment_id"]}
  ],
  "referenced_by": [],
  "unique_constraints": [],
  "indexes": [{"name": "idx_billing_date", "columns": ["bill_date"]}]
}
```

- **قيم `key_roles`:**
  - `primary_key`.
  - `unique`: قيد فريد على هذا العمود وحده.
  - `unique_part`: العمود جزء من قيد فريد مركّب.
  - `foreign_key`.
  - `indexed`: العمود في فهرس عادي غير فريد.
- **القيود والعلاقات:**
  - `unique_constraints` منفصلة عن `indexes`. الفهارس هنا غير فريدة فقط.
  - القيود المركّبة تحتفظ بكل أعمدتها بالترتيب.
  - `references` تُعبّأ فقط للمفتاح الأجنبي ذي العمود الواحد.
  - `referenced_by` هي المفاتيح الأجنبية في جداول أخرى التي تشير إلى هذا الجدول.
- **الأخطاء:**
  - اسم جدول غير موجود يرجع `404 {"status": "not_found", "message": "..."}`. التطابق دقيق وحساس لحالة الأحرف.
  - مصدر ناقص أو غير صالح يرجع `503 {"status": "unavailable", "message": "The data catalog is not available right now."}`. التفاصيل في سجل الخادم فقط.

## تصدير CSV

`POST /export/csv` يحوّل جدول نتيجة، أي `columns` و `rows` و `truncated` من رد `/chat`، إلى ملف CSV للتنزيل.
- **مصدر البيانات:** الجدول يأتي من العميل، ولا يتحقق الخادم من مصدره. ما يُرسل هو ما يُصدَّر.
- **ما لا يحدث:** لا SQL، ولا نموذج، ولا بحث، ولا MySQL، ولا Qdrant، ولا يُعاد تشغيل السؤال.
- **التخزين:** لا يُحفظ أي ملف على القرص، ولا يُقبل اسم ملف أو مسار من العميل.

```powershell
$body = '{"columns": ["payment_method", "total"], "rows": [["Cash", "49366.09"], ["Insurance", "59206.47"]], "truncated": false}'
Invoke-WebRequest -Method Post -Uri http://127.0.0.1:8000/export/csv -ContentType "application/json" -Body $body -OutFile medlens-export.csv
```

**الطلب:** `{"columns": [...], "rows": [[...], ...], "truncated": true|false}`، ولا يُقبل أي مفتاح آخر.
- `columns` قائمة نصوص غير فارغة، والأسماء المكررة مسموحة.
- كل صف فيه نفس عدد الأعمدة بالضبط.
- القيم المقبولة: نص، أو رقم محدود، أو `true`/`false`، أو `null`. لا كائنات ولا قوائم متداخلة، ولا `NaN` و `Infinity`.

**الحدود** (قابلة للتغيير في `.env`):

| الإعداد | الافتراضي | المعنى |
|---|---|---|
| `EXPORT_MAX_BYTES` | 2000000 | أكبر حجم للطلب. يُرفض أثناء القراءة، قبل تحميله كاملًا في الذاكرة |
| `EXPORT_MAX_COLUMNS` | 100 | أكبر عدد أعمدة |
| `EXPORT_MAX_ROWS` | 10000 | أكبر عدد صفوف |
| `EXPORT_MAX_CELL_CHARS` | 10000 | أطول نص في خلية أو اسم عمود |

**الرد 200:** ملف `text/csv; charset=utf-8` بترميز UTF-8 مع BOM ليعمل مع Excel، ونهاية السطر CRLF.
- `Content-Disposition: attachment; filename="medlens-export-YYYYMMDD-HHMMSS.csv"`.
  - يضاف `-partial` قبل `.csv` إذا كان `truncated=true`.
- `X-MedLens-Partial: true|false`. النتيجة الجزئية تُصدَّر بصفوفها فقط، بدون إضافة صفوف تحذير.
- `X-MedLens-Row-Count`.
- `X-MedLens-Neutralized-Cells`: عدد الخلايا والعناوين التي أضيف لها `'`.
- عند تفعيل CORS، هذه الترويسات مكشوفة للمتصفح.

**التحويلات في الملف** (الرد الأصلي من `/chat` لا يتغير):
- **النصوص:** كل النصوص وأسماء الأعمدة بين علامتي تنصيص، فتبقى الفواصل وعلامات التنصيص والأسطر الجديدة والعربي سليمة.
- **الأرقام:** أرقام JSON تُكتب بدون تنصيص، كما أُرسلت بالضبط. مثلًا `1.10` يبقى `1.10`، والسالب يبقى سالبًا.
- **القيم الدقيقة:** النصوص العشرية مثل `"173424.90"` والتواريخ والأوقات تُكتب كما هي.
- **`true` و `false`:** تصبح `"true"` و `"false"`.
- **`NULL` والنص الفارغ:**
  - `NULL` تصبح حقلًا فارغًا بدون تنصيص (`,,`).
  - النص الفارغ يصبح `""`.
  - في جدول بعمود واحد فقط، تُكتب `NULL` أيضًا `""`، لأن السطر الفارغ تماما تتجاهله برامج CSV.
- **حماية من حقن الصيغ** (formula injection):
  - إذا كان أول حرف ظاهر في نص أو اسم عمود هو `=` أو `+` أو `-` أو `@`، أو نسخها العريضة `＝ ＋ － ＠`، يضاف `'` في بدايته.
  - كذلك إذا بدأ النص بـ Tab أو CR.
  - الحرف الظاهر يُحسب بعد تجاوز المسافات والحروف الخفية.
  - لا يتغير النص الذي هو رقم فقط، مثل `"-12.50"`، ولا قيمة TIME مثل `"-01:30:00"`.

**الأخطاء:**
- `422 {"status": "invalid_request", "message": ...}`: ليس JSON، أو الشكل خاطئ، أو عدد الخلايا خاطئ، أو قيمة غير مدعومة، أو `columns` فارغة.
- `413 {"status": "too_large", "message": ...}`: الطلب أو الأعمدة أو الصفوف أو خلية تجاوزت الحد.
- الرسائل تذكر رقم الصف والعمود فقط، ولا تعرض محتوى الخلايا.

## مراقبة الطلبات (لوحة الإدارة)

**ماذا يُسجَّل:**
- كل طلب `POST /chat` يُسجَّل حدثًا واحدًا فقط، أيًّا كانت نتيجته:
  - تحية، وإجابة، وطلب توضيح، وخارج النطاق.
  - SQL مرفوض، وانتهاء مهلة، وانشغال (`busy`)، وطلب غير صالح، وخطأ داخلي.
- **حقول الحدث:**
  - رقم طلب ووقت UTC.
  - المسار (`route`) إن عُرف، وهل دخل مسار البيانات.
  - رمز HTTP وحالة التطبيق ورمزها.
  - الزمن الكلي وأزمنة المراحل.
  - عدد المحاولات، والتصحيح، وهل وصل SQL إلى MySQL أو نُفذ.
  - عدد الصفوف، و `truncated`.
- رقم الطلب يرجع في ترويسة `X-Request-ID` لرد `/chat`.

**ماذا لا يُسجَّل أبدًا:** نص السؤال، ولا SQL، ولا الصفوف، ولا الـ prompts، ولا كلمات المرور، ولا رسائل الأخطاء الداخلية.

**التخزين:**
- ملف SQLite محلي منفصل: `storage/monitoring/medlens_monitoring.sqlite3`، ومستثنى من Git. ليس MySQL ولا Qdrant.
- يُكتب الحدث بعد إرسال الرد، فلا يبطئ `/chat`.
- إذا فشلت المراقبة، لا يتأثر رد `/chat`.
- **التسجيل بأفضل جهد (best-effort)، وليس مضمونًا مرة واحدة بالضبط:**
  - إذا توقف الخادم بين إرسال الرد وكتابة الحدث، أو فشلت الكتابة، يضيع الحدث. يُسجَّل هذا في سجل الخادم فقط.
  - لذلك قد تكون الأرقام أقل قليلًا من العدد الحقيقي للطلبات.

**الاحتفاظ:**
- `MONITORING_RETENTION_DAYS` (الافتراضي 30 يومًا): الأحداث الأقدم تُحذف.
- `MONITORING_MAX_EVENTS` (الافتراضي 50000): يُحتفظ بأحدث الأحداث فقط.
- التنظيف يحدث عند بدء الخادم، وبعد كل 100 حدث.

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/admin/metrics?window=24h" | ConvertTo-Json -Depth 4
Invoke-RestMethod "http://127.0.0.1:8000/admin/activity?window=24h&limit=50&offset=0" | ConvertTo-Json -Depth 5
```

**قيم المعاملات:**
- `window`: `1h` أو `24h` (الافتراضي) أو `7d` فقط.
- `limit`: من 1 إلى 100، والافتراضي 50.
- `offset`: من 0 إلى 10000.
- **الترتيب:** `activity` يرجع الأحدث أولًا. عند تساوي الوقت، يُرتب بترتيب الإدخال.
- قيمة خارج الحدود ترجع 422.
- تعذّر قراءة ملف المراقبة يرجع `503 {"status": "unavailable", ...}`.

**تعريف المقاييس:**
- **طلب مسار البيانات** (`data_pipeline_requests`): طلب دخل مسار البحث ثم SQL، أيًّا كانت نتيجته.
  - لا يشمل: التحية، والردود الموجهة مثل التوضيح وخارج النطاق والسؤال العربي، و `busy`، والطلبات غير الصالحة، والأخطاء قبل اكتمال التوجيه.
- **`operational_answer_success`** = طلبات مسار البيانات المنتهية بـ `answered` ÷ طلبات مسار البيانات.
  - البسط والمقام يُعرضان أيضًا.
  - هذا **نجاح تشغيلي، وليس دقة النموذج**: استعلام صالح لكن خاطئ يُحسب "answered".
- **`data_pipeline_latency`:** وسيط و p95 لـ `total_ms` لكل طلبات مسار البيانات، أيًّا كانت نتيجتها، مع عدد العينات.
  - الحساب بطريقة nearest-rank: القيمة رقم ⌈q × n⌉ بعد الترتيب، بدون استيفاء، فكل قيمة زمن حقيقي مُقاس.
- **`total_ms`:** زمن الطلب كاملًا على الخادم، بساعة monotonic، من وصول الطلب حتى جهوزية الرد. لا يُجمع من المراحل.
  - `correction_ms` يتداخل مع `llm_ms` و `validation_ms` و `database_ms`، فلا يُجمع معها.
- **باقي المقاييس:**
  - `validation_rejections`: الحالة النهائية `rejected`.
  - `correction_attempts`: عدد الطلبات التي جُرّب فيها التصحيح، و `answered_after_correction` منها ما انتهى بـ `answered`.
  - `busy` و `timeouts`: عدد كل حالة.
- النسب والقيم المئوية تكون `null` إذا لم توجد عينات.

**تحذير:** لا يوجد تسجيل دخول ولا صلاحيات. أي شخص يصل إلى الخادم على `127.0.0.1` يستطيع قراءة هذه الواجهات. هي للعرض المحلي فقط.

## حالة النظام والإعدادات (لوحة الإدارة)

**`GET /admin/system`** يفحص المكونات التالية، كل مكون على حدة، فلا يخفي فشل مكون حالة الآخرين:

| المكون | كيف يُفحص |
|---|---|
| `api` | العملية التي ترد على الطلب، مع وقت البدء ومدة التشغيل |
| `ollama` | `GET /api/version` |
| `models` | النموذجان المضبوطان: هل هما مثبتان (`/api/tags`)، وهل هما محمّلان الآن في الذاكرة (`/api/ps`) |
| `mysql` | اتصال بحساب القراءة فقط، وتنفيذ الأمر الثابت `SELECT 1` |
| `index` | عبر عميل Qdrant الذي يملكه التطبيق نفسه، بدون عميل ثانٍ (التفاصيل تحت الجدول) |

**تفاصيل فحص `index`:**
- **ما يُقرأ:** وجود المجموعة، وعدد النقاط، والعدد حسب النوع (`schema` و `documentation` و `example`)، والنموذج المخزن، وطول الـ vector.
- **المقارنة:**
  - النموذج المخزن يُقارن بالنموذج المضبوط.
  - طول الـ vector يُقارن بالطول الذي يذكره Ollama لنموذج الـ Embeddings، عبر `/api/show`، وهي بيانات وصفية فقط.
- **حداثة الفهرس:** يُفحص أن أجزاء السكيما المخزنة تطابق `database/schema.sql` الحالي. حداثة التوثيق والأمثلة **لا تُفحص**.

**قيم الحالة:**
- `ok`: يعمل.
- `degraded`: متاح لكن فيه اختلاف، مثل نموذج مختلف أو سكيما تغيرت.
- `unavailable`: فشل الفحص.
- `unknown`: لم يمكن معرفة الحالة، مثل انتهاء مهلة الفحص أو تعذّر الوصول إلى Ollama.
- `not_configured`: الإعداد غير موجود.
- مع كل حالة `reason` من قائمة ثابتة، مثل `installed_not_loaded` و `collection_missing` و `probe_timeout` و `readonly_credentials_missing`.

**ما يعنيه كل فحص، وما لا يعنيه:**
- **الاتصال (MySQL):** يعني أن `SELECT 1` نجح، وليس أن كل استعلامات التطبيق ستنجح.
- **التثبيت (`installed`):** يعني أن النموذج موجود في Ollama.
- **التحميل (`loaded`):** يعني أن النموذج في الذاكرة الآن. النموذج غير المحمّل يُحمّل عند أول استخدام، وليس خطأ.
- **قدرة الاستدلال:** `inference_verified` دائمًا `false`. الفحص لا يولّد نصًا ولا ينتج embeddings، فلا يثبت أن النموذج يجيب فعلًا.

**ما لا يفعله الفحص أبدًا:** لا تحميل ولا تنزيل نماذج، ولا تغيير إعداداتها، ولا قراءة بيانات المستشفى، ولا تعديل الفهرس.

**المهلات والتخزين المؤقت:**
- لكل استدعاء مهلة `SYSTEM_PROBE_TIMEOUT_SECONDS`، والافتراضي 3 ثوانٍ، مع حد كلي لكل مكون.
- النتيجة تُحفظ مؤقتًا `SYSTEM_STATUS_CACHE_SECONDS`، والافتراضي 15 ثانية. الطلبات المتزامنة تنتظر فحصًا واحدًا مشتركًا.
- `checked_at` يوضح وقت الفحص الفعلي.
- لا يوجد فحص دوري في الخلفية، ولا فحص عند بدء الخادم.
- `GET /health` كما هو: فحص خفيف لحياة العملية فقط.

**`GET /admin/config`** يرجع قائمة محددة من الإعدادات غير السرية:
- أسماء النماذج.
- استراتيجية السياق، و `RAG_CONTEXT_TOP_K`.
- حدود توليد SQL، وعدد محاولات التصحيح.
- مهلة التنفيذ، وحد الصفوف.
- حد نقاط الرسم.
- إعدادات الاحتفاظ بالمراقبة.

لا يرجع كلمات مرور، ولا أسماء حسابات، ولا عناوين خوادم أو روابط، ولا مسارات ملفات.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/admin/system | ConvertTo-Json -Depth 5
Invoke-RestMethod http://127.0.0.1:8000/admin/config | ConvertTo-Json -Depth 3
```

**تحذير:** لا يوجد تسجيل دخول ولا صلاحيات لهذه الواجهات. هي للعرض المحلي على `127.0.0.1` فقط.

## إعادة بناء قاعدة البيانات (للمسؤول فقط)

هذه الأوامر تحذف الجداول وتعيد إنشاءها. لا تحتاجها إذا كانت القاعدة جاهزة.

```powershell
$mysql = "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"
& $mysql -u root -p -e "source database/schema.sql"
python database\load_data.py
```

## الحسابات

- `MYSQL_USER` و `MYSQL_PASSWORD`: حساب الإدارة. يستخدمه `load_data.py` فقط.
- `MYSQL_READONLY_USER` و `MYSQL_READONLY_PASSWORD`: الحساب الوحيد الذي يستخدمه التطبيق. إذا كان ناقصًا يتوقف التطبيق، ولا يرجع إلى حساب root.
