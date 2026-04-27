"use client";
import { useEffect, useState } from "react";
import { MODE_ESTIMATES, type Report } from "@/lib/api";

const TERMINAL_STAGES = new Set(["done", "failed", "cancelled"]);

export function RunStatus({ report }: { report: Report }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (TERMINAL_STAGES.has(report.stage)) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [report.stage]);

  const startedMs = new Date(report.created_at).getTime();
  const elapsedSec = Math.max(0, Math.floor((now - startedMs) / 1000));
  const est = MODE_ESTIMATES[report.mode];
  const etaSec = est.minutes * 60;
  const remainingSec = TERMINAL_STAGES.has(report.stage)
    ? 0
    : Math.max(0, etaSec - elapsedSec);

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
