import { forwardRef, type KeyboardEvent } from "react";
import { SendIcon } from "./Icons";

/** Backend default CHAT_MAX_QUESTION_CHARS; the server still answers 422 if its limit is lower. */
export const MAX_QUESTION_CHARS = 500;

interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (question: string) => void;
  /** A request from this conversation is waiting; sending again is blocked. */
  pending?: boolean;
  /** When set, the composer is disabled and this text explains why. */
  disabledReason?: string;
}

export function questionProblem(text: string): string | null {
  const length = text.trim().length;
  if (length === 0) return "empty";
  if (length > MAX_QUESTION_CHARS) return "too_long";
  return null;
}

export const Composer = forwardRef<HTMLTextAreaElement, ComposerProps>(function Composer(
  { value, onChange, onSend, pending = false, disabledReason }, ref,
) {
  const problem = questionProblem(value);
  const length = value.trim().length;
  const canSend = !pending && !disabledReason && problem === null;

  const send = () => {
    if (canSend) onSend(value.trim());
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      send();
    }
  };

  return (
    <div className="composer">
      <div className={`composer__box${problem === "too_long" ? " composer__box--error" : ""}`}>
        <textarea
          ref={ref}
          className="composer__input"
          rows={1}
          value={value}
          disabled={Boolean(disabledReason)}
          placeholder={pending ? "Preparing your answer…" : "Ask a question about hospital data…"}
          aria-label="Your question"
          aria-describedby="composer-help"
          aria-invalid={problem === "too_long"}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="button" className="composer__send" onClick={send} disabled={!canSend}
          aria-label={pending ? "Waiting for the answer" : "Send question"}>
          <SendIcon size={20} />
        </button>
      </div>
      <p id="composer-help" className="composer__help" aria-live="polite">
        {disabledReason ? (
          disabledReason
        ) : problem === "too_long" ? (
          <span className="text-danger">Questions can be at most {MAX_QUESTION_CHARS} characters ({length} now).</span>
        ) : (
          <>
            <span>Enter to send · Shift+Enter for a new line</span>
            {length > MAX_QUESTION_CHARS - 100 && <span className="composer__count">{length} / {MAX_QUESTION_CHARS}</span>}
          </>
        )}
      </p>
    </div>
  );
});
