import { useCallback, useEffect, useState } from "react";
import { api, type CaseDetail, type Guidance, type Health } from "./api";
import ChatPage from "./pages/ChatPage";
import CasesPage from "./pages/CasesPage";
import GuidancePage from "./pages/GuidancePage";
import DocumentsPage from "./pages/DocumentsPage";
import { ErrorBanner } from "./components/common";

type Page = "chat" | "cases" | "guidance" | "documents";
const NAV: { id: Page; label: string; icon: string }[] = [
  { id: "chat", label: "AI Legal Chat", icon: "💬" },
  { id: "cases", label: "My Cases", icon: "📁" },
  { id: "guidance", label: "Legal Guidance", icon: "⚖️" },
  { id: "documents", label: "Document Studio", icon: "📝" },
];

function readStoredCase(): number | null {
  try {
    const v = localStorage.getItem("nyaya.activeCase");
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}

export default function App() {
  const [page, setPage] = useState<Page>("chat");
  const [activeId, setActiveId] = useState<number | null>(readStoredCase);
  const [caseData, setCaseData] = useState<CaseDetail | null>(null);
  const [loadingCase, setLoadingCase] = useState(false);
  const [caseError, setCaseError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setHealthError((e as Error).message));
  }, []);

  useEffect(() => {
    try {
      if (activeId) localStorage.setItem("nyaya.activeCase", String(activeId));
      else localStorage.removeItem("nyaya.activeCase");
    } catch { /* storage unavailable */ }
  }, [activeId]);

  const loadCase = useCallback(async (id: number) => {
    setLoadingCase(true);
    setCaseError(null);
    try {
      setCaseData(await api.getCase(id));
    } catch (e) {
      const err = e as Error & { status?: number };
      if (err.status === 404) { setActiveId(null); setCaseData(null); }
      else setCaseError(err.message);
    } finally {
      setLoadingCase(false);
    }
  }, []);

  useEffect(() => {
    if (activeId && activeId !== caseData?.id) loadCase(activeId);
    if (!activeId) setCaseData(null);
  }, [activeId, caseData?.id, loadCase]);

  const onCaseUpdated = (c: CaseDetail) => { setCaseData(c); setActiveId(c.id); setRefreshKey((k) => k + 1); };
  const go = (p: Page) => { setPage(p); setMenuOpen(false); };

  return (
    <div className="app">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <div className="brand">
          <div className="logo">न्</div>
          <div>
            <div className="brand-name">NyayaAI</div>
            <div className="brand-sub">Consumer rights assistant</div>
          </div>
        </div>
        <nav>
          {NAV.map((n) => (
            <button key={n.id} type="button" className={`nav-item ${page === n.id ? "active" : ""}`} onClick={() => go(n.id)}>
              <span className="nav-icon" aria-hidden>{n.icon}</span>
              {n.label}
            </button>
          ))}
        </nav>
        {caseData && (
          <div className="active-case">
            <div className="tiny upper">Active case</div>
            <div className="active-title">#{caseData.id} {caseData.title}</div>
          </div>
        )}
        <div className="sys-status">
          {healthError && <div className="dot-row"><span className="dot bad" />Backend unreachable</div>}
          {health && (
            <>
              <div className="dot-row"><span className={`dot ${health.database === "ok" ? "ok" : "bad"}`} />MySQL {health.database === "ok" ? "connected" : "unavailable"}</div>
              <div className="dot-row"><span className={`dot ${health.llm_configured ? "ok" : "bad"}`} />{health.llm_configured ? `Groq · ${health.model}` : "GROQ_API_KEY missing"}</div>
              <div className="dot-row"><span className={`dot ${health.retrieval?.mode?.startsWith("hybrid") ? "ok" : "bad"}`} />
                {health.retrieval?.mode?.startsWith("hybrid")
                  ? `Hybrid search · ${health.legal_index_chunks} passages`
                  : "Legal search index unavailable"}
              </div>
            </>
          )}
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button type="button" className="menu-btn" onClick={() => setMenuOpen(!menuOpen)} aria-label="Menu">☰</button>
          <h1>{NAV.find((n) => n.id === page)?.label}</h1>
          <span className="topbar-note">Legal information, not legal advice</span>
        </header>
        <main className="content">
          {caseError && <ErrorBanner message={caseError} onClose={() => setCaseError(null)} />}
          {page === "chat" && (
            <ChatPage caseData={caseData} loadingCase={loadingCase} onCaseUpdated={onCaseUpdated}
              onNewChat={() => { setActiveId(null); setCaseData(null); }} goTo={go} />
          )}
          {page === "cases" && (
            <CasesPage activeId={activeId} caseData={caseData} onSelect={(id) => setActiveId(id)} onCaseUpdated={onCaseUpdated}
              onDeleted={() => { setActiveId(null); setCaseData(null); setRefreshKey((k) => k + 1); }} goTo={go} refreshKey={refreshKey} />
          )}
          {page === "guidance" && (
            <GuidancePage caseData={caseData} goTo={go}
              onGuidance={(g: Guidance) => caseData && setCaseData({ ...caseData, guidance: g })} />
          )}
          {page === "documents" && (
            <DocumentsPage caseData={caseData} goTo={go} onDocsChanged={() => caseData && loadCase(caseData.id)} />
          )}
        </main>
      </div>
    </div>
  );
}
