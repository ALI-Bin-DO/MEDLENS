// Chat state for one signed-in demo session. It lives in browser memory only (no storage):
// signing out or reloading the page discards it.
import type { ChatResult } from "../services/types";

export interface UserMessage {
  id: string;
  role: "user";
  text: string;
}

export interface AssistantMessage {
  id: string;
  role: "assistant";
  /** The question this reply answers (used by "Edit my question"). */
  question: string;
  result: ChatResult;
}

export type Message = UserMessage | AssistantMessage;

export interface Conversation {
  /** Local key, never sent to the server. */
  localId: string;
  /** conversation_id returned by the server; null until the server has created one. */
  serverId: string | null;
  title: string;
  messages: Message[];
  /** The request waiting for a reply, if any; its reply always lands in this conversation. */
  pending: { requestId: string; question: string } | null;
  /** The server answered 404 conversation_expired; the messages stay visible. */
  expired: boolean;
}

export type View = "chat" | "catalog" | "admin";

export interface ChatState {
  conversations: Conversation[]; // newest first
  /** null = the home screen (a new conversation that has not been sent yet). */
  activeId: string | null;
  view: View;
  /** Unsent composer text per conversation; "" is the home screen. */
  drafts: Record<string, string>;
}

export const HOME_KEY = "";

export const initialChatState: ChatState = { conversations: [], activeId: null, view: "chat", drafts: {} };

export type ChatAction =
  | { type: "new_conversation"; draft?: string }
  | { type: "open_conversation"; localId: string }
  | { type: "open_view"; view: View }
  | { type: "set_draft"; key: string; text: string }
  | { type: "send"; localId: string; requestId: string; question: string; title: string; isNew: boolean }
  | { type: "reply"; localId: string; requestId: string; messageId: string; result: ChatResult };

function updateConversation(state: ChatState, localId: string, change: (c: Conversation) => Conversation): ChatState {
  return { ...state, conversations: state.conversations.map((c) => (c.localId === localId ? change(c) : c)) };
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "new_conversation":
      return {
        ...state, activeId: null, view: "chat",
        drafts: { ...state.drafts, [HOME_KEY]: action.draft ?? "" },
      };
    case "open_conversation":
      if (!state.conversations.some((c) => c.localId === action.localId)) return state;
      return { ...state, activeId: action.localId, view: "chat" };
    case "open_view":
      return { ...state, view: action.view };
    case "set_draft":
      return { ...state, drafts: { ...state.drafts, [action.key]: action.text } };
    case "send": {
      const userMessage: UserMessage = { id: `${action.requestId}-q`, role: "user", text: action.question };
      const pending = { requestId: action.requestId, question: action.question };
      if (action.isNew) {
        const conversation: Conversation = {
          localId: action.localId, serverId: null, title: action.title,
          messages: [userMessage], pending, expired: false,
        };
        return {
          ...state,
          conversations: [conversation, ...state.conversations],
          activeId: action.localId,
          view: "chat",
          drafts: { ...state.drafts, [HOME_KEY]: "", [action.localId]: "" },
        };
      }
      const next = updateConversation(state, action.localId, (c) =>
        c.pending || c.expired ? c : { ...c, messages: [...c.messages, userMessage], pending });
      return { ...next, drafts: { ...next.drafts, [action.localId]: "" } };
    }
    case "reply":
      return updateConversation(state, action.localId, (c) => {
        // Ignore anything that is not the reply this conversation is waiting for.
        if (!c.pending || c.pending.requestId !== action.requestId) return c;
        const reply: AssistantMessage = {
          id: action.messageId, role: "assistant", question: c.pending.question, result: action.result,
        };
        let { serverId, expired } = c;
        if (action.result.kind === "response") {
          const body = action.result.body;
          if (body.status === "conversation_expired") {
            serverId = null;
            expired = true;
          } else if (body.conversation_id) {
            serverId = body.conversation_id;
          }
        }
        return { ...c, messages: [...c.messages, reply], pending: null, serverId, expired };
      });
  }
}

/** Server conversation IDs this session knows about (for best-effort cleanup at sign-out). */
export function knownServerIds(state: ChatState): string[] {
  return state.conversations.flatMap((c) => (c.serverId ? [c.serverId] : []));
}

export function titleFor(question: string): string {
  const oneLine = question.replace(/\s+/g, " ").trim();
  return oneLine.length > 80 ? `${oneLine.slice(0, 79)}…` : oneLine;
}
