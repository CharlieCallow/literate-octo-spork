"use client";
import { useEffect, useState } from "react";
import { AuthGate } from "@/components/Auth";
import { api, type AppSettings } from "@/lib/api";

export default function SettingsPage() {
  const [s, setS] = useState<AppSettings | null>(null);
  const [draft, setDraft] = useState<AppSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    api.getSettings().then((x) => { setS(x); setDraft(x); }).catch((e) => setError(String(e)));
  }, []);

  async function save() {
    if (!draft) return;
    setBusy(true); setError(null); setSaved(null);
    try {
      const updated = await api.updateSettings(draft);
      setS(updated);
      setDraft(updated);
      setSaved("Saved.");
      setTimeout(() => setSaved(null), 2500);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  function bind<K extends keyof AppSettings>(key: K) {
    return {
      value: (draft?.[key] ?? "") as string | number,
      onChange: (e: React.ChangeEvent<HTMLInputElement>) => {
        if (!draft) return;
        const raw = e.target.value;
        const parsed = typeof draft[key] === "number" ? Number(raw) : raw;
        setDraft({ ...draft, [key]: parsed } as AppSettings);
      },
    };
  }

  return (
    <AuthGate>
      <div className="byline">Forte Research · Settings</div>
      <h1 style={{ color: "var(--forte-navy)", marginTop: 4 }}>Runtime settings</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        Edits take effect on the next report. No redeploy needed. Defaults come from your <code>.env</code>; saved values override them.
      </p>

      {error && <div className="card"><p className="muted" style={{ color: "#B8860B" }}>{error}</p></div>}
      {!s && !error && <div className="card"><p className="muted">Loading…</p></div>}

      {draft && (
        <>
          <div className="card">
            <div className="byline">Models</div>
            <div style={{ marginTop: 8 }}>
              <label className="byline">Haiku (Scout / fast mode)</label>
              <input {...bind("model_haiku")} />
            </div>
            <div style={{ marginTop: 8 }}>
              <label className="byline">Sonnet (Analysts / D&amp;C / Recruiter)</label>
              <input {...bind("model_sonnet")} />
            </div>
            <div style={{ marginTop: 8 }}>
              <label className="byline">Opus (Editor-in-Chief)</label>
              <input {...bind("model_opus")} />
            </div>
          </div>

          <div className="card">
            <div className="byline">Cost caps (USD)</div>
            <div style={{ marginTop: 8 }}>
              <label className="byline">Per-report</label>
              <input type="number" step="0.01" min="0" {...bind("cost_per_report_usd")} />
            </div>
            <div style={{ marginTop: 8 }}>
              <label className="byline">Per-day</label>
              <input type="number" step="0.01" min="0" {...bind("cost_per_day_usd")} />
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
            {saved && <span className="muted" style={{ fontSize: 12 }}>{saved}</span>}
          </div>
        </>
      )}
    </AuthGate>
  );
}
