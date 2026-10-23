// Read-only GET clients for the data catalog and the administration pages. None of these endpoints
// runs the model, the SQL pipeline or a query against hospital data, and none is retried
// automatically: every page offers a manual retry/refresh instead.
import type {
  ActivityResponse, AdminConfigResponse, CatalogResponse, Loaded, MetricsResponse, SystemStatusResponse, TableDetail,
  Window,
} from "./adminTypes";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** GET a JSON body; `valid` checks the essentials so a malformed body is reported, not rendered. */
export async function getJson<T>(path: string, valid: (body: Record<string, unknown>) => boolean): Promise<Loaded<T>> {
  let response: Response;
  try {
    response = await fetch(path, { headers: { Accept: "application/json" } });
  } catch {
    return { ok: false, kind: "network", status: null, message: "The browser could not reach the MedLens server." };
  }
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const serverMessage = isRecord(body) && typeof body.message === "string" ? body.message : null;
  if (response.status === 404) {
    return { ok: false, kind: "not_found", status: 404, message: serverMessage ?? "Not found." };
  }
  if (!response.ok) {
    return { ok: false, kind: "unavailable", status: response.status,
      message: serverMessage ?? `The server could not provide this information (HTTP ${response.status}).` };
  }
  if (!isRecord(body) || !valid(body)) {
    return { ok: false, kind: "bad_response", status: response.status, message: "The server's reply was not in the expected format." };
  }
  return { ok: true, data: body as T };
}

export const getCatalog = () =>
  getJson<CatalogResponse>("/catalog/tables", (b) => Array.isArray(b.tables));

export const getTable = (name: string) =>
  getJson<TableDetail>(`/catalog/tables/${encodeURIComponent(name)}`,
    (b) => typeof b.name === "string" && Array.isArray(b.columns));

export const getMetrics = (window: Window) =>
  getJson<MetricsResponse>(`/admin/metrics?window=${window}`,
    (b) => typeof b.total_requests === "number" && isRecord(b.by_status));

export const getActivity = (window: Window, limit: number, offset: number) =>
  getJson<ActivityResponse>(`/admin/activity?window=${window}&limit=${limit}&offset=${offset}`,
    (b) => Array.isArray(b.events) && typeof b.total === "number");

export const getSystem = () =>
  getJson<SystemStatusResponse>("/admin/system", (b) => isRecord(b.components) && typeof b.checked_at === "string");

export const getConfig = () =>
  getJson<AdminConfigResponse>("/admin/config", (b) => isRecord(b.models));
