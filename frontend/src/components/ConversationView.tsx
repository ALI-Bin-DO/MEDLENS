import { useEffect, useRef } from "react";
import type { ChartSpec } from "../services/types";
import type { Conversation, Message } from "../state/chatState";
import { AssistantReply } from "./AssistantReply";
import { LensLoader } from "./LensLoader";
import type { ToastMessage } from "./ResultCard";

interface Props {
  conversation: Conversation;
  onEditQuestion: (question: string) => void;
  onStartNew: (draft: string) => void;
  notify: (toast: ToastMessage) => void;
}

/** The chart of the latest answered reply before `messageId` (null if there is none). */
function previousChart(messages: Message[], messageId: string): ChartSpec | null {
  const index = messages.findIndex((m) => m.id === messageId);
  for (let i = index - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m.role === "assistant" && m.result.kind === "response" && m.result.body.status === "answered") {
      return m.result.body.chart;
    }
  }
  return null;
}

export function ConversationView({ conversation, onEditQuestion, onStartNew, notify }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const count = conversation.messages.length;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [conversation.localId, count, conversation.pending]);

  const lastQuestion = [...conversation.messages].reverse().find((m) => m.role === "user");

  return (
    <div className="thread">
      {conversation.messages.map((message) =>
        message.role === "user" ? (
          <section key={message.id} className="question" aria-label="You asked">
            <p className="speaker">YOU</p>
            <p className="question__bubble">{message.text}</p>
          </section>
        ) : (
          <AssistantReply key={message.id} message={message} onEditQuestion={onEditQuestion} notify={notify}
            previousChart={previousChart(conversation.messages, message.id)} />
        ),
      )}

      {conversation.pending && <LensLoader />}

      {conversation.expired && (
        <div className="expired" role="note">
          <p>
            The server no longer remembers this conversation (memory is temporary and is lost after inactivity or a
            server restart). The messages above stay visible here, but follow-up questions need a new conversation.
            Nothing was sent again.
          </p>
          <button type="button" className="btn btn--primary btn--small"
            onClick={() => onStartNew(lastQuestion && lastQuestion.role === "user" ? lastQuestion.text : "")}>
            Start a new conversation
          </button>
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
