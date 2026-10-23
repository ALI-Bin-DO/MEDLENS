import type { Conversation, View } from "../state/chatState";
import { Logo } from "./Brand";
import { DatabaseIcon, LogOutIcon, MessageIcon, PlusIcon, SettingsIcon } from "./Icons";

interface Props {
  id: string;
  open: boolean;
  conversations: Conversation[];
  activeId: string | null;
  view: View;
  onNewConversation: () => void;
  onOpenConversation: (localId: string) => void;
  onOpenView: (view: View) => void;
  onSignOut: () => void;
}

export function Sidebar({ id, open, conversations, activeId, view, onNewConversation, onOpenConversation, onOpenView, onSignOut }: Props) {
  return (
    <nav id={id} className={`sidebar${open ? " sidebar--open" : ""}`} aria-label="Main navigation" hidden={!open}>
      <div className="sidebar__logo"><Logo size={26} /></div>

      <button type="button" className="btn btn--primary btn--pill sidebar__new" onClick={onNewConversation}>
        <PlusIcon size={18} /> New conversation
      </button>

      <button type="button" className="nav-item" aria-current={view === "catalog" ? "page" : undefined} onClick={() => onOpenView("catalog")}>
        <DatabaseIcon size={18} /> Data catalog
      </button>

      <p className="sidebar__section">This session</p>
      {conversations.length === 0 ? (
        <p className="sidebar__empty">Your conversations will appear here until you sign out.</p>
      ) : (
        <ul className="sidebar__list">
          {conversations.map((c) => {
            const current = view === "chat" && c.localId === activeId;
            return (
              <li key={c.localId}>
                <button type="button" className="nav-item nav-item--conversation" aria-current={current ? "page" : undefined}
                  onClick={() => onOpenConversation(c.localId)} title={c.title}>
                  <MessageIcon size={16} />
                  <span className="nav-item__label">{c.title}</span>
                  {c.pending && <span className="nav-item__badge">Answering…</span>}
                  {c.expired && <span className="nav-item__badge nav-item__badge--muted">Expired</span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <div className="sidebar__bottom">
        <button type="button" className="nav-item" aria-current={view === "admin" ? "page" : undefined} onClick={() => onOpenView("admin")}>
          <SettingsIcon size={18} /> System administration
        </button>
        <div className="user">
          <span className="user__avatar" aria-hidden="true">DU</span>
          <span className="user__text">
            <span className="user__name">Demo User</span>
            <span className="user__role">Demo access</span>
          </span>
          <button type="button" className="icon-btn" onClick={onSignOut} aria-label="Sign out" title="Sign out">
            <LogOutIcon size={18} />
          </button>
        </div>
      </div>
    </nav>
  );
}
