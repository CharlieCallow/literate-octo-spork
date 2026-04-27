"use client";
import { memo, useEffect, useState } from "react";
import { api, type Job, type Report } from "@/lib/api";
import { RunStatus } from "./RunStatus";
import { StageBreakdown } from "./StageBreakdown";

const TERMINAL_STAGES = new Set(["done", "failed", "cancelled"]);

export function LatestReport() {
  const [report, setReport] = useState<Report | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const list = await api.listReports();
        if (!alive) return;
        if (list.length === 0) {
          setReport(null);
        } else {
          setReport(list[0]);
          const js = await api.listJobs(list[0].id);
          if (alive) setJobs(js);
        }
      } catch (e) {
        if (alive) setError(String(e));
      }

      // Schedule the next poll ourselves so we can stop once the report
      // is in a terminal state (done/failed). Stops the iframe being
      // re-evaluated and the PDF re-fetched every 3s.
      if (!alive) return;
      const stage = report?.stage;
      const interval = stage && TERMINAL_STAGES.has(stage) ? 30_000 : 3_000;
      timer = setTimeout(tick, interval);
    };

    tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      </p>
      <p className="muted" style={{ margin: "4px 0 0" }}>
        <RunStatus report={report} />
        {report.stage === "failed" && (
          <>
            {" · "}
            <button onClick={() => resume(report.id)} style={{ padding: "2px 10px", fontSize: 12 }}>Resume</button>
          </>
        )}
      </p>
      {report.error && <pre style={{ whiteSpace: "pre-wrap", color: "#B8860B", fontSize: 12 }}>{report.error}</pre>}

      {jobs.length > 0 && <StageBreakdown jobs={jobs} />}

      {report.pdf_url && <PdfFrame reportId={report.id} />}
    </div>
  );
}

// Memoized so polling re-renders of <LatestReport /> don't re-mount the iframe
// (which would force Chromium to re-fetch the whole PDF on every tick).
const PdfFrame = memo(function PdfFrame({ reportId }: { reportId: number }) {
  return (
    <iframe
      src={api.pdfUrl(reportId)}
      style={{ width: "100%", height: 720, border: 0, marginTop: 12 }}
    />
  );
});
