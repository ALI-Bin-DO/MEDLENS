"""Talks to the local SQL-generating LLM through Ollama's POST /api/chat (standard-library urllib).

Generation only: this module never validates or executes SQL. Its output is a *candidate*
that must still pass sql_validator before anything reaches MySQL. The prompt asks for a
read-only SELECT, but a prompt is not a security control.

Request settings: OLLAMA_SQL_MODEL, stream=false, think=false, temperature=0 and
presence_penalty=0 (a repeatable baseline), num_ctx=OLLAMA_SQL_NUM_CTX,
num_predict=OLLAMA_SQL_NUM_PREDICT, timeout=OLLAMA_GENERATION_TIMEOUT_SECONDS.

Context budget: Ollama silently drops the start of a prompt that is longer than num_ctx
(verified on this setup) and has no tokenize endpoint. So the prompt is checked before sending
with a pessimistic token estimate, and again after the reply with Ollama's own prompt token
count; if either does not leave room for num_predict output tokens, ContextBudgetError is raised
instead of sending a cut-down prompt.
"""

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from backend.app.config import PROMPTS_DIR, MissingSettingError, Settings, get_settings
from backend.app.services.ollama_client import OllamaError, post_json

# Pessimistic characters-per-token ratio for the pre-send estimate. MedLens prompts measured with
# qwen3.5:4b came to about 3.9 characters per token; 2.5 over-estimates tokens on purpose.
CHARS_PER_TOKEN_ESTIMATE = 2.5
# Allowance for the chat template's special tokens around the system and user messages.
TEMPLATE_TOKEN_ALLOWANCE = 64

_PLACEHOLDER = re.compile(r"\{(\w+)\}")

# Intent classification: same model and num_ctx as SQL generation (a different num_ctx would make
# Ollama reload the model), a short output limit, and JSON constrained to the allowed labels.
CLASSIFIER_NUM_PREDICT = 240  # room for the JSON: resolved question, topic, chart request and names
MAX_RESOLVED_QUESTION_CHARS = 1000

CANNOT_ANSWER = "CANNOT_ANSWER:"
NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION:"


class LLMError(OllamaError):
    """Ollama could not produce a usable reply."""


class IncompleteGenerationError(LLMError):
    """Generation stopped before finishing (e.g. it hit num_predict); the output is not usable."""


class ContextBudgetError(LLMError):
    """The prompt does not fit num_ctx with room left for num_predict output tokens."""


@dataclass(frozen=True)
class ChatPrompt:
    system: str
    user: str

    @property
    def characters(self) -> int:
        return len(self.system) + len(self.user)


@dataclass(frozen=True)
class GenerationStats:
    """Timings in seconds and token counts as reported by Ollama (None if not reported)."""

    load_seconds: float | None
    prompt_seconds: float | None
    generation_seconds: float | None
    total_seconds: float | None
    prompt_tokens: int | None
    output_tokens: int | None
    done_reason: str


@dataclass(frozen=True)
class SQLGeneration:
    """The model's reply, classified. kind is "sql", "cannot_answer" or "needs_clarification".

    For kind == "sql", text is a candidate query: NOT validated and NOT executed.
    """

    kind: str
    text: str
    raw_output: str
    markdown_removed: bool
    model: str
    stats: GenerationStats


def load_prompt(name: str) -> str:
    """Read a prompt template from backend/app/prompts/<name>.txt."""
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def load_chat_prompt(name: str) -> tuple[str, str]:
    """Split a prompt file into its [system] and [user] sections."""
    text = load_prompt(name)
    if not text.startswith("[system]\n") or "\n[user]\n" not in text:
        raise ValueError(f"prompt {name}.txt must contain a [system] section followed by a [user] section")
    system, user = text.removeprefix("[system]\n").split("\n[user]\n", 1)
    return system.strip(), user.strip()


def build_sql_prompt(question: str, context: str, calendar: str = "") -> ChatPrompt:
    """Fill the sql_generation template in one pass, so text inside the inserted context or
    question (even a literal "{question}") is never substituted again. calendar is clock.calendar_context()."""
    if not question.strip():
        raise ValueError("question must not be empty")
    return _fill_prompt("sql_generation", context=context, question=question.strip(), calendar=_calendar(calendar))


