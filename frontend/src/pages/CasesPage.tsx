import { useEffect, useState } from "react";
import { api, type CaseDetail, type CaseSummary } from "../api";
import { EmptyState, ErrorBanner, Spinner } from "../components/common";
import { CATEGORY_LABELS, FIELD_LABELS, LANG_LABELS, STATUS_LABELS } from "../labels";

const TEXT_FIELDS = ["title", "seller_name", "platform", "product_details", "order_id", "purchase_date",
  "delivery_date", "amount_paid"] as const;
const AREA_FIELDS = ["description", "previous_communication", "desired_resolution"] as const;

interface Props {
  activeId: number | null;
  caseData: CaseDetail | null;
  onSelect: (id: number) => void;
  onCaseUpdated: (c: CaseDetail) => void;
  onDeleted: (id: number) => void;
  goTo: (page: "chat" | "guidance" | "documents") => void;
  refreshKey: number;
}

type Form = Record<string, string>;
interface EventRow { date: string; description: string }

function toForm(c: CaseDetail): Form {
  const f: Form = {};
  for (const k of [...TEXT_FIELDS, ...AREA_FIELDS, "category"]) f[k] = ((c as unknown as Record<string, unknown>)[k] as string) ?? "";
  f.evidence = (c.evidence ?? []).join("\n");
  return f;
}

