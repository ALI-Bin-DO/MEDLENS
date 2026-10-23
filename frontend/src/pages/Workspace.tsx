// The signed-in demo session: sidebar, header and the current view. All chat state is held
// here in memory; unmounting this component (sign-out) discards it.
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { Composer, questionProblem } from "../components/Composer";
import { ConversationView } from "../components/ConversationView";
import { HomeView } from "../components/HomeView";
import { LockIcon, MenuIcon } from "../components/Icons";
import type { ToastMessage } from "../components/ResultCard";
import { Sidebar } from "../components/Sidebar";
import { Toast } from "../components/Toast";
import { AdminPage, type AdminTab } from "./AdminPage";
import { CatalogPage } from "./CatalogPage";
import { deleteConversation, postChat } from "../services/api";
import { HOME_KEY, chatReducer, initialChatState, knownServerIds, titleFor, type View } from "../state/chatState";

const WIDE_QUERY = "(min-width: 1024px)";

function isWide(): boolean {
  return typeof window.matchMedia === "function" ? window.matchMedia(WIDE_QUERY).matches : true;
}

let idCounter = 0;
function newId(prefix: string): string {
  idCounter += 1;
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}`;
  return `${prefix}-${idCounter}-${random}`;
}

export function Workspace({ onSignOut }: { onSignOut: () => void }) {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [sidebarOpen, setSidebarOpen] = useState(isWide);
  const [toast, setToast] = useState<ToastMessage | null>(null);
  // Catalog and administration navigation lives beside the chat state, so leaving and returning keeps both.
  const [catalogTable, setCatalogTable] = useState<string | null>(null);
  const [adminTab, setAdminTab] = useState<AdminTab>("overview");
  const stateRef = useRef(state);
  stateRef.current = state;
  const alive = useRef(true);
  const sending = useRef(new Set<string>());
  const composerRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const active = state.conversations.find((c) => c.localId === state.activeId) ?? null;
  const draftKey = active ? active.localId : HOME_KEY;
  const draft = state.drafts[draftKey] ?? "";

  const closeOnNarrow = () => {
    if (!isWide()) setSidebarOpen(false);
  };

  const focusComposer = () => requestAnimationFrame(() => composerRef.current?.focus());

  const send = useCallback(async (key: string, question: string) => {
    if (questionProblem(question) !== null) return;
    const current = stateRef.current;
    const conversation = key === HOME_KEY ? null : current.conversations.find((c) => c.localId === key) ?? null;
    if (conversation && (conversation.pending || conversation.expired)) return;
    const localId = conversation ? conversation.localId : newId("c");
    if (sending.current.has(localId)) return;
    sending.current.add(localId);

    const requestId = newId("r");
    dispatch({ type: "send", localId, requestId, question, title: titleFor(question), isNew: !conversation });
    // A new conversation omits conversation_id; later messages send the ID the server returned.
    const result = await postChat(question, conversation?.serverId ?? null);
    sending.current.delete(localId);

    if (!alive.current) {
      // Arrived after sign-out: do not restore anything; just ask the server to forget it.
      if (result.kind === "response" && result.body.conversation_id) void deleteConversation(result.body.conversation_id);
      return;
    }
    dispatch({ type: "reply", localId, requestId, messageId: newId("m"), result });
  }, []);

  const signOut = () => {
    for (const id of knownServerIds(stateRef.current)) void deleteConversation(id);
    alive.current = false;
    onSignOut();
  };

  const openView = (view: View) => {
    if (view === "catalog" && state.view === "catalog") setCatalogTable(null);  // the nav item returns to the table list
    dispatch({ type: "open_view", view });
    closeOnNarrow();
  };

  const startNew = (prefill = "") => {
    dispatch({ type: "new_conversation", draft: prefill });
    closeOnNarrow();
    focusComposer();
  };

  const editQuestion = (question: string) => {
    if (active && active.expired) {
      startNew(question);
      return;
    }
    dispatch({ type: "set_draft", key: draftKey, text: question });
    focusComposer();
  };

  const notify = useCallback((message: ToastMessage) => setToast(message), []);
  const closeToast = useCallback(() => setToast(null), []);

  const title =
    state.view === "catalog" ? "Data catalog"
      : state.view === "admin" ? "System administration"
        : active ? active.title : "New conversation";

  return (
    <div className={`shell${sidebarOpen ? " shell--sidebar-open" : ""}`}
      onKeyDown={(e) => {
        if (e.key === "Escape" && sidebarOpen && !isWide()) setSidebarOpen(false);
      }}>
      <a className="skip-link" href="#main">Skip to main content</a>
      <Sidebar
        id="sidebar"
        open={sidebarOpen}
        conversations={state.conversations}
        activeId={state.activeId}
        view={state.view}
        onNewConversation={() => startNew()}
        onOpenConversation={(localId) => {
          dispatch({ type: "open_conversation", localId });
          closeOnNarrow();
        }}
        onOpenView={openView}
        onSignOut={signOut}
      />
      {sidebarOpen && <div className="scrim" onClick={() => setSidebarOpen(false)} aria-hidden="true" />}

      <div className="main-col">
        <header className="header">
          <button type="button" className="icon-btn icon-btn--bordered" aria-controls="sidebar" aria-expanded={sidebarOpen}
            aria-label={sidebarOpen ? "Hide navigation" : "Show navigation"} onClick={() => setSidebarOpen((v) => !v)}>
            <MenuIcon size={20} />
          </button>
          <p className="header__title" title={title}>{title}</p>
          <span className="badge"><LockIcon size={14} /> Local · Read-only</span>
        </header>

        <main id="main" className="content" tabIndex={-1}>
          {state.view === "catalog" ? (
            <div className="content__scroll"><CatalogPage selected={catalogTable} onSelect={setCatalogTable} onAsk={startNew} /></div>
          ) : state.view === "admin" ? (
            <div className="content__scroll"><AdminPage tab={adminTab} onTab={setAdminTab} /></div>
          ) : (
            <>
              <div className="content__scroll">
                <div className="content__inner">
                  {active ? (
                    <ConversationView conversation={active} onEditQuestion={editQuestion} onStartNew={startNew} notify={notify} />
                  ) : (
                    <HomeView onPick={(text) => {
                      dispatch({ type: "set_draft", key: HOME_KEY, text });
                      focusComposer();
                    }} />
                  )}
                </div>
              </div>
              <div className="content__composer">
                <Composer
                  ref={composerRef}
                  value={draft}
                  onChange={(text) => dispatch({ type: "set_draft", key: draftKey, text })}
                  onSend={(question) => void send(draftKey, question)}
                  pending={Boolean(active?.pending)}
                  disabledReason={active?.expired ? "This conversation's server memory expired. Start a new conversation to continue." : undefined}
                />
                <p className="content__footnote">Processed locally · Read-only access · Verify results before use</p>
              </div>
            </>
          )}
        </main>
      </div>
      <Toast toast={toast} onClose={closeToast} />
    </div>
  );
}
