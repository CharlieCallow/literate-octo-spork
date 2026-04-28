"use client";
import { useEffect, useState } from "react";
import { api, type Report } from "@/lib/api";

type Turn = { role: "user" | "assistant"; content: string };

interface Props {
  report: Report;
  contributorSlugs: string[]; // standing analysts who contributed
}

export function AskAnalyst({ report, contributorSlugs }: Props) {
  const [persona, setPersona] = useState<string | "">(contributorSlugs[0] ?? "");
  const [history, setHistory] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset the chat when the persona changes (each persona is a separate
  // conversation -- mixing histories would confuse the system prompt).
  useEffect(() => { setHistory([]); setError(null); }, [persona]);

  if (report.stage !== "done") return null;
  if (contributorSlugs.length === 0) return null;

  async function send() {
    if (!persona || !draft.trim() || loading) return;
    const message = draft.trim();
    setLoading(true);
    setError(null);
    setDraft("");
    const optimistic: Turn[] = [...history, { role: "user", content: message }];
    setHistory(optimistic);
    try {
      const res = await api.askAnalyst(report.id, {
        persona_slug: persona,
        message,
        history,  // history BEFORE this turn (the agent appends user_message itself)
      });
      setHistory([...optimistic, { role: "assistant", content: res.reply }]);
    } catch (e) {
      setError(String(e));
      // Roll back the optimistic turn on failure.
      setHistory(history);
      setDraft(message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <div className="byline">Ask the analyst</div>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        Chat with a contributor about this report. Their persona file is the
        system prompt; the published report is loaded as context.
      </p>
      <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "center" }}>
        <label style={{ fontSize: 12, color: "var(--forte-muted)" }}>Persona:</label>
        <select
          value={persona}
          onChange={(e) => setPersona(e.target.value)}
          style={{ padding: "3px 8px", fontSize: 13 }}
        >
          {contributorSlugs.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, maxHeight: 360, overflowY: "auto" }}>
        {history.length === 0 && (
          <p className="muted" style={{ fontSize: 12 }}>
            No questions yet. Try: "Why DXY and not EURUSD?" or "What would falsify this trade?"
          </p>
        )}
        {history.map((t, i) => (
          <div
            key={i}
            style={{
              alignSelf: t.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "85%",
              padding: "8px 12px",
              borderRadius: 8,
              background: t.role === "user" ? "var(--forte-bg)" : "#FFF",
              border: t.role === "user" ? "none" : "1px solid var(--forte-rule)",
              fontSize: 13,
              whiteSpace: "pre-wrap",
            }}
          >
            {t.content}
          </div>
        ))}
        {loading && <p className="muted" style={{ fontSize: 12, alignSelf: "flex-start" }}>…thinking</p>}
      </div>

      {error && <p className="muted" style={{ color: "#B8860B", fontSize: 12 }}>{error}</p>}

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send()}
          placeholder="Ask a follow-up…"
          disabled={loading}
          style={{
            flex: 1, padding: "6px 10px", fontSize: 13,
            border: "1px solid var(--forte-rule)", borderRadius: 4,
          }}
        />
        <button
          onClick={send}
          disabled={loading || !draft.trim()}
          style={{ padding: "6px 14px", fontSize: 13 }}
        >
          Send
        </button>
      </div>
    </div>
  );
}
