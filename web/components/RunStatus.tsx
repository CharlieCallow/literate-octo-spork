"use client";
import { useEffect, useState } from "react";
import { api, MODE_ESTIMATES, type Report, type ReportStage } from "@/lib/api";

const TERMINAL_STAGES = new Set(["done", "failed", "cancelled"]);

// Workflow stage order matches state_machine.STAGE_ORDER on the backend.
const STAGE_ORDER: ReportStage[] = [
  "queued", "brief", "recruit", "research", "charts", "draft",
  "redteam", "edit", "audit", "render", "feedback",
  "housekeeping", "done",
];

export function RunStatus({ report }: { report: Report }) {
  const [now, setNow] = useState(() => Date.now());
  const [stageDurations, setStageDurations] = useState<Record<string, number> | null>(null);

  useEffect(() => {
    if (TERMINAL_STAGES.has(report.stage)) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [report.stage]);

  useEffect(() => {
    api.stageDurations()
      .then((rows) => {
        const map: Record<string, number> = {};
        for (const r of rows) map[r.stage] = r.seconds;
        setStageDurations(map);
      })
      .catch(() => { /* no historical data yet -- fall back to mode estimate */ });
  }, []);

  const startedMs = new Date(report.created_at).getTime();
  const elapsedSec = Math.max(0, Math.floor((now - startedMs) / 1000));
  const est = MODE_ESTIMATES[report.mode];

  // Smarter ETA: sum of avg durations for stages we haven't reached yet.
  // Falls back to the static mode estimate when we don't have history yet.
  let remainingSec: number;
  if (TERMINAL_STAGES.has(report.stage)) {
    remainingSec = 0;
  } else if (stageDurations) {
    const idx = STAGE_ORDER.indexOf(report.stage);
    const remainingStages = idx >= 0 ? STAGE_ORDER.slice(idx + 1, -1) : [];
    const sum = remainingStages.reduce(
      (acc, s) => acc + (stageDurations[s] ?? 0), 0,
    );
    remainingSec = Math.round(sum) || Math.max(0, est.minutes * 60 - elapsedSec);
  } else {
    remainingSec = Math.max(0, est.minutes * 60 - elapsedSec);
  }

  return (
    <span className="muted" style={{ fontSize: 13 }}>
      Elapsed {fmt(elapsedSec)}
      {!TERMINAL_STAGES.has(report.stage) && (
        <> · ETA ~{fmt(remainingSec)}</>
      )}
      {" · "}Cost ${report.cost_usd.toFixed(3)}
      {!TERMINAL_STAGES.has(report.stage) && (
        <> / est ${est.cost_lo.toFixed(2)}-${est.cost_hi.toFixed(2)}</>
      )}
    </span>
  );
}

function fmt(s: number): string {
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `${m}m ${r.toString().padStart(2, "0")}s` : `${r}s`;
}
