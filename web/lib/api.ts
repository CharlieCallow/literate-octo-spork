// Tiny client for the Forte backend. Sends HTTP basic auth from localStorage.

export type ReportStage =
  | "queued" | "brief" | "recruit" | "research" | "charts" | "draft"
  | "rebuttal" | "redteam" | "edit" | "audit" | "render" | "feedback"
  | "housekeeping" | "done" | "failed" | "cancelled";

export type ReportMode = "fast" | "standard" | "deep";

// Rough estimates used for the confirm dialog and on-screen ETA. Tuned against
// real reports; treat as "expected" not guaranteed.
export const MODE_ESTIMATES: Record<ReportMode, { cost_lo: number; cost_hi: number; minutes: number }> = {
  fast:     { cost_lo: 0.05, cost_hi: 0.15, minutes: 2 },
  standard: { cost_lo: 0.50, cost_hi: 1.00, minutes: 6 },
  deep:     { cost_lo: 1.50, cost_hi: 3.00, minutes: 12 },
};

export interface Report {
  id: number;
  theme: string;
  subtitle: string | null;
  mode: ReportMode;
  budget_cap_usd: number | null;
  team_override: string[];
  contributor_slugs?: string[];
  stage: ReportStage;
  error: string | null;
  cost_usd: number;
  pdf_url: string | null;
  created_at: string;
  // Source-diversity flag, set at render time (null when not yet computed).
  max_domain_share?: number | null;
  top_domain?: string | null;
}

export interface PositionRow {
  id: number;
  report_id: number;
  asset: string;
  direction: "long" | "short" | "fade" | "avoid";
  horizon_days: number;
  target_level: number | null;
  conviction: number;
  contributor_slug: string;
  claim_text: string;
  made_at: string;
  price_at_call: number | null;
  evaluated_at: string | null;
  price_at_evaluation: number | null;
  outcome: "hit" | "miss" | "partial" | null;
}

export interface AskResponse {
  reply: string;
  cost_usd: number;
}

export interface TeamMember {
  slug: string;
  name: string;
  role: string;
  reports_contributed: number;
  last_assignment_at: string | null;
  rewrite_ratio: number | null;
}

export interface AppSettings {
  model_haiku: string;
  model_sonnet: string;
  model_opus: string;
  cost_per_report_usd: number;
  cost_per_day_usd: number;
}

export interface PersonaDetail {
  slug: string;
  name: string;
  role: string;
  markdown: string;
  reports_contributed: number;
  last_assignment_at: string | null;
}

export type RecKind = "promote" | "fire";
export type RecStatus = "pending" | "approved" | "dismissed";

export interface Recommendation {
  id: number;
  kind: RecKind;
  subject_slug: string;
  subject_name: string;
  subject_role: string;
  reasoning: string;
  status: RecStatus;
  created_at: string;
  resolved_at: string | null;
}

export interface CreateReportPayload {
  theme: string;
  subtitle?: string;
  mode?: ReportMode;
  team_override?: string[];
  budget_cap_usd?: number | null;
}

const API_BASE =
  (typeof window !== "undefined" && (window as any).__API_BASE__) ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://localhost:8000";

function authHeader(): string | null {
  if (typeof window === "undefined") return null;
  const c = window.localStorage.getItem("forte_auth");
  return c ? `Basic ${c}` : null;
}

export function setAuth(user: string, password: string): void {
  window.localStorage.setItem("forte_auth", btoa(`${user}:${password}`));
}

export function clearAuth(): void {
  window.localStorage.removeItem("forte_auth");
}

