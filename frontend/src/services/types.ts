// Shapes of the local FastAPI backend's JSON (see backend/app/models/chat_models.py).

export const CHAT_STATUSES = [
  "conversation", "answered", "cannot_answer", "needs_clarification", "classification_failed",
  "conversation_expired", "conversation_busy",
  "rejected", "generation_failed", "query_failed",
  "invalid_request", "busy", "unavailable", "timeout", "error",
] as const;

export type ChatStatus = (typeof CHAT_STATUSES)[number];

/** A result cell: DECIMAL arrives as an exact string, dates as ISO strings, NULL as null. */
export type Cell = string | number | boolean | null;

export interface SqlAttempt {
  attempt: number;
  purpose: "initial" | "correction";
  outcome: "sql" | "cannot_answer" | "needs_clarification" | "generation_failed";
  sql: string | null;
  validation: "passed" | "rejected" | "not_run";
  execution: "succeeded" | "failed" | "not_run";
  error_code: string | null;
  retryable: boolean;
  llm_ms: number | null;
  validation_ms: number | null;
  database_ms: number | null;
}

export interface ChartAxis {
  label: string;
  fields: string[];
  /** month: x values are "YYYY-MM" (from DATE_FORMAT(col, '%Y-%m')). */
  kind: "category" | "date" | "month" | "number";
}

/** Views the backend allows for a result (chart_builder.py decides; the client never adds others). */
export type ChartViewKind = "bar_vertical" | "bar_horizontal" | "line" | "pie";

export interface ChartPoint {
  x: string;
  y: number;
  /** The exact value from rows, e.g. the Decimal string "2663.415600". */
  y_exact: string | number;
  row_index: number;
}

export interface ChartSpec {
  /** Data shape: bar = categories, line = a date or month series. */
  type: "bar" | "line";
  views: ChartViewKind[];
  default_view: ChartViewKind;
  x: ChartAxis;
  y: ChartAxis;
  /** Why a view is not offered, by view name (explanations only). */
  notes?: Partial<Record<ChartViewKind, string>>;
  points: ChartPoint[];
  point_count: number;
}

export interface Timings {
  routing_ms: number | null;
  context_ms: number | null;
  llm_ms: number | null;
  validation_ms: number | null;
  database_ms: number | null;
  correction_ms: number | null;
  total_ms: number | null;
}

export interface ChatResponse {
  status: ChatStatus;
  message: string;
  question: string | null;
  conversation_id: string | null;
  resolved_question: string | null;
  code: string | null;
  sql: string | null;
  sql_executed: boolean;
  sql_reached_database: boolean;
  attempt_count: number;
  correction_attempted: boolean;
  sql_attempts: SqlAttempt[];
  columns: string[];
  rows: Cell[][];
  row_count: number;
  truncated: boolean;
  timings_ms: Timings;
  source_chunk_ids: string[];
  chart: ChartSpec | null;
  /** Redraw request for the previous result: the view to show it in (no query ran). */
  view_request?: ChartViewKind | null;
}

/** Outcome of one POST /chat call as seen by the browser. */
export type ChatResult =
  | { kind: "response"; httpStatus: number; requestId: string | null; body: ChatResponse }
  | { kind: "network_error" }
  | { kind: "bad_response"; httpStatus: number; requestId: string | null };

export type ExportResult =
  | { ok: true; blob: Blob; filename: string; partial: boolean; rowCount: number | null; neutralizedCells: number }
  | { ok: false; message: string };
