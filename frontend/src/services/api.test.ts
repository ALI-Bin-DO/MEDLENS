import { describe, expect, it, vi } from "vitest";
import { chatResponse, jsonResponse } from "../test/fixtures";
import { deleteConversation, exportCsv, filenameFromDisposition, parseChatResponse, postChat } from "./api";

describe("parseChatResponse", () => {
  it("keeps exact decimal strings and fills omitted fields", () => {
    const parsed = parseChatResponse({ status: "answered", message: "ok", columns: ["total"], rows: [["173424.90"]] });
    expect(parsed?.rows[0][0]).toBe("173424.90");
    expect(parsed?.sql_attempts).toEqual([]);
    expect(parsed?.timings_ms.total_ms).toBeNull();
    expect(parsed?.chart).toBeNull();
  });

  it("rejects bodies without a known status or message", () => {
    expect(parseChatResponse({ status: "weird", message: "x" })).toBeNull();
    expect(parseChatResponse({ status: "answered" })).toBeNull();
    expect(parseChatResponse("<html>")).toBeNull();
    expect(parseChatResponse(null)).toBeNull();
  });
});

describe("postChat", () => {
  it("omits conversation_id for a new conversation and sends it afterwards", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(chatResponse())));
    vi.stubGlobal("fetch", fetchMock);

    await postChat("first", null);
    await postChat("second", "conv_aaaaaaaaaaaaaaaa");

    expect(fetchMock.mock.calls[0][0]).toBe("/chat");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ question: "first" });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ question: "second", conversation_id: "conv_aaaaaaaaaaaaaaaa" });
  });

  it("returns the HTTP status, request ID and body for error statuses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(chatResponse({ status: "busy", message: "Busy." }), 503)));
    const result = await postChat("q", null);
    expect(result).toMatchObject({ kind: "response", httpStatus: 503, requestId: "req-123", body: { status: "busy" } });
  });

  it("reports network failures and unreadable bodies", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    expect(await postChat("q", null)).toEqual({ kind: "network_error" });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Bad gateway", { status: 502 })));
    expect(await postChat("q", null)).toEqual({ kind: "bad_response", httpStatus: 502, requestId: null });
  });
});

describe("deleteConversation", () => {
  it("never throws", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    await expect(deleteConversation("conv_aaaaaaaaaaaaaaaa")).resolves.toBeUndefined();
  });
});

describe("CSV export", () => {
  it("reads the filename from Content-Disposition and keeps it safe", () => {
    expect(filenameFromDisposition('attachment; filename="medlens-export-20261022-101500-partial.csv"'))
      .toBe("medlens-export-20261022-101500-partial.csv");
    expect(filenameFromDisposition('attachment; filename="../../evil.exe"')).toBe("medlens-export.csv");
    expect(filenameFromDisposition(null)).toBe("medlens-export.csv");
  });

  it("posts the original columns, rows and truncated flag and reads the headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("﻿a,a\r\n1,\r\n", {
      status: 200,
      headers: {
        "Content-Disposition": 'attachment; filename="medlens-export-20261022-101500-partial.csv"',
        "X-MedLens-Partial": "true", "X-MedLens-Row-Count": "1", "X-MedLens-Neutralized-Cells": "2",
      },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await exportCsv(["a", "a"], [["173424.90", null]], true);

    expect(fetchMock.mock.calls[0][0]).toBe("/export/csv");
    expect(fetchMock.mock.calls[0][1].body).toBe('{"columns":["a","a"],"rows":[["173424.90",null]],"truncated":true}');
    expect(result).toMatchObject({ ok: true, filename: "medlens-export-20261022-101500-partial.csv", partial: true, rowCount: 1, neutralizedCells: 2 });
  });

  it("returns the server's error message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ status: "too_large", message: "Too many rows." }, 413)));
    expect(await exportCsv(["a"], [[1]], false)).toEqual({ ok: false, message: "Too many rows." });
  });
});
