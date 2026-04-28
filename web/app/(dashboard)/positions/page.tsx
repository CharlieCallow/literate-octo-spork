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
          {overallHitRate !== null && (
            <span className="muted" style={{ marginLeft: "auto", fontSize: 13 }}>
              Firm hit rate: <strong style={{ color: "var(--forte-navy)" }}>{(overallHitRate * 100).toFixed(0)}%</strong>
            </span>
          )}
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
          <th style={th}>Conviction</th>
          <th style={th}>Made</th>
          <th style={th}>By</th>
          {closed && <th style={th}>Outcome</th>}
          {closed && <th style={th}>Move</th>}
          <th style={th}>Report</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const move = r.price_at_call && r.price_at_evaluation
            ? ((r.price_at_evaluation - r.price_at_call) / r.price_at_call) * 100
            : null;
          return (
            <tr key={r.id} style={{ borderBottom: "1px solid var(--forte-rule)" }}>
              <td style={td}><strong>{r.asset}</strong></td>
              <td style={{ ...td, color: dirColor(r.direction), fontWeight: 600 }}>{r.direction}</td>
              <td style={td}>{r.horizon_days}d</td>
              <td style={td}>{r.target_level ?? "—"}</td>
              <td style={td}>{"★".repeat(r.conviction)}</td>
              <td style={td}>{r.made_at.slice(0, 10)}</td>
              <td style={td}>{r.contributor_slug}</td>
              {closed && (
                <td style={{ ...td, color: outcomeColor(r.outcome), fontWeight: 600 }}>
                  {r.outcome ?? "—"}
                </td>
              )}
              {closed && (
                <td style={{ ...td, color: move !== null && move > 0 ? "#1F7A3A" : "#B8860B" }}>
                  {move !== null ? `${move > 0 ? "+" : ""}${move.toFixed(1)}%` : "—"}
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
