import { useState } from "react";
import { api, type CaseDetail, type Guidance, type Source } from "../api";
import { EmptyState, ErrorBanner, RichText, SourceList, Spinner, Warnings } from "../components/common";
import { DISCLAIMER, FIELD_LABELS } from "../labels";

interface Props {
  caseData: CaseDetail | null;
  onGuidance: (g: Guidance) => void;
  goTo: (page: "chat" | "cases" | "documents") => void;
}

function Refs({ refs, onCite }: { refs: string[]; onCite: (r: string) => void }) {
  if (!refs.length) return null;
  return (
    <span className="refs">
      {refs.map((r) => <button key={r} type="button" className="cite-chip" onClick={() => onCite(r)}>{r}</button>)}
    </span>
  );
}

function List({ title, items, tone }: { title: string; items?: string[]; tone?: string }) {
  if (!items || !items.length) return null;
  return (
    <div className={`g-block ${tone ?? ""}`}>
      <h3>{title}</h3>
      <ul>{items.map((t, i) => <li key={i}><RichText text={t} /></li>)}</ul>
    </div>
  );
}

export default function GuidancePage({ caseData, onGuidance, goTo }: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hl, setHl] = useState<string | null>(null);
  const g = caseData?.guidance ?? null;

  async function generate() {
    if (!caseData) return;
    setLoading(true); setError(null);
    try { onGuidance(await api.makeGuidance(caseData.id)); } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  }

  const cite = (ref: string) => {
    setHl(ref);
    const s = g?.sources.find((x: Source) => x.ref === ref);
    if (s) document.getElementById(`src-${s.chunk_id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  if (!caseData)
    return (
      <div className="card"><EmptyState title="No case selected">
        <p className="muted">Start a conversation in AI Legal Chat or pick a case in My Cases.</p>
        <button type="button" className="btn primary" onClick={() => goTo("chat")}>Go to chat</button>
      </EmptyState></div>
    );

  const missing = caseData.missing_information ?? [];

  return (
    <div className="guidance-layout">
      <section className="card">
        <div className="editor-head">
          <div>
            <h2>Legal guidance</h2>
            <p className="muted small">Case #{caseData.id}: {caseData.title}</p>
          </div>
          <button type="button" className="btn primary" onClick={generate} disabled={loading}>
            {loading ? "Analysing…" : g ? "Regenerate guidance" : "Generate guidance"}
          </button>
        </div>
        <p className="disclaimer">{DISCLAIMER}</p>
        {error && <ErrorBanner message={error} onClose={() => setError(null)} />}
        {missing.length > 0 && caseData.status !== "out_of_scope" && (
          <div className="missing inline">
            <strong className="small">Guidance will be more precise if you add:</strong>{" "}
            {missing.map((f) => FIELD_LABELS[f] ?? f).join(" · ")}{" "}
            <button type="button" className="link-btn small" onClick={() => goTo("chat")}>Continue chat</button>
          </div>
        )}
        {loading && <div className="center-pad"><Spinner label="Retrieving official sources and preparing guidance…" /></div>}
        {!loading && !g && (
          <EmptyState title="No guidance yet">
            <p className="muted">NyayaAI will retrieve relevant official provisions and explain your options, citing each source.</p>
          </EmptyState>
        )}

        {!loading && g?.out_of_scope && (
          <div className="g-block warn"><h3>Outside NyayaAI's scope</h3><p>{g.message}</p></div>
        )}

        {!loading && g && !g.out_of_scope && (
          <div className="guidance">
            <div className="g-block">
              <h3>What happened</h3>
              <RichText text={g.what_happened ?? ""} />
            </div>

            <div className="g-block verified">
              <h3>Relevant consumer protections <span className="badge-verified">Verified from official sources</span></h3>
              {g.protections && g.protections.length > 0 ? (
                <ul>
                  {g.protections.map((p, i) => (
                    <li key={i}><RichText text={p.text} onCite={cite} /> <Refs refs={p.source_refs} onCite={cite} /></li>
                  ))}
                </ul>
              ) : (
                <p className="muted">The retrieved sources did not clearly cover this issue, so no protection is stated as verified.</p>
              )}
            </div>

            {g.options && g.options.length > 0 && (
              <div className="g-block">
                <h3>Possible options</h3>
                <div className="options">
                  {g.options.map((o, i) => (
                    <div key={i} className="option">
                      <div className="option-title">{o.title} <Refs refs={o.source_refs} onCite={cite} /></div>
                      <RichText text={o.description} onCite={cite} />
                      {o.source_refs.length === 0 && <div className="muted tiny">General suggestion — not taken from a cited source.</div>}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {g.next_steps && g.next_steps.length > 0 && (
              <div className="g-block">
                <h3>Recommended next steps</h3>
                <ol>{g.next_steps.map((s, i) => <li key={i}><RichText text={s} onCite={cite} /></li>)}</ol>
              </div>
            )}
            <List title="Evidence to preserve" items={g.evidence_to_preserve} />
            <List title="General suggestions (not from cited sources)" items={g.general_suggestions} tone="soft" />
            <List title="Missing information" items={g.missing_information} tone="soft" />
            <List title="Uncertainties & limits" items={g.uncertainties} tone="warn" />
            {g.lawyer_note && (
              <div className={`g-block ${g.lawyer_recommended ? "warn" : "soft"}`}>
                <h3>When to consult a lawyer</h3>
                <p>{g.lawyer_note}</p>
              </div>
            )}
            <Warnings items={g.checks?.unverified_mentions} label="⚠ References not found in retrieved sources (unverified):" />
            {!!g.checks?.demoted_uncited_protections && (
              <p className="muted tiny">
                {g.checks.demoted_uncited_protections} uncited statement(s) were moved from “protections” to “general suggestions” by the verification step.
              </p>
            )}
            <p className="muted tiny">Generated {new Date(g.generated_at).toLocaleString()}</p>
            <div className="btn-row">
              <button type="button" className="btn primary" onClick={() => goTo("documents")}>Draft a complaint</button>
            </div>
          </div>
        )}
      </section>

      <aside className="card sources-aside">
        <h3>Official references</h3>
        {g && !g.out_of_scope && g.sources.length > 0 ? (
          <SourceList sources={g.sources} highlight={hl} title="Cited in this guidance" />
        ) : (
          <p className="muted small">Sources cited in the guidance will appear here, with links to the official documents.</p>
        )}
        {g && !g.out_of_scope && g.retrieved_sources && g.retrieved_sources.length > g.sources.length && (
          <details className="more-sources">
            <summary>Other retrieved passages ({g.retrieved_sources.length - g.sources.length})</summary>
            <SourceList sources={g.retrieved_sources.filter((s) => !g.sources.some((c) => c.ref === s.ref))} title="" />
          </details>
        )}
      </aside>
    </div>
  );
}
