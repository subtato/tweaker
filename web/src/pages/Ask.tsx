import { ArrowUp, Bot, Loader2, MessageSquare, Sparkles } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { RichText } from "../components/Ui";
import { useAsync, useDocumentTitle } from "../hooks";
import { useOrg } from "../OrgContext";
import type { ChatMessage } from "../types";

export function Ask() {
  useDocumentTitle("Agent test");
  const { org, orgId } = useOrg();
  const suggestions = useAsync(() => api.suggestions(orgId), [orgId]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send(question: string) {
    if (!question.trim() || busy) return;
    setBusy(true);
    setInput("");
    setMessages((current) => [
      ...current,
      { role: "user", content: question },
      { role: "assistant", content: "", pending: true },
    ]);
    try {
      const response = await api.ask({
        question,
        org_id: orgId,
        conversation_id: conversationId,
      });
      setConversationId(response.conversation_id);
      setMessages((current) => {
        const next = [...current];
        next[next.length - 1] = {
          role: "assistant",
          content: response.answer,
          citations: response.citations,
          followUps: response.follow_ups,
          provider: response.provider,
          model: response.model,
        };
        return next;
      });
    } catch (error) {
      setMessages((current) => {
        const next = [...current];
        next[next.length - 1] = {
          role: "assistant",
          content: error instanceof Error ? error.message : "The request failed.",
          error: true,
        };
        return next;
      });
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    send(input);
  }

  return (
    <div className="page ask-page">
      <div className="ask-head">
        <div>
          <div className="eyebrow">Seer Agent test surface</div>
          <h1>Ask the service</h1>
          <p>
            This exercises the same grounded-answer path as Sentry's Seer Agent. Sentry's own
            &ldquo;Ask Seer&rdquo; entry point is the primary surface.
          </p>
        </div>
        <div className="ask-status">
          <span className="pulse-dot" />
          {org?.name} · {messages.length ? "conversation active" : "ready"}
        </div>
      </div>

      <div className="chat card">
        <div className="chat__body">
          {!messages.length && (
            <div className="chat__empty">
              <div className="chat__empty-icon"><Sparkles size={22} /></div>
              <h2>What should we look at?</h2>
              <p>Ask about issues, suspects, release regressions, or which error is most fixable.</p>
              <div className="chat__suggestions">
                {suggestions.data?.items.map((suggestion) => (
                  <button key={suggestion} className="suggestion" onClick={() => send(suggestion)}>
                    <MessageSquare size={14} />
                    <span>{suggestion}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((message, index) => (
            <div className={`chat-msg chat-msg--${message.role}`} key={index}>
              <div className="chat-msg__avatar">
                {message.role === "user" ? "You" : <Bot size={15} />}
              </div>
              <div className="chat-msg__bubble">
                {message.pending ? (
                  <span className="chat-typing"><Loader2 size={14} className="spin" /> Reading Sentry context…</span>
                ) : message.error ? (
                  <span className="chat-error">{message.content}</span>
                ) : (
                  <RichText text={message.content} />
                )}
                {message.citations && message.citations.length > 0 && (
                  <div className="citations">
                    {message.citations.map((citation) => (
                      <a
                        className="citation"
                        key={citation.id}
                        href={citation.url ?? "#"}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {citation.title}
                      </a>
                    ))}
                  </div>
                )}
                {message.followUps && message.followUps.length > 0 && (
                  <div className="followups">
                    {message.followUps.map((followUp) => (
                      <button key={followUp} onClick={() => send(followUp)}>{followUp}</button>
                    ))}
                  </div>
                )}
                {message.provider && (
                  <div className="chat-msg__meta">{message.provider} · {message.model}</div>
                )}
              </div>
            </div>
          ))}
          <div ref={endRef} />
        </div>
        <form className="chat__composer" onSubmit={submit}>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                send(input);
              }
            }}
            placeholder="Ask about an issue, a release, or what to fix first…"
            rows={1}
            disabled={busy}
          />
          <button className="btn btn--primary chat__send" disabled={busy || !input.trim()} aria-label="Send">
            {busy ? <Loader2 size={15} className="spin" /> : <ArrowUp size={15} />}
          </button>
        </form>
      </div>
    </div>
  );
}
