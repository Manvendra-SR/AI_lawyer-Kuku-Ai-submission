import { useEffect, useState } from "react";
import { api, type CaseDetail, type Doc } from "../api";
import { EmptyState, ErrorBanner, SourceList, Spinner, Warnings } from "../components/common";
import { DOC_TYPES } from "../labels";

interface Props {
  caseData: CaseDetail | null;
  onDocsChanged: () => void;
  goTo: (page: "chat" | "cases") => void;
}

export default function DocumentsPage({ caseData, onDocsChanged, goTo }: Props) {
  const [docs, setDocs] = useState<Doc[]>(caseData?.documents ?? []);
  const [selected, setSelected] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [lang, setLang] = useState<"en" | "hi">("en");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  useEffect(() => {
    const d = caseData?.documents ?? [];
    setDocs(d);
    if (!d.find((x) => x.id === selected)) setSelected(d.length ? d[d.length - 1].id : null);
    setLang(caseData?.language === "hi" ? "hi" : "en");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseData?.id, caseData?.documents]);

  const doc = docs.find((d) => d.id === selected) ?? null;
  useEffect(() => {
    setText(doc?.content ?? "");
  }, [doc?.id, doc?.content]);

  const note = (m: string) => { setFlash(m); window.setTimeout(() => setFlash(null), 2500); };

  async function generate(type: string) {
    if (!caseData) return;
    setBusy(type); setError(null);
    try {
      const d = await api.makeDocument(caseData.id, type, lang);
      setDocs((x) => [...x, d]);
      setSelected(d.id);
      onDocsChanged();
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  async function save() {
    if (!doc) return;
    setBusy("save");
    try {
      const d = await api.updateDocument(doc.id, text);
      setDocs((x) => x.map((y) => (y.id === d.id ? d : y)));
      onDocsChanged();
      note("Saved ✓");
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  async function remove() {
    if (!doc || !window.confirm("Delete this draft?")) return;
    try {
      await api.deleteDocument(doc.id);
      setDocs((x) => x.filter((y) => y.id !== doc.id));
      setSelected(null);
      onDocsChanged();
    } catch (e) { setError((e as Error).message); }
  }

  async function copy() {
    try { await navigator.clipboard.writeText(text); note("Copied to clipboard ✓"); }
    catch { setError("Clipboard not available — select the text and copy manually."); }
  }

  function download() {
    if (!doc) return;
    if (text !== doc.content) {
      // Download exactly what is on screen, including unsaved edits.
      const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `nyayaai_case${doc.case_id}_${doc.doc_type}_${doc.id}.txt`;
      a.click();
      URL.revokeObjectURL(a.href);
    } else {
      window.location.href = api.downloadUrl(doc.id);
    }
  }

  if (!caseData)
    return (
      <div className="card"><EmptyState title="No case selected">
        <p className="muted">Documents are drafted from a saved case.</p>
        <button type="button" className="btn primary" onClick={() => goTo("chat")}>Start a chat</button>
      </EmptyState></div>
    );

  const outOfScope = caseData.status === "out_of_scope";

  return (
    <div className="docs-layout">
      <aside className="card doc-side">
        <h3>Create a draft</h3>
        <p className="muted small">Case #{caseData.id}: {caseData.title}</p>
        <div className="lang-toggle">
          <span className="small">Language:</span>
          <button type="button" className={`seg ${lang === "en" ? "on" : ""}`} onClick={() => setLang("en")}>English</button>
          <button type="button" className={`seg ${lang === "hi" ? "on" : ""}`} onClick={() => setLang("hi")}>हिंदी</button>
        </div>
        {outOfScope ? (
          <p className="warn-box">This case is outside NyayaAI's consumer scope, so no documents can be drafted.</p>
        ) : (
          DOC_TYPES.map((t) => (
            <button key={t.id} type="button" className="doc-type" onClick={() => generate(t.id)} disabled={!!busy}>
              <div className="doc-type-title">{busy === t.id ? <Spinner label="Drafting…" /> : t.label}</div>
              <div className="muted tiny">{t.hint}</div>
            </button>
          ))
        )}
        {caseData.missing_information && caseData.missing_information.length > 0 && !outOfScope && (
          <p className="muted tiny">
            Missing details will appear as [PLACEHOLDERS]. <button type="button" className="link-btn tiny" onClick={() => goTo("cases")}>Complete your case</button>
          </p>
        )}
        {docs.length > 0 && (
          <>
            <h3 className="section-h">Saved drafts</h3>
            {docs.map((d) => (
              <button key={d.id} type="button" className={`case-item ${d.id === selected ? "active" : ""}`} onClick={() => setSelected(d.id)}>
                <div className="case-item-title">{d.title}</div>
                <div className="muted tiny">#{d.id} · {d.language === "hi" ? "हिंदी" : "English"} · {d.updated_at ? new Date(d.updated_at).toLocaleString() : ""}</div>
              </button>
            ))}
          </>
        )}
      </aside>

      <section className="card doc-editor">
        {error && <ErrorBanner message={error} onClose={() => setError(null)} />}
        {!doc && <EmptyState title="No draft selected"><p className="muted">Choose a document type on the left to generate a draft from your case.</p></EmptyState>}
        {doc && (
          <>
            <div className="editor-head">
              <div>
                <h2>{doc.title}</h2>
                <span className="draft-badge">DRAFT — verify before use</span>
              </div>
              <div className="btn-row">
                {flash && <span className="ok-text">{flash}</span>}
                <button type="button" className="btn secondary" onClick={copy}>Copy</button>
                <button type="button" className="btn secondary" onClick={download}>Download .txt</button>
                <button type="button" className="btn primary" onClick={save} disabled={busy === "save" || text === doc.content}>
                  {busy === "save" ? "Saving…" : "Save edits"}
                </button>
                <button type="button" className="btn danger" onClick={remove}>Delete</button>
              </div>
            </div>
            <Warnings items={doc.checks?.unverified_mentions} label="⚠ Legal references not found in retrieved sources — verify or remove:" />
            <Warnings items={doc.checks?.unknown_numbers} label="⚠ Numbers not found in your case record — check them:" />
            {!!doc.checks?.placeholders && (
              <div className="notice small">{doc.checks.placeholders} placeholder(s) like [YOUR FULL NAME] need to be filled in.</div>
            )}
            <textarea className="doc-text" value={text} onChange={(e) => setText(e.target.value)} spellCheck />
            {doc.sources && doc.sources.length > 0 && <SourceList sources={doc.sources} title="Provisions referred to in this draft" />}
            {doc.checks?.generator && <p className="muted tiny">Generator: {doc.checks.generator}</p>}
          </>
        )}
      </section>
    </div>
  );
}
