"""Decides what kind of message a user sent before any retrieval or SQL work.

Routes: greeting, thanks_or_goodbye, capabilities, data_question, needs_clarification, out_of_scope.

1. Deterministic rules (rule_route) handle obvious STANDALONE greetings, thanks/goodbyes and
   capability questions. The whole message must consist of known phrases (plus a few filler
   words like "please"); a greeting inside a longer message never matches, so
   "Hello, how many patients are registered?" is left for the classifier and the data pipeline.
   No model, index or database is needed for these.
2. Everything else is labelled by the local LLM classifier, which also resolves follow-ups within
   a conversation into standalone questions (LLMService.resolve_message). For "capabilities" it also
   returns a topic (charts, export, data, memory, limitations, change_view, overview); the reply for
   each topic comes from capabilities.py, the single description of implemented features. Asking to
   see the current result differently ("show that as a pie chart") is the change_view topic: it is
   answered with how to switch views, without a new query.

Data questions are answered in English only: Arabic-dominant data questions get a fixed reply
(recognizing an Arabic greeting does not mean Arabic queries are supported).
"""

import re
import unicodedata
from dataclasses import dataclass

from backend.app.services import capabilities

GREETING = "greeting"
THANKS_OR_GOODBYE = "thanks_or_goodbye"
CAPABILITIES = "capabilities"
DATA_QUESTION = "data_question"
NEEDS_CLARIFICATION = "needs_clarification"
OUT_OF_SCOPE = "out_of_scope"
ROUTES = (GREETING, THANKS_OR_GOODBYE, CAPABILITIES, DATA_QUESTION, NEEDS_CLARIFICATION, OUT_OF_SCOPE)
CONVERSATIONAL_ROUTES = (GREETING, THANKS_OR_GOODBYE, CAPABILITIES)


