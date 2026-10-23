// Draws the backend's chart spec as given: same order, same labels, no inferred units. The backend
// lists the views that suit the result (`views`); this component only switches between them, reusing
// the same points (no new query). Plotly comes from the installed package and loads on first use.
import { useEffect, useId, useRef, useState } from "react";
import type { ChartSpec, ChartViewKind } from "../services/types";

const INK = "#1C2430";
const MUTED = "#707372";
const GRID = "#DDE3E0";
const SURFACE = "#FFFFFF";
/** One series = one color (validated against the white surface). */
export const SERIES_COLOR = "#008755";
/**
 * Categorical palette, in this fixed order: emerald, blue, amber, teal, purple. Validated with the dataviz
 * palette checker (lightness, chroma, colour-blind separation, contrast), including the last-to-first pair
 * that meets in a donut. Distinct from the app's error red; never cycled (pies have at most 5 slices).
 */
export const CATEGORY_COLORS = ["#008755", "#2563EB", "#B7791F", "#0891B2", "#7C3AED"];
const MAX_TICK_CHARS = 40;

export const VIEW_LABELS: Record<ChartViewKind, string> = {
  bar_vertical: "Vertical bars",
  bar_horizontal: "Horizontal bars",
  line: "Line",
  pie: "Donut",
};

/** Plotly renders a small HTML subset in labels and templates; show text literally instead. */
export function plotlyText(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/%/g, "&#37;");
}

function tick(label: string): string {
  return plotlyText(label.length > MAX_TICK_CHARS ? `${label.slice(0, MAX_TICK_CHARS - 1)}…` : label);
}

/**
 * Colors for each point. Category results (bars) with at most five categories color each category in row
 * order with the categorical palette, whether or not a donut is offered, and the same way in every view.
 * More categories than palette colors use one series color (colors are never cycled), and a date or month
 * series is one series, so it keeps one color. The mapping never depends on the current view.
 */
export function pointColors(chart: ChartSpec): string[] {
  const categorical = chart.type === "bar" && chart.points.length <= CATEGORY_COLORS.length;
  return chart.points.map((_, i) => (categorical ? CATEGORY_COLORS[i] : SERIES_COLOR));
}

/**
 * Readable label for a result column name ("patient_count" -> "Patient count"; joined category labels
 * keep their " · " separator). Display only: the API column names, rows, SQL and exports are unchanged,
 * and no unit or currency is added.
 */
export function displayLabel(name: string): string {
  return name.split(" · ").map((part) => {
    const text = part.replace(/_/g, " ").replace(/\s+/g, " ").trim();
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : part;
  }).join(" · ");
}

