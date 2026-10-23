# بنية MedLens

يفصل المشروع بين المسؤوليات بأسلوب **مستوحى من MVC**، وليس MVC حرفيًا:

- **View**: الواجهة الأمامية في `frontend/`. تعرض الأسئلة والنتائج فقط.
- **Controller**: الملفات في `backend/app/controllers/`. تستقبل طلب HTTP وترجع الرد فقط.
- **Model**: الملفات في `backend/app/models/`. نماذج Pydantic تحدد شكل الطلب والرد. ليست أوزان نموذج ذكاء اصطناعي.
- **منطق التطبيق**: الملفات في `backend/app/services/`. خدمات RAG والـ LLM جزء من منطق التطبيق، وليست طبقة مستقلة.
- **الوصول للبيانات**: الملفات في `backend/app/repositories/`. هي المكان الوحيد الذي يتصل بـ MySQL أو Qdrant مباشرة.

القاعدة: لا يوجد SQL أو اتصال بقاعدة بيانات داخل controllers أو frontend.

## مسار السؤال (المخطط له)

```text
frontend
  │  POST /chat {"question": "..."}
  ▼
chat_controller.py          يستقبل الطلب
  ├─ intent_router (قواعد)  تحية، شكر، سؤال قدرات: رد ثابت فورًا، بدون نموذج ولا بوابة
  ▼  (غير ذلك: بوابة الاستدلال، ثم ChatService في worker thread)
ChatService                  تصنيف النموذج: data_question / needs_clarification / out_of_scope / ...
  │                          (رد ثابت، أو سؤال عربي عن البيانات: لا يكمل)
  ▼  (سؤال بيانات بالإنجليزي فقط)
  ▼
chat_service.py             ينسّق الخطوات:
  ├─ 1. context_builder.py      "full schema + retrieved documentation/examples"
  │       ├─ vector_repository.py   كل أجزاء السكيما (حسب النوع، بدون ترتيب بالتشابه)
  │       └─ retriever.py           أقرب أجزاء التوثيق والأمثلة
  │             ├─ embedding_service.py   يحوّل السؤال إلى vector عبر Ollama
  │             └─ vector_repository.py   بحث في Qdrant مع فلتر النوع
  ├─ 2. llm_service.py          يطلب SQL من Ollama باستخدام prompts/sql_generation.txt
  ├─ 3. sql_validator.py        يرفض أي استعلام غير آمن
  ├─ 4. mysql_repository.py     ينفذ الاستعلام بحساب medlens_readonly
  └─ 5. إذا كان الخطأ من قائمة الأخطاء القابلة للتصحيح (sql_correction.py)، تحدث محاولة تصحيح واحدة فقط:
         - llm_service.fix_sql (prompts/sql_retry.txt)، بنفس السياق، ثم الخطوتان 3 و 4 مرة أخرى.
         - يُرفض التصحيح إذا حذف شرطًا أو دالة تجميع.
         - لا توجد محاولة ثالثة.
  ▼
ChatResponse {sql, columns, rows, attempts}  →  frontend يعرض النتيجة
```

بناء الفهرس منفصل عن هذا المسار. يُشغّل `backend/scripts/build_index.py` يدويًا عند الإعداد أو عند تغيّر ملفات المعرفة، ولا يُشغّل مع كل سؤال:

```text
database/schema.sql + knowledge_base/*
  → document_loader (قراءة) → chunker (CREATE TABLE فقط من السكيما) → embedding_service → vector_repository (Qdrant)
```

## استراتيجية السياق: "full schema + retrieved documentation/examples"

هذا ما يصل إلى النموذج اللغوي في `{context}`، ويبنيه `services/rag/context_builder.py`:

1. **السكيما كاملة دائمًا:** الجداول الخمسة كلها تدخل في السياق.
   - تُقرأ من الفهرس المحفوظ حسب النوع `schema`، وليس بالبحث بالتشابه، ولا تحمل أي درجة تشابه.
   - قبل الاستخدام تُقارن بملف `database/schema.sql`. إذا كان جدول ناقصًا أو زائدًا أو قديمًا، يظهر خطأ يطلب تشغيل `build_index`.
2. **التوثيق والأمثلة بالبحث الدلالي:** أقرب `RAG_CONTEXT_TOP_K` أجزاء (الافتراضي 5)، من نوعي `documentation` و `example` فقط.
   - الفلتر يُطبّق داخل بحث Qdrant نفسه، لا على النتائج بعد البحث.
