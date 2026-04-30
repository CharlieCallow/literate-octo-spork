"use client";
import { useEffect, useState } from "react";
import { AuthGate } from "@/components/Auth";
import { api, type PositionRow } from "@/lib/api";

type Tab = "open" | "closed";

export default function PositionsPage() {
  const [tab, setTab] = useState<Tab>("open");
  const [open, setOpen] = useState<PositionRow[] | null>(null);
  const [closed, setClosed] = useState<PositionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listOpenPositions().then(setOpen).catch((e) => setError(String(e)));
    api.listClosedPositions().then(setClosed).catch((e) => setError(String(e)));
  }, []);

  const rows = tab === "open" ? open : closed;
  const closedHits = (closed ?? []).filter((c) => c.outcome === "hit").length;
  const closedPartials = (closed ?? []).filter((c) => c.outcome === "partial").length;
  const closedTotal = (closed ?? []).length;
  const overallHitRate = closedTotal > 0 ? (closedHits + 0.5 * closedPartials) / closedTotal : null;

  const openPnl = (open ?? [])
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
            Open ({open?.length ?? "…"})
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
            Closed ({closed?.length ?? "…"})
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
                Firm hit rate: <strong style={{ color: "var(--forte-navy)" }}>{(overallHitRate * 100).toFixed(0)}%</strong>
              </span>
            )}
          </span>
        </div>
      </div>

      {error && <div className="card"><p className="muted" style={{ color: "#B8860B" }}>{error}</p></div>}

      <div className="card">
        {!rows ? (
          <p className="muted">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="muted">
            {tab === "open"
              ? "No open positions yet -- they show up after a report's render stage extracts its calls."
              : "No closed positions yet -- the worker grades calls weekly once they age past their horizon."}
          </p>
        ) : (
          <PositionsTable rows={rows} closed={tab === "closed"} />
        )}
      </div>
    </AuthGate>
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
          // Flip sign for short/fade so a falling price reads as a winner.
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
