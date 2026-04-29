"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Rollup = Awaited<ReturnType<typeof api.costRollup>>;

function fmt(n: number): string {
  return `$${n.toFixed(2)}`;
}

export function CostRollup() {
  const [data, setData] = useState<Rollup | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () => {
      api.costRollup()
        .then((r) => { if (alive) setData(r); })
        .catch((e) => { if (alive) setError(String(e)); });
    };
    tick();
    // 30s refresh -- the audit-log writes that drive this don't change
    // by the second; no point hammering the endpoint.
    const t = setInterval(tick, 30_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (error) return null;
  if (!data) {
    return (
      <div className="card">
        <p className="muted" style={{ margin: 0 }}>Loading cost rollup…</p>
      </div>
    );
  }

  // Heuristic: average per report across the most populated window we
  // have. Useful sanity check next to the totals.
  const avgPerReport =
    data.n_reports_30d > 0
      ? data.last_30d_usd / data.n_reports_30d
      : null;

  return (
    <div className="card">
      <div className="byline" style={{ marginBottom: 6 }}>Spend</div>
      <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
        <Stat
          label="Today"
          big={fmt(data.today_usd)}
          sub={`${data.n_reports_today} report${data.n_reports_today === 1 ? "" : "s"}`}
        />
        <Stat
          label="Last 7 days"
          big={fmt(data.last_7d_usd)}
          sub={`${data.n_reports_7d} reports`}
        />
        <Stat
          label="Last 30 days"
          big={fmt(data.last_30d_usd)}
          sub={`${data.n_reports_30d} reports`}
        />
        {avgPerReport != null && (
          <Stat
            label="Avg per report (30d)"
            big={fmt(avgPerReport)}
            sub="all modes"
          />
        )}
      </div>
    </div>
  );
}

function Stat({ label, big, sub }: { label: string; big: string; sub: string }) {
  return (
    <div style={{ minWidth: 140 }}>
      <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5 }}>
        {label}
      </div>
      <div style={{
        fontSize: 22, fontWeight: 600, color: "var(--forte-navy)",
        fontVariantNumeric: "tabular-nums",
      }}>
        {big}
      </div>
      <div className="muted" style={{ fontSize: 11 }}>{sub}</div>
    </div>
  );
}
