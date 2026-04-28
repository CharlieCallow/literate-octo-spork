"use client";
import { use, useEffect, useRef, useState } from "react";
import { api, type ChartSpec, type PublicReport } from "@/lib/api";

declare global {
  interface Window { Plotly?: any }
}

const PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js";

let plotlyPromise: Promise<void> | null = null;
function loadPlotly(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.Plotly) return Promise.resolve();
  if (plotlyPromise) return plotlyPromise;
  plotlyPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = PLOTLY_CDN;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("failed to load Plotly"));
    document.head.appendChild(script);
  });
  return plotlyPromise;
}

export default function SharePage({ params }: { params: Promise<{ id: string; token: string }> }) {
  const { id, token } = use(params);
  const reportId = Number(id);

  const [report, setReport] = useState<PublicReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [charts, setCharts] = useState<{ filename: string; has_json: boolean }[]>([]);

  useEffect(() => {
    if (!Number.isFinite(reportId)) { setError("Invalid link"); return; }
    api.getPublicReport(reportId, token).then(setReport).catch(() => setError("This link is invalid or has been revoked."));
    api.listPublicChartFiles(reportId, token).then(setCharts).catch(() => { /* silent: charts are a bonus */ });
  }, [reportId, token]);

  if (error) {
    return (
      <div className="card" style={{ maxWidth: 520, margin: "80px auto" }}>
        <h2 style={{ marginTop: 0 }}>Link unavailable</h2>
        <p className="muted">{error}</p>
      </div>
    );
  }

  if (!report) {
    return <div className="card"><p className="muted">Loading…</p></div>;
  }

  const interactive = charts.filter((f) => f.has_json);

  return (
    <>
      <div className="byline">Forte Research · Shared report</div>
      <h1 style={{ color: "var(--forte-navy)", marginTop: 4 }}>{report.theme}</h1>
      {report.subtitle && <p className="muted" style={{ marginTop: 0 }}>{report.subtitle}</p>}
      {report.contributor_slugs.length > 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          Contributors: {report.contributor_slugs.join(", ")}
        </p>
      )}

      <div className="card" style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <a
          href={`/share/${reportId}/${token}/read`}
          style={{
            display: "inline-block", padding: "8px 16px",
            background: "var(--forte-navy)", color: "#FFF", borderRadius: 4,
            textDecoration: "none", fontSize: 14, fontWeight: 600,
          }}
        >
          Reading mode
        </a>
        <span className="muted" style={{ fontSize: 13 }}>web-styled view, easier on mobile</span>
      </div>

      {report.has_pdf ? (
        <div className="card">
          <div className="byline">PDF</div>
          <iframe
            src={api.publicPdfUrl(reportId, token)}
            style={{ width: "100%", height: "85vh", border: 0 }}
          />
          <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            <a href={api.publicPdfUrl(reportId, token)}>Download PDF</a>
          </p>
        </div>
      ) : (
        <div className="card"><p className="muted">PDF is not available for this report.</p></div>
      )}

      {interactive.length > 0 && (
        <div className="card">
          <div className="byline">Interactive charts</div>
          <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
            Same data as the PDF, but you can hover, zoom, and toggle series.
          </p>
          {interactive.map((f) => (
            <PublicChartView key={f.filename} reportId={reportId} token={token} filename={f.filename} />
          ))}
        </div>
      )}
    </>
  );
}

function PublicChartView({ reportId, token, filename }: { reportId: number; token: string; filename: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [spec] = await Promise.all([
          api.getPublicChartJson(reportId, token, filename),
          loadPlotly(),
        ]);
        if (cancelled || !ref.current || !window.Plotly) return;
        const { traces, layout } = specToPlotly(spec);
        await window.Plotly.newPlot(ref.current, traces, layout, { displayModeBar: false, responsive: true });
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [reportId, token, filename]);

  return (
    <div style={{ marginTop: 12 }}>
      <div ref={ref} style={{ width: "100%", height: 360 }} />
      {error && <p className="muted" style={{ color: "#B8860B", fontSize: 12 }}>{error}</p>}
    </div>
  );
}

function specToPlotly(spec: ChartSpec): { traces: any[]; layout: any } {
  const cycle = spec.palette.cycle;
  const cols = Object.keys(spec.series);

  const baseLayout: any = {
    title: {
      text: `<b>${escapeHtml(spec.title)}</b><br><span style="font-size:11px;color:${spec.palette.muted}">${escapeHtml(spec.subtitle)}</span>`,
      font: { color: spec.palette.navy, size: 14 },
      x: 0, xanchor: "left",
    },
    margin: { l: 50, r: 30, t: 60, b: 50 },
    paper_bgcolor: "#FFFFFF",
    plot_bgcolor: "#FFFFFF",
    showlegend: cols.length > 1,
    legend: { orientation: "h", y: -0.18 },
    annotations: [
      {
        x: 0, xref: "paper", y: -0.28, yref: "paper",
        text: `Source: ${escapeHtml(spec.source)}. As of ${escapeHtml(spec.as_of)}.`,
        showarrow: false, font: { size: 10, color: spec.palette.muted },
        xanchor: "left",
      },
    ],
    xaxis: { showgrid: true, gridcolor: spec.palette.rule, gridwidth: 1, zeroline: false },
    yaxis: { showgrid: false, zeroline: false },
    shapes: [] as any[],
  };

  if (spec.kind === "bar") {
    return {
      traces: [{
        type: "bar",
        x: spec.horizontal ? Object.values(spec.series)[0] : spec.index,
        y: spec.horizontal ? spec.index : Object.values(spec.series)[0],
        marker: { color: spec.palette.navy },
        orientation: spec.horizontal ? "h" : "v",
      }],
      layout: baseLayout,
    };
  }

  if (spec.kind === "regime" && spec.shaded) {
    for (const [start, end] of spec.shaded) {
      baseLayout.shapes.push({
        type: "rect", xref: "x", yref: "paper",
        x0: start, x1: end, y0: 0, y1: 1,
        fillcolor: spec.palette.rule, opacity: 0.7, line: { width: 0 },
      });
    }
  }

  if (spec.kind === "event" && spec.events) {
    for (const ev of spec.events) {
      baseLayout.shapes.push({
        type: "line", xref: "x", yref: "paper",
        x0: ev.date, x1: ev.date, y0: 0, y1: 1,
        line: { color: spec.palette.teal, width: 1, dash: "dash" },
      });
      baseLayout.annotations.push({
        x: ev.date, y: 1, yref: "paper",
        text: ev.label, showarrow: false, font: { size: 10, color: spec.palette.navy },
        xanchor: "left", yanchor: "top",
      });
    }
  }

  const traces: any[] = [];
  cols.forEach((col, i) => {
    const isSecond = spec.kind === "comparison" && i === 1 && spec.dual_axis;
    traces.push({
      type: "scatter",
      mode: "lines",
      name: col,
      x: spec.index,
      y: spec.series[col],
      line: { color: cycle[i % cycle.length], width: 2 },
      yaxis: isSecond ? "y2" : undefined,
    });
  });

  if (spec.kind === "comparison" && spec.dual_axis && cols.length >= 2) {
    baseLayout.yaxis = { ...baseLayout.yaxis, title: cols[0], titlefont: { color: cycle[0] }, tickfont: { color: cycle[0] } };
    baseLayout.yaxis2 = {
      title: cols[1], overlaying: "y", side: "right",
      titlefont: { color: cycle[1] }, tickfont: { color: cycle[1] },
      showgrid: false, zeroline: false,
    };
  }

  return { traces, layout: baseLayout };
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
