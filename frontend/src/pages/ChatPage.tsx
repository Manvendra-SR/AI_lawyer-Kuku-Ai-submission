import { useEffect, useRef, useState } from "react";
import { api, type CaseDetail, type Message } from "../api";
import { ErrorBanner, RichText, SourceList, Spinner, Warnings } from "../components/common";
import { CATEGORY_LABELS, FIELD_LABELS, LANG_LABELS, STATUS_LABELS } from "../labels";

const EXAMPLES = [
  { lang: "English", text: "I purchased a laptop online, but it arrived damaged and the seller is refusing a refund." },
  { lang: "Hinglish", text: "Maine Amazon se phone kharida tha, lekin damaged aaya aur seller refund nahi de raha." },
  { lang: "हिंदी", text: "मैंने ऑनलाइन ऑर्डर कैंसिल किया था लेकिन 20 दिन बाद भी रिफंड नहीं आया।" },
];

interface Props {
  caseData: CaseDetail | null;
  loadingCase: boolean;
  onCaseUpdated: (c: CaseDetail) => void;
  onNewChat: () => void;
  goTo: (page: "guidance" | "documents" | "cases") => void;
}

export default function ChatPage({ caseData, loadingCase, onCaseUpdated, onNewChat, goTo }: Props) {
  const [messages, setMessages] = useState<Message[]>(caseData?.messages ?? []);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [openSources, setOpenSources] = useState<Record<number, { open: boolean; hl: string | null }>>({});
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMessages(caseData?.messages ?? []);
  }, [caseData?.id, caseData?.messages]);
  // Block body on purpose: newer browsers return a Promise from scrollIntoView, and returning that
  // from an effect makes React treat it as a cleanup function and crash ("destroy is not a function").
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, sending]);

  async function send(text: string) {
    const msg = text.trim();
    if (!msg || sending) return;
    setError(null);
    setNotice(null);
    setSending(true);
    setInput("");
    const temp: Message = { id: -Date.now(), role: "user", content: msg, language: null, sources: null, meta: null, created_at: null, pending: true };
    setMessages((m) => [...m, temp]);
    try {
      const res = await api.chat(msg, caseData?.id ?? null);
      onCaseUpdated(res.case);
      setMessages(res.case.messages);
      if (res.extraction_error) setNotice(`Reply received, but the case record could not be updated: ${res.extraction_error}`);
    } catch (e) {
      setMessages((m) => m.filter((x) => x.id !== temp.id));
      setInput(msg);
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  const known = caseData
    ? (["seller_name", "platform", "product_details", "purchase_date", "amount_paid", "order_id", "desired_resolution"] as const)
        .filter((k) => caseData[k])
    : [];

  return (
    <div className="chat-layout">
      <section className="chat-main card">
        <div className="chat-header">
          <div>
            <h2>{caseData ? caseData.title : "New consultation"}</h2>
            <p className="muted small">
              Describe your problem in English, हिंदी or Hinglish. NyayaAI replies in your language.
            </p>
          </div>
          <button type="button" className="btn secondary" onClick={onNewChat} disabled={sending}>+ New chat</button>
        </div>

        <div className="chat-scroll">
          {loadingCase && <div className="center-pad"><Spinner label="Loading conversation…" /></div>}
          {!loadingCase && messages.length === 0 && (
            <div className="welcome">
              <h3>What happened with your purchase?</h3>
              <p className="muted">
                NyayaAI helps with damaged or defective products, refunds not received, undelivered or wrong items, and
                sellers refusing refunds. Try an example:
              </p>
              <div className="examples">
                {EXAMPLES.map((ex) => (
                  <button key={ex.text} type="button" className="example" onClick={() => send(ex.text)}>
                    <span className="example-lang">{ex.lang}</span>
                    {ex.text}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m) => (
            <div key={m.id} className={`msg ${m.role} ${m.pending ? "pending" : ""}`}>
              <div className="bubble">
                {m.role === "assistant" ? (
                  <RichText text={m.content} onCite={(ref) => setOpenSources((s) => ({ ...s, [m.id]: { open: true, hl: ref } }))} />
                ) : (
                  <p className="pre">{m.content}</p>
                )}
              </div>
              {m.role === "assistant" && (
                <div className="msg-foot">
                  {m.language && LANG_LABELS[m.language] && <span className="tag">{LANG_LABELS[m.language]}</span>}
                  {m.meta?.scope === "out_of_scope" && <span className="tag warn">Outside scope</span>}
                  {m.sources && m.sources.length > 0 && (
                    <button type="button" className="link-btn small"
                      onClick={() => setOpenSources((s) => ({ ...s, [m.id]: { open: !s[m.id]?.open, hl: null } }))}>
                      {openSources[m.id]?.open ? "Hide" : "Show"} {m.sources.length} source{m.sources.length > 1 ? "s" : ""}
                    </button>
                  )}
                </div>
              )}
              {m.role === "assistant" && (
                <Warnings items={m.meta?.unverified_mentions}
                  label="⚠ Legal references not found in retrieved sources (treat as unverified):" />
              )}
              {m.role === "assistant" && m.sources && openSources[m.id]?.open && (
                <SourceList sources={m.sources} highlight={openSources[m.id]?.hl} />
              )}
            </div>
          ))}
          {sending && (
            <div className="msg assistant">
              <div className="bubble"><Spinner label="Reading the law and thinking…" /></div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        {error && <ErrorBanner message={error} onClose={() => setError(null)} />}
        {notice && <div className="notice">{notice}</div>}

        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <textarea
            value={input}
            placeholder="Type your message… (Enter to send, Shift+Enter for a new line)"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            rows={2}
            disabled={sending}
          />
          <button type="submit" className="btn primary" disabled={sending || !input.trim()}>
            {sending ? "Sending…" : "Send"}
          </button>
        </form>
      </section>

      <aside className="case-panel card">
        <h3>Your case so far</h3>
        {!caseData && <p className="muted small">Facts you share will be organised here automatically.</p>}
        {caseData && (
          <>
            <div className="pill-row">
              <span className={`status-pill ${caseData.status}`}>{STATUS_LABELS[caseData.status] ?? caseData.status}</span>
              {caseData.category && <span className="tag">{CATEGORY_LABELS[caseData.category] ?? caseData.category}</span>}
            </div>
            <dl className="facts">
              {known.map((k) => (
                <div key={k}>
                  <dt>{FIELD_LABELS[k]}</dt>
                  <dd>{caseData[k] as string}</dd>
                </div>
              ))}
            </dl>
            {caseData.missing_information && caseData.missing_information.length > 0 && caseData.status !== "out_of_scope" && (
              <div className="missing">
                <div className="small strong">Still missing</div>
                <ul>{caseData.missing_information.map((f) => <li key={f}>{FIELD_LABELS[f] ?? f}</li>)}</ul>
              </div>
            )}
            <div className="panel-actions">
              <button type="button" className="btn secondary block" onClick={() => goTo("cases")}>Review &amp; edit case</button>
              {caseData.status !== "out_of_scope" && (
                <>
                  <button type="button" className="btn primary block" onClick={() => goTo("guidance")}>Get legal guidance</button>
                  <button type="button" className="btn secondary block" onClick={() => goTo("documents")}>Draft documents</button>
                </>
              )}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
