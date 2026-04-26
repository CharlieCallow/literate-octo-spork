"""Markdown -> HTML -> PDF pipeline using Jinja2 + WeasyPrint."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt
from weasyprint import HTML

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
    read_minutes: int = 8,
    logo_path: Path | None = None,
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
        read_minutes=read_minutes,
        logo_path=logo.as_uri(),
        css_path=(STYLES_DIR / "report.css").as_uri(),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(out_path.parent)).write_pdf(str(out_path))
    return out_path
