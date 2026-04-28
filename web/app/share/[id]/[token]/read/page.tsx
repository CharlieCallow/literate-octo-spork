"use client";
import { use, useCallback } from "react";
import { ReadingView } from "@/components/ReadingView";
import { api } from "@/lib/api";

/* Public reading-mode viewer reachable via a tokenised share link. Sister
 * page to /share/[id]/[token] but renders the report as a web article rather
 * than embedding the PDF -- much nicer on mobile and the destination of the
 * "Read on the web" CTA in the report email. */
export default function PublicReadingPage({
  params,
}: {
  params: Promise<{ id: string; token: string }>;
}) {
  const { id, token } = use(params);
  const reportId = Number(id);
  const loader = useCallback(() => api.getPublicReading(reportId, token), [reportId, token]);

  return (
    <>
      <p style={{ fontSize: 12, color: "var(--forte-muted)", margin: "0 0 12px" }}>
        <a href={`/share/${reportId}/${token}`}>← Open the PDF view instead</a>
      </p>
      <ReadingView loader={loader} emptyMessage="This report has no readable content yet." />
    </>
  );
}
