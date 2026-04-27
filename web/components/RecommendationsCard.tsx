"use client";
import { useEffect, useState } from "react";
import { api, type Recommendation } from "@/lib/api";

export function RecommendationsCard({ onChange }: { onChange?: () => void }) {
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setRecs(await api.listRecommendations("pending"));
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function review() {
    setBusy(true); setError(null);
    try {
      await api.kickRecruiterReview();
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function decide(rec: Recommendation, action: "approve" | "dismiss") {
    try {
      if (action === "approve") await api.approveRecommendation(rec.id);
      else await api.dismissRecommendation(rec.id);
      await load();
      onChange?.();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div className="byline">Recruiter recommendations</div>
        <button onClick={review} disabled={busy} style={{ padding: "4px 12px", fontSize: 12 }}>
          {busy ? "Reviewing…" : "Run review"}
        </button>
      </div>
      {error && <p className="muted" style={{ color: "#B8860B" }}>{error}</p>}
      {recs.length === 0 ? (
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>
          No pending recommendations. The Recruiter looks at the team after each report finishes.
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {recs.map((r) => (
            <div key={r.id} style={{
              border: "1px solid var(--forte-rule)", borderRadius: 4, padding: 10,
              background: r.kind === "promote" ? "rgba(91,192,190,0.06)" : "rgba(184,134,11,0.06)",
            }}>
              <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                <span style={{
                  fontSize: 10, padding: "1px 6px", borderRadius: 3, letterSpacing: "0.06em",
                  textTransform: "uppercase", fontWeight: 600,
                  background: r.kind === "promote" ? "#5BC0BE" : "#B8860B",
                  color: "#FFF",
                }}>{r.kind}</span>
                <strong style={{ color: "var(--forte-navy)" }}>{r.subject_name}</strong>
                <span className="muted" style={{ fontSize: 12 }}>— {r.subject_role}</span>
              </div>
              <p style={{ fontSize: 13, margin: "6px 0 8px" }}>{r.reasoning}</p>
              <div style={{ display: "flex", gap: 8 }}>
                <button onClick={() => decide(r, "approve")} style={{ padding: "3px 12px", fontSize: 12 }}>
                  Approve
                </button>
                <button
                  onClick={() => decide(r, "dismiss")}
                  style={{ padding: "3px 12px", fontSize: 12, background: "#FFF", color: "var(--forte-ink)", border: "1px solid var(--forte-rule)" }}
                >
                  Dismiss
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