3. **بدون تكرار:** لا يظهر أي جزء مرتين.
   - النص مقسّم إلى ثلاثة أقسام واضحة: SCHEMA ثم DOCUMENTATION ثم EXAMPLES.
   - كل جزء يبدأ بمعرّفه ومصدره، مثل `[schema:doctors] (source: database/schema.sql)`.

**السبب:**
- القاعدة صغيرة، خمسة جداول بنحو 3,400 حرف، فإرسال السكيما كاملة رخيص.
- ويضمن وجود كل جدول قد يحتاجه السؤال. مثال: سؤال "physician" لم يسترجع جدول `doctors` بالبحث الدلالي وحده.

البحث الدلالي الخام (`Retriever.retrieve`) ما زال متاحًا للمقارنة عبر `preview_retrieval`.

## مسؤولية كل ملف

| الملف | المسؤولية |
|---|---|
| `backend/app/main.py` | ينشئ تطبيق FastAPI، ويفتح عميل Qdrant واحدًا يملكه التطبيق عند البدء، ويغلقه عند التوقف. لا يتصل بـ Ollama أو MySQL عند البدء، ولا يبني الفهرس. |
| `controllers/chat_controller.py` | `POST /chat`: يشغّل `ChatService` في worker thread عبر البوابة، ويحوّل المرحلة إلى رمز HTTP ورسالة ثابتة. |
| `backend/app/config.py` | يقرأ `.env` من جذر المشروع ويعرّف المسارات. يعرض حساب القراءة فقط. |
| `controllers/catalog_controller.py` | `GET /catalog/tables` و `GET /catalog/tables/{table_name}`: طبقة HTTP رقيقة فوق `CatalogService`، ترجع 404 أو 503 برسائل ثابتة. |
| `controllers/admin_controller.py` | `GET /admin/metrics` و `GET /admin/activity` و `GET /admin/system` و `GET /admin/config`: قراءة فقط. بدون تسجيل دخول أو صلاحيات. |
| `services/system_status.py` | يفحص كل مكون على حدة (API و Ollama والنماذج و MySQL والفهرس)، في worker threads، مع مهلات. يحفظ النتيجة مؤقتًا 15 ثانية، والطلبات المتزامنة تشترك في فحص واحد. يستخدم عميل Qdrant الخاص بالتطبيق، ونقاط Ollama الوصفية فقط. يحتوي أيضًا قائمة إعدادات `/admin/config` المسموح بعرضها. |
| `services/monitoring_service.py` | يبني حدثًا من بيانات الرد الوصفية فقط، ويحسب المقاييس: نجاح الإجابة التشغيلي، ووسيط و p95 بطريقة nearest-rank، والعدادات. |
| `repositories/monitoring_repository.py` | تخزين الأحداث في SQLite محلي منفصل، بوضع WAL، مع اتصال لكل عملية وقفل للكتابة، والاحتفاظ بالأحداث حسب العمر والعدد. |
| `main.py` (middleware) | يسجّل حدثًا واحدًا لكل `POST /chat`، ويقيس الزمن بساعة monotonic. يُكتب الحدث بعد إرسال الرد، وفشله لا يؤثر على الرد. |
| `controllers/export_controller.py` | `POST /export/csv`: يقرأ الطلب على أجزاء ويرفضه بـ 413 عند تجاوز `EXPORT_MAX_BYTES`، ثم يرجع ملف CSV مع اسم يولّده التطبيق وترويسة `X-MedLens-Partial`. |
| `services/csv_export.py` | يتحقق من الجدول القادم من العميل (بيانات غير موثوقة) ويكتبه CSV بـ `csv` القياسية: UTF-8 مع BOM، والأرقام كما أُرسلت، والحماية من حقن الصيغ. لا SQL ولا نموذج ولا قاعدة بيانات ولا تخزين. |
| `services/catalog_service.py` | يبني كتالوج البيانات من `schema.sql` (البنية) و `data_catalog.md` (الأوصاف). يقرأ الملفات كنص فقط، مستقل عن الفهرس و Ollama و MySQL، ويحتفظ بالنتيجة حتى إعادة التشغيل. |
| `models/chat_models.py` | `ChatRequest` و `ChatResponse` |
| `models/catalog_models.py` | `CatalogResponse` و `TableSummary` و `TableDetail` و `ColumnInfo` و `CatalogError` |
| `services/chat_service.py` | ينسّق المسار كاملًا مرة واحدة، مع محاولة تصحيح واحدة على الأكثر. يأخذ تاريخ المحادثة، ويستخدم السؤال المستقل في البحث و SQL. يرجع `AnswerTrace` فيه المرحلة التي توقف عندها. يستخدمه `POST /chat` وسطر الأوامر والتقييم. |
| `services/intent_router.py` | يوجّه الرسالة قبل أي بحث. يرد بقواعد ثابتة على التحية والشكر وسؤال القدرات (تطابق الرسالة كاملة فقط). وإلا يحوّل نتيجة النموذج (`LLMService.resolve_message`: النية والسؤال المستقل) إلى رد أو إلى مسار البيانات. أسئلة البيانات بالإنجليزي فقط. |
| `services/conversation_store.py` | ذاكرة محادثات مؤقتة ومحدودة: TTL، وحد لعدد المحادثات (يُحذف الأقدم استخدامًا)، وحد للأسئلة الناجحة لكل محادثة (السؤال الأصلي، والسؤال المستقل، والـ SQL المنفذ فقط). ترفض سؤالين متزامنين لنفس المحادثة. تضيع عند إعادة التشغيل. |
| `services/clock.py` | The application's current date and calendar periods for the resolver and SQL prompts (see `docs/answer_quality.md`). |
| `services/capabilities.py` | The single description of implemented features; fixed replies per capability topic. |
| `services/chart_builder.py` | يبني وصف الرسم البياني (`bar` أو `line`) مع الأشكال المسموحة (`views`) لنتائج `answered` فقط، بطريقة ثابتة من الأعمدة والصفوف ونوع كل عمود في MySQL (`QueryResult.column_types`). لا يستدعي النموذج ولا ينفذ SQL. إذا لم يكن الشكل واضحًا يرجع `None`، وإذا فشل لا يؤثر على الإجابة. |
| `services/sql_correction.py` | سياسة محاولة التصحيح الواحدة: قوائم الأخطاء القابلة للتصحيح، ورسائل الخطأ الآمنة المختصرة، وحماية المعنى. الافتراضي هو عدم التصحيح. |
| `services/inference_gate.py` | يسمح بسؤال واحد في كل مرة مع عدد انتظار محدود، وما زاد يحصل على `busy`. |
| `services/llm_service.py` | يطلب SQL من Ollama عبر `/api/chat` (`temperature=0`، `think=false`). يرفض الـ prompt الذي لا يتسع في `num_ctx` بدل قصّه، ويعتبر الوصول إلى حد `num_predict` توليدًا ناقصًا. يوفر `fix_sql` محاولة التصحيح الواحدة بنفس الإعدادات والفحوصات، و `resolve_message` تصنيف النية وتحويل سؤال المتابعة إلى سؤال مستقل، في استدعاء واحد مع ميزانية لتاريخ المحادثة. |
| `services/ollama_client.py` | طلبات HTTP المشتركة إلى Ollama، مع رسائل خطأ واضحة. |
| `services/sql_validator.py` | يتحقق من الاستعلام بـ SQLGlot دون اتصال بقاعدة البيانات. يرجع `ValidationResult` فيه سبب الرفض، و `ValidatedSQL` فقط عند النجاح. |
| `services/rag/document_loader.py` | يقرأ ملفات المعرفة الثلاثة كاملة بترميز UTF-8. لا ينفذ شيئًا. |
| `services/rag/chunker.py` | يقسّم المعرفة: جزء لكل `CREATE TABLE` (ويتجاهل DROP و USE و CREATE DATABASE)، وجزء لكل قسم `##` في الكتالوج، وجزء لكل مثال يجمع السؤال مع استعلامه. |
| `services/rag/embedding_service.py` | ينتج embeddings عبر Ollama. |
| `services/rag/retriever.py` | البحث الدلالي الخام: يسترجع أقرب الـ chunks للسؤال، مع فلتر اختياري حسب النوع. |
| `services/rag/context_builder.py` | يبني سياق النموذج اللغوي بالاستراتيجية "full schema + retrieved documentation/examples". |
| `repositories/mysql_repository.py` | ينفذ SELECT للقراءة فقط بعد تحقق جديد، مع مهلة زمنية على الخادم، وحد للصفوف، وإغلاق مضمون للاتصال. يسجّل نوع كل عمود من بيانات MySQL الوصفية (`column_types`) لاستخدامها في الرسوم. |
| `repositories/vector_repository.py` | الحفظ والبحث في Qdrant المحلي (`storage/qdrant/`) بتشابه cosine. يخزّن اسم نموذج الـ Embeddings مع الفهرس، ويرفض الفهرس غير المتوافق دون حذفه. عملية واحدة فقط تفتح التخزين في نفس الوقت. |
| `prompts/*.txt` | نصوص تعليمات توليد SQL وتصحيحه. |
| `scripts/build_index.py` | يبني فهرس المعرفة. |
| `scripts/preview_chunks.py` | يعرض الأجزاء وعناوينها للمراجعة، دون حفظ أي شيء. |
| `scripts/preview_embeddings.py` | يجرّب الـ Embeddings دون حفظ. |
| `scripts/preview_retrieval.py` | يعرض أقرب الأجزاء لسؤال مع درجة التشابه (بحث خام). |
| `scripts/preview_context.py` | يعرض نص السياق بالضبط كما سيُرسل إلى النموذج اللغوي. |
| `scripts/preview_sql.py` | يولّد SQL لسؤال، ويتحقق منه، ويعرض النتيجة مع الأزمنة، دون تنفيذ. |
| `scripts/preview_answer.py` | المسار الكامل من سطر الأوامر: سؤال، ثم سياق، ثم نموذج، ثم تحقق، ثم تنفيذ للقراءة فقط، ثم النتيجة. بدون إعادة محاولة. |

