"use client";
import { useState } from "react";
import { api } from "@/lib/api";

/* Modal: email a finished report. The backend mints (or reuses) a public share
 * token so the recipient can open the reading view + PDF without dashboard auth.
 * Server-side requires RESEND_API_KEY; we surface that error inline. */
export function EmailReportDialog({ reportId, onClose }: { reportId: number; onClose: () => void }) {
  const [to, setTo] = useState("");
  const [note, setNote] = useState("");
  const [includePdf, setIncludePdf] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function send() {
    setBusy(true); setError(null);
    try {
      const res = await api.emailReport(reportId, { to, note, include_pdf: includePdf });
      if (!res.sent) {
        setError(res.detail || "Email not sent");
      } else {
        setSent(true);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(31,27,77,0.4)",
        display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "#FFF", borderRadius: 6, padding: 24, width: "min(440px, 92vw)",
        }}
      >
        <div className="byline">Email this report</div>
        <h2 style={{ marginTop: 4, marginBottom: 12, color: "var(--forte-navy)" }}>Send</h2>

        {sent ? (
          <>
            <p>Sent to <strong>{to}</strong>.</p>
            <button onClick={onClose} style={{ marginTop: 8 }}>Close</button>
          </>
        ) : (
          <>
            <div style={{ marginBottom: 10 }}>
              <label className="byline">Recipient</label>
              <input
                type="email"
                placeholder="name@example.com"
                value={to}
                onChange={(e) => setTo(e.target.value)}
              />
            </div>

            <div style={{ marginBottom: 10 }}>
              <label className="byline">Note (optional)</label>
              <textarea
                rows={3}
                placeholder="Quick line for context."
                value={note}
                onChange={(e) => setNote(e.target.value)}
                style={{ resize: "vertical", fontFamily: "inherit" }}
              />
            </div>

            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, marginBottom: 14, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={includePdf}
                onChange={(e) => setIncludePdf(e.target.checked)}
                style={{ width: "auto" }}
              />
              <span>Attach the PDF as well as the reading-view link</span>
            </label>

            {error && <p style={{ color: "#B8860B", fontSize: 13, margin: "0 0 10px" }}>{error}</p>}

            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={send} disabled={busy || !to.includes("@")}>{busy ? "Sending…" : "Send"}</button>
              <button
                onClick={onClose}
                style={{ background: "#FFF", color: "var(--forte-ink)", border: "1px solid var(--forte-rule)" }}
              >
                Cancel
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
