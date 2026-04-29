"use client";
import { use, useCallback, useEffect, useState } from "react";
import { AskAnalyst } from "@/components/AskAnalyst";
import { AuditLog } from "@/components/AuditLog";
import { AuthGate } from "@/components/Auth";
import { AutoThread } from "@/components/AutoThread";
import { EmailReportDialog } from "@/components/EmailReportDialog";
import { InteractiveCharts } from "@/components/InteractiveCharts";
import { ModelBreakdown } from "@/components/ModelBreakdown";
import { ReadingView } from "@/components/ReadingView";
import { RunStatus } from "@/components/RunStatus";
import { ShareDialog } from "@/components/ShareDialog";
import { StageBreakdown } from "@/components/StageBreakdown";
import { UploadList } from "@/components/UploadList";
import { api, type Job, type Report, type ReportStage } from "@/lib/api";

const TERMINAL_STAGES = new Set(["done", "failed", "cancelled"]);

const RESUME_FROM_STAGES: ReportStage[] = [
  "brief", "recruit", "research", "charts", "draft", "edit", "render", "feedback",
];

export default function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const reportId = Number(id);

  const [report, setReport] = useState<Report | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [emailOpen, setEmailOpen] = useState(false);
  const readingLoader = useCallback(() => api.getReading(reportId), [reportId]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
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
      if (!alive) return;
      const interval = report && TERMINAL_STAGES.has(report.stage) ? 30_000 : 3_000;
      timer = setTimeout(tick, interval);
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportId]);

  async function resume(fromStage?: ReportStage, cleanSlate = false) {
    if (!report) return;
    try { await api.resume(report.id, fromStage, cleanSlate); } catch (e) { setError(String(e)); }
  }

  async function cancel() {
    if (!report) return;
    if (!window.confirm("Cancel this report? Any in-flight stage will finish; nothing further runs.")) return;
    try { await api.cancel(report.id); } catch (e) { setError(String(e)); }
  }

  async function forceFail() {
    if (!report) return;
    if (!window.confirm(
      "Force-fail this report? Use this when a stage is stuck and cancel isn't enough. " +
      "Marks the running job as failed immediately so you can hit Resume.",
    )) return;
    try { await api.forceFail(report.id); } catch (e) { setError(String(e)); }
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
              {report.budget_cap_usd && <> {" · "}Cap: ${report.budget_cap_usd.toFixed(2)}</>}
              {report.team_override.length > 0 && <> {" · "}Team: {report.team_override.join(", ")}</>}
            </p>
            <p style={{ margin: "6px 0 0" }}>
              <RunStatus report={report} />
            </p>

            <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
              {report.stage === "done" && (
                <>
                  <button
                    onClick={() => setShareOpen(true)}
                    style={{ padding: "3px 12px", fontSize: 12 }}
                  >
                    Share link
                  </button>
                  <button
                    onClick={() => setEmailOpen(true)}
                    style={{ padding: "3px 12px", fontSize: 12, background: "#FFF", color: "var(--forte-ink)", border: "1px solid var(--forte-rule)" }}
                  >
                    Email
                  </button>
                </>
              )}
              {!TERMINAL_STAGES.has(report.stage) && (
                <>
                  <button onClick={cancel} style={{ padding: "3px 12px", fontSize: 12, background: "#FFF", color: "var(--forte-ink)", border: "1px solid var(--forte-rule)" }}>
                    Cancel
                  </button>
                  <button onClick={forceFail} title="Force-fail a stuck stage so you can resume" style={{ padding: "3px 12px", fontSize: 12, background: "#FFF", color: "var(--forte-rule)", border: "1px solid var(--forte-rule)" }}>
                    Force fail
                  </button>
                </>
              )}
              {(report.stage === "failed" || report.stage === "cancelled") && (
                <>
                  <button onClick={() => resume()} style={{ padding: "3px 12px", fontSize: 12 }}>
                    Resume
                  </button>
                  <button
                    onClick={() => {
                      if (window.confirm("Clean slate: deletes all draft files / charts and re-runs from brief. Continue?")) {
                        resume("brief", true);
                      }
                    }}
                    style={{ padding: "3px 12px", fontSize: 12, background: "#FFF", color: "var(--forte-ink)", border: "1px solid var(--forte-rule)" }}
                  >
                    Clean slate
                  </button>
                  <details style={{ display: "inline-block" }}>
                    <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--forte-purple)" }}>
                      Re-run from…
                    </summary>
                    <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
                      {RESUME_FROM_STAGES.map((s) => (
                        <button
                          key={s}
                          onClick={() => resume(s)}
                          style={{ padding: "2px 8px", fontSize: 11, background: "#FFF", color: "var(--forte-ink)", border: "1px solid var(--forte-rule)" }}
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  </details>
                </>
              )}
            </div>

            {report.error && (
              <pre style={{
                whiteSpace: "pre-wrap", color: "#B8860B",
                fontSize: 11, marginTop: 12, maxHeight: 220, overflow: "auto",
                background: "var(--forte-bg)", padding: 8, borderRadius: 4,
              }}>{report.error}</pre>
            )}

            {jobs.length > 0 && <StageBreakdown jobs={jobs} />}
          </div>

          {report.max_domain_share != null && report.max_domain_share > 0.4 && report.top_domain && (
            <div className="card" style={{ borderLeft: "3px solid #B8860B" }}>
              <div className="byline" style={{ color: "#B8860B" }}>Source-diversity warning</div>
              <p style={{ margin: 0, fontSize: 13 }}>
                <strong>{(report.max_domain_share * 100).toFixed(0)}%</strong> of citations came from{" "}
                <code>{report.top_domain}</code>. Worth checking the report doesn't lean too hard on
                one publisher.
              </p>
            </div>
          )}

          <UploadList reportId={report.id} canEdit={report.stage !== "done"} />

          <InteractiveCharts reportId={report.id} />

          {report.stage === "done" && (
            <AutoThread reportId={report.id} />
          )}

          {report.stage === "done" && (
            <AskAnalyst
              report={report}
              contributorSlugs={(report.contributor_slugs && report.contributor_slugs.length > 0)
                ? report.contributor_slugs
                : (report.team_override ?? [])}
            />
          )}

          <ModelBreakdown reportId={report.id} />

          <AuditLog reportId={report.id} />

          {report.pdf_url ? (
            <div className="card">
              <div className="byline" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span>Reading mode</span>
                <a
                  href={api.pdfUrl(report.id)}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    padding: "3px 12px", fontSize: 12, borderRadius: 4,
                    background: "var(--forte-navy)", color: "#FFF",
                    textDecoration: "none", fontWeight: 600,
                  }}
                >
                  Download PDF
                </a>
              </div>
              <div style={{ marginTop: 12 }}>
                <ReadingView loader={readingLoader} />
              </div>
            </div>
          ) : report.stage === "done" ? (
            <div className="card">
              <p className="muted">
                PDF for this report is no longer on disk -- typically a Railway
                rebuild wiped it. The full re-render needs to start from the
                brief stage.
              </p>
              <button
                onClick={() => {
                  if (window.confirm("Re-run from brief? This regenerates the report (re-charges API cost) but produces a fresh PDF.")) {
                    resume("brief", true);
                  }
                }}
                style={{ padding: "4px 12px", fontSize: 12 }}
              >
                Re-run from brief
              </button>
            </div>
          ) : (
            <div className="card">
              <p className="muted">PDF not ready yet. Auto-refreshing every 3s.</p>
            </div>
          )}
        </>
      )}
      {shareOpen && report && <ShareDialog reportId={report.id} onClose={() => setShareOpen(false)} />}
      {emailOpen && report && <EmailReportDialog reportId={report.id} onClose={() => setEmailOpen(false)} />}
    </AuthGate>
  );
}
