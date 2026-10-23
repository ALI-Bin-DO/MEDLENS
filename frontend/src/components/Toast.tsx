import { useEffect } from "react";
import { CheckIcon, CloseIcon } from "./Icons";
import type { ToastMessage } from "./ResultCard";

const TOAST_MS = 7000;

export function Toast({ toast, onClose }: { toast: ToastMessage | null; onClose: () => void }) {
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(onClose, TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast, onClose]);

  return (
    <div className="toast-region" role="status" aria-live="polite">
      {toast && (
        <div className="toast">
          <CheckIcon size={18} className="toast__icon" />
          <div className="toast__body">
            <p className="toast__title">{toast.title}</p>
            {toast.details.map((line) => <p key={line} className="toast__detail">{line}</p>)}
          </div>
          <button type="button" className="toast__close" onClick={onClose} aria-label="Dismiss notification">
            <CloseIcon size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
