import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { chatResponse } from "../test/fixtures";
import type { ChartSpec } from "../services/types";
import { CATEGORY_COLORS, SERIES_COLOR, buildFigure, displayLabel, plotlyText, pointColors } from "./ChartView";
import { ResultTable } from "./ResultTable";
import { sqlLabel } from "./SqlPanel";

describe("ResultTable", () => {
  it("renders duplicate column names by position, exact decimals, NULL and empty strings distinctly", () => {
    render(<ResultTable columns={["name", "name", "total"]} rows={[["Ann", "", "173424.90"], ["Bob", null, "0.10"]]} />);
    const headers = screen.getAllByRole("columnheader").map((th) => th.textContent);
    expect(headers).toEqual(["name", "name", "total"]);

    const rows = screen.getAllByRole("row").slice(1);
    const first = within(rows[0]).getAllByRole("cell").map((td) => td.textContent);
    const second = within(rows[1]).getAllByRole("cell").map((td) => td.textContent);
    expect(first).toEqual(["Ann", "(empty text)", "173424.90"]);
    expect(second).toEqual(["Bob", "NULL", "0.10"]);
  });

  it("renders model/database text as text, not HTML", () => {
    render(<ResultTable columns={["<b>x</b>"]} rows={[["<img src=x onerror=alert(1)>"]]} />);
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("sqlLabel", () => {
  it("distinguishes executed, rejected and failed SQL", () => {
    expect(sqlLabel(chatResponse()).tone).toBe("ok");

    const rejected = chatResponse({
      status: "rejected", sql_executed: false, sql_reached_database: false,
      sql_attempts: [{ ...chatResponse().sql_attempts[0], validation: "rejected", execution: "not_run", error_code: "not_select" }],
    });
    expect(sqlLabel(rejected).short).toBe("Rejected · not executed");

    const failed = chatResponse({
      status: "query_failed", sql_executed: false, sql_reached_database: true,
      sql_attempts: [{ ...chatResponse().sql_attempts[0], execution: "failed", error_code: "mysql_1054" }],
    });
    expect(sqlLabel(failed).short).toBe("Failed in MySQL");
  });
});

describe("buildFigure", () => {
  const chart: ChartSpec = {
    type: "bar",
    views: ["bar_horizontal", "bar_vertical"],
    default_view: "bar_horizontal",
    x: { label: "doctor", fields: ["first_name", "last_name"], kind: "category" },
    y: { label: "appointment_count", fields: ["appointment_count"], kind: "number" },
    points: [
      { x: "Jane · Smith", y: 6, y_exact: 6, row_index: 0 },
      { x: "Jane · Smith", y: 0, y_exact: 0, row_index: 1 },
      { x: "<b>Bold</b>", y: 2663.4156, y_exact: "2663.415600", row_index: 2 },
    ],
    point_count: 3,
  };

  it("keeps order, separates repeated labels and puts exact values in the tooltip data", () => {
    const { data, layout } = buildFigure(chart);
    const trace = data[0] as { y: number[]; x: number[]; customdata: string[][] };
    expect(trace.y).toEqual([0, 1, 2]);
    expect(trace.x).toEqual([6, 0, 2663.4156]);
    expect(trace.customdata[2][1]).toBe("2663.415600");
    const yaxis = (layout as { yaxis: { ticktext: string[] } }).yaxis;
    expect(yaxis.ticktext).toEqual(["Jane · Smith", "Jane · Smith", "&lt;b&gt;Bold&lt;/b&gt;"]);
  });

  it("escapes Plotly markup and template syntax", () => {
    expect(plotlyText("%{x} <br> & 50%")).toBe("&#37;{x} &lt;br&gt; &amp; 50&#37;");
  });

  it("draws vertical bars in result order and refuses views the backend did not offer", () => {
    const { data } = buildFigure(chart, "bar_vertical");
    const trace = data[0] as { x: number[]; y: number[]; orientation?: string };
    expect(trace.orientation).toBeUndefined();
    expect(trace.x).toEqual([0, 1, 2]);
    expect(trace.y).toEqual([6, 0, 2663.4156]);
    expect(() => buildFigure(chart, "pie")).toThrow(/not offered/);
    expect(() => buildFigure(chart, "line")).toThrow(/not offered/);
  });

  it("colors categories with the palette even when no donut is offered", () => {
    expect(chart.views).not.toContain("pie");
    expect(pointColors(chart)).toEqual(CATEGORY_COLORS.slice(0, 3));
  });

  it("keeps one series color when there are more categories than palette colors", () => {
    const many: ChartSpec = { ...chart, point_count: 6,
      points: Array.from({ length: 6 }, (_, i) => ({ x: `c${i}`, y: i, y_exact: i, row_index: i })) };
    expect(pointColors(many)).toEqual(Array(6).fill(SERIES_COLOR));
  });

  it("shows readable labels but keeps the exact values", () => {
    const { data } = buildFigure(chart, "bar_vertical");
    const trace = data[0] as { hovertemplate: string; customdata: string[][] };
    expect(displayLabel("patient_count")).toBe("Patient count");
    expect(displayLabel("doctor_id · first_name")).toBe("Doctor id · First name");
    expect(trace.customdata.map((c) => c[1])).toEqual(chart.points.map((p) => String(p.y_exact)));
  });
});

describe("donut and category colors", () => {
  const statuses: ChartSpec = {
    type: "bar",
    views: ["bar_vertical", "bar_horizontal", "pie"],
    default_view: "bar_vertical",
    x: { label: "status", fields: ["status"], kind: "category" },
    y: { label: "appointment_count", fields: ["appointment_count"], kind: "number" },
    points: [
      { x: "Scheduled", y: 51, y_exact: 51, row_index: 0 },
      { x: "No-show", y: 52, y_exact: 52, row_index: 1 },
      { x: "Cancelled", y: 51, y_exact: 51, row_index: 2 },
      { x: "Completed", y: 46, y_exact: 46, row_index: 3 },
    ],
    point_count: 4,
  };

  it("keeps the same category-to-color mapping in every view", () => {
    const expected = CATEGORY_COLORS.slice(0, 4);
    const vertical = buildFigure(statuses, "bar_vertical").data[0] as { marker: { color: string[] } };
    const horizontal = buildFigure(statuses, "bar_horizontal").data[0] as { marker: { color: string[] } };
    const pie = buildFigure(statuses, "pie").data[0] as { marker: { colors: string[] }; labels: string[]; values: number[]; sort: boolean };
    expect(vertical.marker.color).toEqual(expected);
    expect(horizontal.marker.color).toEqual(expected);
    expect(pie.marker.colors).toEqual(expected);
    expect(pie.labels).toEqual(["Scheduled", "No-show", "Cancelled", "Completed"]);
    expect(pie.values).toEqual([51, 52, 51, 46]);
    expect(pie.sort).toBe(false);
  });

  it("puts exact values in donut tooltips", () => {
    const pie = buildFigure(statuses, "pie").data[0] as { customdata: string[]; hovertemplate: string };
    expect(pie.customdata).toEqual(["51", "52", "51", "46"]);
    expect(pie.hovertemplate).toContain("%{label}");
    expect(pie.hovertemplate).toContain("Appointment count: %{customdata}");
    expect(pie.hovertemplate).not.toContain("customdata["); // pie traces cannot index 2-D customdata
  });

  it("draws month series on a date axis with one line color", () => {
    const months: ChartSpec = {
      type: "line", views: ["line", "bar_vertical"], default_view: "line",
      x: { label: "month", fields: ["month"], kind: "month" },
      y: { label: "n", fields: ["n"], kind: "number" },
      points: [{ x: "2023-01", y: 20, y_exact: 20, row_index: 0 }, { x: "2023-02", y: 14, y_exact: 14, row_index: 1 }],
      point_count: 2,
    };
    const { data, layout } = buildFigure(months, "line");
    const trace = data[0] as { x: string[]; line: { color: string } };
    expect(trace.x).toEqual(["2023-01", "2023-02"]);
    expect(trace.line.color).toBe(SERIES_COLOR);
    expect((layout as { xaxis: { type: string } }).xaxis.type).toBe("date");
  });
});
