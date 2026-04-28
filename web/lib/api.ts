// Tiny client for the Forte backend. Sends HTTP basic auth from localStorage.

export type ReportStage =
  | "queued" | "brief" | "recruit" | "research" | "charts" | "draft"
  | "edit" | "render" | "feedback" | "done" | "failed" | "cancelled";

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
  stage: ReportStage;
  error: string | null;
  cost_usd: number;
  pdf_url: string | null;
  created_at: string;
}

export interface TeamMember {
  slug: string;
  name: string;
  role: string;
  reports_contributed: number;
  last_assignment_at: string | null;
  rewrite_ratio: number | null;
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

export interface ScoutRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  n_themes: number;
  cost_usd: number;
  error: string | null;
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
  listArchive: () => req<{ slug: string; name: string; role: string }[]>("/team/archive"),
  rehire: (slug: string) => req<{ slug: string; name: string; role: string; markdown: string }>(`/team/${slug}/rehire`, { method: "POST" }),
  pdfUrl: (id: number) => `${API_BASE}/reports/${id}/pdf`,

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
