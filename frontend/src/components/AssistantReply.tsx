// One MedLens reply. The text is the backend's own message, rendered as plain text; the
// heading above it is a fixed label for the status, never a model-written summary.
import type { AssistantMessage } from "../state/chatState";
import type { ChartSpec, ChatStatus } from "../services/types";
import { LensIcon } from "./Brand";
import { AlertIcon } from "./Icons";
import { ChartView } from "./ChartView";
import { ResultCard, type ToastMessage } from "./ResultCard";
import { SqlPanel, hasSql } from "./SqlPanel";

type Tone = "plain" | "info" | "warning" | "danger";

interface StatusView {
  heading: string | null;
  tone: Tone;
  /** Offer "Edit my question" (puts the question back in the composer; nothing is sent). */
  editable: boolean;
  /** Show the request ID so the server log entry can be found. */
  showRequestId?: boolean;
}

export const STATUS_VIEWS: Record<ChatStatus, StatusView> = {
  conversation: { heading: null, tone: "plain", editable: false },
  answered: { heading: null, tone: "plain", editable: false },
  needs_clarification: { heading: "Clarification needed", tone: "info", editable: true },
  cannot_answer: { heading: "Can't answer this question", tone: "warning", editable: true },
  classification_failed: { heading: "Request not understood", tone: "warning", editable: true },
  rejected: { heading: "Read-only protection", tone: "danger", editable: true },
  generation_failed: { heading: "No usable answer was generated", tone: "danger", editable: true, showRequestId: true },
  query_failed: { heading: "The query failed", tone: "danger", editable: true, showRequestId: true },
  conversation_expired: { heading: "Conversation memory expired", tone: "warning", editable: false },
  conversation_busy: { heading: "This conversation is busy", tone: "warning", editable: true },
  busy: { heading: "MedLens is busy", tone: "warning", editable: true },
  unavailable: { heading: "Service unavailable", tone: "danger", editable: true, showRequestId: true },
  timeout: { heading: "Timed out", tone: "danger", editable: true, showRequestId: true },
  invalid_request: { heading: "Question not accepted", tone: "warning", editable: true },
  error: { heading: "Unexpected error", tone: "danger", editable: true, showRequestId: true },
};

const NOT_RETRIED = new Set<ChatStatus>(["busy", "conversation_busy", "timeout", "unavailable", "error"]);

interface Props {
  message: AssistantMessage;
  onEditQuestion: (question: string) => void;
  notify: (toast: ToastMessage) => void;
  /** The chart of the latest earlier answer in this conversation (used to redraw it on request). */
  previousChart?: ChartSpec | null;
}

export function AssistantReply({ message, onEditQuestion, notify, previousChart }: Props) {
  const { result } = message;
  let view: StatusView;
  let text: string;
  let requestId: string | null = null;
  let note: string | null = null;
  const body = result.kind === "response" ? result.body : null;

  if (result.kind === "network_error") {
    view = { heading: "No response from the server", tone: "danger", editable: true };
    text = "The browser could not get a response from the MedLens server. Check that the backend is running, then send the question again.";
    note = "It was not retried automatically.";
  } else if (result.kind === "bad_response") {
    view = { heading: "Unexpected response", tone: "danger", editable: true, showRequestId: true };
    text = `The server returned a response this page could not read (HTTP ${result.httpStatus}).`;
    requestId = result.requestId;
  } else {
    view = STATUS_VIEWS[result.body.status];
    text = result.body.message;
    requestId = result.requestId;
    if (NOT_RETRIED.has(result.body.status)) note = "It was not retried automatically.";
  }

  const interpreted = body?.resolved_question && body.resolved_question !== body.question ? body.resolved_question : null;

  return (
    <article className={`reply reply--${view.tone}`} aria-label="MedLens reply">
      <p className="speaker speaker--assistant"><LensIcon size={18} handle="var(--color-accent)" /> MEDLENS</p>

      {view.heading && <h2 className="reply__heading">{view.tone === "danger" && <AlertIcon size={18} />}{view.heading}</h2>}

      {interpreted && (
        <details className="interpreted">
          <summary>Interpreted question</summary>
          <p>{interpreted}</p>
        </details>
      )}

      {body?.status === "rejected" ? (
        <div className="alert alert--danger">
          <p>{text}</p>
          {body.code && <p className="alert__code">Validation code: <code>{body.code}</code></p>}
        </div>
      ) : (
        <p className="reply__text">{text}</p>
      )}

      {note && <p className="reply__note">{note}</p>}

      {body?.status === "answered" && <ResultCard response={body} notify={notify} />}

      {body?.view_request && previousChart && previousChart.views.includes(body.view_request) && (
        <div className="result">
          <ChartView chart={previousChart} initialView={body.view_request} />
        </div>
      )}

      {body && hasSql(body) && <SqlPanel response={body} />}

      {(view.editable || (view.showRequestId && requestId)) && (
        <div className="reply__actions">
          {view.editable && (
            <button type="button" className="btn btn--outline btn--small" onClick={() => onEditQuestion(message.question)}>
              Edit my question
            </button>
          )}
          {view.showRequestId && requestId && <span className="reply__request-id">Request ID: <code>{requestId}</code></span>}
        </div>
      )}
    </article>
  );
}