def normalize(text: str) -> str:
    """Lower-case, drop Arabic diacritics/tatweel, unify common Arabic letter variants,
    turn punctuation into spaces and collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[ً-ْٰـ]", "", text)
    text = text.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي", "؟": " ", "،": " "}))
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def is_arabic_dominant(text: str) -> bool:
    """True if more than half of the letters are Arabic script."""
    letters = [c for c in text if c.isalpha()]
    arabic = sum(1 for c in letters if "؀" <= c <= "ۿ")
    return bool(letters) and arabic / len(letters) > 0.5


_PHRASES: dict[str, list[str]] = {
    GREETING: [
        "hi", "hello", "hey", "hiya", "howdy", "greetings", "good morning", "good afternoon", "good evening",
        "how are you", "how are you doing",
        "السلام عليكم", "سلام عليكم", "السلام عليكم ورحمة الله", "السلام عليكم ورحمة الله وبركاته",
        "مرحبا", "اهلا", "اهلا وسهلا", "هلا", "هلا والله", "هاي", "صباح الخير", "مساء الخير", "كيف حالك", "كيفك", "شلونك",
    ],
    THANKS_OR_GOODBYE: [
        "thanks", "thank you", "thx", "ty", "cheers", "appreciate it", "much appreciated", "ok", "okay", "got it",
        "great", "perfect", "bye", "goodbye", "see you", "see you later",
        "شكرا", "شكرا لك", "مشكور", "تسلم", "يعطيك العافيه", "الله يعطيك العافيه", "جزاك الله خير",
        "تمام", "ممتاز", "مع السلامه", "باي", "الي اللقاء",
    ],
    CAPABILITIES: [
        "what can you do", "what can you help with", "what can you help me with", "how can you help",
        "how can you help me", "what do you do", "what are you", "who are you", "help", "how does this work",
        "how do i use this", "what can i ask", "what can i ask you", "what questions can i ask",
        "وش تقدر تسوي", "ايش تقدر تسوي", "وش تسوي", "ماذا تستطيع ان تفعل", "ماذا يمكنك ان تفعل",
        "من انت", "مين انت", "وش انت", "كيف استخدمك", "وش اقدر اسال", "ايش اقدر اسال", "مساعده",
    ],
}
_GOODBYES = {normalize(p) for p in ("bye", "goodbye", "see you", "see you later", "مع السلامه", "باي", "الي اللقاء")}
_SALAM = {normalize(p) for p in _PHRASES[GREETING] if "سلام" in p}
_FILLERS = {"please", "there", "again", "so", "much", "very", "a", "lot", "and", "oh", "medlens", "bot", "assistant",
            "everyone", "all", "you", "too", "يا", "جزيلا", "كثير", "والله", "ميدلنز"}

# Longest phrases first so "thank you" wins over "you" as a filler.
_MATCHERS = sorted(
    ((normalize(p), route) for route, phrases in _PHRASES.items() for p in phrases), key=lambda pr: -len(pr[0])
)


@dataclass(frozen=True)
class RouteDecision:
    route: str
    source: str  # "rules", "classifier" or "classifier_fallback"
    reply: str | None = None  # fixed text for every route except a data question that goes to the pipeline
    reply_code: str | None = None  # e.g. "greeting", "out_of_scope", "unsupported_language"


def rule_route(message: str) -> RouteDecision | None:
    """Route an obvious standalone greeting / thanks / goodbye / capability question, else None."""
    text = f" {normalize(message)} "
    found: set[str] = set()
    matched: set[str] = set()
    for phrase, route in _MATCHERS:
        pattern = f" {phrase} "
        while pattern in text:
            text = text.replace(pattern, " ", 1)
            found.add(route)
            matched.add(phrase)
    leftover = [w for w in text.split() if w not in _FILLERS]
    if not found or leftover:
        return None
    route = CAPABILITIES if CAPABILITIES in found else GREETING if GREETING in found else THANKS_OR_GOODBYE
    arabic = is_arabic_dominant(message)
    if route == GREETING and matched & _SALAM:
        key = "salam"
    elif route == THANKS_OR_GOODBYE and matched <= _GOODBYES:
        key = "goodbye"
    else:
        key = route
    if route == CAPABILITIES and not arabic:
        return RouteDecision(route, "rules", capabilities.reply(capabilities.OVERVIEW), route)
    return RouteDecision(route, "rules", REPLIES[(key, arabic)], route)


def decide_after_classification(message: str, label: str, source: str, topic: str | None = None) -> RouteDecision:
    """Turn a classifier label into a decision with the right fixed reply (none for English data questions).
    topic selects the capability reply (capabilities.reply); unknown or missing topics get the overview."""
    arabic = is_arabic_dominant(message)
    if label == DATA_QUESTION:
        if arabic:
            return RouteDecision(DATA_QUESTION, source, REPLIES[("unsupported_language", True)], "unsupported_language")
        return RouteDecision(DATA_QUESTION, source)
    if label == CAPABILITIES and not arabic:
        return RouteDecision(CAPABILITIES, source, capabilities.reply(topic or capabilities.OVERVIEW), CAPABILITIES)
    key = {GREETING: GREETING, THANKS_OR_GOODBYE: THANKS_OR_GOODBYE, CAPABILITIES: CAPABILITIES,
           NEEDS_CLARIFICATION: "clarify", OUT_OF_SCOPE: OUT_OF_SCOPE}[label]
    code = {"clarify": "ambiguous"}.get(key, key)
    return RouteDecision(label, source, REPLIES[(key, arabic)], code)


_EXAMPLE = '"How many patients are registered?"'
_SCOPE_EN = ("MedLens answers English questions about this hospital's data - patients, doctors, appointments, "
             "treatments and billing - by generating a read-only SQL query, validating it and running it.")
_SCOPE_AR = "MedLens يجاوب على أسئلة بالإنجليزي عن بيانات المستشفى: المرضى، الأطباء، المواعيد، العلاجات، والفواتير."

# (key, arabic) -> reply text
REPLIES: dict[tuple[str, bool], str] = {
    ("greeting", False): f"Hello! I'm MedLens. Ask me a question about the hospital data in English, for example: {_EXAMPLE}",
    ("greeting", True): "أهلًا! أنا MedLens، أساعدك تستكشف بيانات المستشفى. اكتب سؤالك عن البيانات بالإنجليزي.",
    ("salam", False): "وعليكم السلام! أنا MedLens، أساعدك تستكشف بيانات المستشفى. اكتب سؤالك عن البيانات بالإنجليزي.",
    ("salam", True): "وعليكم السلام! أنا MedLens، أساعدك تستكشف بيانات المستشفى. اكتب سؤالك عن البيانات بالإنجليزي.",
    ("thanks_or_goodbye", False): "You're welcome! Ask another question about the hospital data whenever you like.",
    ("thanks_or_goodbye", True): "العفو! اكتب سؤالك عن البيانات بالإنجليزي متى ما احتجت.",
    ("goodbye", False): "Goodbye! Come back any time you have a question about the hospital data.",
    ("goodbye", True): "مع السلامة! ارجع متى ما عندك سؤال عن البيانات.",
    ("capabilities", True): (
        f"{_SCOPE_AR} أكتب لك استعلام SQL للقراءة فقط، أفحصه، ثم أنفذه وأعرض النتيجة. "
        f"مثال: {_EXAMPLE}. تقدر تسأل أسئلة متابعة داخل نفس المحادثة، لكن المحادثات مؤقتة في الذاكرة ولا تُحفظ."
    ),
    ("clarify", False): (
        "Could you ask the full question? I couldn't tell what words like \"them\" or \"that\" refer to "
        f"in this conversation. Name what you want counted or listed, for example: {_EXAMPLE}"
    ),
    ("clarify", True): (
        "ممكن تكتب السؤال كامل؟ ما قدرت أعرف وش تقصد من هذه المحادثة. "
        f"حدد وش تبي تحسب أو تعرض، واكتبه بالإنجليزي، مثل: {_EXAMPLE}"
    ),
    ("out_of_scope", False): f"That's outside what I can help with. {_SCOPE_EN}",
    ("out_of_scope", True): f"هذا خارج نطاقي. {_SCOPE_AR}",
    ("unsupported_language", True): (
        f"استعلام البيانات يدعم الإنجليزي حاليًا فقط. اكتب سؤالك بالإنجليزي، مثل: {_EXAMPLE} "
        "(Data questions are currently supported in English only.)"
    ),
}
