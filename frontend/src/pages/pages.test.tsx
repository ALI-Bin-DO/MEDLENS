// Data catalog and administration pages with a mocked fetch (controlled loading, error and empty states).
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "../App";
import { chatResponse, jsonResponse } from "../test/fixtures";

vi.mock("plotly.js-basic-dist-min", () => ({ default: { newPlot: vi.fn().mockResolvedValue(undefined), purge: vi.fn() } }));

const TABLES = ["patients", "doctors", "appointments", "treatments", "billing"].map((name, i) => ({
  name, display_name: name.charAt(0).toUpperCase() + name.slice(1),
  description: i === 3 ? null : `Stores ${name}.`, grain: `One row per ${name.slice(0, -1)}.`, column_count: 5 + i,
}));

const BILLING = {
  ...TABLES[4], primary_key: ["bill_id"],
  columns: [
    { name: "bill_id", description: "Unique bill identifier.", data_type: "VARCHAR(10)", allowed_values: null, nullable: false,
      key_roles: ["primary_key"], references: null },
    { name: "patient_id", description: null, data_type: "VARCHAR(10)", allowed_values: null, nullable: false,
      key_roles: ["foreign_key"], references: { table: "patients", column: "patient_id" } },
    { name: "bill_date", description: "Date the bill was issued.", data_type: "DATE", allowed_values: null, nullable: false,
      key_roles: ["indexed"], references: null },
    { name: "payment_status", description: "Paid, Pending, or Failed.", data_type: "ENUM", allowed_values: ["Paid", "Pending", "Failed"],
      nullable: false, key_roles: [], references: null },
  ],
  foreign_keys: [{ name: "fk_billing_patient", columns: ["patient_id"], references_table: "patients", references_columns: ["patient_id"] }],
  referenced_by: [], unique_constraints: [], indexes: [{ name: "idx_billing_date", columns: ["bill_date"] }],
};

const PATIENTS = {
  ...TABLES[0], primary_key: ["patient_id"],
  columns: [{ name: "patient_id", description: "Unique patient identifier.", data_type: "VARCHAR(10)", allowed_values: null,
    nullable: false, key_roles: ["primary_key"], references: null }],
  foreign_keys: [], unique_constraints: [{ name: "uq_patients_insurance_number", columns: ["insurance_number"] }], indexes: [],
  referenced_by: [{ name: "fk_billing_patient", from_table: "billing", from_columns: ["patient_id"], to_columns: ["patient_id"] }],
};

const METRICS = {
  window: "24h", window_start: "2026-10-22T02:00:00+00:00", window_end: "2026-10-23T02:00:00+00:00", total_requests: 12,
  by_status: { answered: 6, conversation: 2, needs_clarification: 2, rejected: 1, timeout: 1, error: 0 },
  data_pipeline_requests: 8, operational_answer_success: { numerator: 6, denominator: 8, value: 0.75 },
  data_pipeline_latency: { sample_count: 8, median_ms: 3046.3, p95_ms: 11314.2 }, validation_rejections: 1,
  correction_attempts: 2, answered_after_correction: 1, busy: 0, timeouts: 1,
};

const model = (role: string, name: string, loaded: boolean) => ({
  role, name, status: "ok", reason: loaded ? "installed_loaded" : "installed_not_loaded", installed: true, loaded,
  parameter_size: role === "sql" ? "4.2B" : "137M", quantization: "Q4_K_M", inference_verified: false,
});

const SYSTEM = {
  checked_at: "2026-10-23T02:22:05+00:00", cache_ttl_seconds: 15,
  components: {
    api: { status: "ok", version: "0.1.0", started_at: "2026-10-23T02:08:33+00:00", uptime_seconds: 812 },
    ollama: { status: "ok", reason: null, version: "0.40.8", loaded_models: ["qwen3.5:4b"] },
    models: { sql: model("sql", "qwen3.5:4b", true), embedding: model("embedding", "nomic-embed-text:v1.5", false) },
    mysql: { status: "ok", reason: null, check: "connectivity", latency_ms: 61 },
    index: { status: "ok", reason: null, collection: "medlens_knowledge", points: 268,
      counts_by_type: { schema: 5, documentation: 13, example: 250 }, vector_size: 768, expected_vector_size: 768,
      vector_size_matches: true, stored_embedding_model: "nomic-embed-text:v1.5", expected_embedding_model: "nomic-embed-text:v1.5",
      embedding_model_matches: true, schema_matches_schema_sql: true },
  },
};

const CONFIG = {
  models: { sql_model: "qwen3.5:4b", embedding_model: "nomic-embed-text:v1.5" },
  rag: { context_strategy: "full schema + retrieved documentation/examples", retrieved_top_k: 5 },
  sql_generation: { num_ctx: 4096, num_predict: 512, generation_timeout_seconds: 180, max_corrections: 1 },
  query_execution: { max_execution_ms: 5000, row_cap: 200 }, charts: { max_points: 50 },
  monitoring: { retention_days: 30, max_events: 50000 },
};

