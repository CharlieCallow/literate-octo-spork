"""Markdown -> HTML -> PDF pipeline using Jinja2 + Playwright (Chromium).

Playwright was picked over WeasyPrint because the WeasyPrint system deps
(GTK3 / Pango / Cairo) are painful on Windows. Chromium installs cleanly via
`playwright install chromium`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt
from playwright.sync_api import sync_playwright

TEMPLATES_DIR = Path(__file__).parent / "templates"
STYLES_DIR = Path(__file__).parent / "styles"
ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"


@dataclass
class Contributor:
    name: str
    role: str


@dataclass
class Section:
    heading: str
    body_md: str
    author: str | None = None
    role: str | None = None


def _md() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": True}).enable("table")


_HEADER_TEMPLATE = """
<div style="font-size: 9pt; font-family: 'Inter','Segoe UI',sans-serif;
            color: #6B6B7A; width: 100%; padding: 0 18mm;
            display: flex; justify-content: space-between; align-items: center;">
  <span style="color: #1F1B4D; font-weight: 600;">Forte Research</span>
  <span class="title" style="color: #6B6B7A;"></span>
</div>
"""

_FOOTER_TEMPLATE = """
<div style="font-size: 9pt; font-family: 'Inter','Segoe UI',sans-serif;
            color: #6B6B7A; width: 100%; padding: 0 18mm;
            display: flex; justify-content: space-between;">
  <span>Confidential</span>
  <span><span class="pageNumber"></span> / <span class="totalPages"></span></span>
  <span class="date"></span>
</div>
"""


def render_pdf(
    *,
    out_path: Path,
    title: str,
    subtitle: str,
    date: str,
    contributors: Sequence[Contributor],
    sections: Sequence[Section],
    house_view_top: str | None = None,
    house_view_bottom: str | None = None,
    disagreement: str | None = None,
    bear_case: str | None = None,
    read_minutes: int = 8,
    logo_path: Path | None = None,
    sources: Sequence[dict[str, str | None]] | None = None,
) -> Path:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    md = _md()

    rendered_sections = [
        {
            "heading": s.heading,
            "body_html": md.render(s.body_md),
            "author": s.author,
            "role": s.role,
        }
        for s in sections
    ]

    logo = logo_path or (ASSETS_DIR / "Fortesecurities_V2-1024x476.png")

    html = env.get_template("report.html").render(
        title=title,
        subtitle=subtitle,
        date=date,
        contributors=[{"name": c.name, "role": c.role} for c in contributors],
        sections=rendered_sections,
        house_view_top=house_view_top,
        house_view_bottom=house_view_bottom,
        disagreement_html=md.render(disagreement) if disagreement else None,
        bear_case_html=md.render(bear_case) if bear_case else None,
        read_minutes=read_minutes,
        logo_path=logo.as_uri(),
        css_path=(STYLES_DIR / "report.css").as_uri(),
        sources=list(sources or []),
    )

    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Write HTML alongside the PDF and load it via file:// so Chromium can
    # resolve <img src="file://..."> references. set_content() leaves the
    # page origin as about:blank, which blocks file:// image loads.
    html_path = out_path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.emulate_media(media="print")
        page.pdf(
            path=str(out_path),
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template=_HEADER_TEMPLATE,
            footer_template=_FOOTER_TEMPLATE,
            margin={"top": "20mm", "bottom": "16mm", "left": "0", "right": "0"},
        )
        browser.close()

    return out_path
