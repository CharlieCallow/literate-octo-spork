"""Storage module tests. The R2 path needs boto3 + a live bucket to test
end-to-end; here we just verify the local-only fallback behaviour so the rest
of the codebase keeps working when R2 isn't configured."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def no_r2(monkeypatch):  # type: ignore[no-untyped-def]
    """Force the R2-disabled state regardless of the host's env."""
    import api.settings as settings_module
    for key in ("r2_account_id", "r2_bucket", "r2_access_key", "r2_secret_key"):
        monkeypatch.setattr(settings_module.settings, key, "")


def test_is_r2_enabled_false_when_unconfigured(no_r2) -> None:  # type: ignore[no-untyped-def]
    from api import storage
    assert storage.is_r2_enabled() is False


def test_upload_artifacts_noop_without_r2(no_r2, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from api import storage
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4 stub")
    assert storage.upload_artifacts(42, tmp_path) == 0


def test_signed_url_returns_none_without_r2(no_r2) -> None:  # type: ignore[no-untyped-def]
    from api import storage
    assert storage.signed_url(42, "report.pdf") is None


def test_object_exists_false_without_r2(no_r2) -> None:  # type: ignore[no-untyped-def]
    from api import storage
    assert storage.object_exists(42, "report.pdf") is False


def test_fetch_to_local_returns_none_without_r2(no_r2, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from api import storage
    assert storage.fetch_to_local(42, tmp_path, "report.pdf") is None


def test_list_chart_files_empty_without_r2(no_r2) -> None:  # type: ignore[no-untyped-def]
    from api import storage
    assert storage.list_chart_files(42) == []


def test_is_r2_enabled_true_when_all_set(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from api import settings as s
    monkeypatch.setattr(s.settings, "r2_account_id", "acc")
    monkeypatch.setattr(s.settings, "r2_bucket", "bucket")
    monkeypatch.setattr(s.settings, "r2_access_key", "key")
    monkeypatch.setattr(s.settings, "r2_secret_key", "secret")
    from api import storage
    assert storage.is_r2_enabled() is True


def test_is_r2_enabled_false_when_partial(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from api import settings as s
    monkeypatch.setattr(s.settings, "r2_account_id", "acc")
    monkeypatch.setattr(s.settings, "r2_bucket", "")
    monkeypatch.setattr(s.settings, "r2_access_key", "key")
    monkeypatch.setattr(s.settings, "r2_secret_key", "secret")
    from api import storage
    assert storage.is_r2_enabled() is False
