"use client";
import { useEffect, useMemo, useState } from "react";
import { api, type AuditEntry } from "@/lib/api";

const KNOWN_EVENTS = ["model_call", "tool_call", "tool_error", "rate_limit"];

export function AuditLog({ reportId }: { reportId: number }) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [eventFilter, setEventFilter] = useState<string>("");
  const [actorFilter, setActorFilter] = useState<string>("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    api.getAuditLog(reportId).then(setEntries).catch((e) => setError(String(e)));
  }, [reportId, open]);

  const actors = useMemo(() => {
    const set = new Set<string>();
    entries?.forEach((e) => set.add(e.actor));
    return Array.from(set).sort();
  }, [entries]);

  const filtered = useMemo(() => {
    if (!entries) return [];
    return entries.filter((e) => {
      if (eventFilter && e.event !== eventFilter) return false;
      if (actorFilter && e.actor !== actorFilter) return false;
      return true;
    });
  }, [entries, eventFilter, actorFilter]);

  const totalCost = filtered.reduce((acc, e) => acc + e.cost_usd, 0);

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div className="byline">Audit log</div>
        <button onClick={() => setOpen((x) => !x)} style={{ padding: "3px 12px", fontSize: 12 }}>
          {open ? "Hide" : "Show"}
        </button>
      </div>

      {open && (
        <>
          {error && <p className="muted" style={{ color: "#B8860B" }}>{error}</p>}
          {!entries && !error && <p className="muted" style={{ fontSize: 12 }}>Loading…</p>}
          {entries && entries.length === 0 && <p className="muted" style={{ fontSize: 12 }}>No audit entries yet.</p>}
          {entries && entries.length > 0 && (
            <>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                <select value={eventFilter} onChange={(e) => setEventFilter(e.target.value)} style={{ width: "auto" }}>
                  <option value="">All events</option>
                  {KNOWN_EVENTS.map((ev) => (<option key={ev} value={ev}>{ev}</option>))}
                </select>
                <select value={actorFilter} onChange={(e) => setActorFilter(e.target.value)} style={{ width: "auto" }}>
                  <option value="">All actors</option>
                  {actors.map((a) => (<option key={a} value={a}>{a}</option>))}
                </select>
                <span className="muted" style={{ fontSize: 12, alignSelf: "center" }}>
                  {filtered.length} entr{filtered.length === 1 ? "y" : "ies"} · ${totalCost.toFixed(4)}
                </span>
              </div>

              <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse", marginTop: 12 }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "var(--forte-muted)", fontSize: 11 }}>
                    <th style={{ padding: "4px 0" }}>Time</th>
                    <th>Actor</th>
                    <th>Event</th>
                    <th style={{ textAlign: "right" }}>Cost</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((e) => (
                    <tr key={e.id} style={{ borderTop: "1px solid var(--forte-rule)", verticalAlign: "top" }}>
                      <td style={{ padding: "6px 6px 6px 0", whiteSpace: "nowrap" }}>
                        {new Date(e.created_at).toLocaleTimeString()}
                      </td>
                      <td style={{ paddingRight: 8 }}>{e.actor}</td>
                      <td style={{ paddingRight: 8 }}>
                        <span className="stage-pill">{e.event}</span>
                      </td>
                      <td style={{ textAlign: "right", fontFamily: "monospace", paddingRight: 8 }}>
                        {e.cost_usd ? `$${e.cost_usd.toFixed(4)}` : "—"}
                      </td>
                      <td style={{ fontFamily: "ui-monospace, monospace", color: "var(--forte-muted)" }}>
                        {detailSummary(e)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </div>
  );
}

function detailSummary(e: AuditEntry): string {
  const d = e.details || {};
  if (e.event === "model_call") {
    const tokens = `in:${d.input_tokens ?? "?"}/out:${d.output_tokens ?? "?"}`;
    return `${d.model ?? ""} ${tokens}`;
  }
  if (e.event === "tool_call") {
    const input = d.input ? JSON.stringify(d.input) : "";
    return `${d.tool ?? ""} ${input.length > 80 ? input.slice(0, 80) + "…" : input}`;
  }
  if (e.event === "tool_error") {
    return `${d.tool ?? ""}: ${d.error ?? ""}`;
  }
  if (e.event === "rate_limit") {
    return `wait ${d.wait_s ?? "?"}s (attempt ${d.attempt ?? "?"})`;
  }
  return "";
}
