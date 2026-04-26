// Tiny client for the Forte backend. Sends HTTP basic auth from localStorage.

export type ReportStage =
  | "queued" | "brief" | "research" | "charts" | "draft"
  | "edit" | "render" | "feedback" | "done" | "failed";

export interface Report {
  id: number;
  theme: string;
  subtitle: string | null;
  stage: ReportStage;
  error: string | null;
  cost_usd: number;
  pdf_url: string | null;
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

export const api = {
  listReports: () => req<Report[]>("/reports"),
  getReport: (id: number) => req<Report>(`/reports/${id}`),
  createReport: (theme: string, subtitle?: string) =>
    req<Report>("/reports", { method: "POST", body: JSON.stringify({ theme, subtitle }) }),
  pdfUrl: (id: number) => `${API_BASE}/reports/${id}/pdf`,
  apiBase: () => API_BASE,
};