def _calendar(calendar: str) -> str:
    return calendar.strip() or "(current date not provided)"


def _fill_prompt(name: str, **values: str) -> ChatPrompt:
    """Fill {name} placeholders of a [system]/[user] prompt in one pass; other braces stay as written."""
    system, user = load_chat_prompt(name)
    return ChatPrompt(system, _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), user))


class ClassificationError(LLMError):
    """The intent classifier's reply was not one of the allowed labels."""


@dataclass(frozen=True)
class MessageRouting:
    intent: str
    resolved_question: str | None  # standalone question for data_question, else None
    history_turns_used: int  # earlier turns that fitted in the prompt
    topic: str | None = None  # capability topic (capabilities.TOPICS) for intent "capabilities", else None
    chart_request: str | None = None  # chart view the user asked for (CHART_REQUESTS), else None
    doctor_names: tuple[str, ...] = ()  # doctors named in the request (raw; cleaned by entity_grounding)
    patient_names: tuple[str, ...] = ()  # patients named in the request (raw; cleaned by entity_grounding)


def _escape(text: str) -> str:
    """Stored and current text cannot open or close the prompt's tags."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _history_block(turns) -> str:
    if not turns:
        return "<history>\n(no earlier questions in this conversation)\n</history>"
    parts = []
    for n, t in enumerate(turns, 1):
        clarification = getattr(t, "clarification", None)
        outcome = (f"<clarification_asked>{_escape(clarification)}</clarification_asked>" if clarification
                   else f"<executed_sql>{_escape(t.sql or '')}</executed_sql>")
        parts.append(f"<turn number=\"{n}\">\n<user_message>{_escape(t.question)}</user_message>\n"
                     f"<resolved_question>{_escape(t.resolved_question)}</resolved_question>\n"
                     f"{outcome}\n</turn>")
    return "<history>\n" + "\n".join(parts) + "\n</history>"


CHART_REQUESTS = ("bar_vertical", "bar_horizontal", "line", "pie")
MAX_NAMES = 3


def _names(value: object) -> tuple[str, ...]:
    """At most MAX_NAMES short strings from the classifier (cleaned and checked later)."""
    if not isinstance(value, list):
        return ()
    return tuple(v.strip()[:60] for v in value if isinstance(v, str) and v.strip())[:MAX_NAMES]


def _routing_schema(labels: tuple[str, ...], topics: tuple[str, ...]) -> dict:
    """JSON schema that constrains the classifier's reply to the allowed labels (and topics)."""
    properties = {"intent": {"type": "string", "enum": list(labels)}, "resolved_question": {"type": "string"}}
    required = ["intent", "resolved_question"]
    if topics:
        names = {"type": "array", "items": {"type": "string"}, "maxItems": MAX_NAMES}
        properties.update({"topic": {"type": "string", "enum": ["none", *topics]},
                           "chart_request": {"type": "string", "enum": ["none", *CHART_REQUESTS]},
                           "doctor_names": names, "patient_names": names})
        required += ["topic", "chart_request", "doctor_names", "patient_names"]
    return {"type": "object", "properties": properties, "required": required}


def estimate_prompt_tokens(prompt: ChatPrompt) -> int:
    """Pessimistic token estimate (over-counts on purpose; see CHARS_PER_TOKEN_ESTIMATE)."""
    return math.ceil(prompt.characters / CHARS_PER_TOKEN_ESTIMATE) + TEMPLATE_TOKEN_ALLOWANCE


