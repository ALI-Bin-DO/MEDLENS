"""Bounded, in-memory conversation memory for POST /chat (lost on restart; never persisted).

A conversation keeps at most CONVERSATION_MAX_TURNS data turns: the user's original
question, the resolved standalone question and either the SQL that was validated and executed or
the clarifying question the SQL model asked about it (server-generated, so a reply such as "yes,
use bill_date" can be resolved). It never keeps result rows, prompts, retrieved chunks or anything
the client claims the assistant said.

Lifecycle: create() returns an opaque random ID; begin() starts a turn (raises ConversationExpired
for unknown/expired IDs and ConversationBusy if a turn is already running for that ID - overlapping
turns are rejected, not queued); end() finishes it, appending a turn only when one is given.
Conversations expire after CONVERSATION_TTL_SECONDS without use; when CONVERSATION_MAX_COUNT is
reached, the least recently used idle conversation is evicted. IDs are not authentication: anyone
holding an ID can continue or delete that conversation.
"""

import secrets
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    question: str  # the user's original message
    resolved_question: str  # standalone question actually answered (or awaiting the clarification below)
    sql: str | None  # SQL that passed validation and ran successfully; None for a clarification turn
    # The SQL model's clarifying question when it could not answer resolved_question without guessing
    # (e.g. "payment date is not stored; use bill_date?"), so the user's reply ("yes") can be resolved.
    clarification: str | None = None
    # Chart views the answered result offered, and why others were not (presentation metadata only, never
    # rows), so "show that as a donut" can reuse the result in the browser or explain why it cannot.
    # None: no answered result in this turn; (): an answered result without a chart.
    chart_views: tuple[str, ...] | None = None
    # For a "which person?" clarification: the people offered, as (role, id, first, last, details...).
    # Only identifying values already shown to the user; the reply must pick one of them.
    candidates: tuple[tuple[str, ...], ...] = ()
    chart_notes: tuple[tuple[str, str], ...] = ()
    # The grouping the answered request used (request_spec dimension names, e.g. ("doctor",)), so a follow-up
    # such as "Do it by hospital branch" replaces it. Part of the request description; never rows.
    grouping: tuple[str, ...] = ()


class ConversationExpired(LookupError):
    """Unknown, expired, evicted or deleted conversation ID."""


class ConversationBusy(RuntimeError):
    """A turn is already running for this conversation."""


class _Conversation:
    __slots__ = ("turns", "last_used", "busy")

    def __init__(self, max_turns: int, now: float) -> None:
        self.turns: deque[Turn] = deque(maxlen=max_turns)
        self.last_used = now
        self.busy = False


class ConversationStore:
    def __init__(self, ttl_seconds: int, max_conversations: int, max_turns: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_conversations = max_conversations
        self.max_turns = max_turns
        self._clock = clock
        self._lock = threading.Lock()
        self._items: OrderedDict[str, _Conversation] = OrderedDict()  # least recently used first

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def create(self, begin: bool = False) -> str:
        """New empty conversation. begin=True also starts its first turn atomically, so it cannot
        be evicted before that turn begins."""
        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            while len(self._items) >= self.max_conversations:
                idle = next((cid for cid, c in self._items.items() if not c.busy), None)
                if idle is None:
                    break  # every conversation is mid-turn; allow a temporary overshoot
                del self._items[idle]
            conversation_id = secrets.token_urlsafe(24)
            self._items[conversation_id] = _Conversation(self.max_turns, now)
            self._items[conversation_id].busy = begin
            return conversation_id

    def begin(self, conversation_id: str) -> list[Turn]:
        """Start a turn; returns the retained turns, oldest first."""
        with self._lock:
            now = self._clock()
            conversation = self._items.get(conversation_id)
            if conversation is None or (not conversation.busy and now - conversation.last_used > self.ttl_seconds):
                self._items.pop(conversation_id, None)
                raise ConversationExpired(conversation_id)
            if conversation.busy:
                raise ConversationBusy(conversation_id)
            conversation.busy = True
            conversation.last_used = now
            self._items.move_to_end(conversation_id)
            return list(conversation.turns)

    def end(self, conversation_id: str, turn: Turn | None = None) -> None:
        """Finish a turn; a turn is stored only for successful data answers. Safe if deleted meanwhile."""
        with self._lock:
            conversation = self._items.get(conversation_id)
            if conversation is None:
                return
            if turn is not None:
                conversation.turns.append(turn)
            conversation.busy = False
            conversation.last_used = self._clock()
            self._items.move_to_end(conversation_id)

    def delete(self, conversation_id: str) -> bool:
        with self._lock:
            return self._items.pop(conversation_id, None) is not None

    def _purge_expired(self, now: float) -> None:
        for cid in [cid for cid, c in self._items.items() if not c.busy and now - c.last_used > self.ttl_seconds]:
            del self._items[cid]
