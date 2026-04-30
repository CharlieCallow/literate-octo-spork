"""Per-page layout analysis of a rendered PDF.

Used by the Stylist agent: after the first render, we extract a small
JSON-able summary of each page (bottom gap in mm, the first/last text
block, image presence) so the Stylist can decide where to insert
forced page breaks before re-rendering.

Cheap (no vision model). Uses PyMuPDF, which is also imported as
`pymupdf` in 1.24+."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf  # type: ignore[import-untyped]


PT_PER_MM = 72.0 / 25.4


@dataclass
class PageSummary:
    page: int                  # 1-indexed
    height_mm: float
    bottom_gap_mm: float       # empty space below the lowest text/image block
    first_text: str            # first ~160 chars of the topmost block
    last_text: str             # last ~160 chars of the bottommost block
    has_image: bool
    image_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "page": self.page,
            "bottom_gap_mm": round(self.bottom_gap_mm, 1),
            "first_text": self.first_text,
            "last_text": self.last_text,
            "has_image": self.has_image,
        }


def summarise_layout(pdf_path: Path) -> list[PageSummary]:
    """Return one PageSummary per page in the PDF.

    The cover page (page 1) is included for completeness but the Stylist
    typically ignores it -- the cover is layout-clamped to one page by
    CSS and not amenable to mid-prose breaks anyway."""
    doc = pymupdf.open(str(pdf_path))
    out: list[PageSummary] = []
    try:
        for i, page in enumerate(doc):
            rect = page.rect
            page_h_pt = float(rect.height)
            blocks = page.get_text("blocks") or []
            # blocks: (x0, y0, x1, y1, text, block_no, block_type)
            # block_type 1 == image; 0 == text. Treat both as content.
            content = [b for b in blocks if (b[4] or "").strip() or b[6] == 1]
            images = page.get_images(full=False) or []
            if not content:
                # Empty page -- record max gap so the stylist can skip it.
                out.append(PageSummary(
                    page=i + 1,
                    height_mm=page_h_pt / PT_PER_MM,
                    bottom_gap_mm=page_h_pt / PT_PER_MM,
                    first_text="",
                    last_text="",
                    has_image=bool(images),
                    image_count=len(images),
                ))
                continue
            top = min(content, key=lambda b: b[1])
            bot = max(content, key=lambda b: b[3])
            bottom_gap_pt = max(0.0, page_h_pt - float(bot[3]))
            out.append(PageSummary(
                page=i + 1,
                height_mm=page_h_pt / PT_PER_MM,
                bottom_gap_mm=bottom_gap_pt / PT_PER_MM,
                first_text=_clip(str(top[4] or "")),
                last_text=_clip(str(bot[4] or "")),
                has_image=bool(images),
                image_count=len(images),
            ))
    finally:
        doc.close()
    return out


def _clip(s: str, n: int = 160) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n].rstrip() + "…"


def format_for_prompt(pages: list[PageSummary], *, gap_threshold_mm: float = 60.0) -> str:
    """Render the page summary as a compact prompt-friendly string,
    skipping pages with no notable layout issue."""
    lines: list[str] = []
    for p in pages:
        flags: list[str] = []
        if p.bottom_gap_mm >= gap_threshold_mm:
            flags.append(f"large bottom gap ({p.bottom_gap_mm:.0f}mm of empty space)")
        if not flags:
            continue
        lines.append(
            f"- Page {p.page}: {'; '.join(flags)}.\n"
            f"  Page ends with: \"{p.last_text}\""
        )
    if not lines:
        return "(no notable layout issues detected)"
    # Always give the stylist the next page's first text too, so they can
    # see what's about to land at the top of the following page.
    by_idx = {p.page: p for p in pages}
    enriched: list[str] = []
    for p in pages:
        if p.bottom_gap_mm < gap_threshold_mm:
            continue
        nxt = by_idx.get(p.page + 1)
        next_first = nxt.first_text if nxt else ""
        enriched.append(
            f"- Page {p.page}: {p.bottom_gap_mm:.0f}mm of empty space at the bottom.\n"
            f"  Page ends with: \"{p.last_text}\"\n"
            f"  Next page starts with: \"{next_first}\""
        )
    return "\n".join(enriched)
