"use client";
import { useEffect, useRef, useState } from "react";
import { AuthGate } from "@/components/Auth";
import { api, type JobActivity, type WorkersStatus } from "@/lib/api";

const POLL_MS = 3000;

function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds - m * 60);
  return `${m}m${String(s).padStart(2, "0")}s`;
}

function fmtTimestamp(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString();
}

function stuckColor(j: JobActivity): string | undefined {
  if (j.is_stuck) return "var(--forte-amber, #C77F2C)";
  if (j.last_event_seconds_ago != null && j.last_event_seconds_ago > 90) {
    // Quiet for >90s during a running job is the early-warning signal --
    // colour amber even before the watchdog deadline so it's obvious.
    return "#C77F2C";
  }
  return undefined;
}

export default function WorkersPage() {
  const [data, setData] = useState<WorkersStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [actingOn, setActingOn] = useState<number | null>(null);
  const tickRef = useRef<number | null>(null);

  async function refresh(): Promise<void> {
    try {
      setData(await api.workersStatus());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    void refresh();
    if (paused) return;
    tickRef.current = window.setInterval(refresh, POLL_MS) as unknown as number;
    return () => {
      if (tickRef.current != null) window.clearInterval(tickRef.current);
    };
  }, [paused]);

  async function killOne(jobId: number): Promise<void> {
    if (!window.confirm(
      "Force-fail this job? Routes through the standard failure path so " +
      "retry/backoff still applies. The report's other in-flight jobs are not touched.",
    )) return;
    try {
      setActingOn(jobId);
      await api.forceFailJob(jobId);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setActingOn(null);
    }
  }

  function StatusPill({ j }: { j: JobActivity }) {
    return (
      <span className={`stage-pill ${j.status}`} style={{ color: stuckColor(j) }}>
        {j.status}
        {j.is_stuck && " · stuck"}
      </span>
    );
  }

  function JobRow({ j }: { j: JobActivity }) {
    const colour = stuckColor(j);
    return (
      <tr>
        <td>
          <a href={`/reports/${j.report_id}`}>{j.report_theme}</a>
          <div className="muted" style={{ fontSize: 11 }}>
            #{j.report_id} · {j.report_mode}
          </div>
        </td>
        <td><span className="stage-pill">{j.stage}</span></td>
        <td><StatusPill j={j} /></td>
        <td>{j.attempts}</td>
        <td style={{ color: colour, fontVariantNumeric: "tabular-nums" }}>
          {fmtDuration(j.age_seconds)}
          {j.deadline_seconds != null && j.status === "running" && (
            <div className="muted" style={{ fontSize: 11 }}>
              of {fmtDuration(j.deadline_seconds)} lease
            </div>
          )}
        </td>
        <td style={{ fontVariantNumeric: "tabular-nums", color: colour }}>
          {fmtDuration(j.last_event_seconds_ago)}
          {j.last_event && (
            <div className="muted" style={{ fontSize: 11 }}>
              {j.last_event_actor ?? "?"} · {j.last_event}
            </div>
          )}
        </td>
        <td style={{ fontSize: 11, maxWidth: 280, color: "#A33" }}>
          {j.last_error}
        </td>
        <td>
          {j.status === "running" && (
            <button
              onClick={() => killOne(j.job_id)}
              disabled={actingOn === j.job_id}
              style={{
                padding: "2px 8px", fontSize: 11,
                background: "#FFF", color: "var(--forte-ink)",
                border: "1px solid var(--forte-rule)",
              }}
            >
              {actingOn === j.job_id ? "…" : "Force fail"}
            </button>
          )}
        </td>
      </tr>
    );
  }

  function JobTable({ rows, empty }: { rows: JobActivity[]; empty: string }) {
    if (rows.length === 0) {
      return <p className="muted" style={{ margin: "8px 0" }}>{empty}</p>;
    }
    return (
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid var(--forte-rule)" }}>
            <th style={{ padding: "6px 4px" }}>Report</th>
            <th>Stage</th>
            <th>Status</th>
            <th>Try</th>
            <th>Age</th>
            <th>Last activity</th>
            <th>Error</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((j) => <JobRow key={j.job_id} j={j} />)}
        </tbody>
      </table>
    );
  }

  return (
    <AuthGate>
      <h1 style={{ color: "var(--forte-navy)", marginTop: 4 }}>Workers</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        Live view of in-flight + recently-finished jobs. Polls every {POLL_MS / 1000}s.
        Amber means the job is past its deadline or has been quiet ({">"}90s)
        during a running stage — the silent-hang signal.
      </p>

      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          {data ? (
            <p style={{ margin: 0 }}>
              Worker process:{" "}
              <strong style={{ color: data.worker_alive ? "var(--forte-navy)" : "#A33" }}>
                {data.worker_alive ? "alive" : "OFFLINE"}
              </strong>
              {data.worker_last_seen_at ? (
                <>
                  {" "}— last heartbeat{" "}
                  <strong>{fmtDuration(data.worker_last_seen_seconds_ago)} ago</strong>{" "}
                  <span className="muted" style={{ fontSize: 12 }}>
                    ({fmtTimestamp(data.worker_last_seen_at)})
                  </span>
                </>
              ) : (
                <span className="muted"> — no heartbeat recorded yet</span>
              )}
            </p>
          ) : (
            <p style={{ margin: 0 }} className="muted">Loading worker state…</p>
          )}
          <button
            onClick={() => setPaused((p) => !p)}
            style={{ marginLeft: "auto", padding: "3px 12px", fontSize: 12 }}
          >
            {paused ? "Resume polling" : "Pause polling"}
          </button>
          <button onClick={refresh} style={{ padding: "3px 12px", fontSize: 12 }}>
            Refresh now
          </button>
        </div>
        {data?.last_activity_at && (
          <p style={{ margin: 0 }} className="muted" >
            Last agent activity:{" "}
            {fmtDuration(data.last_activity_seconds_ago)} ago{" "}
            <span style={{ fontSize: 12 }}>
              ({fmtTimestamp(data.last_activity_at)})
            </span>
          </p>
        )}
        {data?.supervisor.enabled && (
          <p style={{ margin: 0, fontSize: 12 }} className="muted">
            Supervisor: {data.supervisor.crash_count > 0
              ? <>worker has crashed <strong>{data.supervisor.crash_count}</strong> time(s)
                {data.supervisor.consecutive_failures > 1 && <> · <strong style={{ color: "#A33" }}>
                  {data.supervisor.consecutive_failures} consecutive — likely crash loop
                </strong></>}
                {data.supervisor.last_exit_code != null &&
                  <> · last exit code {data.supervisor.last_exit_code}</>}
                {data.supervisor.last_crash_at &&
                  <> · last crash {fmtTimestamp(data.supervisor.last_crash_at)}</>}
              </>
              : <>no crashes — worker pid {data.supervisor.pid ?? "—"}</>}
          </p>
        )}
        {data && !data.worker_alive && (
          <p style={{ margin: 0, color: "#A33" }}>
            The worker has not written a heartbeat in over 30s.{" "}
            {data.supervisor.enabled
              ? "The supervisor will respawn it automatically; if this persists check Railway logs for a crash loop."
              : "Bundled supervisor isn't running on this deploy — restart the API service on Railway to recover."}
          </p>
        )}
      </div>

      {data?.worker_last_error && (
        <div className="card" style={{ borderLeft: "3px solid #A33" }}>
          <h2 style={{ marginTop: 0, color: "#A33", fontSize: 16 }}>
            Last unhandled worker error
          </h2>
          <p style={{ margin: "4px 0 8px", fontSize: 12 }} className="muted">
            Captured at {fmtTimestamp(data.worker_last_error.captured_at)}.
            The poll loop now logs and continues rather than crashing — but
            this is the traceback. Triage at the source rather than rolling
            the dice on retries.
          </p>
          <pre style={{
            fontSize: 11, lineHeight: 1.4,
            background: "#FAF7F0", color: "var(--forte-ink)",
            padding: "8px 12px", borderRadius: 4,
            overflowX: "auto", maxHeight: 320,
            whiteSpace: "pre-wrap",
          }}>
            {data.worker_last_error.message}
          </pre>
        </div>
      )}

      {error && <div className="card"><p style={{ color: "#A33" }}>{error}</p></div>}

      {data && (
        <>
          <div className="card">
            <h2 style={{ marginTop: 0, color: "var(--forte-navy)" }}>
              Running ({data.running.length})
            </h2>
            <JobTable rows={data.running} empty="No jobs running right now." />
          </div>

          <div className="card">
            <h2 style={{ marginTop: 0, color: "var(--forte-navy)" }}>
              Pending ({data.pending.length})
            </h2>
            <JobTable rows={data.pending} empty="No jobs queued." />
          </div>

          <div className="card">
            <h2 style={{ marginTop: 0, color: "var(--forte-navy)" }}>
              Recent failures ({data.recent_failures.length})
            </h2>
            <JobTable rows={data.recent_failures} empty="No failures in the last hour." />
          </div>
        </>
      )}
    </AuthGate>
  );
}
