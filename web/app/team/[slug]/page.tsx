"use client";
import { use, useEffect, useState } from "react";
import { AuthGate } from "@/components/Auth";
import { api, type PersonaDetail } from "@/lib/api";

export default function PersonaPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);

  const [persona, setPersona] = useState<PersonaDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    api.getPersona(slug)
      .then((p) => { setPersona(p); setDraft(p.markdown); })
      .catch((e) => setError(String(e)));
  }, [slug]);

  async function save() {
    if (!persona) return;
    setSaving(true); setError(null); setSaved(null);
    try {
      const updated = await api.updatePersona(slug, draft);
      setPersona(updated);
      setDraft(updated.markdown);
      setEditing(false);
      setSaved("Saved.");
      setTimeout(() => setSaved(null), 2500);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  function cancel() {
    if (persona) setDraft(persona.markdown);
    setEditing(false);
    setError(null);
  }

  return (
    <AuthGate>
      <div className="byline">
        <a href="/team" style={{ color: "var(--forte-muted)" }}>← Team</a>
      </div>
      {error && <div className="card"><p className="muted" style={{ color: "#B8860B" }}>{error}</p></div>}
      {!persona ? (
        <div className="card"><p className="muted">Loading…</p></div>
      ) : (
        <>
          <h1 style={{ color: "var(--forte-navy)", marginTop: 4 }}>{persona.name}</h1>
          <p className="muted" style={{ marginTop: 0 }}>
            <span className="byline" style={{ marginRight: 8 }}>{persona.role}</span>
            {persona.reports_contributed} report{persona.reports_contributed === 1 ? "" : "s"}
            {persona.last_assignment_at && (
              <> · last assignment {new Date(persona.last_assignment_at).toLocaleDateString()}</>
            )}
          </p>

          <div className="card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <div className="byline">Persona file</div>
              <div style={{ display: "flex", gap: 8 }}>
                {!editing && (
                  <button onClick={() => setEditing(true)} style={{ padding: "4px 12px", fontSize: 12 }}>
                    Edit
                  </button>
                )}
                {editing && (
                  <>
                    <button
                      onClick={cancel}
                      style={{ padding: "4px 12px", fontSize: 12, background: "#FFF", color: "var(--forte-ink)", border: "1px solid var(--forte-rule)" }}
                    >
                      Cancel
                    </button>
                    <button onClick={save} disabled={saving} style={{ padding: "4px 12px", fontSize: 12 }}>
                      {saving ? "Saving…" : "Save"}
                    </button>
                  </>
                )}
                {saved && <span className="muted" style={{ fontSize: 12, alignSelf: "center" }}>{saved}</span>}
              </div>
            </div>

            {editing ? (
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck={false}
                style={{
                  width: "100%",
                  minHeight: 600,
                  fontFamily: "ui-monospace, 'SF Mono', monospace",
                  fontSize: 13,
                  lineHeight: 1.45,
                  padding: 12,
                  border: "1px solid var(--forte-rule)",
                  borderRadius: 4,
                  background: "#FAFAFC",
                  resize: "vertical",
                  whiteSpace: "pre",
                }}
              />
            ) : (
              <pre style={{
                whiteSpace: "pre-wrap",
                fontFamily: "ui-monospace, 'SF Mono', monospace",
                fontSize: 13,
                lineHeight: 1.5,
                background: "#FAFAFC",
                padding: 16,
                borderRadius: 4,
                border: "1px solid var(--forte-rule)",
                margin: 0,
              }}>{persona.markdown}</pre>
            )}
          </div>
        </>
      )}
    </AuthGate>
  );
}
