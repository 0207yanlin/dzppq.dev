# -*- coding: utf-8 -*-
"""Unit tests for process_match_batch orchestration helpers."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.process_match_batch import (  # noqa: E402
    DEFAULT_DB_PATH,
    DEFAULT_GT_PATH,
    META_SCRIPT,
    build_import_cmd,
    build_label_cmd,
    build_meta_cmd,
    default_batch_mmdd,
    normalize_batch,
    path_prefix_for_batch,
    process_batch,
    screenshot_dir_for_batch,
)


def test_default_batch_mmdd() -> None:
    assert default_batch_mmdd(date(2026, 7, 22)) == "0722"
    assert default_batch_mmdd(date(2026, 1, 5)) == "0105"


def test_normalize_batch_rejects_invalid() -> None:
    assert normalize_batch("0705") == "0705"
    with pytest.raises(ValueError, match="MMDD"):
        normalize_batch("7-5")
    with pytest.raises(ValueError, match="MMDD"):
        normalize_batch("07051")


def test_path_helpers() -> None:
    assert path_prefix_for_batch("0705") == "screenshots.0705/"
    assert screenshot_dir_for_batch("0705", root=ROOT) == (ROOT / "screenshots.0705").resolve()


def test_build_commands_include_expected_args() -> None:
    screenshot_dir = ROOT / "screenshots.0705"
    gt_path = DEFAULT_GT_PATH
    db_path = DEFAULT_DB_PATH

    label_cmd = build_label_cmd(
        screenshot_dir=screenshot_dir,
        gt_path=gt_path,
        workers=4,
    )
    assert label_cmd[1].endswith("label_match_ground_truth.py")
    assert "--workers" in label_cmd
    assert label_cmd[label_cmd.index("--workers") + 1] == "4"
    assert label_cmd[-2:] == ["label", "--all"]

    import_cmd = build_import_cmd(
        screenshot_dir=screenshot_dir,
        path_prefix="screenshots.0705/",
        gt_path=gt_path,
        db_path=db_path,
    )
    assert import_cmd[1].endswith("build_match_database.py")
    assert "--path-prefix" in import_cmd
    assert import_cmd[import_cmd.index("--path-prefix") + 1] == "screenshots.0705/"
    assert "--force" in import_cmd
    assert "--allow-partial" in import_cmd
    assert "--predict" not in import_cmd

    meta_cmd = build_meta_cmd(db_path=db_path)
    assert Path(meta_cmd[1]) == META_SCRIPT
    assert meta_cmd[meta_cmd.index("--db") + 1] == str(db_path)


def test_process_batch_runs_three_steps_in_order(tmp_path: Path, monkeypatch) -> None:
    batch = "0705"
    screenshot_dir = tmp_path / f"screenshots.{batch}"
    screenshot_dir.mkdir()
    (screenshot_dir / "a.png").write_bytes(b"png")
    (screenshot_dir / "b.png").write_bytes(b"png")

    monkeypatch.setattr(
        "scripts.process_match_batch.screenshot_dir_for_batch",
        lambda value, root=ROOT: screenshot_dir,
    )
    monkeypatch.setattr(
        "scripts.process_match_batch.META_SCRIPT",
        tmp_path / "analyze_latest_meta.py",
    )
    (tmp_path / "analyze_latest_meta.py").write_text("# stub\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_runner(cmd: list[str], *, dry_run: bool = False) -> None:
        assert dry_run is False
        calls.append(cmd)

    process_batch(batch=batch, workers=4, runner=fake_runner)

    assert len(calls) == 3
    assert calls[0][-2:] == ["label", "--all"]
    assert calls[0][calls[0].index("--workers") + 1] == "4"
    assert "--path-prefix" in calls[1]
    assert calls[1][calls[1].index("--path-prefix") + 1] == "screenshots.0705/"
    assert "--predict" not in calls[1]
    assert "--db" in calls[2]


def test_process_batch_stops_when_first_step_fails(tmp_path: Path, monkeypatch) -> None:
    batch = "0705"
    screenshot_dir = tmp_path / f"screenshots.{batch}"
    screenshot_dir.mkdir()
    (screenshot_dir / "a.png").write_bytes(b"png")

    monkeypatch.setattr(
        "scripts.process_match_batch.screenshot_dir_for_batch",
        lambda value, root=ROOT: screenshot_dir,
    )
    monkeypatch.setattr(
        "scripts.process_match_batch.META_SCRIPT",
        tmp_path / "analyze_latest_meta.py",
    )
    (tmp_path / "analyze_latest_meta.py").write_text("# stub\n", encoding="utf-8")

    calls: list[list[str]] = []

    def failing_runner(cmd: list[str], *, dry_run: bool = False) -> None:
        calls.append(cmd)
        raise subprocess.CalledProcessError(1, cmd)

    with pytest.raises(subprocess.CalledProcessError):
        process_batch(batch=batch, workers=4, runner=failing_runner)

    assert len(calls) == 1


def test_process_batch_requires_pngs(tmp_path: Path, monkeypatch) -> None:
    batch = "0705"
    screenshot_dir = tmp_path / f"screenshots.{batch}"
    screenshot_dir.mkdir()
    monkeypatch.setattr(
        "scripts.process_match_batch.screenshot_dir_for_batch",
        lambda value, root=ROOT: screenshot_dir,
    )
    with pytest.raises(SystemExit, match="No PNG files"):
        process_batch(batch=batch, runner=mock.Mock())
