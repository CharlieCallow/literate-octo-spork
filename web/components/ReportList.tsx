"use client";
import { useEffect, useState } from "react";
import { api, type Job, type Report } from "@/lib/api";

export function ReportList() {
  const [reports, setReports] = useState<Report[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const list = await api.listReports();
        if (!alive) return;
        setReports(list);
        if (list.length > 0) {
          const js = await api.listJobs(list[0].id);
          if (alive) setJobs(js);
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    tick();
    const t = setInterval(tick, 3000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  async function resume(id: number) {
    try {
      await api.resume(id);
    } catch (e) {
      setError(String(e));
    }
  }

  if (error) return <div className="card"><p className="muted">{error}</p></div>;
  if (reports.length === 0) return <div className="card"><p className="muted">No reports yet. Commission one from <a href="/new">New report</a>.</p></div>;

  const latest = reports[0];

  return (
    <>
      <div className="card">
        <div className="byline">Latest report</div>
        <h2>{latest.theme}</h2>
        <p className="muted">
          Stage: <span className={`stage-pill ${latest.stage}`}>{latest.stage}</span>
          {" · "}Mode: <span className="stage-pill">{latest.mode}</span>
          {" · "}Cost: ${latest.cost_usd.toFixed(3)}
          {latest.stage === "failed" && (
            <>
              {" · "}
              <button onClick={() => resume(latest.id)} style={{ padding: "2px 10px", fontSize: 12 }}>
                Resume
              </button>
            </>
          )}
        </p>
        {latest.error && <pre style={{ whiteSpace: "pre-wrap", color: "#B8860B", fontSize: 12 }}>{latest.error}</pre>}

        {jobs.length > 0 && <StageBreakdown jobs={jobs} />}

        {latest.pdf_url && (
          <iframe src={api.pdfUrl(latest.id)} style={{ width: "100%", height: 720, border: 0, marginTop: 12 }} />
        )}
      </div>

      <div className="card">
        <div className="byline">All reports</div>
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {reports.map((r) => (
            <li key={r.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--forte-rule)" }}>
              <span className={`stage-pill ${r.stage}`}>{r.stage}</span>{" "}
              <strong>{r.theme}</strong>{" "}
              <span className="muted">· {r.mode} · ${r.cost_usd.toFixed(3)}</span>
              {r.pdf_url && <> · <a href={api.pdfUrl(r.id)}>PDF</a></>}
              {r.stage === "failed" && (
                <> · <button onClick={() => resume(r.id)} style={{ padding: "2px 8px", fontSize: 11 }}>Resume</button></>
              )}
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}

function StageBreakdown({ jobs }: { jobs: Job[] }) {
  // Show only the most recent attempt per stage.
  const latestByStage: Record<string, Job> = {};
  for (const j of jobs) latestByStage[j.stage] = j;
  const ordered = Object.values(latestByStage).sort(
    (a, b) => (a.started_at || "").localeCompare(b.started_at || "")
  );
  if (ordered.length === 0) return null;

  return (
    <div style={{ marginTop: 12 }}>
      <div className="byline">Stage breakdown</div>
      <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", color: "var(--forte-muted)" }}>
            <th style={{ padding: "4px 0" }}>Stage</th>
            <th>Status</th>
            <th>Attempts</th>
            <th style={{ textAlign: "right" }}>Cost</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((j) => (
            <tr key={j.id} style={{ borderTop: "1px solid var(--forte-rule)" }}>
              <td style={{ padding: "4px 0" }}>{j.stage}</td>
              <td><span className={`stage-pill ${j.status}`}>{j.status}</span></td>
              <td>{j.attempts}</td>
              <td style={{ textAlign: "right", fontFamily: "monospace" }}>${j.cost_usd.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
