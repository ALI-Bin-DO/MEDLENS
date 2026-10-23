// System administration for the local demo: Overview | Models & RAG | Query Activity | Configuration.
// Every value comes from the read-only admin endpoints (GET /admin/metrics, /admin/activity, /admin/system,
// /admin/config). A tab loads when it is opened and on "Refresh"; nothing polls. A failing endpoint only
// affects the part of the page that needs it. Monitoring holds request metadata only: no questions, SQL,
// rows or conversation IDs exist in these responses, so none are shown. This page has no authentication
// or role checks; it is reachable by anyone who can reach the local API.
import { useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { Failure, Loading } from "../components/PageStatus";
import { getActivity, getConfig, getMetrics, getSystem } from "../services/adminApi";
import type {
  ActivityResponse, AdminConfigResponse, ComponentState, Loaded, MetricsResponse, ModelStatus, SystemStatusResponse, Window,
} from "../services/adminTypes";

export type AdminTab = "overview" | "models" | "activity" | "config";

const TABS: { id: AdminTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "models", label: "Models & RAG" },
  { id: "activity", label: "Query Activity" },
  { id: "config", label: "Configuration" },
];

const WINDOWS: { id: Window; label: string }[] = [
  { id: "1h", label: "Last hour" },
  { id: "24h", label: "24 hours" },
  { id: "7d", label: "7 days" },
];

const STATE_LABELS: Record<ComponentState, string> = {
  ok: "OK", degraded: "Degraded", unavailable: "Unavailable", unknown: "Unknown", not_configured: "Not configured",
};

const STATUS_LABELS: Record<string, string> = {
  answered: "Answered", conversation: "Conversation", needs_clarification: "Clarification needed",
  cannot_answer: "Cannot answer", rejected: "Rejected by validation", generation_failed: "Generation failed",
  query_failed: "Query failed", classification_failed: "Classification failed", invalid_request: "Invalid request",
  conversation_expired: "Conversation expired", conversation_busy: "Conversation busy", busy: "Busy",
  unavailable: "Unavailable", timeout: "Timeout", error: "Error",
};

/** Labels for configuration keys whose snake_case form reads poorly (the raw key stays in a tooltip). */
const KEY_LABELS: Record<string, string> = {
  sql_model: "SQL model",
  embedding_model: "Embedding model",
  context_strategy: "Context strategy",
  retrieved_top_k: "Retrieved chunks (top k)",
  num_ctx: "Context window (tokens)",
  num_predict: "Max output tokens",
  generation_timeout_seconds: "Generation timeout (s)",
  max_corrections: "Max SQL corrections",
  max_execution_ms: "Max execution time (ms)",
  row_cap: "Row cap",
  max_points: "Max chart points",
  retention_days: "Retention (days)",
  max_events: "Max stored events",
};

/** Readable text for snake_case keys and codes; the raw key stays available as a tooltip. */
export function humanize(key: string): string {
  if (KEY_LABELS[key]) return KEY_LABELS[key];
  const text = key.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(ms >= 10000 ? 1 : 2)} s` : `${Math.round(ms)} ms`;
}

function formatTime(iso: string, withDate = true): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, withDate
    ? { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }
    : { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDuration(seconds: number): string {
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${m} min` : m ? `${m} min ${s % 60} s` : `${s} s`;
}

function useLoaded<T>(load: () => Promise<Loaded<T>>, deps: unknown[]) {
  const [state, setState] = useState<Loaded<T> | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setState(null);
    void load().then((r) => {
      if (!cancelled) setState(r);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt]);
  return { state, reload: useCallback(() => setAttempt((n) => n + 1), []) };
}

interface Props {
  tab: AdminTab;
  onTab: (tab: AdminTab) => void;
}

