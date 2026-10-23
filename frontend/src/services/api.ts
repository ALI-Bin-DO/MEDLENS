// The only module that talks to the backend. Paths are relative: the Vite dev server proxies
// them to the local FastAPI app (vite.config.ts). No timeouts and no automatic retries: a
// question can legitimately take minutes while the local model loads or corrects its SQL.
import { CHAT_STATUSES, type Cell, type ChatResponse, type ChatResult, type ExportResult } from "./types";

const DEFAULT_TIMINGS = {
  routing_ms: null, context_ms: null, llm_ms: null, validation_ms: null,
  database_ms: null, correction_ms: null, total_ms: null,
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Accept a body only if it has the ChatResponse essentials; fill defaults for omitted fields. */
export function parseChatResponse(value: unknown): ChatResponse | null {
  if (!isRecord(value)) return null;
  if (typeof value.status !== "string" || !(CHAT_STATUSES as readonly string[]).includes(value.status)) return null;
  if (typeof value.message !== "string") return null;
  const columns = Array.isArray(value.columns) ? (value.columns as string[]) : [];
  const rows = Array.isArray(value.rows) ? (value.rows as Cell[][]) : [];
  return {
    question: null, conversation_id: null, resolved_question: null, code: null, sql: null,
    sql_executed: false, sql_reached_database: false, attempt_count: 0, correction_attempted: false,
    sql_attempts: [], row_count: rows.length, truncated: false, source_chunk_ids: [], chart: null, view_request: null,
    ...(value as Partial<ChatResponse>),
    status: value.status as ChatResponse["status"],
    message: value.message,
    columns,
    rows,
    timings_ms: { ...DEFAULT_TIMINGS, ...(isRecord(value.timings_ms) ? value.timings_ms : {}) },
  };
}

export async function postChat(question: string, conversationId: string | null): Promise<ChatResult> {
  const payload: { question: string; conversation_id?: string } = { question };
  if (conversationId) payload.conversation_id = conversationId;
  let response: Response;
  try {
    response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    return { kind: "network_error" };
  }
  const requestId = response.headers.get("X-Request-ID");
  let body: ChatResponse | null = null;
  try {
    body = parseChatResponse(await response.json());
  } catch {
    body = null;
  }
  if (!body) return { kind: "bad_response", httpStatus: response.status, requestId };
  return { kind: "response", httpStatus: response.status, requestId, body };
}

/** Best effort: forget a server-side conversation. Never throws. */
export async function deleteConversation(conversationId: string): Promise<void> {
  try {
    await fetch(`/chat/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE", keepalive: true });
  } catch {
    // Sign-out must not depend on this; the server also expires idle conversations.
  }
}

/** Read the filename from `attachment; filename="..."`; keep only safe characters. */
export function filenameFromDisposition(header: string | null): string {
  const match = header?.match(/filename="?([^";]+)"?/i);
  const name = match?.[1]?.replace(/[^A-Za-z0-9._-]/g, "") ?? "";
  return name.toLowerCase().endsWith(".csv") ? name : "medlens-export.csv";
}

export async function exportCsv(columns: string[], rows: Cell[][], truncated: boolean): Promise<ExportResult> {
  let response: Response;
  try {
    response = await fetch("/export/csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ columns, rows, truncated }),
    });
  } catch {
    return { ok: false, message: "The browser could not reach the MedLens server." };
  }
  if (!response.ok) {
    let message = `The export failed (HTTP ${response.status}).`;
    try {
      const body: unknown = await response.json();
      if (isRecord(body) && typeof body.message === "string") message = body.message;
    } catch {
      // Keep the generic message.
    }
    return { ok: false, message };
  }
  const rowCount = Number(response.headers.get("X-MedLens-Row-Count"));
  return {
    ok: true,
    blob: await response.blob(),
    filename: filenameFromDisposition(response.headers.get("Content-Disposition")),
    partial: response.headers.get("X-MedLens-Partial") === "true",
    rowCount: Number.isFinite(rowCount) && response.headers.has("X-MedLens-Row-Count") ? rowCount : null,
    neutralizedCells: Number(response.headers.get("X-MedLens-Neutralized-Cells")) || 0,
  };
}

/** Save a blob through a temporary object URL, revoked right after the download starts. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
