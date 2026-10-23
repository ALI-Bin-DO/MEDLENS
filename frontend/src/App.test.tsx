import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import { chatResponse, jsonResponse } from "./test/fixtures";

vi.mock("plotly.js-basic-dist-min", () => ({ default: { newPlot: vi.fn().mockResolvedValue(undefined), purge: vi.fn() } }));

type Handler = (url: string, init?: RequestInit) => Promise<Response>;

/** Records every request; chat replies come from the queue (or stay pending if it is a deferred). */
function mockFetch(handler: Handler) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => handler(url, init));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function chatCalls(fetchMock: ReturnType<typeof mockFetch>) {
  return fetchMock.mock.calls.filter(([url, init]) => url === "/chat" && init?.method === "POST")
    .map(([, init]) => JSON.parse(String(init?.body)));
}

function deferred() {
  let resolve!: (r: Response) => void;
  const promise = new Promise<Response>((r) => { resolve = r; });
  return { promise, resolve };
}

async function signIn(user: ReturnType<typeof userEvent.setup>) {
  render(<App />);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("demo login", () => {
  it("enters with blank fields via Sign in, transmitting nothing", async () => {
    const fetchMock = mockFetch(() => Promise.reject(new Error("no network expected")));
    const user = userEvent.setup();
    render(<App />);
    expect(screen.getByText("Demo access — no authentication.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByText("What would you like to know?")).toBeInTheDocument();
    expect(screen.getByText("Demo User")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("enters via SSO with arbitrary credentials, transmitting nothing, and clears them", async () => {
    const fetchMock = mockFetch(() => Promise.reject(new Error("no network expected")));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Username"), "someone");
    await user.type(screen.getByLabelText("Password"), "anything");
    await user.click(screen.getByRole("button", { name: "Continue with SSO" }));
    expect(screen.getByText("What would you like to know?")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(screen.getByLabelText("Username")).toHaveValue("");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });
});

describe("composer", () => {
  it("fills from a suggestion without sending; Shift+Enter adds a line; Enter sends", async () => {
    const fetchMock = mockFetch(() => Promise.resolve(jsonResponse(chatResponse())));
    const user = userEvent.setup();
    await signIn(user);

    await user.click(screen.getByRole("button", { name: /How many patients are registered\?/ }));
    const box = screen.getByLabelText("Your question");
    expect(box).toHaveValue("How many patients are registered?");
    expect(fetchMock).not.toHaveBeenCalled();

    await user.type(box, "{Shift>}{Enter}{/Shift}");
    expect(box).toHaveValue("How many patients are registered?\n");
    expect(fetchMock).not.toHaveBeenCalled();

    await user.type(box, "{Enter}");
    await screen.findByText("Here is the result.");
    expect(chatCalls(fetchMock)).toEqual([{ question: "How many patients are registered?" }]);
    expect(screen.getByRole("cell", { name: "50" })).toBeInTheDocument();
  });

  it("rejects blank and over-long questions", async () => {
    const fetchMock = mockFetch(() => Promise.resolve(jsonResponse(chatResponse())));
    const user = userEvent.setup();
    await signIn(user);
    const box = screen.getByLabelText("Your question");

    await user.type(box, "   {Enter}");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send question" })).toBeDisabled();

    await user.clear(box);
    await user.click(box);
    await user.paste("x".repeat(501));
    expect(screen.getByText(/at most 500 characters \(501 now\)/)).toBeInTheDocument();
    await user.type(box, "{Enter}");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("blocks duplicate submissions while a request is pending", async () => {
    const pending = deferred();
    const fetchMock = mockFetch(() => pending.promise);
    const user = userEvent.setup();
    await signIn(user);
    const box = screen.getByLabelText("Your question");

    await user.type(box, "Hello{Enter}");
    expect(screen.getByText("Preparing your answer…")).toBeInTheDocument();
    await user.type(box, "Hello again{Enter}");
    expect(chatCalls(fetchMock)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Waiting for the answer" })).toBeDisabled();

    await act(async () => pending.resolve(jsonResponse(chatResponse({ status: "conversation", message: "Hello! Ask me about the data." }))));
    await screen.findByText("Hello! Ask me about the data.");
  });
});

describe("conversations", () => {
  it("reuses the returned ID, starts fresh on New conversation and restores old messages without requests", async () => {
    const replies = [
      chatResponse({ conversation_id: "conversation_one_0001", message: "First answer." }),
      chatResponse({ conversation_id: "conversation_one_0001", message: "Follow-up answer.", question: "Only completed ones",
        resolved_question: "How many completed appointments does each doctor have?" }),
      chatResponse({ status: "needs_clarification", code: "ambiguous", conversation_id: "conversation_two_0002", sql: null,
        sql_executed: false, sql_attempts: [], columns: [], rows: [], row_count: 0, message: "Which records do you mean?" }),
    ];
    const fetchMock = mockFetch(() => Promise.resolve(jsonResponse(replies.shift())));
    const user = userEvent.setup();
    await signIn(user);
    const box = () => screen.getByLabelText("Your question");

    await user.type(box(), "How many appointments does each doctor have?{Enter}");
    await screen.findByText("First answer.");
    await user.type(box(), "Only completed ones{Enter}");
    await screen.findByText("Follow-up answer.");
    expect(screen.getByText("Interpreted question")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "New conversation" }));
    await user.type(box(), "Only completed ones{Enter}");
    await screen.findByText("Which records do you mean?");
    expect(screen.getByText("Clarification needed")).toBeInTheDocument();

    expect(chatCalls(fetchMock)).toEqual([
      { question: "How many appointments does each doctor have?" },
      { question: "Only completed ones", conversation_id: "conversation_one_0001" },
      { question: "Only completed ones" },
    ]);

    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    await user.click(within(nav).getByRole("button", { name: /How many appointments does each doctor have\?/ }));
    expect(screen.getByText("Follow-up answer.")).toBeInTheDocument();
    expect(screen.queryByText("Which records do you mean?")).not.toBeInTheDocument();
    expect(chatCalls(fetchMock)).toHaveLength(3);
  });

  it("keeps messages on conversation_expired, never resends, and offers a new conversation", async () => {
    const replies: Array<[unknown, number]> = [
      [chatResponse({ conversation_id: "conversation_one_0001", message: "First answer." }), 200],
      [chatResponse({ status: "conversation_expired", conversation_id: null, message: "This conversation has expired or does not exist." }), 404],
    ];
    const fetchMock = mockFetch(() => {
      const [body, status] = replies.shift()!;
      return Promise.resolve(jsonResponse(body, status));
    });
    const user = userEvent.setup();
    await signIn(user);

    await user.type(screen.getByLabelText("Your question"), "How many patients are registered?{Enter}");
    await screen.findByText("First answer.");
    await user.type(screen.getByLabelText("Your question"), "Only completed ones{Enter}");
    await screen.findByText("Conversation memory expired");
    expect(screen.getByText("First answer.")).toBeInTheDocument();
    expect(screen.getByLabelText("Your question")).toBeDisabled();
    expect(chatCalls(fetchMock)).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Start a new conversation" }));
    expect(screen.getByLabelText("Your question")).toHaveValue("Only completed ones");
    expect(chatCalls(fetchMock)).toHaveLength(2);
  });

  it("shows busy without retrying, and a reply lands in its own conversation after switching", async () => {
    const first = deferred();
    const fetchMock = mockFetch(() => first.promise);
    const user = userEvent.setup();
    await signIn(user);

    await user.type(screen.getByLabelText("Your question"), "How many patients are registered?{Enter}");
    await user.click(screen.getByRole("button", { name: "New conversation" }));
    expect(screen.getByText("What would you like to know?")).toBeInTheDocument();

    await act(async () => first.resolve(jsonResponse(chatResponse({ status: "busy", conversation_id: null, sql: null,
      sql_executed: false, sql_attempts: [], message: "Another question is being processed." }), 503, { "Retry-After": "5" })));
    expect(screen.queryByText("Another question is being processed.")).not.toBeInTheDocument();

    await user.click(within(screen.getByRole("navigation", { name: "Main navigation" }))
      .getByRole("button", { name: /How many patients are registered\?/ }));
    expect(screen.getByText("Another question is being processed.")).toBeInTheDocument();
    expect(screen.getByText("It was not retried automatically.")).toBeInTheDocument();
    expect(chatCalls(fetchMock)).toHaveLength(1);
  });

  it("renders every error status from mocks", async () => {
    const cases: Array<[string, number, string]> = [
      ["rejected", 200, "Read-only protection"],
      ["cannot_answer", 200, "Can't answer this question"],
      ["classification_failed", 200, "Request not understood"],
      ["generation_failed", 200, "No usable answer was generated"],
      ["query_failed", 200, "The query failed"],
      ["conversation_busy", 409, "This conversation is busy"],
      ["unavailable", 503, "Service unavailable"],
      ["timeout", 504, "Timed out"],
      ["invalid_request", 422, "Question not accepted"],
      ["error", 500, "Unexpected error"],
    ];
    const queue = [...cases];
    mockFetch(() => {
      const [status, http] = queue.shift()!;
      return Promise.resolve(jsonResponse(chatResponse({ status: status as never, conversation_id: null, sql: null,
        sql_executed: false, sql_attempts: [], columns: [], rows: [], row_count: 0, message: `Message for ${status}.` }), http));
    });
    const user = userEvent.setup();
    await signIn(user);
    for (const [status, , heading] of cases) {
      await user.click(screen.getByRole("button", { name: "New conversation" }));
      await user.type(screen.getByLabelText("Your question"), `Question for ${status}{Enter}`);
      expect(await screen.findByText(heading)).toBeInTheDocument();
      expect(screen.getByText(`Message for ${status}.`)).toBeInTheDocument();
    }
  }, 20000);

  it("handles an unreachable server", async () => {
    mockFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    const user = userEvent.setup();
    await signIn(user);
    await user.type(screen.getByLabelText("Your question"), "Hello{Enter}");
    expect(await screen.findByText("No response from the server")).toBeInTheDocument();
  });
});

describe("redrawing the previous result", () => {
  it("shows the previous chart in the requested view without a new data query", async () => {
    const chart = {
      type: "bar" as const, views: ["bar_vertical" as const, "bar_horizontal" as const, "pie" as const],
      default_view: "bar_vertical" as const,
      x: { label: "status", fields: ["status"], kind: "category" as const },
      y: { label: "n", fields: ["n"], kind: "number" as const },
      points: [{ x: "Open", y: 2, y_exact: 2, row_index: 0 }, { x: "Closed", y: 3, y_exact: 3, row_index: 1 }],
      point_count: 2,
    };
    const replies = [
      chatResponse({ message: "Counts per status.", columns: ["status", "n"], rows: [["Open", 2], ["Closed", 3]], row_count: 2, chart }),
      chatResponse({ status: "conversation", code: "capabilities", sql: null, sql_executed: false, sql_attempts: [],
        columns: [], rows: [], row_count: 0, chart: null, view_request: "pie",
        message: "Here is the previous result as a donut. It reuses the same rows; no new query was run." }),
    ];
    const fetchMock = mockFetch(() => Promise.resolve(jsonResponse(replies.shift())));
    const user = userEvent.setup();
    await signIn(user);
    await user.type(screen.getByLabelText("Your question"), "How many per status?{Enter}");
    await screen.findByText("Counts per status.");
    await user.type(screen.getByLabelText("Your question"), "Could I see it as a donut?{Enter}");
    await screen.findByText(/Here is the previous result as a donut/);
    expect(screen.getByRole("img", { name: /Donut chart of N by Status/ })).toBeInTheDocument();
    expect(chatCalls(fetchMock)).toHaveLength(2);
  });
});

describe("sign-out", () => {
  it("deletes known server conversations and ignores late replies", async () => {
    const late = deferred();
    const calls: string[] = [];
    let chatCount = 0;
    mockFetch((url, init) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url === "/chat") {
        chatCount += 1;
        return chatCount === 1 ? Promise.resolve(jsonResponse(chatResponse({ conversation_id: "conversation_one_0001", message: "First answer." }))) : late.promise;
      }
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    const user = userEvent.setup();
    await signIn(user);

    await user.type(screen.getByLabelText("Your question"), "How many patients are registered?{Enter}");
    await screen.findByText("First answer.");
    await user.click(screen.getByRole("button", { name: "New conversation" }));
    await user.type(screen.getByLabelText("Your question"), "What is the total amount of paid bills?{Enter}");

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(calls).toContain("DELETE /chat/conversations/conversation_one_0001");

    await act(async () => late.resolve(jsonResponse(chatResponse({ conversation_id: "conversation_two_0002", message: "Late answer." }))));
    await waitFor(() => expect(calls).toContain("DELETE /chat/conversations/conversation_two_0002"));
    expect(screen.queryByText("Late answer.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByText("Your conversations will appear here until you sign out.")).toBeInTheDocument();
  });
});

describe("results", () => {
  it("offers a chart only when the server sends one, shows SQL, and exports the original rows", async () => {
    const answer = chatResponse({
      message: "Appointments per doctor.",
      columns: ["doctor", "appointment_count"],
      rows: [["Jane Smith", 6], ["David Jones", 0]],
      row_count: 2,
      truncated: true,
      chart: {
        type: "bar", views: ["bar_vertical", "bar_horizontal", "pie"], default_view: "bar_vertical",
        x: { label: "doctor", fields: ["doctor"], kind: "category" },
        y: { label: "appointment_count", fields: ["appointment_count"], kind: "number" },
        points: [{ x: "Jane Smith", y: 6, y_exact: 6, row_index: 0 }, { x: "David Jones", y: 0, y_exact: 0, row_index: 1 }],
        point_count: 2,
      },
    });
    const fetchMock = mockFetch((url) => {
      if (url === "/chat") return Promise.resolve(jsonResponse(answer));
      return Promise.resolve(new Response("csv", {
        status: 200,
        headers: { "Content-Disposition": 'attachment; filename="medlens-export-20261022-101500-partial.csv"',
          "X-MedLens-Partial": "true", "X-MedLens-Row-Count": "2", "X-MedLens-Neutralized-Cells": "0" },
      }));
    });
    const createUrl = vi.fn(() => "blob:test");
    const revokeUrl = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: revokeUrl }));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    const user = userEvent.setup();
    await signIn(user);
    await user.type(screen.getByLabelText("Your question"), "How many appointments does each doctor have?{Enter}");
    await screen.findByText("Appointments per doctor.");

    expect(screen.getByText(/Only the first 2 rows are shown/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Chart" }));
    expect(screen.getByRole("img", { name: /Vertical bar chart of Appointment count by Doctor/ })).toBeInTheDocument();
    const requestsBefore = fetchMock.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Donut" }));
    expect(screen.getByRole("img", { name: /Donut chart of Appointment count by Doctor/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Horizontal bars" }));
    expect(screen.getByRole("img", { name: /Horizontal bar chart/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Line" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.length).toBe(requestsBefore);

    await user.click(screen.getByRole("button", { name: "View SQL" }));
    expect(screen.getByText("Executed SQL · validated read-only SELECT")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Export CSV/ }));
    await screen.findByText("CSV ready — export complete");
    expect(screen.getByText(/Partial export/)).toBeInTheDocument();
    const exportCall = fetchMock.mock.calls.find(([url]) => url === "/export/csv")!;
    expect(JSON.parse(String(exportCall[1]?.body))).toEqual({ columns: answer.columns, rows: answer.rows, truncated: true });
    expect(chatCalls(fetchMock)).toHaveLength(1);
    await waitFor(() => expect(revokeUrl).toHaveBeenCalledWith("blob:test"));
  });
});