## طبقات الحماية حول تنفيذ SQL

1. **`sql_validator.py`** (منفذ، ويعمل دون اتصال بقاعدة البيانات):
   - يحلل الاستعلام بـ **SQLGlot** (لهجة MySQL)، ويفحص شجرة الـ AST كلها، لا أول كلمة فقط.
   - يقبل مجموعة محدودة من SELECT، ويرفض أي شيء غير مسموح صراحة (fail-closed).
   - لا يعدّل الاستعلام ولا يضيف LIMIT: النص المقبول يُرجع كما هو.
   - النتيجة `ValidationResult` فيها: `ok` و `code` و `reason` و `tables`.
   - `MySQLRepository.execute()` لا يقبل إلا `ValidatedSQL`.
2. **حساب `medlens_readonly`**: صلاحية SELECT فقط على `medlens`. التحقق يكمّل هذا الحساب ولا يحل محله.
3. **طبقة التنفيذ (`repositories/mysql_repository.py`، منفذة)**، وتعمل بهذا الترتيب داخل `execute()`:
   1. **تحقق جديد:** تتحقق من النص نفسه مرة أخرى قبل أي اتصال، حتى لو وصلها كائن `ValidatedSQL`. الاستعلام المرفوض لا يصل إلى MySQL.
   2. **الحساب:** تتصل بحساب `MYSQL_READONLY_*` فقط، بدون أي رجوع إلى root، مع مهلات محددة:
      - `MYSQL_CONNECT_TIMEOUT_SECONDS` لفتح الاتصال.
      - `MYSQL_IO_TIMEOUT_SECONDS` للقراءة والكتابة على الشبكة.
   3. **إعداد الجلسة:** تنفذ أوامر جلسة ثابتة يكتبها التطبيق، لا النموذج، ثم تقرأ قيمها للتأكد منها:
      - `SET SESSION max_execution_time = <MYSQL_MAX_EXECUTION_MS>`.
      - `SET SESSION transaction_read_only = ON`.
      - إذا لم تُطبّق القيم، لا يُنفذ الاستعلام.
   4. **التنفيذ:** تنفذ الاستعلام كما هو، دون تعديل، بـ cursor غير مخزّن مؤقتًا (unbuffered).
   5. **حد الصفوف:** تقرأ `MYSQL_ROW_CAP + 1` صفًا على الأكثر، وترجع `MYSQL_ROW_CAP`، و `truncated=true` إذا وُجد صف إضافي.
      - إذا بقيت صفوف لم تُقرأ، يُغلق الاتصال فورًا (`shutdown`) دون قراءة الباقي.
   - **حد الصفوف** يحدد عدد الصفوف المرجعة فقط، لا كل عمل الخادم. مثلًا GROUP BY قد يقرأ كل الجدول.
   - **المهلة الزمنية** (`max_execution_time`) هي الحد المستقل لعمل الخادم.
   - **مثال حقيقي:** استعلام SELECT صالح بأربعة joins قبله التحقق، وأوقفه الخادم بعد ثانية واحدة. التحقق وحده لا يحمي من الاستعلامات الثقيلة.
   - **شكل النتيجة:**
     - أسماء الأعمدة بترتيبها، مع الأسماء المكررة.
     - الأرقام العشرية (Decimal) كنص دقيق مثل `"173424.90"`.
     - التواريخ بصيغة ISO، وقيم TIME مثل `"15:15:00"`.
     - NULL تبقى `null`.

