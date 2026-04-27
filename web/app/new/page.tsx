"use client";
import { useState } from "react";
import { AuthGate } from "@/components/Auth";
import { api } from "@/lib/api";

type Mode = "fast" | "standard";

export default function NewReportPage() {
  const [theme, setTheme] = useState("");
  const [subtitle, setSubtitle] = useState("");
  const [mode, setMode] = useState<Mode>("standard");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true); setError(null);
    try {
      const r = await api.createReport(theme, subtitle || undefined, mode);
      window.location.href = `/?just=${r.id}`;
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
      <div className="card" style={{ maxWidth: 640 }}>
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
          <label className="byline">Mode</label>
          <div style={{ display: "flex", gap: 12, marginTop: 4 }}>
            <ModeOption
              value="standard" active={mode === "standard"} onPick={setMode}
              title="Standard"
              tagline="Opus EIC + Sonnet team. Web search on. ~$0.50-1.00."
            />
            <ModeOption
              value="fast" active={mode === "fast"} onPick={setMode}
              title="Fast (testing)"
              tagline="Haiku end-to-end. No web search. ~$0.05-0.15."
            />
          </div>
        </div>

        <button onClick={submit} disabled={!theme.trim() || busy}>{busy ? "Commissioning..." : "Commission"}</button>
        {error && <p style={{ color: "#B8860B", marginTop: 12 }}>{error}</p>}
      </div>
    </AuthGate>
  );
}

function ModeOption({ value, active, onPick, title, tagline }: {
  value: Mode; active: boolean; onPick: (m: Mode) => void;
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
