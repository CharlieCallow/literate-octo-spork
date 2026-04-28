"use client";
import { useEffect, useRef, useState } from "react";
import { api, type ChartSpec } from "@/lib/api";

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

export function InteractiveCharts({ reportId }: { reportId: number }) {
  const [files, setFiles] = useState<{ filename: string; has_json: boolean }[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listChartFiles(reportId)
      .then(setFiles)
      .catch((e) => setError(String(e)));
  }, [reportId]);

  if (error) return <p className="muted" style={{ color: "#B8860B" }}>{error}</p>;
  if (files.length === 0) return null;

  const interactive = files.filter((f) => f.has_json);
  if (interactive.length === 0) return null;

  return (
    <div className="card">
      <div className="byline">Interactive charts</div>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        Same data as the PDF, but you can hover, zoom, and toggle series.
      </p>
      {interactive.map((f) => (
        <ChartView key={f.filename} reportId={reportId} filename={f.filename} />
      ))}
    </div>
  );
}

function ChartView({ reportId, filename }: { reportId: number; filename: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [spec] = await Promise.all([
          api.getChartJson(reportId, filename),
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
  }, [reportId, filename]);

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

  // Line / regime / event / comparison all use line traces.
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