**ما يقبله التحقق (الإصدار الأول):**
- **الهيكل:**
  - استعلام SELECT واحد، مع `;` واحدة اختيارية في النهاية.
  - أعمدة، و `*`، و `table.*`، وأسماء بديلة (aliases)، و DISTINCT.
- **الجداول والربط:** FROM لجدول واحد، ثم `[INNER] JOIN` أو `LEFT [OUTER] JOIN` أو `RIGHT [OUTER] JOIN` مع شرط ON.
- **الشروط والترتيب:** WHERE و GROUP BY و HAVING و ORDER BY، و LIMIT/OFFSET بأرقام صحيحة غير سالبة.
- **التعبيرات:** المقارنات، و AND/OR/NOT، و IN مع قائمة قيم، و BETWEEN و LIKE و IS NULL، والعمليات الحسابية، و CASE و IF و CAST.
- **الدوال (قائمة سماح):**
  - التجميع: COUNT و SUM و AVG و MIN و MAX.
  - الأرقام: ROUND و ABS.
  - القيم الفارغة: COALESCE و IFNULL و NULLIF.
  - النصوص: CONCAT و LOWER و UPPER و LENGTH و TRIM.
  - التواريخ: YEAR و MONTH و DAY و DATE و DATE_FORMAT و DATEDIFF و TIMESTAMPDIFF و DATE_ADD و DATE_SUB و CURDATE.
