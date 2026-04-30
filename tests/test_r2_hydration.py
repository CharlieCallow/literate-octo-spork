"""Tests for the R2 hydration helper.

The bug: re-running `edit` on a done report after a Railway rebuild
produced a content-less PDF. The edit stage reads section-*.md /
redteam.md / rebuttals.md from the local working dir, which is
ephemeral on Railway, so on a fresh container those files are absent
and the EIC ends up with no input.

Fix: storage.hydrate_working_dir(report_id, wd) lists every R2 object
under the report's prefix and downloads any that aren't already local.
Wired into run_stage so each stage gets its inputs back automatically.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_hydrate_working_dir_fetches_missing_files(tmp_path: Path) -> None:
    """All R2 objects for the report should land in the working dir."""
    import api.storage as storage_mod

    wd = tmp_path / "wd"
    wd.mkdir()

    listed = {
        "Contents": [
            {"Key": "reports/42/brief.md"},
            {"Key": "reports/42/edited.md"},
            {"Key": "reports/42/section-macro-strategist.md"},
            {"Key": "reports/42/redteam.md"},
            {"Key": "reports/42/charts/yield.png"},
        ],
    }
    downloaded: list[tuple[str, str]] = []

    class FakeClient:
        def list_objects_v2(self, **_kwargs):  # type: ignore[no-untyped-def]
            return listed

        def download_file(self, _bucket, key, target):  # type: ignore[no-untyped-def]
            downloaded.append((key, target))
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_text(f"fetched:{key}")

    with patch.object(storage_mod, "is_r2_enabled", lambda: True), \
         patch.object(storage_mod, "_client", lambda: FakeClient()), \
         patch.object(storage_mod.settings, "r2_bucket", "test-bucket"):
        n = storage_mod.hydrate_working_dir(42, wd)

    assert n == 5
    # Every listed file landed at its expected local path.
    assert (wd / "brief.md").read_text() == "fetched:reports/42/brief.md"
    assert (wd / "section-macro-strategist.md").exists()
    assert (wd / "charts" / "yield.png").exists()


def test_hydrate_working_dir_skips_files_already_local(tmp_path: Path) -> None:
    """If the local copy exists already, hydrate should NOT clobber it
    -- the on-disk version is the latest after the current run wrote
    it. R2's job is the safety net for rebuilds, not source of truth."""
    import api.storage as storage_mod

    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "brief.md").write_text("local-newer-content")

    listed = {"Contents": [{"Key": "reports/42/brief.md"}]}
    download_calls: list[str] = []

    class FakeClient:
        def list_objects_v2(self, **_kwargs):  # type: ignore[no-untyped-def]
            return listed

        def download_file(self, _bucket, key, _target):  # type: ignore[no-untyped-def]
            download_calls.append(key)

    with patch.object(storage_mod, "is_r2_enabled", lambda: True), \
         patch.object(storage_mod, "_client", lambda: FakeClient()), \
         patch.object(storage_mod.settings, "r2_bucket", "test-bucket"):
        n = storage_mod.hydrate_working_dir(42, wd)

    assert n == 0
    assert download_calls == []
    # Local content untouched.
    assert (wd / "brief.md").read_text() == "local-newer-content"


def test_hydrate_working_dir_noop_when_r2_disabled(tmp_path: Path) -> None:
    """No R2 config -> no-op. Doesn't crash, returns 0. Local-only
    deploys mustn't be required to set R2 env vars."""
    import api.storage as storage_mod

    with patch.object(storage_mod, "is_r2_enabled", lambda: False):
        assert storage_mod.hydrate_working_dir(42, tmp_path) == 0


