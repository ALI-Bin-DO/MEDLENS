// Shapes of the read-only catalog and administration endpoints, copied from the backend's Pydantic
// models (backend/app/models/catalog_models.py and admin_models.py). Nothing here is inferred.

// --- GET /catalog/tables, GET /catalog/tables/{table_name} ---------------------------------------

export type KeyRole = "primary_key" | "unique" | "unique_part" | "foreign_key" | "indexed";

export interface TableSummary {
  name: string;
  display_name: string;
  /** From data_catalog.md, or null when the table is not documented. */
  description: string | null;
  grain: string | null;
  column_count: number;
}

export interface CatalogResponse {
  tables: TableSummary[];
}

export interface ColumnReference {
  table: string;
  column: string;
}

export interface ColumnInfo {
  name: string;
  description: string | null;
  data_type: string;
  /** ENUM values in declared order; null for other types. */
  allowed_values: string[] | null;
  nullable: boolean;
  key_roles: KeyRole[];
  references: ColumnReference | null;
}

export interface ForeignKeyInfo {
  name: string | null;
  columns: string[];
  references_table: string;
  references_columns: string[];
}

export interface IncomingReference {
  name: string | null;
  from_table: string;
  from_columns: string[];
  to_columns: string[];
}

export interface KeyInfo {
  name: string | null;
  columns: string[];
}

export interface TableDetail extends TableSummary {
  primary_key: string[];
  columns: ColumnInfo[];
  foreign_keys: ForeignKeyInfo[];
  referenced_by: IncomingReference[];
  unique_constraints: KeyInfo[];
  /** Non-unique indexes only. */
  indexes: KeyInfo[];
}

// --- GET /admin/metrics, GET /admin/activity ----------------------------------------------------

export type Window = "1h" | "24h" | "7d";

export interface Ratio {
  numerator: number;
  denominator: number;
  value: number | null;
}

export interface MetricsResponse {
  window: Window;
  window_start: string;
  window_end: string;
  total_requests: number;
  by_status: Record<string, number>;
  data_pipeline_requests: number;
  operational_answer_success: Ratio;
  data_pipeline_latency: { sample_count: number; median_ms: number | null; p95_ms: number | null };
  validation_rejections: number;
  correction_attempts: number;
  answered_after_correction: number;
  busy: number;
  timeouts: number;
}

export interface EventTimings {
  total_ms: number;
  routing_ms: number | null;
  context_ms: number | null;
  llm_ms: number | null;
  validation_ms: number | null;
  database_ms: number | null;
  correction_ms: number | null;
}

export interface ActivityEvent {
  request_id: string;
  timestamp: string;
  route: string | null;
  route_source: string | null;
  data_pipeline: boolean;
  http_status: number;
  status: string;
  code: string | null;
  timings: EventTimings;
  attempt_count: number;
  correction_attempted: boolean;
  sql_reached_database: boolean;
  sql_executed: boolean;
  row_count: number;
  truncated: boolean;
}

export interface ActivityResponse {
  window: Window;
  limit: number;
  offset: number;
  total: number;
  events: ActivityEvent[];
}

// --- GET /admin/system, GET /admin/config --------------------------------------------------------

export type ComponentState = "ok" | "degraded" | "unavailable" | "unknown" | "not_configured";

export interface ModelStatus {
  role: "sql" | "embedding";
  name: string | null;
  status: ComponentState;
  reason: string | null;
  installed: boolean | null;
  loaded: boolean | null;
  parameter_size: string | null;
  quantization: string | null;
  /** Always false: the status check never generates text or embeds anything. */
  inference_verified: boolean;
}

export interface IndexStatus {
  status: ComponentState;
  reason: string | null;
  collection: string | null;
  points: number | null;
  counts_by_type: Record<string, number> | null;
  vector_size: number | null;
  expected_vector_size: number | null;
  vector_size_matches: boolean | null;
  stored_embedding_model: string | null;
  expected_embedding_model: string | null;
  embedding_model_matches: boolean | null;
  schema_matches_schema_sql: boolean | null;
}

export interface SystemStatusResponse {
  checked_at: string;
  cache_ttl_seconds: number;
  components: {
    api: { status: ComponentState; version: string; started_at: string; uptime_seconds: number };
    ollama: { status: ComponentState; reason: string | null; version: string | null; loaded_models: string[] };
    models: { sql: ModelStatus; embedding: ModelStatus };
    mysql: { status: ComponentState; reason: string | null; check: string; latency_ms: number | null };
    index: IndexStatus;
  };
}

export interface AdminConfigResponse {
  models: Record<string, string | null>;
  rag: Record<string, string | number>;
  sql_generation: Record<string, number>;
  query_execution: Record<string, number>;
  charts: Record<string, number>;
  monitoring: Record<string, number>;
}

/** Outcome of one GET: the parsed body, or why there is none (HTTP status kept for 404 vs 503). */
export type Loaded<T> =
  | { ok: true; data: T }
  | { ok: false; kind: "not_found" | "unavailable" | "network" | "bad_response"; status: number | null; message: string };