- **الجداول والأعمدة:**
  - الجداول من `database/schema.sql` فقط، بدون اسم قاعدة أو مع `medlens.` فقط. أسماء الجداول حساسة لحالة الأحرف.
  - كل عمود يجب أن يُحل إلى عمود واحد في السكيما.
  - العمود الموجود في أكثر من جدول بدون تحديد الجدول يُرفض لأنه غامض.
  - الأسماء البديلة تتبع قواعد MySQL:
    - في ORDER BY يُبحث في أسماء SELECT أولًا.
    - في GROUP BY و HAVING يُبحث في أعمدة الجداول أولًا.
    - في WHERE لا يُسمح بها.

**ما يُرفض دائمًا:**
- **أوامر غير القراءة:** الكتابة، وأوامر DDL، و CALL و SET و SHOW، وأكثر من أمر واحد.
- **مداخل خطرة:**
  - `INTO OUTFILE/DUMPFILE/@var`.
  - أقفال القراءة: FOR UPDATE و LOCK IN SHARE MODE.
  - المتغيرات والإسناد.
  - الدوال SLEEP و BENCHMARK و LOAD_FILE و GET_LOCK، وأي دالة خارج القائمة.
- **التعليقات والنصوص:**
  - أي تعليق في الاستعلام. السبب: MySQL ينفّذ ما داخل `/*! ... */`، بينما يتجاهله المحلل، فيجب رفضه قبل التحليل.
  - النصوص بعلامات تنصيص مزدوجة، والشرطة المائلة العكسية (`\`)، لأن معناها يتغير حسب إعدادات الخادم.
  - أما الكلمات داخل النصوص العادية مثل `'delete'` فهي مقبولة، لأن الفحص يتم على الشجرة وليس بالبحث عن كلمات.

**غير مدعوم في هذا الإصدار، ويُرفض برسالة واضحة:**
- الاستعلامات الفرعية، و CTE (`WITH`)، و UNION/INTERSECT/EXCEPT، ودوال النوافذ (`OVER`).
- أنواع الربط: الربط بالفاصلة، و CROSS و NATURAL و STRAIGHT_JOIN، و `JOIN ... USING`.
- `WITH ROLLUP`، وتلميحات الفهارس، والأعمدة بثلاثة أجزاء.

**حدود التحقق:**
- قبول الاستعلام لا يعني أنه يجيب على السؤال بشكل صحيح.
- ولا يعني أن تنفيذه سريع أو أن نتيجته صغيرة.
- لا يفحص قواعد GROUP BY الصارمة (`ONLY_FULL_GROUP_BY`)، ولا أنواع القيم. هذه الأخطاء يكتشفها MySQL عند التنفيذ.

**قرار تصميم: SQLGlot بدل sqlparse:**
- الخطة الأصلية ذكرت `sqlparse`، لكنه يقسّم النص إلى tokens فقط، ولا يبني شجرة تفهم الجداول والأعمدة والدوال.
- SQLGlot (`sqlglot==30.24.0`) يبني AST كاملة للهجة MySQL، فيمكن فحص كل جزء من الاستعلام.
- التعليقات تُفحص على مستوى الـ tokens، لأن الـ AST لا تحتويها.
