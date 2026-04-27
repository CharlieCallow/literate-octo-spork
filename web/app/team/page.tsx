"use client";
import { useCallback, useEffect, useState } from "react";
import { AuthGate } from "@/components/Auth";
import { RecommendationsCard } from "@/components/RecommendationsCard";
import { api, type TeamMember } from "@/lib/api";

export default function TeamPage() {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listTeam().then(setMembers).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <AuthGate>
      <div className="byline">Forte Research · Team</div>
      <h1 style={{ color: "var(--forte-navy)", marginTop: 4 }}>Roster</h1>
      <RecommendationsCard onChange={load} />
      {error && <div className="card"><p className="muted">{error}</p></div>}
      {members.length === 0 && !error && (
        <div className="card"><p className="muted">Loading roster…</p></div>
      )}
      {members.map((m) => (
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
        </div>
      ))}
    </AuthGate>
  );
}
