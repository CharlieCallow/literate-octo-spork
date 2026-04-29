"""Object storage for report artifacts (PDFs + chart PNGs + JSON sidecars).

The report run still writes to the local filesystem during execution -- agents
are file-based and that pattern is everywhere. After the render stage finishes
we upload the durable artifacts to R2 so they survive container rebuilds. The
PDF and chart-data endpoints fall back to a signed R2 URL when the local file
is missing.

Switching backends is a one-env-var change: leave R2_* unset to stay local-only,
fill them in to enable R2. boto3 is imported lazily so local-only deploys don't
need it on disk.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from api.settings import settings

log = logging.getLogger("storage")


def is_r2_enabled() -> bool:
    return bool(
        settings.r2_account_id
        and settings.r2_bucket
        and settings.r2_access_key
        and settings.r2_secret_key
    )


@lru_cache(maxsize=1)
def _client() -> Any:
    """Lazy boto3 S3 client pointed at R2. Cached for the process lifetime."""
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key,
        aws_secret_access_key=settings.r2_secret_key,
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def _key(report_id: int, *parts: str) -> str:
    return "/".join(["reports", str(report_id), *parts])


def upload_artifacts(report_id: int, wd: Path) -> int:
    """Upload report.pdf + everything in charts/ + the source markdown files
    needed to rebuild reading mode for the given report. Returns the number
    of files uploaded. No-op if R2 isn't configured."""
    if not is_r2_enabled():
        return 0
    client = _client()
    uploaded = 0

    pdf = wd / "report.pdf"
    if pdf.exists():
        client.upload_file(
            str(pdf), settings.r2_bucket, _key(report_id, "report.pdf"),
            ExtraArgs={"ContentType": "application/pdf"},
        )
        uploaded += 1

    # Markdown sources reading mode rebuilds from. Without these, the working
    # dir on a fresh container has only the PDF and the reading endpoint 404s.
    for name in ("brief.md", "edited.md", "data-section.md", "sources.json"):
        f = wd / name
        if f.exists():
            ct = "application/json" if name.endswith(".json") else "text/markdown"
            client.upload_file(
                str(f), settings.r2_bucket, _key(report_id, name),
                ExtraArgs={"ContentType": ct},
            )
            uploaded += 1

    # Per-analyst notes + section files. These power /reports/{id}/rerun_analyst,
    # which needs notes-<slug>.md to redo a draft after a rebuild has wiped
    # the working dir. Cheap and small (markdown, kilobytes).
    for f in wd.iterdir():
        if not f.is_file():
            continue
        name = f.name
        if not (name.startswith("notes-") or name.startswith("section-")) or not name.endswith(".md"):
            continue
        client.upload_file(
            str(f), settings.r2_bucket, _key(report_id, name),
            ExtraArgs={"ContentType": "text/markdown"},
        )
        uploaded += 1

    charts = wd / "charts"
    if charts.exists():
        for f in charts.iterdir():
            if not f.is_file():
                continue
            ct = "image/png" if f.suffix.lower() == ".png" else "application/json"
            client.upload_file(
                str(f), settings.r2_bucket, _key(report_id, "charts", f.name),
                ExtraArgs={"ContentType": ct},
            )
            uploaded += 1

    log.info("R2 upload report=%d files=%d", report_id, uploaded)
    return uploaded


def signed_url(report_id: int, *parts: str, ttl: int = 3600) -> str | None:
    """Presigned R2 URL for one artifact, or None if R2 isn't on."""
    if not is_r2_enabled():
        return None
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.r2_bucket, "Key": _key(report_id, *parts)},
        ExpiresIn=ttl,
    )


def object_exists(report_id: int, *parts: str) -> bool:
    """Cheap existence check (HEAD on the object). False if R2 isn't on."""
    if not is_r2_enabled():
        return False
    try:
        _client().head_object(Bucket=settings.r2_bucket, Key=_key(report_id, *parts))
        return True
    except Exception:  # noqa: BLE001 - boto raises a botocore.exceptions.ClientError
        return False


def fetch_to_local(report_id: int, wd: Path, *parts: str) -> Path | None:
    """Download an artifact from R2 to its local path. Returns the local path
    on success, None if missing or R2 isn't on."""
    if not is_r2_enabled():
        return None
    target = wd.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        _client().download_file(settings.r2_bucket, _key(report_id, *parts), str(target))
        return target
    except Exception as e:  # noqa: BLE001
        log.info("R2 fetch missed report=%d %s: %s", report_id, parts, e)
        return None


def list_chart_files(report_id: int) -> list[str]:
    """List PNG filenames for a report's charts on R2. Empty if R2 isn't on
    or the report has no charts."""
    if not is_r2_enabled():
        return []
    prefix = _key(report_id, "charts") + "/"
    try:
        resp = _client().list_objects_v2(Bucket=settings.r2_bucket, Prefix=prefix)
    except Exception as e:  # noqa: BLE001
        log.info("R2 list missed report=%d: %s", report_id, e)
        return []
    out: list[str] = []
    for obj in resp.get("Contents", []) or []:
        key = obj.get("Key", "")
        if key.endswith(".png"):
            out.append(Path(key).name)
    return sorted(out)
