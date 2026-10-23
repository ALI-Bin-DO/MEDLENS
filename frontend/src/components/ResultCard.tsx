import { useState } from "react";
import { exportCsv, saveBlob } from "../services/api";
import type { ChatResponse } from "../services/types";
import { ChartView } from "./ChartView";
import { AlertIcon, ChartIcon, ClockIcon, DownloadIcon, TableIcon } from "./Icons";
import { ResultTable } from "./ResultTable";

export interface ToastMessage {
  title: string;
  details: string[];
}

function formatSeconds(ms: number | null): string | null {
  return ms === null ? null : `${(ms / 1000).toFixed(1)} s`;
}

function rowsText(count: number): string {
  return `${count} ${count === 1 ? "row" : "rows"}`;
}

/** An answered data question: table (always), chart (only when the server sent one), export. */
export function ResultCard({ response, notify }: { response: ChatResponse; notify: (toast: ToastMessage) => void }) {
  const [tab, setTab] = useState<"table" | "chart">("table");
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const time = formatSeconds(response.timings_ms.total_ms);

  if (response.columns.length === 0 || response.rows.length === 0) {
    return (
      <div className="result result--empty">
        <p className="result__empty-title">0 rows returned</p>
        <p className="muted">The query ran successfully, but no rows matched.{time ? ` (${time})` : ""}</p>
      </div>
    );
  }

  const runExport = async () => {
    setExporting(true);
    setExportError(null);
    // Exports exactly the rows already shown; the question is not run again.
    const result = await exportCsv(response.columns, response.rows, response.truncated);
    setExporting(false);
    if (!result.ok) {
      setExportError(result.message);
      return;
    }
    saveBlob(result.blob, result.filename);
    const details = [result.filename];
    if (result.partial) details.push("Partial export: the result was cut at the server's row limit.");
    if (result.neutralizedCells > 0) {
      details.push(`${result.neutralizedCells} ${result.neutralizedCells === 1 ? "cell was" : "cells were"} prefixed with an apostrophe so spreadsheets do not run them as formulas.`);
    }
    notify({ title: "CSV ready — export complete", details });
  };

  return (
    <div className="result">
      <div className="result__toolbar">
        <div className="segmented" role="group" aria-label="Result view">
          <button type="button" className="segmented__btn" aria-pressed={tab === "table"} onClick={() => setTab("table")}>
            <TableIcon size={16} /> Table
          </button>
          {response.chart && (
            <button type="button" className="segmented__btn" aria-pressed={tab === "chart"} onClick={() => setTab("chart")}>
              <ChartIcon size={16} /> Chart
            </button>
          )}
        </div>
        <button type="button" className="btn btn--outline btn--small" onClick={runExport} disabled={exporting}>
          <DownloadIcon size={16} /> {exporting ? "Exporting…" : "Export CSV"}
        </button>
      </div>

      {exportError && <p className="result__error" role="alert"><AlertIcon size={16} /> Export failed: {exportError}</p>}

      {response.truncated && (
        <p className="result__warning" role="note">
          <AlertIcon size={16} />
          Only the first {rowsText(response.row_count)} are shown. The query matched more rows than the server returns,
          so this table, the chart and the export are incomplete.
        </p>
      )}

      {tab === "chart" && response.chart ? <ChartView chart={response.chart} /> : <ResultTable columns={response.columns} rows={response.rows} />}

      <div className="result__meta">
        <span className="meta-pill">
          <ClockIcon size={14} />
          {[time, rowsText(response.row_count), response.truncated ? "truncated" : null].filter(Boolean).join(" · ")}
        </span>
      </div>
    </div>
  );
}