def test_hydrate_working_dir_handles_list_failure_gracefully(tmp_path: Path) -> None:
    """A transient R2 outage during list_objects_v2 must not blow up
    the stage runner -- the worst case is a re-run with empty inputs,
    which is what we had before. Better to log and continue than to
    fail the whole stage."""
    import api.storage as storage_mod

    class BrokenClient:
        def list_objects_v2(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("R2 had a bad day")

    with patch.object(storage_mod, "is_r2_enabled", lambda: True), \
         patch.object(storage_mod, "_client", lambda: BrokenClient()), \
         patch.object(storage_mod.settings, "r2_bucket", "test-bucket"):
        # Should not raise.
        n = storage_mod.hydrate_working_dir(42, tmp_path)
    assert n == 0


def test_run_stage_hydrates_before_reading_inputs(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The smoking gun. run_stage must call hydrate_working_dir before
    any non-brief stage reads files from disk, so a re-run from edit
    on a fresh container has its section-*.md / redteam.md inputs."""
    import api.storage as storage_mod
    import api.workflow.state_machine as sm_mod
    from api.models import Report, ReportStage

    hydrate_calls: list[tuple[int, Path]] = []

    def fake_hydrate(report_id, wd):  # type: ignore[no-untyped-def]
        hydrate_calls.append((report_id, wd))
        return 0

    monkeypatch.setattr(storage_mod, "hydrate_working_dir", fake_hydrate)
    monkeypatch.setattr(sm_mod.settings, "reports_dir", tmp_path)

    # Stub heavy collaborators that run_stage's edit branch reaches for.
    monkeypatch.setattr(sm_mod, "_resolved_contributors", lambda *_a, **_k: [])
    monkeypatch.setattr(sm_mod, "_read_sources", lambda _wd: [])

    class FakeEIC:
        def __init__(self, *_a, **_k) -> None:
            pass

        def edit(self, **_kwargs):  # type: ignore[no-untyped-def]
            from api.agents.base import AgentResult
            return AgentResult(text="ok", cost_usd=0.0, stop_reason="end_turn")

    monkeypatch.setattr(sm_mod, "EditorInChief", FakeEIC)
    # _record writes to DB; stub it so we don't need a session.
    monkeypatch.setattr(sm_mod, "_record", lambda *_a, **_k: None)
    # _tracker hits audit_log; bypass with a no-op tracker.
    from api.agents.cost import CostTracker
    monkeypatch.setattr(
        sm_mod, "_tracker",
        lambda _r: CostTracker(report_cap=1.0, day_cap=5.0),
    )
    monkeypatch.setattr(sm_mod, "audit_hook", lambda _id: lambda _e, _d: None)
    # Edit stage at the end re-saves report.error on truncation; stub
    # the engine session so it doesn't try to write.
    monkeypatch.setattr(sm_mod, "Session", MagicMock(return_value=MagicMock(
        __enter__=lambda self: MagicMock(get=lambda *_a: None),
        __exit__=lambda self, *_a: False,
    )))

    report = Report(id=99, theme="t", stage=ReportStage.edit)
    # Don't care if the stage completes -- we're verifying that hydrate
    # ran first, before whatever DB / file plumbing trips the stage up.
    with contextlib.suppress(Exception):
        sm_mod.run_stage(report, ReportStage.edit)

    assert len(hydrate_calls) == 1
    rid, wd = hydrate_calls[0]
    assert rid == 99
    assert wd == tmp_path / "99"


def test_run_stage_skips_hydrate_for_brief(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Brief is the first stage -- there's nothing yet to hydrate from
    R2. Skipping the call avoids a useless list-objects round-trip on
    every new report."""
    import api.storage as storage_mod
    import api.workflow.state_machine as sm_mod
    from api.models import Report, ReportStage

    hydrate_calls: list[tuple[int, Path]] = []

    def fake_hydrate(report_id, wd):  # type: ignore[no-untyped-def]
        hydrate_calls.append((report_id, wd))
        return 0

    monkeypatch.setattr(storage_mod, "hydrate_working_dir", fake_hydrate)
    monkeypatch.setattr(sm_mod.settings, "reports_dir", tmp_path)

    # Brief stage reaches for house_view + EIC + DB writes; stub them
    # all out and let the stage early-out by mocking the EIC to no-op
    # then expect an exception (we only care that hydrate didn't run
    # before the exception, not that the stage actually completed).
    class FakeEIC:
        def __init__(self, *_a, **_k) -> None:
            pass

        def write_brief(self, *_a, **_kwargs):  # type: ignore[no-untyped-def]
            from api.agents.base import AgentResult
            return AgentResult(text="brief", cost_usd=0.0, stop_reason="end_turn")

    monkeypatch.setattr(sm_mod, "EditorInChief", FakeEIC)
    monkeypatch.setattr(sm_mod, "_record", lambda *_a, **_k: None)
    monkeypatch.setattr(sm_mod, "get_roster", lambda: [])
    monkeypatch.setattr(sm_mod, "_past_reports_summary", lambda **_k: [])
    # house_view import inside the stage; stub that submodule's get().
    fake_hv = MagicMock()
    fake_hv.get = lambda: ""
    monkeypatch.setitem(__import__("sys").modules, "api.house_view", fake_hv)

    report = Report(id=100, theme="t", stage=ReportStage.brief)
    # The brief stage does DB writes we haven't stubbed; we only care
    # that hydrate didn't fire BEFORE the exception.
    with contextlib.suppress(Exception):
        sm_mod.run_stage(report, ReportStage.brief)

    assert hydrate_calls == [], (
        "hydrate_working_dir should not run for the brief stage "
        "(nothing yet to hydrate from R2)"
    )
