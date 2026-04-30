"use client";
import { useEffect, useRef, useState } from "react";
import { AuthGate } from "@/components/Auth";
import { api, MODE_ESTIMATES, type RenderOptions, type ReportMode, type TeamMember } from "@/lib/api";

const MODES: { value: ReportMode; title: string; tagline: string }[] = [
  { value: "test",     title: "Test (smoke)",    tagline: "Stripped pipeline, 2 analysts, no charts/rebuttal/redteam/audit. ~$0.02-0.05." },
  { value: "fast",     title: "Fast",            tagline: "Haiku end-to-end. No web search. Tight loop. ~$0.05-0.15." },
  { value: "standard", title: "Standard",        tagline: "Opus EIC + Sonnet team. Web search on. ~$0.50-1.00." },
  { value: "deep",     title: "Deep dive",       tagline: "Same models as standard, larger token + iter budget. ~$1.50-3.00." },
];

const CONFIRM_THRESHOLD_USD = 0.50;

export default function NewReportPage() {
  const [theme, setTheme] = useState("");
  const [subtitle, setSubtitle] = useState("");
  const [mode, setMode] = useState<ReportMode>("standard");
  const [team, setTeam] = useState<TeamMember[]>([]);
  const [pickedSlugs, setPickedSlugs] = useState<string[]>([]);
  const [budgetText, setBudgetText] = useState("");
  const [renderOptions, setRenderOptions] = useState<RenderOptions>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Trailing-30-day actuals per mode -- ground-truth for the confirm
  // dialog. The hard-coded MODE_ESTIMATES is the fallback when there's
  // no recent history.
  const [costStats, setCostStats] = useState<Record<ReportMode, { median: number | null; p90: number | null; n: number }> | null>(null);
  // Files queued client-side; we don't have a report id until commission, so
  // upload happens as a follow-up step right after createReport.
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.listTeam().then(setTeam).catch(() => { /* ignore — auth-gated */ });
    api.costStats().then((rows) => {
      const out = {} as Record<ReportMode, { median: number | null; p90: number | null; n: number }>;
      for (const r of rows) {
        out[r.mode] = { median: r.median_cost_usd, p90: r.p90_cost_usd, n: r.n };
      }
      setCostStats(out);
    }).catch(() => { /* ignore */ });
  }, []);

  function toggleSlug(slug: string) {
    setPickedSlugs((cur) => cur.includes(slug) ? cur.filter((s) => s !== slug) : [...cur, slug]);
  }

  async function submit() {
    setBusy(true); setError(null);
    try {
      const budget_cap_usd = budgetText.trim() ? parseFloat(budgetText) : null;
      if (budget_cap_usd !== null && (Number.isNaN(budget_cap_usd) || budget_cap_usd <= 0)) {
        throw new Error("Budget must be a positive number");
      }
      const est = MODE_ESTIMATES[mode];
      const actuals = costStats?.[mode];
      const cap = budget_cap_usd ?? est.cost_hi;
      if (cap >= CONFIRM_THRESHOLD_USD) {
        // Prefer trailing-30-day actuals when we have at least 3 rows;
        // below that the estimate is too noisy and the static MODE_
        // ESTIMATES window is honest about it being a guess.
        const showActuals = actuals && actuals.n >= 3 && actuals.median != null && actuals.p90 != null;
        const costLine = showActuals
          ? `Recent actuals (last 30d, n=${actuals!.n}): median $${actuals!.median!.toFixed(2)}, p90 $${actuals!.p90!.toFixed(2)}`
          : `Estimated cost: $${est.cost_lo.toFixed(2)}-$${est.cost_hi.toFixed(2)}`;
        const ok = window.confirm(
          `Commission "${theme}" in ${mode} mode?\n\n` +
          `${costLine}\n` +
          `Budget cap: $${cap.toFixed(2)}\n` +
          `Estimated runtime: ~${est.minutes} min\n\n` +
          `Click OK to charge it; Cancel to back out.`
        );
        if (!ok) {
          setBusy(false);
          return;
        }
      }
      const cleaned: RenderOptions = Object.fromEntries(
        Object.entries(renderOptions).filter(([, v]) => v),
      );
      const r = await api.createReport({
        theme,
        subtitle: subtitle || undefined,
        mode,
        team_override: pickedSlugs,
        budget_cap_usd,
        render_options: Object.keys(cleaned).length ? cleaned : undefined,
      });
      // Upload any attached docs before nav so the research stage sees them.
      // Workers poll on a few-second interval, so a quick sequence here is fine.
      for (const f of pendingFiles) {
        setUploadStatus(`Uploading ${f.name}…`);
        try {
          await api.uploadDocument(r.id, f);
        } catch (e) {
          // Don't block navigation if one file fails -- the report can still
          // run. Surface the error so the user can re-upload from the report
          // page if it matters.
          setUploadStatus(`Upload failed for ${f.name}: ${e}`);
        }
      }
      window.location.href = `/reports/${r.id}`;
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthGate>
      <div className="byline">Forte Research · New report</div>
      <h1 style={{ color: "var(--forte-navy)", marginTop: 4 }}>Commission a theme</h1>
      <div className="card" style={{ maxWidth: 720 }}>
        <p className="muted">Type a theme. The Editor-in-Chief writes a brief, the team researches, Data &amp; Charts builds the visualisations, the Editor edits, and the report is rendered to PDF.</p>

        <div style={{ marginBottom: 8 }}>
          <label className="byline">Theme</label>
          <input value={theme} onChange={(e) => setTheme(e.target.value)} placeholder="the nuclear renaissance, sized" />
        </div>

        <div style={{ marginBottom: 12 }}>
          <label className="byline">Subtitle (optional)</label>
          <input value={subtitle} onChange={(e) => setSubtitle(e.target.value)} placeholder="A first look at the supply, demand, and political reality" />
        </div>

        <div style={{ marginBottom: 16 }}>
          <label className="byline">Depth</label>
          <div style={{ display: "flex", gap: 10, marginTop: 4 }}>
            {MODES.map((m) => (
              <ModeOption
                key={m.value} value={m.value} active={mode === m.value} onPick={setMode}
                title={m.title} tagline={m.tagline}
              />
            ))}
          </div>
        </div>

        {team.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <label className="byline">Team override (optional)</label>
            <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
              Leave all unticked to let the Editor-in-Chief decide.
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {team.map((m) => (
                <label key={m.slug} style={{ display: "flex", gap: 8, alignItems: "center", cursor: "pointer", fontSize: 14 }}>
                  <input
                    type="checkbox"
                    checked={pickedSlugs.includes(m.slug)}
                    onChange={() => toggleSlug(m.slug)}
                    style={{ width: "auto" }}
                  />
                  <span><strong>{m.name}</strong> <span className="muted">— {m.role}</span></span>
                </label>
              ))}
            </div>
          </div>
        )}

        <div style={{ marginBottom: 16 }}>
          <label className="byline">Attach research notes / spreadsheets (optional)</label>
          <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
            PDFs, CSVs, XLSX, Markdown. The team treats these as primary sources.
          </div>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.csv,.tsv,.xlsx,.xlsm,.md,.markdown,.txt"
            onChange={(e) => {
              const files = Array.from(e.target.files || []);
              setPendingFiles((cur) => [...cur, ...files]);
              if (fileInputRef.current) fileInputRef.current.value = "";
            }}
            style={{ padding: 6 }}
          />
          {pendingFiles.length > 0 && (
            <ul style={{ marginTop: 8, paddingLeft: 18, fontSize: 13 }}>
              {pendingFiles.map((f, i) => (
                <li key={i} style={{ marginBottom: 2 }}>
                  {f.name} <span className="muted">({(f.size / 1024).toFixed(1)} KB)</span>
                  <button
                    onClick={() => setPendingFiles((cur) => cur.filter((_, idx) => idx !== i))}
                    style={{ marginLeft: 8, padding: "1px 8px", fontSize: 11, background: "#FFF", color: "var(--forte-ink)", border: "1px solid var(--forte-rule)" }}
                  >
                    remove
                  </button>
                </li>
              ))}
            </ul>
          )}
          {uploadStatus && <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>{uploadStatus}</p>}
        </div>

        <div style={{ marginBottom: 16 }}>
          <label className="byline">Report options</label>
          <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
            Toggle off any sections you don't want in the rendered PDF.
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {([
              { key: "hide_bylines",     label: "Hide author names (cover contributors + per-section bylines)" },
              { key: "hide_positions",   label: "Hide cover positions table" },
              { key: "hide_disclosures", label: "Hide disclosures appendix" },
            ] as { key: keyof RenderOptions; label: string }[]).map((o) => (
              <label key={o.key} style={{ display: "flex", gap: 8, alignItems: "center", cursor: "pointer", fontSize: 14 }}>
                <input
                  type="checkbox"
                  checked={!!renderOptions[o.key]}
                  onChange={(e) => setRenderOptions((cur) => ({ ...cur, [o.key]: e.target.checked }))}
                  style={{ width: "auto" }}
                />
                <span>{o.label}</span>
              </label>
            ))}
          </div>
        </div>

        <div style={{ marginBottom: 16 }}>
          <label className="byline">Budget cap override (USD, optional)</label>
          <input
            type="number" step="0.10" min="0"
            value={budgetText}
            onChange={(e) => setBudgetText(e.target.value)}
            placeholder="leave blank to use global cap from .env"
          />
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
            Useful for deep-dive runs that may exceed the default $1.00 cap.
          </div>
        </div>

        <button onClick={submit} disabled={!theme.trim() || busy}>{busy ? "Commissioning..." : "Commission"}</button>
        {error && <p style={{ color: "#B8860B", marginTop: 12 }}>{error}</p>}
      </div>
    </AuthGate>
  );
}

function ModeOption({ value, active, onPick, title, tagline }: {
  value: ReportMode; active: boolean; onPick: (m: ReportMode) => void;
  title: string; tagline: string;
}) {
  return (
    <div
      onClick={() => onPick(value)}
      style={{
        flex: 1,
        cursor: "pointer",
        padding: "10px 12px",
        border: `1px solid ${active ? "var(--forte-teal)" : "var(--forte-rule)"}`,
        background: active ? "rgba(91,192,190,0.06)" : "#FFF",
        borderRadius: 4,
      }}
    >
      <div style={{ fontWeight: 600, color: active ? "var(--forte-navy)" : "var(--forte-ink)" }}>{title}</div>
      <div className="muted" style={{ fontSize: 12 }}>{tagline}</div>
    </div>
  );
}
