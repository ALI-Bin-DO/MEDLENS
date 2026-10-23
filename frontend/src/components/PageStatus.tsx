// Loading and failure blocks shared by the catalog and administration pages.
import type { ReactNode } from "react";
import { AlertIcon } from "./Icons";

export function Loading({ label }: { label: string }) {
  return (
    <p className="page-status" role="status" aria-live="polite">
      <span className="page-status__spinner" aria-hidden="true" /> {label}
    </p>
  );
}

export function Failure({ title, message, onRetry, children }: {
  title: string; message: string; onRetry?: () => void; children?: ReactNode;
}) {
  return (
    <div className="page-failure" role="alert">
      <AlertIcon size={18} />
      <div className="page-failure__body">
        <p className="page-failure__title">{title}</p>
        <p className="page-failure__text">{message}</p>
        <div className="page-failure__actions">
          {onRetry && <button type="button" className="btn btn--outline btn--small" onClick={onRetry}>Try again</button>}
          {children}
        </div>
      </div>
    </div>
  );
}
