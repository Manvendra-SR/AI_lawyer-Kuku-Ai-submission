import { useState, type ReactNode } from "react";
import type { Source } from "../api";

/** Renders inline **bold** and [S#] citation chips. No HTML injection: everything is React text. */
function renderInline(text: string, onCite?: (ref: string) => void): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|\[S\d{1,2}\])/g);
  return parts.map((p, i) => {
    if (/^\*\*[^*]+\*\*$/.test(p)) return <strong key={i}>{p.slice(2, -2)}</strong>;
    const m = p.match(/^\[(S\d{1,2})\]$/);
    if (m)
      return (
        <button key={i} type="button" className="cite-chip" onClick={() => onCite?.(m[1])} title="View source">
          {m[1]}
        </button>
      );
    return <span key={i}>{p}</span>;
  });
}

/** Minimal block renderer: paragraphs, bullet and numbered lists, simple headings. */
export function RichText({ text, onCite }: { text: string; onCite?: (ref: string) => void }) {
  const blocks: ReactNode[] = [];
  const lines = text.split(/\r?\n/);
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const items = list.items.map((it, i) => <li key={i}>{renderInline(it, onCite)}</li>);
    blocks.push(list.ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>);
    list = null;
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const num = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (bullet || num) {
      const ordered = !!num;
      if (!list || list.ordered !== ordered) {
        flush();
        list = { ordered, items: [] };
      }
      list.items.push((bullet || num)![1]);
      continue;
    }
    flush();
    if (!line.trim()) continue;
    const h = line.match(/^#{1,4}\s+(.*)$/);
    if (h) blocks.push(<h4 key={blocks.length}>{renderInline(h[1], onCite)}</h4>);
    else blocks.push(<p key={blocks.length}>{renderInline(line, onCite)}</p>);
  }
  flush();
  return <div className="rich">{blocks}</div>;
}

export function SourceCard({ s, highlighted }: { s: Source; highlighted?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`source-card ${highlighted ? "hl" : ""}`} id={`src-${s.chunk_id}`}>
      <div className="source-head">
        <span className="ref-badge">{s.ref}</span>
        <div className="source-meta">
          <a href={s.url} target="_blank" rel="noreferrer" className="source-title">
            {s.title}
          </a>
          <div className="source-loc">
            {s.label}
            {s.pages ? ` · ${s.pages}` : ""} · <span className="verified">Official source</span>
          </div>
        </div>
        <button type="button" className="link-btn" onClick={() => setOpen(!open)}>
          {open ? "Hide text" : "Show text"}
        </button>
      </div>
      {open && <pre className="source-excerpt">{s.excerpt}</pre>}
    </div>
  );
}

export function SourceList({ sources, highlight, title = "Official sources" }: {
  sources: Source[]; highlight?: string | null; title?: string;
}) {
  if (!sources.length) return null;
  return (
    <div className="source-list">
      <div className="source-list-title">{title}</div>
      {sources.map((s) => <SourceCard key={s.chunk_id} s={s} highlighted={highlight === s.ref} />)}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner-wrap">
      <span className="spinner" aria-hidden />
      {label && <span>{label}</span>}
    </span>
  );
}

export function ErrorBanner({ message, onClose }: { message: string; onClose?: () => void }) {
  return (
    <div className="error-banner" role="alert">
      <span>{message}</span>
      {onClose && <button type="button" className="link-btn" onClick={onClose}>Dismiss</button>}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export function Warnings({ items, label }: { items?: string[]; label: string }) {
  if (!items || !items.length) return null;
  return (
    <div className="warn-box">
      <strong>{label}</strong> {items.join(", ")}
    </div>
  );
}