export default function CasesPage({ activeId, caseData, onSelect, onCaseUpdated, onDeleted, goTo, refreshKey }: Props) {
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [form, setForm] = useState<Form>({});
  const [events, setEvents] = useState<EventRow[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.listCases().then(setCases).catch((e) => setListError((e as Error).message));
  }, [refreshKey, caseData?.updated_at]);

  useEffect(() => {
    if (!caseData) return;
    setForm(toForm(caseData));
    setEvents(caseData.events.map((e) => ({ date: e.date ?? "", description: e.description })));
    setDirty(false);
    setSaved(false);
  }, [caseData]);

  const set = (k: string, v: string) => { setForm((f) => ({ ...f, [k]: v })); setDirty(true); setSaved(false); };

  async function save() {
    if (!caseData) return;
    setBusy("save"); setError(null);
    const orig = toForm(caseData);
    const payload: Record<string, unknown> = {};
    for (const k of Object.keys(form)) {
      if (form[k] === orig[k]) continue;
      payload[k] = k === "evidence" ? form[k].split("\n").map((s) => s.trim()).filter(Boolean) : form[k];
    }
    const origEvents = caseData.events.map((e) => ({ date: e.date ?? "", description: e.description }));
    if (JSON.stringify(origEvents) !== JSON.stringify(events)) {
      payload.events = events.filter((e) => e.description.trim()).map((e) => ({ date: e.date.trim() || null, description: e.description.trim() }));
    }
    try {
      const updated = await api.updateCase(caseData.id, payload);
      onCaseUpdated(updated);
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function reExtract() {
    if (!caseData) return;
    setBusy("extract"); setError(null);
    try { onCaseUpdated(await api.reExtract(caseData.id)); } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  async function remove() {
    if (!caseData || !window.confirm(`Delete case "${caseData.title}" and its conversation? This cannot be undone.`)) return;
    setBusy("delete");
    try { await api.deleteCase(caseData.id); onDeleted(caseData.id); } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  const locked = new Set(caseData?.locked_fields ?? []);

  return (
    <div className="cases-layout">
      <aside className="card case-list">
        <h3>My cases</h3>
        {listError && <ErrorBanner message={listError} />}
        {!cases && !listError && <Spinner label="Loading…" />}
        {cases && cases.length === 0 && <p className="muted small">No cases yet. Start in AI Legal Chat.</p>}
        {cases?.map((c) => (
          <button key={c.id} type="button" className={`case-item ${c.id === activeId ? "active" : ""}`} onClick={() => onSelect(c.id)}>
            <div className="case-item-title">{c.title}</div>
            <div className="case-item-meta">
              <span className={`status-pill ${c.status}`}>{STATUS_LABELS[c.status] ?? c.status}</span>
              <span className="muted small">#{c.id}{LANG_LABELS[c.language] ? ` · ${LANG_LABELS[c.language]}` : ""}</span>
            </div>
          </button>
        ))}
      </aside>

      <section className="card case-editor">
        {!caseData && <EmptyState title="Select a case"><p className="muted">Pick a case on the left, or start a new chat.</p></EmptyState>}
        {caseData && (
          <>
            <div className="editor-head">
              <div>
                <h2>Case #{caseData.id}</h2>
                <p className="muted small">
                  Extracted automatically from your conversation. Review carefully and correct anything wrong —
                  fields you edit are marked and will not be overwritten by the AI. Empty fields were not mentioned; NyayaAI never fills them in by guessing.
                </p>
              </div>
              <div className="btn-row">
                <button type="button" className="btn secondary" onClick={() => goTo("chat")}>Open chat</button>
                <button type="button" className="btn secondary" onClick={reExtract} disabled={!!busy}>
                  {busy === "extract" ? "Re-reading…" : "Re-read conversation"}
                </button>
                <button type="button" className="btn danger" onClick={remove} disabled={!!busy}>Delete</button>
              </div>
            </div>
            {error && <ErrorBanner message={error} onClose={() => setError(null)} />}

            {caseData.missing_information && caseData.missing_information.length > 0 && caseData.status !== "out_of_scope" && (
              <div className="missing inline">
                <strong className="small">Missing information:</strong>{" "}
                {caseData.missing_information.map((f) => FIELD_LABELS[f] ?? f).join(" · ")}
              </div>
            )}

            <div className="form-grid">
              <label className="field">
                <span>{FIELD_LABELS.category}{locked.has("category") && <em className="edited">edited by you</em>}</span>
                <select value={form.category ?? ""} onChange={(e) => set("category", e.target.value)}>
                  <option value="">—</option>
                  {Object.entries(CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </label>
              {TEXT_FIELDS.map((k) => (
                <label key={k} className={`field ${k === "title" ? "wide" : ""}`}>
                  <span>{FIELD_LABELS[k]}{locked.has(k) && <em className="edited">edited by you</em>}</span>
                  <input value={form[k] ?? ""} placeholder="Not provided" onChange={(e) => set(k, e.target.value)} />
                </label>
              ))}
              {AREA_FIELDS.map((k) => (
                <label key={k} className="field wide">
                  <span>{FIELD_LABELS[k]}{locked.has(k) && <em className="edited">edited by you</em>}</span>
                  <textarea rows={3} value={form[k] ?? ""} placeholder="Not provided" onChange={(e) => set(k, e.target.value)} />
                </label>
              ))}
              <label className="field wide">
                <span>{FIELD_LABELS.evidence} <em className="muted">(one per line)</em>{locked.has("evidence") && <em className="edited">edited by you</em>}</span>
                <textarea rows={3} value={form.evidence ?? ""} placeholder="e.g. Invoice PDF&#10;Photos of damaged box" onChange={(e) => set("evidence", e.target.value)} />
              </label>
            </div>

            <h3 className="section-h">Timeline {locked.has("events") && <em className="edited">edited by you</em>}</h3>
            <div className="timeline-edit">
              {events.length === 0 && <p className="muted small">No events recorded yet.</p>}
              {events.map((ev, i) => (
                <div key={i} className="timeline-row">
                  <span className="tl-dot" />
                  <input className="tl-date" value={ev.date} placeholder="Date (as you know it)"
                    onChange={(e) => { const v = [...events]; v[i] = { ...v[i], date: e.target.value }; setEvents(v); setDirty(true); }} />
                  <input className="tl-desc" value={ev.description} placeholder="What happened"
                    onChange={(e) => { const v = [...events]; v[i] = { ...v[i], description: e.target.value }; setEvents(v); setDirty(true); }} />
                  <button type="button" className="link-btn" aria-label="Remove event"
                    onClick={() => { setEvents(events.filter((_, j) => j !== i)); setDirty(true); }}>✕</button>
                </div>
              ))}
              <button type="button" className="link-btn" onClick={() => { setEvents([...events, { date: "", description: "" }]); setDirty(true); }}>
                + Add event
              </button>
            </div>

            <div className="save-bar">
              {saved && !dirty && <span className="ok-text">Saved to database ✓</span>}
              <button type="button" className="btn primary" disabled={!dirty || !!busy} onClick={save}>
                {busy === "save" ? "Saving…" : "Save changes"}
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
