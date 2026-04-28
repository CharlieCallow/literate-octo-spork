"use client";
import { useEffect, useState } from "react";
import { api, type ShareInfo } from "@/lib/api";

export function ShareDialog({
  reportId,
  onClose,
}: {
  reportId: number;
  onClose: () => void;
}) {
  const [info, setInfo] = useState<ShareInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api.getShare(reportId).then(setInfo).catch((e) => setError(String(e)));
  }, [reportId]);

  const shareUrl =
    info?.share_token && typeof window !== "undefined"
      ? `${window.location.origin}/share/${reportId}/${info.share_token}`
      : null;

  async function mint() {
    setBusy(true); setError(null);
    try { setInfo(await api.createShare(reportId)); } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function rotate() {
    if (!window.confirm("Rotate the link? The current URL will stop working.")) return;
    setBusy(true); setError(null);
    try { setInfo(await api.createShare(reportId, true)); } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function revoke() {
    if (!window.confirm("Revoke the link? Anyone with the URL will get a 404.")) return;
    setBusy(true); setError(null);
    try { setInfo(await api.revokeShare(reportId)); } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function copy() {
    if (!shareUrl) return;
    await navigator.clipboard.writeText(shareUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(15, 14, 30, 0.55)",
        display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="card"
        style={{ maxWidth: 520, width: "92%", margin: 0 }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <h2 style={{ margin: 0 }}>Share link</h2>
          <button
            onClick={onClose}
            style={{ background: "transparent", color: "var(--forte-muted)", padding: "2px 8px", fontSize: 18 }}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <p className="muted" style={{ marginTop: 4 }}>
          Anyone with this URL can view the report. They can&apos;t see anything
          else on the dashboard.
        </p>

        {error && <p style={{ color: "#B8860B", fontSize: 12 }}>{error}</p>}

        {!info ? (
          <p className="muted">Loading…</p>
        ) : info.share_token && shareUrl ? (
          <>
            <input
              readOnly
              value={shareUrl}
              onFocus={(e) => e.currentTarget.select()}
              style={{ fontFamily: "ui-monospace, SF Mono, monospace", fontSize: 12 }}
            />
            <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
              <button onClick={copy} disabled={busy}>
                {copied ? "Copied" : "Copy URL"}
              </button>
              <button
                onClick={rotate}
                disabled={busy}
                style={{ background: "#FFF", color: "var(--forte-ink)", border: "1px solid var(--forte-rule)" }}
              >
                Rotate
              </button>
              <button
                onClick={revoke}
                disabled={busy}
                style={{ background: "#FFF", color: "#B8860B", border: "1px solid var(--forte-rule)" }}
              >
                Revoke
              </button>
            </div>
            {info.shared_at && (
              <p className="muted" style={{ fontSize: 11, marginTop: 10, marginBottom: 0 }}>
                Created {new Date(info.shared_at).toLocaleString()}
              </p>
            )}
          </>
        ) : (
          <div style={{ marginTop: 8 }}>
            <p className="muted" style={{ marginTop: 0 }}>
              No share link yet. Create one to let someone view this report
              without signing in.
            </p>
            <button onClick={mint} disabled={busy}>Create share link</button>
          </div>
        )}
      </div>
    </div>
  );
}