class LLMService:
    """Generates SQL candidates using the model named in OLLAMA_SQL_MODEL."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def model_name(self) -> str:
        if not self.settings.ollama_sql_model:
            raise MissingSettingError("OLLAMA_SQL_MODEL must be set in .env")
        return self.settings.ollama_sql_model

    def check_budget(self, prompt: ChatPrompt, num_predict: int | None = None) -> int:
        """Raise ContextBudgetError unless the estimated prompt leaves room for the output. Returns the estimate."""
        estimate = estimate_prompt_tokens(prompt)
        num_ctx = self.settings.ollama_sql_num_ctx
        num_predict = self.settings.ollama_sql_num_predict if num_predict is None else num_predict
        if estimate + num_predict > num_ctx:
            raise ContextBudgetError(
                f"Prompt is about {estimate} tokens (estimated, {prompt.characters} characters); with "
                f"{num_predict} output tokens it exceeds num_ctx={num_ctx}. Nothing was sent. Increase "
                "OLLAMA_SQL_NUM_CTX or lower RAG_CONTEXT_TOP_K; schema and context are never cut."
            )
        return estimate

    def generate_sql(self, question: str, context: str, calendar: str = "") -> SQLGeneration:
        """Ask the model for one SQL query answering question, given the builder's context text and
        the calendar facts for the current date (clock.calendar_context)."""
        return self._sql_chat(build_sql_prompt(question, context, calendar))

    def fix_sql(self, question: str, context: str, failed_sql: str, error: str, calendar: str = "") -> SQLGeneration:
        """Ask for one corrected query after a recoverable validation or MySQL error (sql_retry prompt).

        Same model, generation controls, context-budget and incomplete-generation checks as
        generate_sql. error must already be a sanitized, bounded diagnostic (sql_correction.py).
        The reply may also be CANNOT_ANSWER / NEEDS_CLARIFICATION. The result is NOT validated here.
        """
        if not question.strip():
            raise ValueError("question must not be empty")
        prompt = _fill_prompt("sql_retry", context=context, question=question.strip(),
                              failed_sql=failed_sql.strip(), error=error.strip(), calendar=_calendar(calendar))
        return self._sql_chat(prompt)

    def _sql_chat(self, prompt: ChatPrompt) -> SQLGeneration:
        """One SQL-generation request with the configured controls, budget and reply checks."""
        self.check_budget(prompt)
        model = self.model_name
        payload = post_json(
            self.settings.ollama_base_url,
            "/api/chat",
            {
                "model": model,
                "stream": False,
                "think": False,
                "messages": [{"role": "system", "content": prompt.system}, {"role": "user", "content": prompt.user}],
                "options": {
                    "num_ctx": self.settings.ollama_sql_num_ctx,
                    "num_predict": self.settings.ollama_sql_num_predict,
                    "temperature": 0,
                    "presence_penalty": 0,
                },
            },
            timeout=self.settings.ollama_generation_timeout_seconds,
            error=LLMError,
        )
        output, stats = self._read_reply(payload, self.settings.ollama_sql_num_predict)
        return _classify(output, model, stats)

    def resolve_message(self, message: str, labels: tuple[str, ...], history: Sequence = (), calendar: str = "",
                        topics: tuple[str, ...] = ()) -> MessageRouting:
        """Classify the message and, for data questions, resolve it into one standalone question.

        calendar: clock.calendar_context() for the current date, so relative periods ("this month")
        are resolved into explicit ones. topics: allowed capability topics; when given, the reply
        also carries one (kept only for intent "capabilities"; anything else becomes None).

        history: earlier successful turns (objects with question, resolved_question and sql), oldest
        first. They are escaped and passed as data. Whole turns are dropped, oldest first, until the
        prompt fits num_ctx with room for the reply; ContextBudgetError if even no history fits.
        Raises ClassificationError for any reply that is not {"intent": <label>, "resolved_question":
        <string>} with a non-empty resolved_question for data_question (fail closed), and LLMError
        subclasses (with .kind) for connection, timeout or incomplete output.
        """
        turns = list(history)
        while True:
            prompt = _fill_prompt("intent_classification", message=_escape(message.strip()), history=_history_block(turns),
                                  calendar=_calendar(calendar))
            try:
                self.check_budget(prompt, CLASSIFIER_NUM_PREDICT)
                break
            except ContextBudgetError:
                if not turns:
                    raise
                turns = turns[1:]  # drop the oldest complete turn and try again
        payload = post_json(
            self.settings.ollama_base_url,
            "/api/chat",
            {
                "model": self.model_name,
                "stream": False,
                "think": False,
                "format": _routing_schema(labels, topics),
                "messages": [{"role": "system", "content": prompt.system}, {"role": "user", "content": prompt.user}],
                "options": {"num_ctx": self.settings.ollama_sql_num_ctx, "num_predict": CLASSIFIER_NUM_PREDICT,
                            "temperature": 0, "presence_penalty": 0},
            },
            timeout=self.settings.ollama_generation_timeout_seconds,
            error=LLMError,
        )
        output, _ = self._read_reply(payload, CLASSIFIER_NUM_PREDICT)
        try:
            reply = json.loads(output)
            label, resolved = reply["intent"], reply["resolved_question"]
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            raise ClassificationError(f"classifier reply is not the expected JSON: {output[:100]!r}") from exc
        if label not in labels:
            raise ClassificationError(f"classifier returned an unknown label: {label!r}")
        if not isinstance(resolved, str):
            raise ClassificationError("resolved_question is not a string")
        resolved = " ".join(resolved.split())
        if label == "data_question" and not (0 < len(resolved) <= MAX_RESOLVED_QUESTION_CHARS):
            raise ClassificationError("data_question without a usable resolved_question")
        topic = reply.get("topic") if isinstance(reply, dict) else None
        topic = topic if label == "capabilities" and topic in topics else None
        chart = reply.get("chart_request")
        chart = chart if chart in CHART_REQUESTS else None
        return MessageRouting(label, resolved if label == "data_question" else None, len(turns), topic, chart,
                              _names(reply.get("doctor_names")), _names(reply.get("patient_names")))

    def _read_reply(self, payload: object, num_predict: int) -> tuple[str, GenerationStats]:
        """Validate the /api/chat response and return (output text, stats)."""
        if not isinstance(payload, dict):
            raise LLMError("Ollama /api/chat response is not a JSON object")
        if payload.get("error"):
            raise LLMError(f"Ollama reported an error: {payload['error']}")
        message = payload.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMError("Ollama /api/chat response has no message content")

        done_reason = payload.get("done_reason") or ""
        stats = GenerationStats(
            load_seconds=_seconds(payload.get("load_duration")),
            prompt_seconds=_seconds(payload.get("prompt_eval_duration")),
            generation_seconds=_seconds(payload.get("eval_duration")),
            total_seconds=_seconds(payload.get("total_duration")),
            prompt_tokens=payload.get("prompt_eval_count"),
            output_tokens=payload.get("eval_count"),
            done_reason=done_reason,
        )
        if payload.get("done") is not True:
            raise IncompleteGenerationError("Ollama did not finish the response (done is not true)")
        if done_reason == "length":
            raise IncompleteGenerationError(
                f"Output hit the {num_predict}-token limit (num_predict) before "
                "finishing; the partial output is not usable SQL"
            )
        if done_reason != "stop":
            raise IncompleteGenerationError(f"Generation ended with done_reason={done_reason!r}, expected 'stop'")
        if isinstance(stats.prompt_tokens, int):
            room = self.settings.ollama_sql_num_ctx - num_predict
            if stats.prompt_tokens > room:
                raise ContextBudgetError(
                    f"Ollama counted {stats.prompt_tokens} prompt tokens, more than the {room} allowed "
                    f"(num_ctx {self.settings.ollama_sql_num_ctx} - num_predict {num_predict})"
                )
        if not content.strip():
            raise LLMError("The model returned empty output")
        return content, stats


def _seconds(nanoseconds: object) -> float | None:
    return nanoseconds / 1e9 if isinstance(nanoseconds, (int, float)) and not isinstance(nanoseconds, bool) else None


def _classify(output: str, model: str, stats: GenerationStats) -> SQLGeneration:
    """Sort the reply into sql / cannot_answer / needs_clarification. No SQL validation happens here.
    A CANNOT_ANSWER reply that ends by asking the user a question ("... Should I use bill_date
    instead?") is a clarification request, so it is classified as one and the user can answer it."""
    text, markdown_removed = _strip_code_fence(output.strip())
    for marker, kind in ((CANNOT_ANSWER, "cannot_answer"), (NEEDS_CLARIFICATION, "needs_clarification")):
        if text.upper().startswith(marker):
            reason = text[len(marker):].strip()
            if kind == "cannot_answer" and reason.endswith("?"):
                kind = "needs_clarification"
            return SQLGeneration(kind, reason, output, markdown_removed, model, stats)
    return SQLGeneration("sql", text, output, markdown_removed, model, stats)


def _strip_code_fence(text: str) -> tuple[str, bool]:
    """Remove one ```...``` fence wrapping the whole reply, despite the prompt asking for none."""
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip(), True
    return text, False
