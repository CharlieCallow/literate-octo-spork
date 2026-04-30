"use client";
import { useEffect, useMemo, useState } from "react";
import { AuthGate } from "@/components/Auth";
import { api, type BasketSeries, type BasketSide, type PositionRow } from "@/lib/api";

type Tab = "open" | "closed";

const BENCHMARKS: { ticker: string; label: string }[] = [
  { ticker: "^GSPC", label: "SPX" },
  { ticker: "^NDX", label: "NDX" },
  { ticker: "^RUT", label: "RUT" },
  { ticker: "ACWI", label: "ACWI" },
];

export default function PositionsPage() {
  const [tab, setTab] = useState<Tab>("open");
  const [open, setOpen] = useState<PositionRow[] | null>(null);
  const [closed, setClosed] = useState<PositionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [side, setSide] = useState<BasketSide>("all");
  const [minConviction, setMinConviction] = useState<number>(1);
  const [benchmark, setBenchmark] = useState<string>("^GSPC");
  const [basket, setBasket] = useState<BasketSeries | null>(null);
  const [basketLoading, setBasketLoading] = useState(false);

  useEffect(() => {
    api.listOpenPositions().then(setOpen).catch((e) => setError(String(e)));
    api.listClosedPositions().then(setClosed).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    setBasketLoading(true);
    api.getBasket({ side, min_conviction: minConviction, benchmark })
      .then((b) => { setBasket(b); setBasketLoading(false); })
      .catch((e) => { setError(String(e)); setBasketLoading(false); });
  }, [side, minConviction, benchmark]);

  const rows = tab === "open" ? open : closed;
  const filteredRows = useMemo(() => {
    if (!rows) return rows;
    return rows.filter((r) => {
      if (r.conviction < minConviction) return false;
      if (side === "long" && r.direction !== "long") return false;
      if (side === "short" && r.direction !== "short" && r.direction !== "fade" && r.direction !== "avoid") return false;
      return true;
    });
  }, [rows, side, minConviction]);

  const closedFiltered = useMemo(() => (closed ?? []).filter((c) => {
    if (c.conviction < minConviction) return false;
    if (side === "long" && c.direction !== "long") return false;
    if (side === "short" && c.direction !== "short" && c.direction !== "fade" && c.direction !== "avoid") return false;
    return true;
  }), [closed, side, minConviction]);
  const closedHits = closedFiltered.filter((c) => c.outcome === "hit").length;
  const closedPartials = closedFiltered.filter((c) => c.outcome === "partial").length;
  const closedTotal = closedFiltered.length;
  const overallHitRate = closedTotal > 0 ? (closedHits + 0.5 * closedPartials) / closedTotal : null;

  const openFiltered = (open ?? []).filter((c) => {
    if (c.conviction < minConviction) return false;
    if (side === "long" && c.direction !== "long") return false;
    if (side === "short" && c.direction !== "short" && c.direction !== "fade" && c.direction !== "avoid") return false;
    return true;
  });
  const openPnl = openFiltered
    .map((c) => {
      if (!c.price_at_call || !c.price_current) return null;
      const raw = ((c.price_current - c.price_at_call) / c.price_at_call) * 100;
      return c.direction === "short" || c.direction === "fade" ? -raw : raw;
    })
    .filter((p): p is number => p !== null);
  const openWinners = openPnl.filter((p) => p > 0.5).length;
  const openLosers = openPnl.filter((p) => p < -0.5).length;

  return (
    <AuthGate>
      <h1 style={{ color: "var(--forte-navy)", marginTop: 4 }}>Positions</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        Every directional call extracted from a published report. Open = unmatured;
        closed = graded against the tape on its horizon.
      </p>

      <div className="card">
        <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
          <FilterGroup label="Basket">
            <Pill active={side === "all"} onClick={() => setSide("all")}>All</Pill>
            <Pill active={side === "long"} onClick={() => setSide("long")}>Long only</Pill>
            <Pill active={side === "short"} onClick={() => setSide("short")}>Short only</Pill>
          </FilterGroup>
          <FilterGroup label="Conviction">
            {[1, 3, 4, 5].map((c) => (
              <Pill key={c} active={minConviction === c} onClick={() => setMinConviction(c)}>
                {c === 1 ? "All" : c === 3 ? "≥3★" : c === 4 ? "≥4★ high" : "5★ top"}
              </Pill>
            ))}
          </FilterGroup>
          <FilterGroup label="Benchmark">
            {BENCHMARKS.map((b) => (
              <Pill key={b.ticker} active={benchmark === b.ticker} onClick={() => setBenchmark(b.ticker)}>
                {b.label}
              </Pill>
            ))}
          </FilterGroup>
        </div>
      </div>

      <BasketCard basket={basket} loading={basketLoading} />

      <div className="card">
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <button
            onClick={() => setTab("open")}
            style={{
              padding: "4px 12px", fontSize: 13,
              background: tab === "open" ? "var(--forte-navy)" : "#FFF",
              color: tab === "open" ? "#FFF" : "var(--forte-ink)",
              border: "1px solid var(--forte-rule)",
            }}
          >
            Open ({openFiltered.length})
          </button>
          <button
            onClick={() => setTab("closed")}
            style={{
              padding: "4px 12px", fontSize: 13,
              background: tab === "closed" ? "var(--forte-navy)" : "#FFF",
              color: tab === "closed" ? "#FFF" : "var(--forte-ink)",
              border: "1px solid var(--forte-rule)",
            }}
          >
            Closed ({closedFiltered.length})
          </button>
          <span className="muted" style={{ marginLeft: "auto", fontSize: 13, display: "flex", gap: 16 }}>
            {openPnl.length > 0 && (
              <span>
                Open: <strong style={{ color: "#1F7A3A" }}>{openWinners} up</strong>
                {" / "}
                <strong style={{ color: "#B8860B" }}>{openLosers} down</strong>
              </span>
            )}
            {overallHitRate !== null && (
              <span>
                Hit rate: <strong style={{ color: "var(--forte-navy)" }}>{(overallHitRate * 100).toFixed(0)}%</strong>
              </span>
            )}
          </span>
        </div>
      </div>

      {error && <div className="card"><p className="muted" style={{ color: "#B8860B" }}>{error}</p></div>}

      <div className="card">
        {!filteredRows ? (
          <p className="muted">Loading…</p>
        ) : filteredRows.length === 0 ? (
          <p className="muted">
            {tab === "open"
              ? "No open positions match these filters."
              : "No closed positions match these filters."}
          </p>
        ) : (
          <PositionsTable rows={filteredRows} closed={tab === "closed"} />
        )}
      </div>
    </AuthGate>
  );
}

function FilterGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
      <span className="muted" style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</span>
      <div style={{ display: "flex", gap: 4 }}>{children}</div>
    </div>
  );
}

function Pill({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "3px 10px", fontSize: 12,
        background: active ? "var(--forte-navy)" : "#FFF",
        color: active ? "#FFF" : "var(--forte-ink)",
        border: "1px solid var(--forte-rule)",
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}

function BasketCard({ basket, loading }: { basket: BasketSeries | null; loading: boolean }) {
  if (loading && !basket) {
    return <div className="card"><p className="muted">Computing basket…</p></div>;
  }
  if (!basket) return null;
  if (basket.error) {
    return <div className="card"><p className="muted" style={{ color: "#B8860B" }}>{basket.error}</p></div>;
  }
  if (basket.n_positions === 0) {
    return <div className="card"><p className="muted">No positions match these filters — basket is empty.</p></div>;
  }
  const br = basket.basket_return ?? 0;
  const yr = basket.benchmark_return ?? 0;
  const alpha = br - yr;
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
        <div>
          <strong style={{ color: "var(--forte-navy)" }}>Basket vs benchmark</strong>
          <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>
            {basket.n_positions} positions · rolling equal-weighted · {basket.benchmark_ticker}
          </span>
        </div>
        <div style={{ display: "flex", gap: 18, fontSize: 13 }}>
          <Stat label="Basket" value={br} color="var(--forte-navy)" />
          <Stat label="Benchmark" value={yr} color="var(--forte-teal, #5A8DA6)" />
          <Stat label="Alpha" value={alpha} color={alpha >= 0 ? "#1F7A3A" : "#B8860B"} />
        </div>
      </div>
      <BasketChart basket={basket} />
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: number; color: string }) {
  const pct = (value * 100).toFixed(1);
  return (
    <span>
      <span className="muted" style={{ marginRight: 4 }}>{label}</span>
      <strong style={{ color }}>{value > 0 ? "+" : ""}{pct}%</strong>
    </span>
  );
}

