"use client";
import { useState } from "react";
import { AuthGate } from "@/components/Auth";
import { api } from "@/lib/api";

export default function NewReportPage() {
  const [theme, setTheme] = useState("");
  const [subtitle, setSubtitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true); setError(null);
    try {
      const r = await api.createReport(theme, subtitle || undefined);
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
        <p className="muted">Type a theme. The Editor-in-Chief writes a brief, the team researches, Data &amp; Charts builds a chart, the Editor edits, and the report is rendered to PDF.</p>
        <div style={{ marginBottom: 8 }}>
          <label className="byline">Theme</label>
          <input value={theme} onChange={(e) => setTheme(e.target.value)} placeholder="the nuclear renaissance, sized" />
        </div>
        <div style={{ marginBottom: 12 }}>
          <label className="byline">Subtitle (optional)</label>
          <input value={subtitle} onChange={(e) => setSubtitle(e.target.value)} placeholder="A first look at the supply, demand, and political reality" />
        </div>
        <button onClick={submit} disabled={!theme.trim() || busy}>{busy ? "Commissioning..." : "Commission"}</button>
        {error && <p style={{ color: "#B8860B", marginTop: 12 }}>{error}</p>}
      </div>
    </AuthGate>
  );
}
