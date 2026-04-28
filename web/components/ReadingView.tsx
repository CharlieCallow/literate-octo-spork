"use client";
import { useEffect, useState } from "react";
import type { ReadingMode } from "@/lib/api";

/* Web-styled report reader. The server returns sections pre-rendered to HTML
 * (markdown-it + inline-citation superscripts + chart <img> tags rewritten to
 * API URLs). We just style and lay it out -- much nicer on mobile than the
 * embedded PDF iframe. */
export function ReadingView({
  loader,
  emptyMessage = "Reading view not available yet.",
}: {
  loader: () => Promise<ReadingMode>;
  emptyMessage?: string;
}) {
  const [data, setData] = useState<ReadingMode | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loader().then(setData).catch((e) => setError(String(e)));
  }, [loader]);

  if (error) {
    return <p className="muted" style={{ color: "#B8860B" }}>{error}</p>;
  }
  if (!data) {
    return <p className="muted">Loading…</p>;
  }
  if (data.sections.length === 0) {
    return <p className="muted">{emptyMessage}</p>;
  }

  return (
    <article className="reading">
      <header className="reading-cover">
        <div className="byline" style={{ color: "var(--forte-teal)" }}>Forte Research</div>
        <h1>{data.theme}</h1>
        {data.subtitle && <p className="reading-sub">{data.subtitle}</p>}
        {data.contributors.length > 0 && (
          <p className="muted reading-contribs">
            By {data.contributors.map((c, i) => (
              <span key={i}>
                {i > 0 && ", "}
                <strong>{c.name}</strong>
                {c.role && <span style={{ color: "var(--forte-muted)" }}> — {c.role}</span>}
              </span>
            ))}
          </p>
        )}
      </header>

      {data.house_view_top && (
        <div className="reading-house">
          <span className="label">House view.</span> {data.house_view_top}
        </div>
      )}

      {data.sections.map((s, i) => (
        <section key={i} className="reading-section">
          <h2>{s.heading}</h2>
          {s.author && (
            <p className="byline">By <span style={{ color: "var(--forte-purple)", fontWeight: 600 }}>{s.author}</span>{s.role && ` — ${s.role}`}</p>
          )}
          <div className="reading-body" dangerouslySetInnerHTML={{ __html: s.body_html }} />
        </section>
      ))}

      {data.house_view_bottom && (
        <div className="reading-house">
          <span className="label">Bottom line.</span> {data.house_view_bottom}
        </div>
      )}

      {data.sources.length > 0 && (
        <section className="reading-sources">
          <h2>Sources</h2>
          <ol>
            {data.sources.map((s) => (
              <li key={s.n} id={`cite-${s.n}`} value={s.n}>
                <a href={s.url} target="_blank" rel="noopener noreferrer">{s.title || s.url}</a>
                <span className="muted-tag"> — {s.source}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </article>
  );
}
