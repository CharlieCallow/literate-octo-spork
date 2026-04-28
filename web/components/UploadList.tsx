"use client";
import { useEffect, useRef, useState } from "react";
import { api, type UploadedDoc } from "@/lib/api";

/* List the user's attachments for a report and (when the report is still
 * runnable) allow adding more / removing existing ones. The agent picks these
 * up via the `uploaded_documents` tool during the research stage. */
export function UploadList({ reportId, canEdit }: { reportId: number; canEdit: boolean }) {
  const [docs, setDocs] = useState<UploadedDoc[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function refresh() {
    try {
      const list = await api.listUploads(reportId);
      setDocs(list);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    let cancelled = false;
    api.listUploads(reportId).then((d) => { if (!cancelled) setDocs(d); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [reportId]);

  async function onPick(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true); setError(null);
    try {
      for (const f of Array.from(files)) {
        await api.uploadDocument(reportId, f);
      }
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function remove(filename: string) {
    if (!window.confirm(`Remove ${filename}? Already-completed stages won't see the change.`)) return;
    try {
      await api.deleteUpload(reportId, filename);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  if (docs.length === 0 && !canEdit) return null;

  return (
    <div className="card">
      <div className="byline">Uploaded sources</div>
      {docs.length === 0 ? (
        <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
          {canEdit
            ? "Drop a research note (PDF), a CSV, or an XLSX. The team treats these as primary sources."
            : "No sources attached."}
        </p>
      ) : (
        <ul style={{ paddingLeft: 18, marginTop: 4, fontSize: 14 }}>
          {docs.map((d) => (
            <li key={d.id} style={{ marginBottom: 6 }}>
              <strong>{d.filename}</strong>{" "}
              <span className="muted" style={{ fontSize: 12 }}>({(d.size_bytes / 1024).toFixed(1)} KB · {d.mime || "unknown"})</span>
              {canEdit && (
                <button
                  onClick={() => remove(d.filename)}
                  style={{ marginLeft: 8, padding: "1px 8px", fontSize: 11, background: "#FFF", color: "var(--forte-ink)", border: "1px solid var(--forte-rule)" }}
                >
                  remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canEdit && (
        <div style={{ marginTop: 8 }}>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            disabled={busy}
            accept=".pdf,.csv,.tsv,.xlsx,.xlsm,.md,.markdown,.txt"
            onChange={(e) => onPick(e.target.files)}
            style={{ padding: 6 }}
          />
          {busy && <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>Uploading…</p>}
        </div>
      )}
      {error && <p style={{ color: "#B8860B", fontSize: 12, marginTop: 6 }}>{error}</p>}
    </div>
  );
}
