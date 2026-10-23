// Shows the SQL behind an answer and says plainly whether it ran, was rejected or failed.
import { useId, useState } from "react";
import type { ChatResponse, SqlAttempt } from "../services/types";
import { CheckIcon, CopyIcon } from "./Icons";

export interface SqlLabel {
  short: string;
  title: string;
  tone: "ok" | "danger" | "neutral";
}

export function sqlLabel(response: ChatResponse): SqlLabel {
  if (response.sql_executed) {
    return { short: "Executed · read-only", title: "Executed SQL · validated read-only SELECT", tone: "ok" };
  }
  const last = response.sql_attempts[response.sql_attempts.length - 1];
  if (last?.validation === "rejected") {
    return { short: "Rejected · not executed", title: "Rejected SQL · failed validation and was not executed", tone: "danger" };
  }
  if (last?.execution === "failed") {
    return response.sql_reached_database
      ? { short: "Failed in MySQL", title: "Failed SQL · sent to MySQL, which could not complete it", tone: "danger" }
      : { short: "Not executed", title: "Failed SQL · validated but could not be run", tone: "danger" };
  }
  return { short: "Not executed", title: "Generated SQL · not executed", tone: "neutral" };
}

function describeAttempt(a: SqlAttempt): string {
  const parts = [`Attempt ${a.attempt} (${a.purpose})`];
  if (a.outcome !== "sql") parts.push(`model outcome: ${a.outcome.replace(/_/g, " ")}`);
  if (a.validation !== "not_run") parts.push(`validation ${a.validation}`);
  if (a.execution !== "not_run") parts.push(`execution ${a.execution}`);
  if (a.error_code) parts.push(`code ${a.error_code}`);
  return parts.join(" · ");
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Fallback for browsers that block the async clipboard API.
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

export function hasSql(response: ChatResponse): boolean {
  return Boolean(response.sql) || response.sql_attempts.some((a) => a.sql);
}

export function SqlPanel({ response }: { response: ChatResponse }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  const panelId = useId();
  const label = sqlLabel(response);
  const sql = response.sql ?? [...response.sql_attempts].reverse().find((a) => a.sql)?.sql ?? "";
  const earlier = response.sql_attempts.length > 1 ? response.sql_attempts : [];

  const copy = async () => {
    setCopied((await copyText(sql)) ? "copied" : "failed");
    setTimeout(() => setCopied("idle"), 2000);
  };

  return (
    <div className="sql">
      <div className="sql__bar">
        <button type="button" className="btn btn--outline btn--small" aria-expanded={open} aria-controls={panelId}
          onClick={() => setOpen((v) => !v)}>
          {open ? "Hide SQL" : "View SQL"}
        </button>
        <span className={`sql__short sql__short--${label.tone}`}>{label.short}</span>
      </div>
      {open && (
        <div id={panelId} className={`sql__panel sql__panel--${label.tone}`}>
          <div className="sql__head">
            <span className="sql__title">{label.title}</span>
            <button type="button" className="btn btn--ghost btn--small" onClick={copy} disabled={!sql}>
              {copied === "copied" ? <CheckIcon size={16} /> : <CopyIcon size={16} />}
              {copied === "copied" ? "Copied" : copied === "failed" ? "Copy failed" : "Copy SQL"}
            </button>
          </div>
          <pre className="sql__code"><code>{sql}</code></pre>
          {earlier.length > 0 && (
            <div className="sql__attempts">
              <p className="sql__attempts-title">The first SQL failed, so one correction was attempted:</p>
              <ol>
                {earlier.map((a) => (
                  <li key={a.attempt}>
                    <span>{describeAttempt(a)}</span>
                    {a.sql && a.sql !== sql && (
                      <details>
                        <summary>Show attempt {a.attempt} SQL</summary>
                        <pre className="sql__code sql__code--small"><code>{a.sql}</code></pre>
                      </details>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
