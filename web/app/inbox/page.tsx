"use client";
import { useEffect, useState } from "react";
import { AuthGate } from "@/components/Auth";
import { api, type ReportMode, type ScoutRun, type Theme } from "@/lib/api";

export default function InboxPage() {
  const [themes, setThemes] = useState<Theme[]>([]);
  const [runs, setRuns] = useState<ScoutRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const [t, r] = await Promise.all([api.latestThemes(), api.listScoutRuns()]);
        if (alive) { setThemes(t); setRuns(r); }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    tick();
    const i = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(i); };
  }, []);

  async function kick() {
    setBusy(true); setError(null);
    try { await api.kickScout(); } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  async function commission(theme: Theme, mode: ReportMode) {
    try {
      const r = await api.commissionTheme(theme.id, mode);
      window.location.href = `/reports/${r.report_id}`;
    } catch (e) {
      setError(String(e));
    }
  }

  const lastRun = runs[0];

  return (
    <AuthGate>
      <div className="byline">Forte Research · Inbox</div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h1 style={{ color: "var(--forte-navy)", marginTop: 4 }}>Today's themes</h1>
        <button onClick={kick} disabled={busy} style={{ padding: "6px 14px", fontSize: 13 }}>
          {busy ? "Running…" : "Run Scout now"}
        </button>
      </div>

      {error && <div className="card"><p className="muted">{error}</p></div>}

      {lastRun && (
        <div className="card" style={{ paddingBottom: 12 }}>
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            Last run: {lastRun.started_at}
            {lastRun.finished_at ? <> · {lastRun.n_themes} themes · ${lastRun.cost_usd.toFixed(3)}</> : <> · running…</>}
            {lastRun.error && <> · <span style={{ color: "#B8860B" }}>{lastRun.error}</span></>}
          </p>
        </div>
      )}

      {themes.length === 0 ? (
        <div className="card">
          <p className="muted">No themes yet. Hit <strong>Run Scout now</strong> to kick a digest.</p>
        </div>
      ) : (
        themes.map((t) => <ThemeCard key={t.id} theme={t} onCommission={commission} />)
      )}
    </AuthGate>
  );
}

function ThemeCard({ theme, onCommission }: {
  theme: Theme;
  onCommission: (theme: Theme, mode: ReportMode) => void;
}) {
  const commissioned = theme.commissioned_report_id !== null;
  return (
    <div className="card">
      <h2 style={{ marginBottom: 4, fontSize: 18 }}>{theme.headline}</h2>
      {theme.why_now && <p style={{ marginTop: 0 }}><span className="byline" style={{ marginRight: 6 }}>Why now</span>{theme.why_now}</p>}
      {theme.dig_into && <p style={{ marginTop: 0 }}><span className="byline" style={{ marginRight: 6 }}>Dig into</span>{theme.dig_into}</p>}
      {theme.source_urls.length > 0 && (
        <p className="muted" style={{ fontSize: 11, marginTop: 8 }}>
          Sources:{" "}
          {theme.source_urls.map((u, i) => (
            <span key={u}>
              <a href={u} target="_blank" rel="noreferrer">{new URL(u).hostname}</a>
              {i < theme.source_urls.length - 1 && ", "}
            </span>
          ))}
        </p>
      )}
      <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center" }}>
        {commissioned ? (
          <a href={`/reports/${theme.commissioned_report_id}`} style={{ fontSize: 13 }}>
            → View report #{theme.commissioned_report_id}
          </a>
        ) : (
          <>
            <button onClick={() => onCommission(theme, "fast")} style={{ padding: "4px 10px", fontSize: 12 }}>
              Commission (fast)
            </button>
            <button onClick={() => onCommission(theme, "standard")} style={{ padding: "4px 10px", fontSize: 12 }}>
              Commission (standard)
            </button>
          </>
        )}
      </div>
    </div>
  );
}
