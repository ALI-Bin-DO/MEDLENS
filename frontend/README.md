# الواجهة الأمامية

React + TypeScript + Vite. الواجهة تعرض الأسئلة والنتائج فقط. لا تكتب SQL ولا تتصل بقاعدة البيانات، وكل شيء يمر عبر الخادم (FastAPI).

## التشغيل

المتطلبات: Node.js 20 أو أحدث (مجرَّب على 24 LTS)، والخادم يعمل على `127.0.0.1:8000`.

1. شغّل الخادم من جذر المشروع:
   ```
   .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
   ```
2. في نافذة أخرى، داخل `frontend/`:
   ```
   npm install
   npm run dev
   ```
3. افتح `http://127.0.0.1:5173`.

خادم Vite يستمع على `127.0.0.1` فقط، ويمرّر `/chat` و `/export` و `/health` و `/catalog` و `/admin` إلى `http://127.0.0.1:8000`. لذلك الواجهة تستخدم مسارات نسبية ولا تحتاج CORS.

أوامر أخرى:
- `npm run typecheck`: فحص الأنواع.
- `npm test`: الاختبارات (Vitest، مع fetch وهمي، بدون خادم).
- `npm run build`: بناء الإنتاج في `dist/`. للمعاينة: `npm run preview` (على `127.0.0.1:4173` مع نفس التمرير).

الخطوط (Inter) و Plotly تُحمّل من الحزم المثبتة محليًا، بدون CDN.

## الدخول التجريبي

- زر "Sign in" وزر "Continue with SSO" يفتحان التطبيق مباشرة، مهما كان في الحقلين، حتى لو كانا فارغين.
- هذا ليس تسجيل دخول: لا تحقق، ولا SSO، ولا مستخدمين، ولا صلاحيات. الحساب يظهر باسم "Demo User".
- ما يُكتب في الحقلين لا يُرسل ولا يُسجَّل ولا يُحفظ، ويُمسح عند الدخول. قد يعرض المتصفح حفظ كلمة المرور؛ تجاهله.
- "Sign out" يمسح كل حالة الواجهة ويرجع لشاشة الدخول. ويطلب من الخادم حذف محادثات هذه الجلسة (`DELETE /chat/conversations/{id}`) بدون انتظار؛ فشل الحذف لا يمنع الخروج. الرد الذي يصل بعد الخروج لا يُعرض، ومحادثته تُحذف أيضًا.

## ذاكرة المحادثة مؤقتة

- أول سؤال في محادثة يُرسل بدون `conversation_id`. الواجهة تحفظ المعرّف الراجع وترسله مع الأسئلة التالية في نفس المحادثة، لذلك تعمل أسئلة المتابعة مثل "Only completed ones".
- "New conversation" يبدأ دائمًا بدون معرّف.
- المحادثات في ذاكرة المتصفح فقط (بدون localStorage). تضيع عند تحديث الصفحة أو الخروج.
- ذاكرة الخادم مؤقتة أيضًا: تنتهي بعد فترة بدون استخدام (`CONVERSATION_TTL_SECONDS`، افتراضيًا 30 دقيقة) وتضيع عند إعادة تشغيل الخادم. عندها يرجع الخادم 404 `conversation_expired`: الواجهة تبقي الرسائل ظاهرة، وتعرض زر "Start a new conversation"، ولا تعيد إرسال السؤال من نفسها.
- لا إعادة محاولة تلقائية لـ `POST /chat` (ولا عند `busy` أو `conversation_busy`)، ولا مهلة قصيرة في المتصفح: السؤال الأول قد يأخذ دقائق أثناء تحميل النموذج.

## أين الأشياء

- `src/services/api.ts`: كل الاتصال بالخادم (`postChat`، `deleteConversation`، `exportCsv`).
- `src/services/types.ts`: أشكال الردود.
- `src/state/chatState.ts`: حالة المحادثات (reducer).
- `src/pages/`: `LoginPage` و `Workspace` (الهيكل: الشريط الجانبي، الرأس، المحتوى).
- `src/components/`: الرسائل، الجدول، الرسم، لوحة SQL، حقل السؤال، وغيرها.
- `src/styles/tokens.css`: ألوان ومسافات Figma.

## Charts

- The backend lists the chart views that fit each result (`chart.views`, `chart.default_view`); the
  "Chart type" buttons offer only those: vertical bars, horizontal bars, line, donut.
- Switching views reuses the same rows: no new question, SQL or request.
- Results that can be a donut color each category with a fixed, validated palette (emerald, blue,
  amber, teal, purple), the same in every view; other results use one emerald series. Tooltips show
  exact values.

## لم يُنفذ بعد

- شاشتا "Data catalog" و "System administration" تعرضان "Not implemented yet" فقط.
- الوضع الداكن.
