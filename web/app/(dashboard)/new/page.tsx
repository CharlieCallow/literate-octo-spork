"use client";
import { useEffect, useState } from "react";
import { AuthGate } from "@/components/Auth";
import { api, MODE_ESTIMATES, type ReportMode, type TeamMember } from "@/lib/api";

const MODES: { value: ReportMode; title: string; tagline: string }[] = [
  { value: "fast",     title: "Fast (testing)",  tagline: "Haiku end-to-end. No web search. Tight loop. ~$0.05-0.15." },
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listTeam().then(setTeam).catch(() => { /* ignore — auth-gated */ });
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
      const cap = budget_cap_usd ?? est.cost_hi;
      if (cap >= CONFIRM_THRESHOLD_USD) {
        const ok = window.confirm(
          `Commission "${theme}" in ${mode} mode?\n\n` +
          `Estimated cost: $${est.cost_lo.toFixed(2)}-$${est.cost_hi.toFixed(2)}\n` +
          `Budget cap: $${cap.toFixed(2)}\n` +
          `Estimated runtime: ~${est.minutes} min\n\n` +
          `Click OK to charge it; Cancel to back out.`
        );
        if (!ok) {
          setBusy(false);
          return;
        }
      }
      const r = await api.createReport({
        theme,
        subtitle: subtitle || undefined,
        mode,
        team_override: pickedSlugs,
        budget_cap_usd,
      });
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