export function hasAuth(): boolean {
  return !!authHeader();
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const auth = authHeader();
  const headers: Record<string, string> = {
    "content-type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (auth) headers["authorization"] = auth;
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export interface Job {
  id: number;
  stage: ReportStage;
  status: "pending" | "running" | "done" | "failed";
  attempts: number;
  cost_usd: number;
  last_error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Theme {
  id: number;
  scout_run_id: number;
  headline: string;
  why_now: string;
  dig_into: string;
  source_urls: string[];
  score: number;
  surfaced_at: string;
  commissioned_report_id: number | null;
}

export interface AuditEntry {
  id: number;
  actor: string;
  event: string;
  cost_usd: number;
  details: Record<string, unknown>;
  created_at: string;
}

export interface ChartSpec {
  kind: "line" | "bar" | "regime" | "comparison" | "event";
  title: string;
  subtitle: string;
  source: string;
  as_of: string;
  index: string[];
  series: Record<string, (number | null)[]>;
  shaded?: [string, string][];
  events?: { date: string; label: string }[];
  dual_axis?: boolean;
  horizontal?: boolean;
  palette: { cycle: string[]; navy: string; teal: string; rule: string; muted: string };
}

export interface ScoutRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  n_themes: number;
  cost_usd: number;
  error: string | null;
}

export interface ShareInfo {
  report_id: number;
  share_token: string | null;
  shared_at: string | null;
}

export interface PublicReport {
  id: number;
  theme: string;
  subtitle: string | null;
  contributor_slugs: string[];
  created_at: string;
  has_pdf: boolean;
}

export interface UploadedDoc {
  id: number;
  report_id: number;
  filename: string;
  mime: string;
  size_bytes: number;
  summary: string;
  created_at: string;
}

export interface ReadingSection {
  heading: string;
  body_html: string;
  author: string | null;
  role: string | null;
}

export interface ReadingSource {
  n: number;
  url: string;
  title: string | null;
  source: string;
}

export interface ReadingMode {
  id: number;
  theme: string;
  subtitle: string | null;
  contributors: { name: string; role: string }[];
  house_view_top: string | null;
  house_view_bottom: string | null;
  sections: ReadingSection[];
  sources: ReadingSource[];
  created_at: string;
}

export interface ModelBreakdownRow {
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface EmailReportResponse {
  sent: boolean;
  detail: string;
}

export const api = {
  listReports: () => req<Report[]>("/reports"),
  getReport: (id: number) => req<Report>(`/reports/${id}`),
  listJobs: (id: number) => req<Job[]>(`/reports/${id}/jobs`),
  listTeam: () => req<TeamMember[]>("/team"),
  getPersona: (slug: string) => req<PersonaDetail>(`/team/${slug}`),
  updatePersona: (slug: string, markdown: string) =>
    req<PersonaDetail>(`/team/${slug}`, { method: "PUT", body: JSON.stringify({ markdown }) }),

  // Recruiter
  kickRecruiterReview: () => req<{ added: number }>("/recruiter/review", { method: "POST" }),
  listRecommendations: (status: RecStatus | "all" = "pending") =>
    req<Recommendation[]>(status === "all" ? "/recruiter/recommendations" : `/recruiter/recommendations?status=${status}`),
  approveRecommendation: (id: number) =>
    req<Recommendation>(`/recruiter/recommendations/${id}/approve`, { method: "POST" }),
  dismissRecommendation: (id: number) =>
    req<Recommendation>(`/recruiter/recommendations/${id}/dismiss`, { method: "POST" }),
  createReport: (p: CreateReportPayload) =>
    req<Report>("/reports", { method: "POST", body: JSON.stringify(p) }),
  resume: (id: number, fromStage?: ReportStage, cleanSlate?: boolean) => {
    const params = new URLSearchParams();
    if (fromStage) params.set("from_stage", fromStage);
    if (cleanSlate) params.set("clean_slate", "true");
    const qs = params.toString() ? `?${params}` : "";
    return req<Report>(`/reports/${id}/resume${qs}`, { method: "POST" });
  },
  cancel: (id: number) => req<Report>(`/reports/${id}/cancel`, { method: "POST" }),
  forceFail: (id: number) => req<Report>(`/reports/${id}/force_fail`, { method: "POST" }),
  listArchive: () => req<{ slug: string; name: string; role: string }[]>("/team/archive"),
  rehire: (slug: string) => req<{ slug: string; name: string; role: string; markdown: string }>(`/team/${slug}/rehire`, { method: "POST" }),

  // Runtime settings
  getSettings: () => req<AppSettings>("/settings"),
  updateSettings: (payload: Partial<AppSettings>) =>
    req<AppSettings>("/settings", { method: "PUT", body: JSON.stringify(payload) }),

  // Per-stage average duration in seconds, computed from past completed jobs.
  stageDurations: () => req<{ stage: ReportStage; seconds: number }[]>("/reports/eta/stage_durations"),

  // Interactive charts
  listChartFiles: (reportId: number) =>
    req<{ filename: string; has_json: boolean }[]>(`/reports/${reportId}/charts`),
  getChartJson: (reportId: number, filename: string) =>
    req<ChartSpec>(`/reports/${reportId}/chart.json?filename=${encodeURIComponent(filename)}`),

  // Audit log
  getAuditLog: (reportId: number, filter?: { event?: string; actor?: string }) => {
    const params = new URLSearchParams();
    if (filter?.event) params.set("event", filter.event);
    if (filter?.actor) params.set("actor", filter.actor);
    const qs = params.toString() ? `?${params}` : "";
    return req<AuditEntry[]>(`/reports/${reportId}/audit${qs}`);
  },

  // Auto-thread (5-tweet distillation, generated in housekeeping)
  getThread: (id: number) => req<{ text: string | null }>(`/reports/${id}/thread`),

  // Ask-the-analyst (chat with a contributing persona about a published report)
  askAnalyst: (id: number, payload: { persona_slug: string; message: string; history: { role: string; content: string }[] }) =>
    req<AskResponse>(`/reports/${id}/ask`, { method: "POST", body: JSON.stringify(payload) }),

  // Position tracker
  listOpenPositions: () => req<PositionRow[]>("/reports/positions/open"),
  listClosedPositions: () => req<PositionRow[]>("/reports/positions/closed"),

  pdfUrl: (id: number) => `${API_BASE}/reports/${id}/pdf`,
  chartJsonUrl: (reportId: number, filename: string) =>
    `${API_BASE}/reports/${reportId}/chart.json?filename=${encodeURIComponent(filename)}`,

  // Reading mode (HTML view, alternative to embedded PDF)
  getReading: (id: number) => req<ReadingMode>(`/reports/${id}/reading`),
  getPublicReading: async (id: number, token: string): Promise<ReadingMode> => {
    const res = await fetch(`${API_BASE}/reports/${id}/share/${encodeURIComponent(token)}/reading`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as ReadingMode;
  },

  // Uploads (PDF / CSV / spreadsheet attached to a report)
  listUploads: (id: number) => req<UploadedDoc[]>(`/reports/${id}/uploads`),
  uploadDocument: async (id: number, file: File): Promise<UploadedDoc> => {
    const auth = authHeader();
    const headers: Record<string, string> = {};
    if (auth) headers["authorization"] = auth;
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${API_BASE}/reports/${id}/uploads`, {
      method: "POST",
      headers,  // intentionally no content-type: browser sets multipart boundary
      body: fd,
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as UploadedDoc;
  },
  deleteUpload: (id: number, filename: string) =>
    req<{ deleted: boolean }>(`/reports/${id}/uploads/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    }),

  // Email a finished report
  emailReport: (id: number, payload: { to: string; note?: string; include_pdf?: boolean }) =>
    req<EmailReportResponse>(`/reports/${id}/email`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // Per-model token + cost breakdown (Opus / Sonnet / Haiku)
  getModelBreakdown: (id: number) =>
    req<ModelBreakdownRow[]>(`/reports/${id}/model_breakdown`),

  // Public share links
  getShare: (id: number) => req<ShareInfo>(`/reports/${id}/share`),
  createShare: (id: number, rotate = false) =>
    req<ShareInfo>(`/reports/${id}/share${rotate ? "?rotate=true" : ""}`, { method: "POST" }),
  revokeShare: (id: number) => req<ShareInfo>(`/reports/${id}/share`, { method: "DELETE" }),

  // Public (unauthenticated) reads -- used by the share viewer page.
  getPublicReport: async (id: number, token: string): Promise<PublicReport> => {
    const res = await fetch(`${API_BASE}/reports/${id}/share/${encodeURIComponent(token)}`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as PublicReport;
  },
  publicPdfUrl: (id: number, token: string) =>
    `${API_BASE}/reports/${id}/share/${encodeURIComponent(token)}/pdf`,
  publicChartJsonUrl: (id: number, token: string, filename: string) =>
    `${API_BASE}/reports/${id}/share/${encodeURIComponent(token)}/chart.json?filename=${encodeURIComponent(filename)}`,
  listPublicChartFiles: async (id: number, token: string): Promise<{ filename: string; has_json: boolean }[]> => {
    const res = await fetch(`${API_BASE}/reports/${id}/share/${encodeURIComponent(token)}/charts`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as { filename: string; has_json: boolean }[];
  },
  getPublicChartJson: async (id: number, token: string, filename: string): Promise<ChartSpec> => {
    const url = `${API_BASE}/reports/${id}/share/${encodeURIComponent(token)}/chart.json?filename=${encodeURIComponent(filename)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as ChartSpec;
  },

  // Scout
  latestThemes: () => req<Theme[]>("/scout/themes"),
  listScoutRuns: () => req<ScoutRun[]>("/scout/runs"),
  kickScout: () => req<{ status: string }>("/scout/run", { method: "POST" }),
  commissionTheme: (themeId: number, mode: ReportMode = "standard") =>
    req<{ report_id: number; status: string }>(
      `/scout/themes/${themeId}/commission`,
      { method: "POST", body: JSON.stringify({ mode }) },
    ),

  apiBase: () => API_BASE,
};
