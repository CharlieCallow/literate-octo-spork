"""Email delivery via Resend. No-op when not configured."""

from __future__ import annotations

import base64
import html
import logging
from typing import Any

import httpx

from api.models import Report, Theme
from api.settings import settings

log = logging.getLogger("email")


def _public_base_url() -> str:
    """Where the recipient should land when they click a share link. Best
    effort: pull the first allowlisted CORS origin (that's the dashboard URL on
    Vercel for prod; localhost for dev). Falls back to a placeholder so we
    never silently mail out a broken link."""
    for raw in settings.cors_origins.split(","):
        host = raw.strip().rstrip("/")
        if host:
            return host
    return "http://localhost:3000"


def _format_html(themes: list[Theme]) -> str:
    rows: list[str] = []
    for i, t in enumerate(themes, 1):
        sources_html = ""
        if t.source_urls:
            links = " · ".join(
                f'<a href="{html.escape(u)}" style="color:#6B6B7A;">{html.escape(_host(u))}</a>'
                for u in t.source_urls[:3]
            )
            sources_html = f'<p style="font-size:11px;color:#6B6B7A;margin:6px 0 0;">{links}</p>'

        rows.append(f"""
<div style="border-top:1px solid #E8E8EE;padding:14px 0;">
  <div style="font-size:11px;letter-spacing:0.05em;text-transform:uppercase;color:#6B6B7A;">Theme {i}</div>
  <div style="font-size:16px;font-weight:600;color:#1F1B4D;margin:2px 0 6px;">{html.escape(t.headline)}</div>
  <div style="font-size:13px;color:#1A1A1A;"><strong style="color:#1F1B4D;">Why now.</strong> {html.escape(t.why_now)}</div>
  <div style="font-size:13px;color:#1A1A1A;margin-top:4px;"><strong style="color:#1F1B4D;">Dig into.</strong> {html.escape(t.dig_into)}</div>
  {sources_html}
</div>""")

    body = "".join(rows)
    return f"""<!doctype html><html><body style="font-family:Inter,Helvetica,Arial,sans-serif;color:#1A1A1A;background:#FAFAFC;margin:0;padding:24px;">
<div style="max-width:640px;margin:0 auto;background:#FFFFFF;padding:24px;border:1px solid #E8E8EE;border-radius:6px;">
  <div style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:#5BC0BE;font-weight:600;">Forte Research</div>
  <h1 style="font-size:22px;color:#1F1B4D;margin:6px 0 0;">Daily digest</h1>
  <p style="color:#6B6B7A;margin:4px 0 16px;font-size:13px;">{len(themes)} themes for the morning. Click into the dashboard to commission any of them.</p>
  {body}
</div>
</body></html>"""


def _host(url: str) -> str:
    try:
        return url.split("/")[2]
    except IndexError:
        return url


def send_digest(themes: list[Theme]) -> bool:
    """Send the daily digest email. Returns True on success, False if skipped or failed.
    Skips silently if RESEND_API_KEY or SCOUT_DIGEST_EMAIL aren't set."""
    if not settings.resend_api_key or not settings.scout_digest_email:
        log.info("email skipped: RESEND_API_KEY or SCOUT_DIGEST_EMAIL not configured")
        return False
    if not themes:
        log.info("email skipped: no themes to send")
        return False

    payload: dict[str, Any] = {
        "from": settings.scout_digest_from,
        "to": [settings.scout_digest_email],
        "subject": f"Forte Research — {len(themes)} themes for today",
        "html": _format_html(themes),
    }
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "authorization": f"Bearer {settings.resend_api_key}",
                "content-type": "application/json",
            },
            json=payload,
            timeout=15.0,
        )
        r.raise_for_status()
        log.info("digest email sent to %s", settings.scout_digest_email)
        return True
    except httpx.HTTPError as e:
        log.warning("failed to send digest email: %s", e)
        return False


def _format_report_html(report: Report, share_token: str, cover_note: str) -> str:
    base = _public_base_url()
    reading_url = f"{base}/share/{report.id}/{share_token}/read"
    pdf_url = f"{base}/share/{report.id}/{share_token}"
    note_block = ""
    if cover_note.strip():
        note_block = (
            f'<div style="background:#FAFAFC;border:1px solid #E8E8EE;border-radius:6px;'
            f'padding:14px;margin:0 0 16px;font-size:14px;color:#1A1A1A;">'
            f'{html.escape(cover_note).replace(chr(10), "<br>")}'
            f'</div>'
        )
    subtitle_block = (
        f'<p style="color:#6B6B7A;font-size:14px;margin:0 0 18px;">{html.escape(report.subtitle)}</p>'
        if report.subtitle else ""
    )
    return f"""<!doctype html><html><body style="font-family:Inter,Helvetica,Arial,sans-serif;color:#1A1A1A;background:#FAFAFC;margin:0;padding:24px;">
<div style="max-width:640px;margin:0 auto;background:#FFFFFF;padding:24px;border:1px solid #E8E8EE;border-radius:6px;">
  <div style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:#5BC0BE;font-weight:600;">Forte Research</div>
  <h1 style="font-size:22px;color:#1F1B4D;margin:6px 0 4px;">{html.escape(report.theme)}</h1>
  {subtitle_block}
  {note_block}
  <p style="font-size:14px;color:#1A1A1A;margin:0 0 18px;">A new research report is ready. Read on the web (recommended on mobile) or open the PDF.</p>
  <p style="margin:0 0 12px;">
    <a href="{html.escape(reading_url)}" style="display:inline-block;background:#1F1B4D;color:#FFFFFF;padding:10px 18px;border-radius:4px;text-decoration:none;font-weight:600;">Read on the web</a>
    <a href="{html.escape(pdf_url)}" style="display:inline-block;background:#FFFFFF;color:#1F1B4D;border:1px solid #1F1B4D;padding:10px 18px;border-radius:4px;text-decoration:none;font-weight:600;margin-left:8px;">Open the PDF</a>
  </p>
  <p style="color:#6B6B7A;font-size:12px;margin:18px 0 0;">Sent via Forte Research. Anyone with this link can read the report -- treat it like a tokenised URL.</p>
</div>
</body></html>"""


def send_report(
    *,
    to: str,
    report: Report,
    share_token: str,
    cover_note: str = "",
    pdf_bytes: bytes | None = None,
) -> tuple[bool, str]:
    """Send a finished report to a recipient. Returns (sent, detail).
    `detail` is empty on success and carries the Resend error / skip reason
    otherwise so the dashboard can surface it."""
    if not settings.resend_api_key:
        return False, "RESEND_API_KEY not configured on the server"
    if "@" not in to:
        return False, "Invalid recipient address"

    payload: dict[str, Any] = {
        "from": settings.scout_digest_from,
        "to": [to],
        "subject": f"Forte Research — {report.theme}",
        "html": _format_report_html(report, share_token, cover_note),
    }
    if pdf_bytes:
        payload["attachments"] = [{
            "filename": f"forte-report-{report.id}.pdf",
            "content": base64.b64encode(pdf_bytes).decode("ascii"),
        }]
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "authorization": f"Bearer {settings.resend_api_key}",
                "content-type": "application/json",
            },
            json=payload,
            timeout=20.0,
        )
        r.raise_for_status()
        log.info("report %s emailed to %s", report.id, to)
        return True, ""
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response is not None else ""
        log.warning("failed to send report email: %s %s", e, body)
        return False, f"{e.response.status_code if e.response is not None else 'http_error'}: {body}"
    except httpx.HTTPError as e:
        log.warning("failed to send report email: %s", e)
        return False, str(e)