function BasketChart({ basket }: { basket: BasketSeries }) {
  const W = 760;
  const H = 220;
  const PAD = { top: 8, right: 8, bottom: 22, left: 44 };
  const n = basket.dates.length;
  if (n < 2) return <p className="muted" style={{ fontSize: 12 }}>Not enough data to plot yet.</p>;

  const all = [...basket.basket, ...basket.benchmark, 0];
  const yMin = Math.min(...all);
  const yMax = Math.max(...all);
  const yPad = (yMax - yMin) * 0.08 || 0.01;
  const lo = yMin - yPad;
  const hi = yMax + yPad;

  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const xAt = (i: number) => PAD.left + (i / (n - 1)) * innerW;
  const yAt = (v: number) => PAD.top + (1 - (v - lo) / (hi - lo)) * innerH;

  const toPath = (vals: number[]) =>
    vals.map((v, i) => `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`).join(" ");

  const zeroY = yAt(0);
  const ticks = [lo, (lo + hi) / 2, hi];

  // Sparse x-axis labels: first, middle, last.
  const xLabels = [0, Math.floor(n / 2), n - 1];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", maxHeight: 260 }}>
      {/* Zero line */}
      <line x1={PAD.left} x2={W - PAD.right} y1={zeroY} y2={zeroY}
            stroke="var(--forte-rule)" strokeDasharray="3 3" />
      {/* Y ticks */}
      {ticks.map((t, i) => (
        <g key={i}>
          <text x={PAD.left - 6} y={yAt(t) + 3} fontSize="10" textAnchor="end" fill="var(--forte-muted)">
            {(t * 100).toFixed(1)}%
          </text>
        </g>
      ))}
      {/* X ticks */}
      {xLabels.map((i) => (
        <text key={i} x={xAt(i)} y={H - 6} fontSize="10" textAnchor="middle" fill="var(--forte-muted)">
          {basket.dates[i]}
        </text>
      ))}
      {/* Benchmark */}
      <path d={toPath(basket.benchmark)} fill="none" stroke="#5A8DA6" strokeWidth={1.5} />
      {/* Basket */}
      <path d={toPath(basket.basket)} fill="none" stroke="var(--forte-navy)" strokeWidth={2} />
      {/* Legend */}
      <g transform={`translate(${PAD.left + 4}, ${PAD.top + 8})`}>
        <rect width={120} height={32} fill="#FFFFFFCC" stroke="var(--forte-rule)" />
        <line x1={6} x2={20} y1={11} y2={11} stroke="var(--forte-navy)" strokeWidth={2} />
        <text x={24} y={14} fontSize="10" fill="var(--forte-ink)">Basket</text>
        <line x1={6} x2={20} y1={25} y2={25} stroke="#5A8DA6" strokeWidth={1.5} />
        <text x={24} y={28} fontSize="10" fill="var(--forte-ink)">{basket.benchmark_ticker}</text>
      </g>
    </svg>
  );
}

function PositionsTable({ rows, closed }: { rows: PositionRow[]; closed: boolean }) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
      <thead>
        <tr style={{ borderBottom: "1px solid var(--forte-rule)", color: "var(--forte-muted)" }}>
          <th style={th}>Asset</th>
          <th style={th}>View</th>
          <th style={th}>Horizon</th>
          <th style={th}>Target</th>
          <th style={th}>Entry</th>
          <th style={th}>{closed ? "At grade" : "Now"}</th>
          <th style={th}>P/L</th>
          <th style={th}>Conviction</th>
          <th style={th}>Made</th>
          <th style={th}>By</th>
          {closed && <th style={th}>Outcome</th>}
          <th style={th}>Report</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const reference = closed ? r.price_at_evaluation : r.price_current;
          const rawMove = r.price_at_call && reference
            ? ((reference - r.price_at_call) / r.price_at_call) * 100
            : null;
          const pnl = rawMove !== null && (r.direction === "short" || r.direction === "fade")
            ? -rawMove
            : rawMove;
          return (
            <tr key={r.id} style={{ borderBottom: "1px solid var(--forte-rule)" }}>
              <td style={td}><strong>{r.asset}</strong></td>
              <td style={{ ...td, color: dirColor(r.direction), fontWeight: 600 }}>{r.direction}</td>
              <td style={td}>{r.horizon_days}d</td>
              <td style={td}>{r.target_level !== null ? fmtPrice(r.target_level) : "—"}</td>
              <td style={td}>{r.price_at_call !== null ? fmtPrice(r.price_at_call) : "—"}</td>
              <td style={td}>{reference !== null ? fmtPrice(reference) : "—"}</td>
              <td style={{ ...td, color: pnlColor(pnl), fontWeight: 600 }}>
                {pnl !== null ? `${pnl > 0 ? "+" : ""}${pnl.toFixed(1)}%` : "—"}
              </td>
              <td style={td}>{"★".repeat(r.conviction)}</td>
              <td style={td}>{r.made_at.slice(0, 10)}</td>
              <td style={td}>{r.contributor_slug}</td>
              {closed && (
                <td style={{ ...td, color: outcomeColor(r.outcome), fontWeight: 600 }}>
                  {r.outcome ?? "—"}
                </td>
              )}
              <td style={td}>
                <a href={`/reports/${r.report_id}`} style={{ color: "var(--forte-purple)" }}>#{r.report_id}</a>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function fmtPrice(p: number): string {
  if (p >= 1000) return p.toFixed(0);
  if (p >= 10) return p.toFixed(2);
  return p.toFixed(3);
}

function pnlColor(p: number | null): string {
  if (p === null) return "var(--forte-muted)";
  if (p > 0.5) return "#1F7A3A";
  if (p < -0.5) return "#B8860B";
  return "var(--forte-ink)";
}

const th: React.CSSProperties = { textAlign: "left", padding: "6px 8px", fontWeight: 600 };
const td: React.CSSProperties = { padding: "6px 8px" };

function dirColor(d: PositionRow["direction"]): string {
  if (d === "long") return "#1F7A3A";
  if (d === "short" || d === "fade") return "#B8860B";
  return "var(--forte-muted)";
}
function outcomeColor(o: PositionRow["outcome"]): string {
  if (o === "hit") return "#1F7A3A";
  if (o === "miss") return "#B8860B";
  if (o === "partial") return "var(--forte-purple)";
  return "var(--forte-muted)";
}
