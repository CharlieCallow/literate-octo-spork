"use client";
import { useEffect, useMemo, useRef, useState } from "react";
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
  const articleRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    loader().then(setData).catch((e) => setError(String(e)));
  }, [loader]);

  // Pull `**TERM** — definition` pairs out of the rendered glossary HTML so we
  // can hang them on inline tooltips in the body. Glossary lives in its own
  // section at the end of the read, so this also applies to every old report
  // already in storage -- nothing about the report itself needs to change.
  const glossaryTerms = useMemo(() => extractGlossaryTerms(data?.glossary_html ?? null), [data?.glossary_html]);

  useEffect(() => {
    if (!data || glossaryTerms.length === 0) return;
    const root = articleRef.current;
    if (!root) return;
    const bodies = root.querySelectorAll<HTMLElement>(
      ".reading-section:not(.reading-glossary) .reading-body",
    );
    bodies.forEach((body) => {
      // First occurrence per section only — quieter than peppering every hit.
      glossaryTerms.forEach(({ term, def }) => wrapFirstMatch(body, term, def));
    });
  }, [data, glossaryTerms]);

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
    <article className="reading" ref={articleRef}>
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

      {data.glossary_html && (
        <section className="reading-section reading-glossary">
          <h2>Glossary</h2>
          <div className="reading-body" dangerouslySetInnerHTML={{ __html: data.glossary_html }} />
        </section>
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

interface GlossaryTerm {
  term: string;
  def: string;
}

function extractGlossaryTerms(glossaryHtml: string | null): GlossaryTerm[] {
  if (!glossaryHtml || typeof window === "undefined") return [];
  const doc = new DOMParser().parseFromString(glossaryHtml, "text/html");
  const out: GlossaryTerm[] = [];
  doc.querySelectorAll("strong").forEach((strong) => {
    const term = (strong.textContent ?? "").trim();
    const parent = strong.parentElement;
    if (!term || !parent) return;
    let def = "";
    let after = false;
    parent.childNodes.forEach((node) => {
      if (after) def += node.textContent ?? "";
      if (node === strong) after = true;
    });
    def = def.replace(/^\s*[—–-]\s*/, "").trim();
    // The line may contain extra trailing prose from the next entry if the
    // glossary renders as one paragraph with <br>s. Cut at the first <br>'s
    // text-equivalent (newline) just in case.
    def = def.split("\n")[0].trim();
    if (term && def) out.push({ term, def });
  });
  // Longer phrases first so "operator margin" wins over "margin".
  out.sort((a, b) => b.term.length - a.term.length);
  return out;
}

const SKIP_TAGS = new Set([
  "A", "ABBR", "CODE", "PRE", "H1", "H2", "H3", "H4", "H5", "H6",
  "FIGCAPTION", "TH", "SUP", "SCRIPT", "STYLE",
]);

function wrapFirstMatch(root: HTMLElement, term: string, def: string): void {
  // Acronyms / proper nouns: case-sensitive. Lowercase phrases: case-insensitive.
  const caseSensitive = /[A-Z]/.test(term);
  const flags = caseSensitive ? "" : "i";
  const pattern = new RegExp(`\\b${escapeRegex(term)}\\b`, flags);

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      let p: Node | null = node.parentNode;
      while (p && p !== root) {
        if (p.nodeType === 1 && SKIP_TAGS.has((p as Element).tagName)) {
          return NodeFilter.FILTER_REJECT;
        }
        p = p.parentNode;
      }
      return pattern.test(node.nodeValue ?? "")
        ? NodeFilter.FILTER_ACCEPT
        : NodeFilter.FILTER_REJECT;
    },
  });

  const node = walker.nextNode() as Text | null;
  if (!node) return;
  const text = node.nodeValue ?? "";
  const match = pattern.exec(text);
  if (!match) return;
  const start = match.index;
  const end = start + match[0].length;

  const before = text.slice(0, start);
  const matched = text.slice(start, end);
  const after = text.slice(end);

  const abbr = document.createElement("abbr");
  abbr.className = "annex-term";
  abbr.title = def;
  abbr.textContent = matched;

  const parent = node.parentNode;
  if (!parent) return;
  if (before) parent.insertBefore(document.createTextNode(before), node);
  parent.insertBefore(abbr, node);
  if (after) parent.insertBefore(document.createTextNode(after), node);
  parent.removeChild(node);
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
