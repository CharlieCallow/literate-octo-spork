"use client";
import { useEffect, useState } from "react";
import { api, type Report } from "@/lib/api";

export function ReportList() {
  const [reports, setReports] = useState<Report[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const list = await api.listReports();
        if (alive) setReports(list);
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    tick();
    const t = setInterval(tick, 3000);
    return () => { alive = false; clearInterval(t); };
  }, []);

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
          {" · "}Cost: ${latest.cost_usd.toFixed(3)}
        </p>
        {latest.error && <pre style={{ whiteSpace: "pre-wrap", color: "#B8860B", fontSize: 12 }}>{latest.error}</pre>}
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
              <strong>{r.theme}</strong>
              {" "}
              <span className="muted">· ${r.cost_usd.toFixed(3)}</span>
              {r.pdf_url && <> · <a href={api.pdfUrl(r.id)}>PDF</a></>}
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}