export function AdminPage({ tab, onTab }: Props) {
  const [window, setWindow] = useState<Window>("24h");
  const [refresh, setRefresh] = useState(0);
  const tabsId = useId();

  return (
    <div className="page page--wide">
      <div className="admin__head">
        <div>
          <h1 className="page__title">System administration</h1>
          <p className="page__subtitle">Local MedLens deployment · Operational status from the running API.</p>
        </div>
        <button type="button" className="btn btn--outline btn--small" onClick={() => setRefresh((n) => n + 1)}>Refresh</button>
      </div>
      <p className="admin__notice">
        Read-only view of this local deployment. No authentication or role checks protect it; anyone who can reach the
        local API can read it. It shows request metadata only, never questions, SQL or results.
      </p>

      <div className="tabs" role="tablist" aria-label="Administration sections"
        onKeyDown={(e) => {
          const i = TABS.findIndex((t) => t.id === tab);
          const next = e.key === "ArrowRight" ? (i + 1) % TABS.length : e.key === "ArrowLeft" ? (i - 1 + TABS.length) % TABS.length
            : e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : -1;
          if (next < 0) return;
          e.preventDefault();
          onTab(TABS[next].id);
          document.getElementById(`${tabsId}-${TABS[next].id}`)?.focus();
        }}>
        {TABS.map((t) => (
          <button key={t.id} id={`${tabsId}-${t.id}`} type="button" role="tab" className="tabs__tab"
            aria-selected={t.id === tab} aria-controls={`${tabsId}-panel`} tabIndex={t.id === tab ? 0 : -1}
            onClick={() => onTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      <div id={`${tabsId}-panel`} role="tabpanel" aria-labelledby={`${tabsId}-${tab}`} className="admin__panel">
        {tab === "overview" && <Overview window={window} onWindow={setWindow} refresh={refresh} />}
        {tab === "models" && <ModelsRag refresh={refresh} />}
        {tab === "activity" && <Activity window={window} onWindow={setWindow} refresh={refresh} />}
        {tab === "config" && <Configuration refresh={refresh} />}
      </div>
    </div>
  );
}

function WindowPicker({ window, onWindow }: { window: Window; onWindow: (w: Window) => void }) {
  return (
    <div className="segmented" role="group" aria-label="Reporting period">
      {WINDOWS.map((w) => (
        <button key={w.id} type="button" className="segmented__btn" aria-pressed={w.id === window} onClick={() => onWindow(w.id)}>
          {w.label}
        </button>
      ))}
    </div>
  );
}

function StateBadge({ state }: { state: ComponentState }) {
  return <span className={`state-badge state-badge--${state}`}>{STATE_LABELS[state] ?? state}</span>;
}

function Stat({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return (
    <div className="stat">
      <p className="stat__label">{label}</p>
      <p className="stat__value">{value}</p>
      {detail && <p className="stat__detail">{detail}</p>}
    </div>
  );
}

// --- Overview --------------------------------------------------------------------------------------

function Overview({ window, onWindow, refresh }: { window: Window; onWindow: (w: Window) => void; refresh: number }) {
  const metrics = useLoaded(() => getMetrics(window), [window, refresh]);
  const system = useLoaded(getSystem, [refresh]);
  return (
    <>
      <div className="admin__toolbar">
        <WindowPicker window={window} onWindow={onWindow} />
        {metrics.state?.ok && (
          <p className="muted">
            Reporting period: {formatTime(metrics.state.data.window_start)} – {formatTime(metrics.state.data.window_end)} (local time)
          </p>
        )}
      </div>
      {metrics.state === null && <Loading label="Loading request metrics…" />}
      {metrics.state && !metrics.state.ok && (
        <Failure title="Request metrics are unavailable" message={metrics.state.message} onRetry={metrics.reload} />
      )}
      {metrics.state?.ok && <MetricsView m={metrics.state.data} />}

      <h2 className="section-title">Component health</h2>
      {system.state === null && <Loading label="Checking components…" />}
      {system.state && !system.state.ok && (
        <Failure title="Component status is unavailable" message={system.state.message} onRetry={system.reload} />
      )}
      {system.state?.ok && <Health s={system.state.data} />}
    </>
  );
}

function MetricsView({ m }: { m: MetricsResponse }) {
  const success = m.operational_answer_success;
  const statuses = Object.entries(m.by_status).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...statuses.map(([, n]) => n));
  return (
    <>
      <div className="stats">
        <Stat label="Chat requests" value={m.total_requests} detail="All requests in the period, every route" />
        <Stat label="Answered data requests" value={success.value === null ? "—" : `${(success.value * 100).toFixed(1)}%`}
          detail={`${success.numerator} answered / ${success.denominator} data-pipeline requests`} />
        <Stat label="Median response time" value={formatMs(m.data_pipeline_latency.median_ms)}
          detail={`p95 ${formatMs(m.data_pipeline_latency.p95_ms)} · ${m.data_pipeline_latency.sample_count} data-pipeline requests, any outcome`} />
        <Stat label="Rejected by validation" value={m.validation_rejections}
          detail={`Final status rejected · of ${m.data_pipeline_requests} data-pipeline requests`} />
      </div>
      <p className="admin__footnote">
        “Answered” is operational: a validated read-only query ran and returned a result. It does not measure whether
        the answer is correct. Greetings and other non-data requests are excluded from that rate.
      </p>

      <div className="admin__grid">
        <section className="panel" aria-labelledby="outcomes-h">
          <h2 id="outcomes-h" className="panel__title">Request outcomes</h2>
          <p className="panel__note">Final /chat status per request, all routes ({m.total_requests} requests).</p>
          {m.total_requests === 0 ? <p className="muted">No requests in this period.</p> : (
            <ul className="bars">
              {statuses.filter(([, n]) => n > 0).map(([status, n]) => (
                <li key={status} className="bars__row">
                  <span className="bars__label" title={status}>{STATUS_LABELS[status] ?? humanize(status)}</span>
                  <span className="bars__track" aria-hidden="true"><span className="bars__fill" style={{ width: `${(n / max) * 100}%` }} /></span>
                  <span className="bars__value">{n}</span>
                </li>
              ))}
            </ul>
          )}
          {statuses.some(([, n]) => n === 0) && (
            <p className="panel__note">
              None: {statuses.filter(([, n]) => n === 0).map(([s]) => STATUS_LABELS[s] ?? humanize(s)).join(", ")}.
            </p>
          )}
        </section>
        <section className="panel" aria-labelledby="ops-h">
          <h2 id="ops-h" className="panel__title">Corrections and operational errors</h2>
          <dl className="kv">
            <dt>Data-pipeline requests</dt><dd>{m.data_pipeline_requests}</dd>
            <dt>One SQL correction attempted</dt><dd>{m.correction_attempts}</dd>
            <dt>Answered after the correction</dt><dd>{m.answered_after_correction}</dd>
            <dt>Rejected by validation</dt><dd>{m.validation_rejections}</dd>
            <dt>Timeouts</dt><dd>{m.timeouts}</dd>
            <dt>Busy (another question was running)</dt><dd>{m.busy}</dd>
            <dt>Errors</dt><dd>{m.by_status.error ?? 0}</dd>
            <dt>Dependency unavailable</dt><dd>{m.by_status.unavailable ?? 0}</dd>
          </dl>
        </section>
      </div>
    </>
  );
}

function Health({ s }: { s: SystemStatusResponse }) {
  const c = s.components;
  const rows: { name: string; state: ComponentState; detail: string }[] = [
    { name: "API", state: c.api.status, detail: `Version ${c.api.version} · up ${formatDuration(c.api.uptime_seconds)}` },
    { name: "Ollama", state: c.ollama.status, detail: c.ollama.version ? `Version ${c.ollama.version}` : humanize(c.ollama.reason ?? "no details") },
    { name: "SQL model", state: c.models.sql.status, detail: modelSummary(c.models.sql) },
    { name: "Embedding model", state: c.models.embedding.status, detail: modelSummary(c.models.embedding) },
    { name: "MySQL", state: c.mysql.status, detail: c.mysql.status === "ok"
      ? `Connectivity check passed${c.mysql.latency_ms !== null ? ` (${Math.round(c.mysql.latency_ms)} ms)` : ""}`
      : humanize(c.mysql.reason ?? "no details") },
    { name: "Knowledge index", state: c.index.status, detail: c.index.points !== null ? `${c.index.points} points` : humanize(c.index.reason ?? "no details") },
  ];
  return (
    <>
      <ul className="health">
        {rows.map((r) => (
          <li key={r.name} className="health__row">
            <span className="health__name">{r.name}</span>
            <StateBadge state={r.state} />
            <span className="health__detail">{r.detail}</span>
          </li>
        ))}
      </ul>
      <Snapshot s={s} />
    </>
  );
}

function Snapshot({ s }: { s: SystemStatusResponse }) {
  return (
    <p className="admin__footnote">
      Checked at {formatTime(s.checked_at)}. The server reuses a check for up to {s.cache_ttl_seconds} s, so a refresh within
      that time can show the same snapshot. MySQL is a connectivity check only.
    </p>
  );
}

function yesNo(value: boolean | null, yes: string, no: string): string {
  return value === null ? "Unknown" : value ? yes : no;
}

function modelSummary(m: ModelStatus): string {
  if (!m.name) return "No model configured";
  const installed = yesNo(m.installed, "installed", "not installed");
  const loaded = m.loaded === null ? "load state unknown" : m.loaded ? "loaded" : "not loaded (loads on first use)";
  return `${m.name} · ${installed} · ${loaded}`;
}

// --- Models & RAG ----------------------------------------------------------------------------------

function ModelsRag({ refresh }: { refresh: number }) {
  const system = useLoaded(getSystem, [refresh]);
  if (system.state === null) return <Loading label="Checking models and the knowledge index…" />;
  if (!system.state.ok) return <Failure title="Model and index status is unavailable" message={system.state.message} onRetry={system.reload} />;
  const s = system.state.data;
  const c = s.components;
  const idx = c.index;
  return (
    <>
      <div className="admin__grid">
        <ModelCard title="SQL model" purpose="Intent classification and SQL generation" m={c.models.sql} />
        <ModelCard title="Embedding model" purpose="Question and knowledge embeddings for retrieval" m={c.models.embedding} />
      </div>
      <p className="admin__footnote">
        Installed: present in Ollama. Loaded: currently in memory; a model that is installed but not loaded loads on its first
        use and is not an error. Inference is never verified by this check: it does not generate text or embed anything.
        Ollama {c.ollama.version ? `version ${c.ollama.version}` : `status: ${STATE_LABELS[c.ollama.status]}`}
        {c.ollama.loaded_models.length ? ` · loaded now: ${c.ollama.loaded_models.join(", ")}` : " · no model loaded now"}.
      </p>

      <section className="panel" aria-labelledby="index-h">
        <div className="panel__head">
          <h2 id="index-h" className="panel__title">Knowledge index</h2>
          <StateBadge state={idx.status} />
        </div>
        {idx.reason && <p className="panel__note">Reason: {humanize(idx.reason)}</p>}
        <dl className="kv">
          <dt>Collection</dt><dd>{idx.collection ?? "—"}</dd>
          <dt>Points</dt><dd>{idx.points ?? "—"}</dd>
          {idx.counts_by_type && Object.entries(idx.counts_by_type).map(([type, n]) => (
            <FragmentKV key={type} k={`${humanize(type)} chunks`} v={n} />
          ))}
          <dt>Vector dimensions</dt>
          <dd>{idx.vector_size ?? "—"}{idx.expected_vector_size !== null && ` (embedding model: ${idx.expected_vector_size})`}
            {" "}<Match ok={idx.vector_size_matches} /></dd>
          <dt>Embedding model of the index</dt>
          <dd>{idx.stored_embedding_model ?? "—"}{idx.expected_embedding_model && ` (configured: ${idx.expected_embedding_model})`}
            {" "}<Match ok={idx.embedding_model_matches} /></dd>
          <dt>Schema chunks match database/schema.sql</dt><dd><Match ok={idx.schema_matches_schema_sql} labelled /></dd>
        </dl>
        <p className="panel__note">Only the schema chunks are compared with their source. The freshness of the documentation and
          example chunks is not checked.</p>
      </section>
      <Snapshot s={s} />
    </>
  );
}

function FragmentKV({ k, v }: { k: string; v: ReactNode }) {
  return <><dt>{k}</dt><dd>{v}</dd></>;
}

function Match({ ok, labelled = false }: { ok: boolean | null; labelled?: boolean }) {
  if (ok === null) return <span className="muted">{labelled ? "Not checked" : "(match not checked)"}</span>;
  return <span className={ok ? "match match--ok" : "match match--bad"}>{ok ? (labelled ? "Yes" : "match") : (labelled ? "No" : "mismatch")}</span>;
}

function ModelCard({ title, purpose, m }: { title: string; purpose: string; m: ModelStatus }) {
  return (
    <section className="panel model-card" aria-label={title}>
      <div className="panel__head">
        <p className="model-card__role">{title}</p>
        <StateBadge state={m.status} />
      </div>
      <p className="model-card__name">{m.name ?? "Not configured"}</p>
      <p className="panel__note">{purpose}</p>
      <dl className="kv">
        <dt>Installed</dt><dd>{yesNo(m.installed, "Yes", "No")}</dd>
        <dt>Loaded in memory</dt><dd>{m.loaded === null ? "Unknown" : m.loaded ? "Yes" : "No — loads on first use"}</dd>
        <dt>Inference verified</dt><dd>{m.inference_verified ? "Yes" : "Not checked"}</dd>
        <dt>Parameters</dt><dd>{m.parameter_size ?? "Not reported"}</dd>
        <dt>Quantization</dt><dd>{m.quantization ?? "Not reported"}</dd>
        {m.reason && <><dt>Status reason</dt><dd title={m.reason}>{humanize(m.reason)}</dd></>}
      </dl>
    </section>
  );
}

// --- Query Activity --------------------------------------------------------------------------------

const PAGE_SIZES = [25, 50, 100];

function Activity({ window, onWindow, refresh }: { window: Window; onWindow: (w: Window) => void; refresh: number }) {
  const [limit, setLimit] = useState(25);
  const [offset, setOffset] = useState(0);
  useEffect(() => setOffset(0), [window, limit]);
  const activity = useLoaded(() => getActivity(window, limit, offset), [window, limit, offset, refresh]);
  const data: ActivityResponse | null = activity.state?.ok ? activity.state.data : null;

  return (
    <>
      <div className="admin__toolbar">
        <WindowPicker window={window} onWindow={onWindow} />
        <label className="admin__select">
          Rows per page
          <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      </div>
      <p className="admin__footnote">Newest first. Request metadata only: no question text, SQL, result rows or conversation IDs are recorded.</p>
      {activity.state === null && <Loading label="Loading recent requests…" />}
      {activity.state && !activity.state.ok && (
        <Failure title="Query activity is unavailable" message={activity.state.message} onRetry={activity.reload} />
      )}
      {data && data.events.length === 0 && <p className="page__empty">No requests in this period.</p>}
      {data && data.events.length > 0 && (
        <>
          <div className="table-scroll table-scroll--tall">
            <table className="result-table activity-table">
              <caption className="visually-hidden">Chat requests, newest first</caption>
              <thead>
                <tr>
                  <th scope="col">Time</th><th scope="col">Route</th><th scope="col">Outcome</th><th scope="col">Code</th>
                  <th scope="col" className="num">Total</th><th scope="col" className="num">Model</th><th scope="col" className="num">Database</th>
                  <th scope="col" className="num">Attempts</th><th scope="col">SQL ran</th><th scope="col" className="num">Rows</th>
                  <th scope="col" className="num">HTTP</th><th scope="col">Request ID</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((e) => (
                  <tr key={e.request_id}>
                    <td>{formatTime(e.timestamp)}</td>
                    <td title={e.route_source ? `Routed by ${e.route_source}` : undefined}>{e.route ? humanize(e.route) : "—"}</td>
                    <td><span className={`outcome outcome--${e.status}`}>{STATUS_LABELS[e.status] ?? humanize(e.status)}</span></td>
                    <td>{e.code ? <code>{e.code}</code> : "—"}</td>
                    <td className="num">{formatMs(e.timings.total_ms)}</td>
                    <td className="num">{formatMs(e.timings.llm_ms)}</td>
                    <td className="num">{formatMs(e.timings.database_ms)}</td>
                    <td className="num">{e.attempt_count}{e.correction_attempted ? " (corrected)" : ""}</td>
                    <td>{e.sql_executed ? "Yes" : e.sql_reached_database ? "Failed" : "No"}</td>
                    <td className="num">{e.sql_executed ? `${e.row_count}${e.truncated ? "+" : ""}` : "—"}</td>
                    <td className="num">{e.http_status}</td>
                    <td><code title={e.request_id}>{e.request_id.slice(0, 8)}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <p className="muted">{data.offset + 1}–{data.offset + data.events.length} of {data.total} requests</p>
            <div className="pager__buttons">
              <button type="button" className="btn btn--outline btn--small" disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - limit))}>Newer</button>
              <button type="button" className="btn btn--outline btn--small" disabled={offset + data.events.length >= data.total}
                onClick={() => setOffset(offset + limit)}>Older</button>
            </div>
          </div>
        </>
      )}
    </>
  );
}

// --- Configuration ---------------------------------------------------------------------------------

const GROUPS: { key: keyof AdminConfigResponse; title: string }[] = [
  { key: "models", title: "Models" },
  { key: "rag", title: "Retrieval (RAG)" },
  { key: "sql_generation", title: "SQL generation" },
  { key: "query_execution", title: "Query execution" },
  { key: "charts", title: "Charts" },
  { key: "monitoring", title: "Monitoring" },
];

function Configuration({ refresh }: { refresh: number }) {
  const config = useLoaded(getConfig, [refresh]);
  if (config.state === null) return <Loading label="Loading configuration…" />;
  if (!config.state.ok) return <Failure title="Configuration is unavailable" message={config.state.message} onRetry={config.reload} />;
  const c = config.state.data;
  return (
    <>
      <p className="admin__footnote">
        Read-only. The server reports an allowlist of non-secret settings; credentials, hosts and file paths are never
        included. Settings are changed in the server's environment and take effect after a restart, not from this page.
      </p>
      <div className="admin__grid admin__grid--three">
        {GROUPS.map((g) => (
          <section key={g.key} className="panel" aria-labelledby={`cfg-${g.key}`}>
            <h2 id={`cfg-${g.key}`} className="panel__title">{g.title}</h2>
            {Object.keys(c[g.key] ?? {}).length === 0 ? <p className="muted">No settings reported.</p> : (
              <dl className="kv">
                {Object.entries(c[g.key] ?? {}).map(([k, v]) => (
                  <FragmentKV key={k} k={humanize(k)} v={<span title={k}>{v === null ? "Not set" : String(v)}</span>} />
                ))}
              </dl>
            )}
          </section>
        ))}
      </div>
    </>
  );
}
