import type { ChatResponse } from "../services/types";

export function chatResponse(overrides: Partial<ChatResponse> = {}): ChatResponse {
  return {
    status: "answered",
    message: "Here is the result.",
    question: "How many patients are registered?",
    conversation_id: "conv_aaaaaaaaaaaaaaaa",
    resolved_question: null,
    code: null,
    sql: "SELECT COUNT(*) AS patient_count FROM patients",
    sql_executed: true,
    sql_reached_database: true,
    attempt_count: 1,
    correction_attempted: false,
    sql_attempts: [{
      attempt: 1, purpose: "initial", outcome: "sql", sql: "SELECT COUNT(*) AS patient_count FROM patients",
      validation: "passed", execution: "succeeded", error_code: null, retryable: false,
      llm_ms: 1000, validation_ms: 2, database_ms: 3,
    }],
    columns: ["patient_count"],
    rows: [[50]],
    row_count: 1,
    truncated: false,
    timings_ms: { routing_ms: 1, context_ms: 2, llm_ms: 1000, validation_ms: 2, database_ms: 3, correction_ms: null, total_ms: 1800 },
    source_chunk_ids: [],
    chart: null,
    ...overrides,
  };
}

/** A fetch Response carrying a JSON body. */
export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "req-123", ...headers },
  });
}
