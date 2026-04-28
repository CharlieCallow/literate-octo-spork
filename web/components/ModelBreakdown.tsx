"use client";
import { useEffect, useState } from "react";
import { api, type ModelBreakdownRow } from "@/lib/api";

/* Per-report tokens + cost split by model (Opus / Sonnet / Haiku). Reads from
 * the audit log so it reflects the actual model that ran each call, not the
 * mode setting. Empty until at least one model_call event has been recorded. */
export function ModelBreakdown({ reportId }: { reportId: number }) {
  const [rows, setRows] = useState<ModelBreakdownRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getModelBreakdown(reportId)
      .then((r) => { if (!cancelled) setRows(r); })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [reportId]);

  if (error) return null;
  if (rows.length === 0) return null;

  const totalCost = rows.reduce((s, r) => s + r.cost_usd, 0);
  const totalIn = rows.reduce((s, r) => s + r.input_tokens, 0);
  const totalOut = rows.reduce((s, r) => s + r.output_tokens, 0);

  return (
    <div className="card">
      <div className="byline">Model breakdown</div>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        Tokens and cost by model. {rows.length === 1 ? "Single tier this run." : "Tiered: Opus for the editor, Sonnet for analysts, Haiku for grunt work."}
      </p>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr style={{ background: "var(--forte-bg)" }}>
            <th style={{ textAlign: "left", padding: "6px 8px", borderBottom: "1px solid var(--forte-rule)" }}>Model</th>
            <th style={{ textAlign: "right", padding: "6px 8px", borderBottom: "1px solid var(--forte-rule)" }}>Calls</th>
            <th style={{ textAlign: "right", padding: "6px 8px", borderBottom: "1px solid var(--forte-rule)" }}>Input tokens</th>
            <th style={{ textAlign: "right", padding: "6px 8px", borderBottom: "1px solid var(--forte-rule)" }}>Output tokens</th>
            <th style={{ textAlign: "right", padding: "6px 8px", borderBottom: "1px solid var(--forte-rule)" }}>Cost</th>
            <th style={{ textAlign: "right", padding: "6px 8px", borderBottom: "1px solid var(--forte-rule)" }}>Share</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.model}>
              <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--forte-rule)" }}>
                <span style={{ fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12 }}>{r.model}</span>
                <span className="stage-pill" style={{ marginLeft: 6, background: tierColor(r.model).bg, color: tierColor(r.model).fg }}>
                  {tierLabel(r.model)}
                </span>
              </td>
              <td style={{ padding: "6px 8px", textAlign: "right", borderBottom: "1px solid var(--forte-rule)" }}>{r.calls}</td>
              <td style={{ padding: "6px 8px", textAlign: "right", borderBottom: "1px solid var(--forte-rule)" }}>{r.input_tokens.toLocaleString()}</td>
              <td style={{ padding: "6px 8px", textAlign: "right", borderBottom: "1px solid var(--forte-rule)" }}>{r.output_tokens.toLocaleString()}</td>
              <td style={{ padding: "6px 8px", textAlign: "right", borderBottom: "1px solid var(--forte-rule)" }}>${r.cost_usd.toFixed(4)}</td>
              <td style={{ padding: "6px 8px", textAlign: "right", borderBottom: "1px solid var(--forte-rule)", color: "var(--forte-muted)" }}>
                {totalCost > 0 ? `${((r.cost_usd / totalCost) * 100).toFixed(0)}%` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td style={{ padding: "6px 8px", fontWeight: 600, color: "var(--forte-navy)" }}>Total</td>
            <td style={{ padding: "6px 8px", textAlign: "right", color: "var(--forte-muted)" }}>—</td>
            <td style={{ padding: "6px 8px", textAlign: "right", fontWeight: 600 }}>{totalIn.toLocaleString()}</td>
            <td style={{ padding: "6px 8px", textAlign: "right", fontWeight: 600 }}>{totalOut.toLocaleString()}</td>
            <td style={{ padding: "6px 8px", textAlign: "right", fontWeight: 600 }}>${totalCost.toFixed(4)}</td>
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function tierLabel(model: string): string {
  const m = model.toLowerCase();
  if (m.includes("opus")) return "OPUS";
  if (m.includes("sonnet")) return "SONNET";
  if (m.includes("haiku")) return "HAIKU";
  return "OTHER";
}

function tierColor(model: string): { bg: string; fg: string } {
  const m = model.toLowerCase();
  if (m.includes("opus")) return { bg: "#EEEEF6", fg: "#1F1B4D" };
  if (m.includes("sonnet")) return { bg: "rgba(91,192,190,0.18)", fg: "#1F1B4D" };
  if (m.includes("haiku")) return { bg: "#FFF8E1", fg: "#B8860B" };
  return { bg: "var(--forte-rule)", fg: "var(--forte-ink)" };
}
