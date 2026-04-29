"use client";
import type { Job } from "@/lib/api";

function fmtSeconds(j: Job): string {
  if (!j.started_at || !j.finished_at) return "—";
  const ms = new Date(j.finished_at).getTime() - new Date(j.started_at).getTime();
  if (ms <= 0) return "—";
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms - m * 60_000) / 1000);
  return `${m}m${String(s).padStart(2, "0")}s`;
}

export function StageBreakdown({ jobs }: { jobs: Job[] }) {
  // Show only the most recent attempt per stage.
  const latestByStage: Record<string, Job> = {};
  for (const j of jobs) latestByStage[j.stage] = j;
  const ordered = Object.values(latestByStage).sort(
    (a, b) => (a.started_at || "").localeCompare(b.started_at || "")
  );
  if (ordered.length === 0) return null;

  const total = ordered.reduce((acc, j) => acc + j.cost_usd, 0);
  const maxCost = ordered.reduce((acc, j) => Math.max(acc, j.cost_usd), 0);

  return (
    <div style={{ marginTop: 12 }}>
      <div className="byline">
        Stage breakdown
        <span className="muted" style={{ marginLeft: 8, fontWeight: 400 }}>
          total ${total.toFixed(3)}
        </span>
      </div>
      <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", color: "var(--forte-muted)" }}>
            <th style={{ padding: "4px 0" }}>Stage</th>
            <th>Status</th>
            <th>Try</th>
            <th>Took</th>
            <th>Cost share</th>
            <th style={{ textAlign: "right" }}>Cost</th>
            <th style={{ textAlign: "right" }}>%</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((j) => {
            const share = total > 0 ? j.cost_usd / total : 0;
            const widthPct = maxCost > 0 ? (j.cost_usd / maxCost) * 100 : 0;
            return (
              <tr key={j.id} style={{ borderTop: "1px solid var(--forte-rule)" }}>
                <td style={{ padding: "4px 0" }}>{j.stage}</td>
                <td><span className={`stage-pill ${j.status}`}>{j.status}</span></td>
                <td>{j.attempts}</td>
                <td style={{ fontVariantNumeric: "tabular-nums" }}>{fmtSeconds(j)}</td>
                <td style={{ width: 120 }}>
                  {/* Inline bar: width pegged to the most expensive stage on
                      this report so the visual is always usable; the % column
                      gives the absolute share for comparison across reports. */}
                  <div style={{
                    height: 6, borderRadius: 3,
                    background: "var(--forte-rule)",
                    overflow: "hidden",
                  }}>
                    <div style={{
                      width: `${widthPct}%`, height: "100%",
                      background: "var(--forte-navy)",
                    }} />
                  </div>
                </td>
                <td style={{ textAlign: "right", fontFamily: "monospace" }}>
                  ${j.cost_usd.toFixed(3)}
                </td>
                <td style={{ textAlign: "right", color: "var(--forte-muted)" }}>
                  {(share * 100).toFixed(0)}%
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
