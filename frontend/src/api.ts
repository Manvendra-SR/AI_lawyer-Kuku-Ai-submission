// Typed client for the FastAPI backend. All requests go through /api (proxied by Vite in dev).

export type Lang = "en" | "hi" | "hinglish" | "other";

export interface Source {
  ref: string;
  chunk_id: string;
  source_id: string;
  title: string;
  short: string;
  label: string;
  pages: string | null;
  url: string;
  excerpt: string;
  score: number;
}

export interface MessageMeta {
  cited_refs?: string[];
  removed_invalid_refs?: string[];
  unverified_mentions?: string[];
  retrieved_count?: number;
  scope?: string;
}

export interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  language: Lang | null;
  sources: Source[] | null;
  meta: MessageMeta | null;
  created_at: string | null;
  pending?: boolean;
}

export interface CaseEvent {
  id: number;
  position: number;
  date: string | null;
  description: string;
  origin: string;
}

export interface Doc {
  id: number;
  case_id: number;
  doc_type: string;
  title: string;
  content: string;
  language: string;
  sources: Source[] | null;
  checks: { generator?: string; unverified_mentions?: string[]; unknown_numbers?: string[]; placeholders?: number } | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface CaseSummary {
  id: number;
  title: string;
  status: string;
  language: Lang;
  category: string | null;
  seller_name: string | null;
  amount_paid: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface GuidanceItem { text: string; source_refs: string[] }
export interface GuidanceOption { title: string; description: string; source_refs: string[] }

export interface Guidance {
  out_of_scope: boolean;
  message?: string;
  language: Lang;
  generated_at: string;
  what_happened?: string;
  protections?: GuidanceItem[];
  options?: GuidanceOption[];
  next_steps?: string[];
  evidence_to_preserve?: string[];
  general_suggestions?: string[];
  missing_information?: string[];
  uncertainties?: string[];
  sources_sufficient?: boolean;
  lawyer_recommended?: boolean;
  lawyer_note?: string;
  sources: Source[];
  retrieved_sources?: Source[];
  checks: { demoted_uncited_protections?: number; removed_invalid_refs?: string[]; unverified_mentions?: string[] };
}

export interface CaseDetail extends CaseSummary {
  platform: string | null;
  product_details: string | null;
  order_id: string | null;
  purchase_date: string | null;
  delivery_date: string | null;
  description: string | null;
  previous_communication: string | null;
  evidence: string[] | null;
  desired_resolution: string | null;
  missing_information: string[] | null;
  locked_fields: string[] | null;
  guidance: Guidance | null;
  events: CaseEvent[];
  documents: Doc[];
  messages: Message[];
}

export interface ChatResponse {
  case: CaseDetail;
  user_message: Message;
  assistant_message: Message;
  extraction_error: string | null;
}

export interface Health {
  status: string;
  model: string;
  llm_configured: boolean;
  database: string;
  legal_index_chunks: number;
  retrieval?: { mode: string; embedding_model?: string; dense_vectors?: number; error?: string };
}

export interface SourceDoc {
  id: string;
  title: string;
  publisher: string;
  url: string;
  status: string;
  chunks: number;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    });
  } catch {
    throw new ApiError("Cannot reach the NyayaAI backend. Is it running on port 8000?", 0);
  }
  if (res.status === 204) return undefined as T;
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON */
  }
  if (!res.ok) {
    const detail = (body as { detail?: unknown })?.detail;
    const msg = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((d: { msg?: string }) => d.msg).join("; ")
      : `Request failed (${res.status})`;
    throw new ApiError(msg, res.status);
  }
  return body as T;
}

export const api = {
  health: () => request<Health>("/health"),
  sources: () => request<SourceDoc[]>("/sources"),
  chat: (message: string, case_id: number | null) =>
    request<ChatResponse>("/chat", { method: "POST", body: JSON.stringify({ message, case_id }) }),
  listCases: () => request<CaseSummary[]>("/cases"),
  getCase: (id: number) => request<CaseDetail>(`/cases/${id}`),
  updateCase: (id: number, data: Record<string, unknown>) =>
    request<CaseDetail>(`/cases/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteCase: (id: number) => request<void>(`/cases/${id}`, { method: "DELETE" }),
  reExtract: (id: number) => request<CaseDetail>(`/cases/${id}/extract`, { method: "POST" }),
  makeGuidance: (id: number) => request<Guidance>(`/cases/${id}/guidance`, { method: "POST" }),
  makeDocument: (id: number, doc_type: string, language: string) =>
    request<Doc>(`/cases/${id}/documents`, { method: "POST", body: JSON.stringify({ doc_type, language }) }),
  updateDocument: (id: number, content: string) =>
    request<Doc>(`/documents/${id}`, { method: "PUT", body: JSON.stringify({ content }) }),
  deleteDocument: (id: number) => request<void>(`/documents/${id}`, { method: "DELETE" }),
  downloadUrl: (id: number) => `/api/documents/${id}/download`,
};
