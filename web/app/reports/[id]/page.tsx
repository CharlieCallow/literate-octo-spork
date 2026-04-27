"use client";
import { use, useEffect, useState } from "react";
import { AuthGate } from "@/components/Auth";
import { StageBreakdown } from "@/components/StageBreakdown";
import { api, type Job, type Report } from "@/lib/api";

export default function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const reportId = Number(id);

  const [report, setReport] = useState<Report | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await api.getReport(reportId);
        if (!alive) return;
        setReport(r);
        const js = await api.listJobs(reportId);
        if (alive) setJobs(js);
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    tick();
    const t = setInterval(tick, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [reportId]);

  async function resume() {
    if (!report) return;
    try { await api.resume(report.id); } catch (e) { setError(String(e)); }
  }

  return (
    <AuthGate>
      <div className="byline">
        <a href="/archive" style={{ color: "var(--forte-muted)" }}>← Archive</a>
      </div>

      {error && <div className="card"><p className="muted">{error}</p></div>}

      {!report ? (
        <div className="card"><p className="muted">Loading…</p></div>
      ) : (
        <>
          <h1 style={{ color: "var(--forte-navy)", marginTop: 4 }}>{report.theme}</h1>
          {report.subtitle && <p className="muted" style={{ marginTop: 0 }}>{report.subtitle}</p>}

          <div className="card">
            <p style={{ margin: 0 }}>
              Stage: <span className={`stage-pill ${report.stage}`}>{report.stage}</span>
              {" · "}Mode: <span className="stage-pill">{report.mode}</span>
              {" · "}Cost: ${report.cost_usd.toFixed(3)}
              {report.budget_cap_usd && <> {" · "}Cap: ${report.budget_cap_usd.toFixed(2)}</>}
              {report.team_override.length > 0 && <> {" · "}Team: {report.team_override.join(", ")}</>}
              {report.stage === "failed" && (
                <>
                  {" · "}
                  <button onClick={resume} style={{ padding: "2px 10px", fontSize: 12 }}>Resume</button>
                </>
              )}
            </p>

            {report.error && (
              <pre style={{
                whiteSpace: "pre-wrap", color: "#B8860B",
                fontSize: 11, marginTop: 12, maxHeight: 220, overflow: "auto",
                background: "var(--forte-bg)", padding: 8, borderRadius: 4,
              }}>{report.error}</pre>
            )}

            {jobs.length > 0 && <StageBreakdown jobs={jobs} />}
          </div>

          {report.pdf_url ? (
            <div className="card">
              <div className="byline">PDF</div>
              <iframe
                src={api.pdfUrl(report.id)}
                style={{ width: "100%", height: "85vh", border: 0 }}
              />
              <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                <a href={api.pdfUrl(report.id)}>Download PDF</a>
              </p>
            </div>
          ) : (
            <div className="card">
              <p className="muted">PDF not ready yet. Auto-refreshing every 3s.</p>
            </div>
          )}
        </>
      )}
    </AuthGate>
  );
}
