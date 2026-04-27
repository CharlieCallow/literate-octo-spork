"use client";
import type { Job } from "@/lib/api";

export function StageBreakdown({ jobs }: { jobs: Job[] }) {
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
