# الاختبارات

التشغيل من جذر المشروع:

```powershell
python -m unittest discover -s tests -v
```

**الموجود الآن:**
- `test_sql_validator.py`: اختبارات التحقق من SQL. لا تنفذ أي SQL.
- دالة الاتصال بـ MySQL معطّلة طوال الاختبار، فأي محاولة اتصال تُفشل الاختبار.

القوائم التالية كانت الخطة الأصلية. اختبارات التحقق منها صارت في `test_sql_validator.py`، وما زالت اختبارات الاسترجاع والخادم مطلوبة.

## التحقق من SQL (`sql_validator.py`)

يجب أن **يُقبل**:
- `SELECT` بسيط، و `JOIN`، و `GROUP BY`، و `WITH ... SELECT`.
- الاستعلامات الخمسة في `knowledge_base/examples.sql`.

يجب أن **يُرفض**:
- `INSERT` و `UPDATE` و `DELETE` و `DROP` و `ALTER` و `CREATE` و `GRANT` و `SET`.
- أكثر من أمر واحد، مثل `SELECT 1; DROP TABLE patients`.
- أوامر مخفية داخل تعليقات أو بحروف كبيرة وصغيرة مختلطة.
- `SELECT ... INTO OUTFILE` و `LOAD_FILE()`.
- الدوال الخطرة: `SLEEP()` و `BENCHMARK()`.
- الجداول خارج السكيما، مثل `information_schema` و `mysql.user`.

ويجب التأكد من فرض حد للصفوف (LIMIT).

## حماية التنفيذ

- `MySQLRepository.execute()` ترفض النص العادي، وتقبل `ValidatedSQL` فقط.
- التطبيق لا يستخدم `MYSQL_USER` ولا يرجع إلى root إذا كانت إعدادات القراءة فقط ناقصة.

## الاسترجاع (RAG)

- `chunker` يرجع 5 أجزاء `CREATE TABLE` كاملة، ولا يحتوي أي منها على `DROP` أو `CREATE DATABASE` أو `USE` أو `GRANT`.
- أجزاء الكتالوج والأمثلة (حاليًا 10 و200)، ولا يوجد جزء فارغ أو معرّف مكرر. ووجود `;` داخل تعليق أو نص لا يقسم الجدول.
- `test_answer_quality.py`: the application calendar, capability replies and topics, clarification memory, the evaluation reference time, fitting the SQL context to the budget, and chart views with pie suitability.
- سؤال عن المبالغ المدفوعة يسترجع جدول `billing` وقاعدة `payment_status = 'Paid'`.
- سؤال عن الأطباء يسترجع جدول `doctors`.

## الخادم

- `GET /health` يرجع 200 دون الحاجة إلى MySQL أو Ollama أو Qdrant.
- `POST /chat` يرجع 501 ما دام الخط غير مكتمل، ولا ينفذ أي SQL.
