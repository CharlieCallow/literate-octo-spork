"use client";
import { useEffect, useMemo, useState } from "react";
import { api, type Report, type ReportMode, type ReportStage } from "@/lib/api";

type Sort = "newest" | "oldest" | "expensive";

const ALL_STAGES: ReportStage[] = [
  "queued", "brief", "research", "charts", "draft", "redteam", "edit", "audit", "render", "feedback", "housekeeping", "done", "failed",
];
const ALL_MODES: ReportMode[] = ["fast", "standard", "deep"];

export function ArchiveTable() {
  const [reports, setReports] = useState<Report[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [stages, setStages] = useState<Set<ReportStage>>(new Set());
  const [modes, setModes] = useState<Set<ReportMode>>(new Set());
  const [sort, setSort] = useState<Sort>("newest");

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const list = await api.listReports();
        if (alive) setReports(list);
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    tick();
    const t = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    let out = reports.filter((r) => {
      if (q && !r.theme.toLowerCase().includes(q) && !(r.subtitle || "").toLowerCase().includes(q)) return false;
      if (stages.size > 0 && !stages.has(r.stage)) return false;
      if (modes.size > 0 && !modes.has(r.mode)) return false;
      return true;
    });
    out = [...out].sort((a, b) => {
      if (sort === "expensive") return b.cost_usd - a.cost_usd;
      // newest reports come first when listing /reports; preserve that for "newest"
      // and reverse for "oldest". The API already sorts by created_at desc.
      if (sort === "oldest") return reports.indexOf(b) - reports.indexOf(a);
      return reports.indexOf(a) - reports.indexOf(b);
    });
    return out;
  }, [reports, search, stages, modes, sort]);

  function toggle<T>(set: Set<T>, value: T): Set<T> {
    const next = new Set(set);
    if (next.has(value)) next.delete(value); else next.add(value);
    return next;
  }

  async function resume(id: number) {
    try { await api.resume(id); } catch (e) { setError(String(e)); }
  }

  return (
    <>
      <div className="card">
        <div className="byline">Search</div>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter by theme or subtitle"
        />

        <div className="byline" style={{ marginTop: 12 }}>Stage</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {ALL_STAGES.map((s) => (
            <FilterPill key={s} label={s} active={stages.has(s)} onClick={() => setStages((cur) => toggle(cur, s))} />
          ))}
        </div>

        <div className="byline" style={{ marginTop: 12 }}>Mode</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {ALL_MODES.map((m) => (
            <FilterPill key={m} label={m} active={modes.has(m)} onClick={() => setModes((cur) => toggle(cur, m))} />
          ))}
        </div>

        <div className="byline" style={{ marginTop: 12 }}>Sort</div>
        <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} style={{ width: 200 }}>
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="expensive">Most expensive first</option>
        </select>
      </div>

      {error && <div className="card"><p className="muted">{error}</p></div>}

      <div className="card">
        <div className="byline">{filtered.length} report{filtered.length === 1 ? "" : "s"}</div>
        {filtered.length === 0 ? (
          <p className="muted">No reports match. {reports.length > 0 ? "Adjust filters." : <>Commission one from <a href="/new">New report</a>.</>}</p>
        ) : (
          <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--forte-muted)", fontSize: 11 }}>
                <th style={{ padding: "4px 0" }}>Theme</th>
                <th>Stage</th>
                <th>Mode</th>
                <th style={{ textAlign: "right" }}>Cost</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={r.id} style={{ borderTop: "1px solid var(--forte-rule)" }}>
                  <td style={{ padding: "8px 0" }}>
                    <a href={`/reports/${r.id}`} style={{ color: "var(--forte-purple)" }}>
                      <strong>{r.theme}</strong>
                    </a>
                    {r.subtitle && <div className="muted" style={{ fontSize: 11 }}>{r.subtitle}</div>}
                  </td>
                  <td><span className={`stage-pill ${r.stage}`}>{r.stage}</span></td>
                  <td><span className="stage-pill">{r.mode}</span></td>
                  <td style={{ textAlign: "right", fontFamily: "monospace" }}>${r.cost_usd.toFixed(3)}</td>
                  <td style={{ textAlign: "right" }}>
                    {r.pdf_url && <a href={api.pdfUrl(r.id)}>PDF</a>}
                    {r.stage === "failed" && (
                      <> · <button onClick={() => resume(r.id)} style={{ padding: "2px 8px", fontSize: 11 }}>Resume</button></>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

function FilterPill({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        background: active ? "var(--forte-navy)" : "#FFF",
        color: active ? "#FFF" : "var(--forte-ink)",
        border: `1px solid ${active ? "var(--forte-navy)" : "var(--forte-rule)"}`,
        padding: "3px 10px",
        fontSize: 11,
        fontWeight: 500,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
        borderRadius: 999,
        cursor: "pointer",
      }}
    >
      {label}
    </button>
  );
}
