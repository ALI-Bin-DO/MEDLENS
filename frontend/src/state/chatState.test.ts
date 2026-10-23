import { describe, expect, it } from "vitest";
import { chatResponse } from "../test/fixtures";
import { HOME_KEY, chatReducer, initialChatState, knownServerIds, type ChatState } from "./chatState";

function sendNew(state: ChatState, localId: string, requestId: string, question = "How many patients?") {
  return chatReducer(state, { type: "send", localId, requestId, question, title: question, isNew: true });
}

describe("chatReducer", () => {
  it("keeps the server conversation ID from the first reply", () => {
    let state = sendNew(initialChatState, "c1", "r1");
    expect(state.activeId).toBe("c1");
    expect(state.conversations[0].pending?.requestId).toBe("r1");

    state = chatReducer(state, {
      type: "reply", localId: "c1", requestId: "r1", messageId: "m1",
      result: { kind: "response", httpStatus: 200, requestId: null, body: chatResponse({ conversation_id: "server_conversation_1" }) },
    });
    expect(state.conversations[0].serverId).toBe("server_conversation_1");
    expect(state.conversations[0].pending).toBeNull();
    expect(knownServerIds(state)).toEqual(["server_conversation_1"]);
  });

  it("a new conversation starts without a server ID and keeps the old one separate", () => {
    let state = sendNew(initialChatState, "c1", "r1");
    state = chatReducer(state, {
      type: "reply", localId: "c1", requestId: "r1", messageId: "m1",
      result: { kind: "response", httpStatus: 200, requestId: null, body: chatResponse({ conversation_id: "server_conversation_1" }) },
    });
    state = chatReducer(state, { type: "new_conversation" });
    expect(state.activeId).toBeNull();
    state = sendNew(state, "c2", "r2", "Only completed ones");
    expect(state.conversations.map((c) => c.localId)).toEqual(["c2", "c1"]);
    expect(state.conversations[0].serverId).toBeNull();
  });

  it("delivers a reply to its own conversation even after switching away", () => {
    let state = sendNew(initialChatState, "c1", "r1");
    state = chatReducer(state, { type: "new_conversation" });
    state = chatReducer(state, {
      type: "reply", localId: "c1", requestId: "r1", messageId: "m1",
      result: { kind: "response", httpStatus: 200, requestId: null, body: chatResponse() },
    });
    expect(state.activeId).toBeNull();
    expect(state.conversations[0].messages).toHaveLength(2);
  });

  it("ignores replies that are not the pending request", () => {
    const state = sendNew(initialChatState, "c1", "r1");
    const next = chatReducer(state, {
      type: "reply", localId: "c1", requestId: "stale", messageId: "m1",
      result: { kind: "network_error" },
    });
    expect(next).toEqual(state);
    // Unknown conversation (e.g. state was reset): nothing changes.
    expect(chatReducer(initialChatState, {
      type: "reply", localId: "c1", requestId: "r1", messageId: "m1", result: { kind: "network_error" },
    })).toEqual(initialChatState);
  });

  it("marks a conversation expired, keeps its messages and blocks further sends", () => {
    let state = sendNew(initialChatState, "c1", "r1");
    state = chatReducer(state, {
      type: "reply", localId: "c1", requestId: "r1", messageId: "m1",
      result: { kind: "response", httpStatus: 200, requestId: null, body: chatResponse({ conversation_id: "server_conversation_1" }) },
    });
    state = chatReducer(state, { type: "send", localId: "c1", requestId: "r2", question: "Only completed ones", title: "", isNew: false });
    state = chatReducer(state, {
      type: "reply", localId: "c1", requestId: "r2", messageId: "m2",
      result: { kind: "response", httpStatus: 404, requestId: null, body: chatResponse({ status: "conversation_expired", conversation_id: null }) },
    });
    const conversation = state.conversations[0];
    expect(conversation.expired).toBe(true);
    expect(conversation.serverId).toBeNull();
    expect(conversation.messages).toHaveLength(4);

    const after = chatReducer(state, { type: "send", localId: "c1", requestId: "r3", question: "again", title: "", isNew: false });
    expect(after.conversations[0].messages).toHaveLength(4);
    expect(after.conversations[0].pending).toBeNull();
  });

  it("clears the draft on send and fills it from a suggestion", () => {
    let state = chatReducer(initialChatState, { type: "set_draft", key: HOME_KEY, text: "How many patients are registered?" });
    expect(state.drafts[HOME_KEY]).toBe("How many patients are registered?");
    state = sendNew(state, "c1", "r1", "How many patients are registered?");
    expect(state.drafts[HOME_KEY]).toBe("");
  });
});
