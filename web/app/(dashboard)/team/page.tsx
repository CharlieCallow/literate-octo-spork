"use client";
import { useCallback, useEffect, useState } from "react";
import { AuthGate } from "@/components/Auth";
import { RecommendationsCard } from "@/components/RecommendationsCard";
import { api, type TeamMember } from "@/lib/api";

interface ArchivedMember { slug: string; name: string; role: string; }

export default function TeamPage() {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [archived, setArchived] = useState<ArchivedMember[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listTeam().then(setMembers).catch((e) => setError(String(e)));
    api.listArchive().then(setArchived).catch(() => { /* archive is optional */ });
  }, []);

  useEffect(() => { load(); }, [load]);

  async function rehire(slug: string) {
    try {
      await api.rehire(slug);
      load();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <AuthGate>
      <div className="byline">Forte Research · Team</div>
      <h1 style={{ color: "var(--forte-navy)", marginTop: 4 }}>Roster</h1>
      <RecommendationsCard onChange={load} />
      {error && <div className="card"><p className="muted">{error}</p></div>}
      {members.length === 0 && !error && (
        <div className="card"><p className="muted">Loading roster…</p></div>
      )}
      {members.map((m) => {
        const hitRatePct =
          m.hit_rate != null ? `${Math.round(m.hit_rate * 100)}%` : null;
        const overconfident =
          m.hit_rate != null
          && m.avg_conviction != null
          && m.avg_conviction >= 4.0
          && m.hit_rate < 0.5;
        return (
          <div key={m.slug} className="card">
            <h2 style={{ marginBottom: 4, fontSize: 18 }}>
              <a href={`/team/${m.slug}`} style={{ color: "var(--forte-navy)" }}>{m.name}</a>
            </h2>
            <p className="muted" style={{ marginTop: 0 }}>
              <span className="byline" style={{ marginRight: 8 }}>{m.role}</span>
              {m.reports_contributed} report{m.reports_contributed === 1 ? "" : "s"}
              {m.last_assignment_at && (
                <> · last assignment {new Date(m.last_assignment_at).toLocaleDateString()}</>
              )}
            </p>
            {(m.calls_total ?? 0) > 0 && (
              <p style={{ marginTop: 4, fontSize: 12 }}>
                <span className="byline" style={{ marginRight: 6 }}>Calibration</span>
                {(m.calls_graded ?? 0) === 0 ? (
                  <span className="muted">
                    {m.calls_total} calls open · avg conviction {m.avg_conviction?.toFixed(1) ?? "—"}/5 · none graded yet
                  </span>
                ) : (
                  <>
                    <strong style={{ color: overconfident ? "#A33" : "var(--forte-navy)" }}>
                      {hitRatePct ?? "early"}
                    </strong>
                    <span className="muted">
                      {" "}hit rate over {m.calls_graded} graded
                      {m.avg_conviction != null && <> · avg conviction {m.avg_conviction.toFixed(1)}/5</>}
                      {overconfident && <> · <span style={{ color: "#A33" }}>overconfident</span></>}
                    </span>
                  </>
                )}
              </p>
            )}
          </div>
        );
      })}

      {archived.length > 0 && (
        <div className="card">
          <div className="byline">Archive</div>
          <p className="muted" style={{ fontSize: 12, margin: "0 0 8px" }}>
            Fired personas. Click rehire to bring them back to the standing roster.
          </p>
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {archived.map((a) => (
              <li key={a.slug} style={{ padding: "6px 0", borderTop: "1px solid var(--forte-rule)" }}>
                <strong>{a.name}</strong>{" "}
                <span className="muted">— {a.role}</span>{" "}
                <button
                  onClick={() => rehire(a.slug)}
                  style={{ padding: "2px 10px", fontSize: 11, marginLeft: 8 }}
                >
                  Rehire
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </AuthGate>
  );
}