/** Build Plotly traces and layout. Categories are placed by index so repeated labels stay separate. */
export function buildFigure(chart: ChartSpec, view: ChartViewKind = chart.default_view) {
  if (!chart.views.includes(view)) throw new Error(`view ${view} is not offered for this result`);
  const points = chart.points;
  const indices = points.map((_, i) => i);
  const colors = pointColors(chart);
  const xLabel = plotlyText(displayLabel(chart.x.label));
  const yLabel = plotlyText(displayLabel(chart.y.label));
  const customdata = points.map((p) => [plotlyText(p.x), plotlyText(String(p.y_exact))]);
  const hovertemplate = `${xLabel}: %{customdata[0]}<br>${yLabel}: %{customdata[1]}<extra></extra>`;
  const font = { family: "Inter, system-ui, sans-serif", size: 12, color: INK };
  const ticktext = points.map((p) => tick(p.x));
  const categoryAxis = { tickmode: "array", tickvals: indices, ticktext, showgrid: false, linecolor: GRID, tickfont: { color: INK } };
  const valueAxis = {
    title: { text: yLabel, font: { color: MUTED } },
    gridcolor: GRID, zerolinecolor: GRID, tickfont: { color: MUTED }, rangemode: "tozero",
  };
  const base = {
    font, paper_bgcolor: SURFACE, plot_bgcolor: SURFACE, showlegend: false,
    hoverlabel: { bgcolor: INK, bordercolor: INK, font: { family: font.family, color: "#FFFFFF" } },
  };
  const timeAxis = chart.x.kind === "date" || chart.x.kind === "month";
  const xTitle = { text: xLabel, font: { color: MUTED } };

  if (view === "pie") {
    // Pie traces do not index 2-D customdata, so the label comes from %{label} and customdata holds
    // only the exact values. Labels are complete (at most 5, unique per the backend).
    return {
      data: [{
        type: "pie", hole: 0.55, sort: false, direction: "clockwise",
        labels: points.map((p) => plotlyText(p.x)), values: points.map((p) => p.y),
        customdata: points.map((p) => plotlyText(String(p.y_exact))),
        hovertemplate: `${xLabel}: %{label}<br>${yLabel}: %{customdata}<br>Share: %{percent}<extra></extra>`,
        marker: { colors, line: { color: SURFACE, width: 2 } },
        textinfo: "percent", textfont: { color: "#FFFFFF" }, insidetextorientation: "horizontal",
      }],
      layout: {
        ...base, showlegend: true, height: 340, margin: { l: 16, r: 16, t: 12, b: 12 },
        legend: { font: { color: INK }, orientation: "v", x: 1, xanchor: "left", y: 0.5 },
      },
    };
  }

  if (view === "line") {
    return {
      data: [{ type: "scatter", mode: "lines+markers", x: timeAxis ? points.map((p) => p.x) : indices, y: points.map((p) => p.y),
        customdata, hovertemplate, line: { color: SERIES_COLOR, width: 2 }, marker: { color: SERIES_COLOR, size: 8 } }],
      layout: {
        ...base, height: 340, margin: { l: 64, r: 24, t: 12, b: 64 }, hovermode: "closest",
        xaxis: timeAxis
          ? { type: "date", title: xTitle, showgrid: false, linecolor: GRID, tickfont: { color: MUTED } }
          : { ...categoryAxis, title: xTitle },
        yaxis: valueAxis,
      },
    };
  }

  const bar = { type: "bar", customdata, hovertemplate, marker: { color: colors } };
  if (view === "bar_horizontal") {
    const longest = Math.max(...points.map((p) => Math.min(p.x.length, MAX_TICK_CHARS)), 4);
    return {
      data: [{ ...bar, orientation: "h", x: points.map((p) => p.y), y: indices }],
      layout: {
        ...base, height: Math.max(220, points.length * 30 + 90),
        margin: { l: Math.min(24 + longest * 7, 300), r: 24, t: 12, b: 56 }, bargap: 0.35, barcornerradius: 4,
        yaxis: { ...categoryAxis, autorange: "reversed", title: { text: "" } }, xaxis: valueAxis,
      },
    };
  }
  // Vertical bars: categories in result order, or a date/month series in date order.
  return {
    data: [{ ...bar, x: timeAxis ? points.map((p) => p.x) : indices, y: points.map((p) => p.y) }],
    layout: {
      ...base, height: 340, margin: { l: 64, r: 24, t: 12, b: 96 }, bargap: 0.3, barcornerradius: 4,
      xaxis: timeAxis
        ? { type: "date", title: xTitle, showgrid: false, linecolor: GRID, tickfont: { color: MUTED } }
        : { ...categoryAxis, title: xTitle, tickangle: points.length > 6 ? -35 : 0, automargin: true },
      yaxis: valueAxis,
    },
  };
}

function describe(chart: ChartSpec, view: ChartViewKind): string {
  const count = `${chart.point_count} ${chart.point_count === 1 ? "point" : "points"}`;
  const y = displayLabel(chart.y.label);
  const x = displayLabel(chart.x.label);
  if (view === "pie") return `Donut chart of ${y} by ${x}, ${count}.`;
  const kind = view === "line" ? "Line chart" : view === "bar_vertical" ? "Vertical bar chart" : "Horizontal bar chart";
  return `${kind} of ${y} by ${x}, ${count}.`;
}

export function ChartView({ chart, initialView }: { chart: ChartSpec; initialView?: ChartViewKind }) {
  const ref = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<ChartViewKind>(
    initialView && chart.views.includes(initialView) ? initialView : chart.default_view);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const groupId = useId();
  const active = chart.views.includes(view) ? view : chart.default_view;

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    let cancelled = false;
    let plotly: { purge(el: HTMLElement): void } | null = null;
    import("plotly.js-basic-dist-min")
      .then(async ({ default: Plotly }) => {
        if (cancelled) return;
        plotly = Plotly;
        const { data, layout } = buildFigure(chart, active);
        Plotly.purge(element);
        await Plotly.newPlot(element, data, layout, { displayModeBar: false, responsive: true, displaylogo: false });
        if (!cancelled) setState("ready");
      })
      .catch(() => {
        if (!cancelled) setState("failed");
      });
    return () => {
      cancelled = true;
      plotly?.purge(element);
    };
  }, [chart, active]);

  const summary = describe(chart, active);
  const hint = active === "pie" ? "Hover a slice for its exact value and share."
    : `Hover a ${active === "line" ? "point" : "bar"} for its exact value.`;
  return (
    <figure className="chart">
      {chart.views.length > 1 && (
        <div className="chart__views" role="group" aria-labelledby={`${groupId}-label`}>
          <span id={`${groupId}-label`} className="chart__views-label">Chart type</span>
          <div className="segmented segmented--small">
            {chart.views.map((v) => (
              <button key={v} type="button" className="segmented__btn" aria-pressed={v === active} onClick={() => setView(v)}>
                {VIEW_LABELS[v]}
              </button>
            ))}
          </div>
        </div>
      )}
      {state === "loading" && <p className="muted">Loading chart…</p>}
      {state === "failed" && <p className="text-danger" role="alert">The chart could not be drawn. The table view still has the full result.</p>}
      <div ref={ref} className="chart__plot" role="img" aria-label={summary} />
      <figcaption className="chart__caption">{summary} {hint}</figcaption>
    </figure>
  );
}
