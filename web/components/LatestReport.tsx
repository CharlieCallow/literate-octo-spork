"use client";
import { useEffect, useState } from "react";
import { api, type Job, type Report } from "@/lib/api";
import { StageBreakdown } from "./StageBreakdown";

export function LatestReport() {
  const [report, setReport] = useState<Report | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const list = await api.listReports();
        if (!alive) return;
        if (list.length === 0) {
          setReport(null);
          return;
        }
        setReport(list[0]);
        const js = await api.listJobs(list[0].id);
        if (alive) setJobs(js);
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    tick();
    const t = setInterval(tick, 3000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  async function resume(id: number) {
    try { await api.resume(id); } catch (e) { setError(String(e)); }
  }

  if (error) return <div className="card"><p className="muted">{error}</p></div>;
  if (!report) return <div className="card"><p className="muted">No reports yet. Commission one from <a href="/new">New report</a>.</p></div>;

  return (
    <div className="card">
      <div className="byline">Latest report</div>
      <h2 style={{ marginBottom: 4 }}>
        <a href={`/reports/${report.id}`} style={{ color: "var(--forte-navy)" }}>{report.theme}</a>
      </h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Stage: <span className={`stage-pill ${report.stage}`}>{report.stage}</span>
        {" · "}Mode: <span className="stage-pill">{report.mode}</span>
        {" · "}Cost: ${report.cost_usd.toFixed(3)}
        {report.stage === "failed" && (
          <>
            {" · "}
            <button onClick={() => resume(report.id)} style={{ padding: "2px 10px", fontSize: 12 }}>Resume</button>
          </>
        )}
      </p>
      {report.error && <pre style={{ whiteSpace: "pre-wrap", color: "#B8860B", fontSize: 12 }}>{report.error}</pre>}

      {jobs.length > 0 && <StageBreakdown jobs={jobs} />}

      {report.pdf_url && (
        <iframe src={api.pdfUrl(report.id)} style={{ width: "100%", height: 720, border: 0, marginTop: 12 }} />
      )}
    </div>
  );
}