const event = (n: number) => ({
  request_id: `req${String(n).padStart(29, "0")}`, timestamp: "2026-10-23T02:19:17+00:00", route: "data_question",
  route_source: "classifier", data_pipeline: true, http_status: 200, status: "answered", code: null,
  timings: { total_ms: 2226.2, routing_ms: 1281, context_ms: 24, llm_ms: 858.9, validation_ms: 0.9, database_ms: 55.9, correction_ms: null },
  attempt_count: 1, correction_attempted: false, sql_reached_database: true, sql_executed: true, row_count: 5, truncated: false,
});

type Routes = Record<string, () => Response | Promise<Response>>;

function mockApi(routes: Routes) {
  const fetchMock = vi.fn((url: string) => {
    const key = Object.keys(routes).find((k) => url === k || url.startsWith(`${k}?`));
    return key ? Promise.resolve(routes[key]()) : Promise.reject(new Error(`unexpected request ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const urls = (fetchMock: ReturnType<typeof mockApi>) => fetchMock.mock.calls.map(([url]) => String(url));

async function signIn(user: ReturnType<typeof userEvent.setup>) {
  render(<App />);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("data catalog (mocked API)", () => {
  it("lists the tables, filters them and opens a table with its keys and relationships", async () => {
    const fetchMock = mockApi({
      "/catalog/tables": () => jsonResponse({ tables: TABLES }),
      "/catalog/tables/billing": () => jsonResponse(BILLING),
      "/catalog/tables/patients": () => jsonResponse(PATIENTS),
    });
    const user = userEvent.setup();
    await signIn(user);
    await user.click(screen.getByRole("button", { name: "Data catalog" }));
    const list = await screen.findByRole("list", { name: "Tables" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByText("5 tables available · Read-only access")).toBeInTheDocument();
    expect(within(list).getByText("No description available")).toBeInTheDocument();  // treatments: null description

    await user.type(screen.getByLabelText("Filter tables"), "bill");
    expect(within(screen.getByRole("list", { name: "Tables" })).getAllByRole("listitem")).toHaveLength(1);
    await user.clear(screen.getByLabelText("Filter tables"));
    await user.type(screen.getByLabelText("Filter tables"), "zzz");
    expect(screen.getByText("No table matches “zzz”.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Filter tables"));

    await user.click(screen.getByRole("button", { name: /Billing/ }));
    expect(await screen.findByRole("heading", { level: 1, name: "Billing" })).toHaveFocus();
    expect(screen.getByText("Primary key", { selector: ".role-chip" })).toBeInTheDocument();
    expect(screen.getByText("Indexed", { selector: ".role-chip" })).toBeInTheDocument();
    expect(screen.getByLabelText("Allowed values: Paid, Pending, Failed")).toBeInTheDocument();
    expect(screen.getByText("idx_billing_date")).toBeInTheDocument();
    expect(screen.getByText("None besides the primary key.")).toBeInTheDocument();
    expect(screen.getByText("No other table references this one.")).toBeInTheDocument();
    expect(screen.getAllByText("No description available").length).toBeGreaterThan(0);  // patient_id: null description

    await user.click(screen.getAllByRole("button", { name: "patients" })[0]);
    expect(await screen.findByRole("heading", { level: 1, name: "Patients" })).toBeInTheDocument();
    expect(screen.getByText("uq_patients_insurance_number")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "billing" })).toBeInTheDocument();  // referenced by

    expect(urls(fetchMock).every((u) => u.startsWith("/catalog/"))).toBe(true);  // no chat, no model, no SQL
  });

  it("shows unavailable and unknown-table states with a working retry", async () => {
    let fail = true;
    mockApi({
      "/catalog/tables": () => (fail ? jsonResponse({ status: "unavailable", message: "Catalog sources unreadable." }, 503)
        : jsonResponse({ tables: TABLES })),
      "/catalog/tables/doctors": () => jsonResponse({ status: "not_found", message: "No table with that name." }, 404),
    });
    const user = userEvent.setup();
    await signIn(user);
    await user.click(screen.getByRole("button", { name: "Data catalog" }));
    expect(await screen.findByText("The data catalog is unavailable")).toBeInTheDocument();
    expect(screen.getByText("Catalog sources unreadable.")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await user.click(await screen.findByRole("button", { name: /Doctors/ }));
    expect(await screen.findByText("There is no table called “doctors”")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "← Data catalog" }));
    expect(await screen.findByRole("list", { name: "Tables" })).toBeInTheDocument();
  });

  it("keeps the session's conversations when moving between chat and catalog", async () => {
    const fetchMock = mockApi({
      "/chat": () => jsonResponse(chatResponse({ message: "There are 50 patients." })),
      "/catalog/tables": () => jsonResponse({ tables: TABLES }),
    });
    const user = userEvent.setup();
    await signIn(user);
    await user.type(screen.getByRole("textbox", { name: /question/i }), "How many patients are registered?");
    await user.click(screen.getByRole("button", { name: /send/i }));
    expect(await screen.findByText("There are 50 patients.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Data catalog" }));
    await screen.findByRole("list", { name: "Tables" });
    await user.click(screen.getByRole("button", { name: "System administration" }));
    await user.click(screen.getByRole("button", { name: /How many patients are registered/ }));
    expect(screen.getByText("There are 50 patients.")).toBeInTheDocument();
    expect(urls(fetchMock).filter((u) => u === "/chat")).toHaveLength(1);
  });
});

describe("system administration (mocked API)", () => {
  it("loads only the open tab, labels operational success and survives a failing component", async () => {
    const fetchMock = mockApi({
      "/admin/metrics": () => jsonResponse(METRICS),
      "/admin/system": () => jsonResponse({ detail: "boom" }, 500),
      "/admin/config": () => jsonResponse(CONFIG),
      "/admin/activity": () => jsonResponse({ window: "24h", limit: 25, offset: 0, total: 0, events: [] }),
    });
    const user = userEvent.setup();
    await signIn(user);
    await user.click(screen.getByRole("button", { name: "System administration" }));
    expect(await screen.findByText("75.0%")).toBeInTheDocument();
    expect(screen.getByText("6 answered / 8 data-pipeline requests")).toBeInTheDocument();
    expect(screen.getByText(/does not measure whether\s+the answer is correct/)).toBeInTheDocument();
    expect(screen.queryByText(/accuracy/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Reporting period:/)).toBeInTheDocument();
    expect(await screen.findByText("Component status is unavailable")).toBeInTheDocument();  // metrics stay visible
    expect(screen.getByText("75.0%")).toBeInTheDocument();
    expect(urls(fetchMock).some((u) => u.startsWith("/admin/config") || u.startsWith("/admin/activity"))).toBe(false);

    await user.click(screen.getByRole("tab", { name: "Query Activity" }));
    expect(await screen.findByText("No requests in this period.")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Configuration" }));
    expect(await screen.findByText("4096")).toBeInTheDocument();
    expect(screen.getByText("Retrieval (RAG)")).toBeInTheDocument();
    const panel = screen.getByRole("tabpanel");
    expect(within(panel).queryByRole("textbox")).not.toBeInTheDocument();
    expect(within(panel).queryByRole("button")).not.toBeInTheDocument();  // read-only: no save, restart or re-index
    expect(urls(fetchMock).some((u) => u.startsWith("/chat"))).toBe(false);
  });

  it("shows installed-but-not-loaded models as normal and never claims verified inference", async () => {
    mockApi({ "/admin/metrics": () => jsonResponse(METRICS), "/admin/system": () => jsonResponse(SYSTEM) });
    const user = userEvent.setup();
    await signIn(user);
    await user.click(screen.getByRole("button", { name: "System administration" }));
    await user.click(screen.getByRole("tab", { name: "Models & RAG" }));
    const embedding = await screen.findByRole("region", { name: "Embedding model" });
    expect(within(embedding).getByText("No — loads on first use")).toBeInTheDocument();
    expect(within(embedding).getByText("OK")).toBeInTheDocument();
    expect(screen.getAllByText("Not checked").length).toBe(2);
    expect(screen.getByText("268")).toBeInTheDocument();
    expect(screen.getByText(/freshness of the documentation and\s+example chunks is not checked/)).toBeInTheDocument();
    expect(screen.getByText(/reuses a check for up to 15 s/)).toBeInTheDocument();
  });

  it("pages through activity with the supported limit and offset, metadata only", async () => {
    const fetchMock = mockApi({
      "/admin/metrics": () => jsonResponse(METRICS),
      "/admin/system": () => jsonResponse(SYSTEM),
      "/admin/activity": () => jsonResponse({ window: "24h", limit: 25, offset: 0, total: 30,
        events: Array.from({ length: 25 }, (_, i) => event(i)) }),
    });
    const user = userEvent.setup();
    await signIn(user);
    await user.click(screen.getByRole("button", { name: "System administration" }));
    await user.click(screen.getByRole("tab", { name: "Query Activity" }));
    expect(await screen.findByText("1–25 of 30 requests")).toBeInTheDocument();
    expect(screen.queryByText(/Question/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Older" }));
    await waitFor(() => expect(urls(fetchMock)).toContain("/admin/activity?window=24h&limit=25&offset=25"));
  });

  it("switches tabs with the arrow keys", async () => {
    mockApi({ "/admin/metrics": () => jsonResponse(METRICS), "/admin/system": () => jsonResponse(SYSTEM),
      "/admin/config": () => jsonResponse(CONFIG) });
    const user = userEvent.setup();
    await signIn(user);
    await user.click(screen.getByRole("button", { name: "System administration" }));
    screen.getByRole("tab", { name: "Overview" }).focus();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Configuration" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Configuration" })).toHaveFocus();
  });
});
