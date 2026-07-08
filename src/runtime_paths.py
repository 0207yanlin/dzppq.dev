# -*- coding: utf-8 -*-
"""Resolve resource paths for source checkout and PyInstaller exe runs."""

from __future__ import annotations

import sys
from pathlib import Path

META_JSON_NAME = "latest_meta_analysis.json"
MATCH_DB_NAME = "match_latest.db"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_base_dir() -> Path:
    """Directory containing the exe, or the project root when running from source."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path | None:
    """PyInstaller onefile/onedir extraction dir, if present."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return None


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_data_file(
    name: str,
    explicit: Path | str | None,
    *,
    missing_hint: str,
) -> Path:
    if explicit is not None:
        path = Path(explicit)
        if not path.is_absolute():
            base = app_base_dir() if is_frozen() else project_root()
            path = (base / path).resolve()
        else:
            path = path.resolve()
        if path.is_file():
            return path
        raise FileNotFoundError(f"{name} not found: {path}")

    candidates: list[Path] = [
        app_base_dir() / "data" / name,
    ]
    bundled = bundle_dir()
    if bundled is not None:
        candidates.append(bundled / "data" / name)
    if not is_frozen():
        candidates.append(project_root() / "data" / name)

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved

    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"{name} not found. Searched: {searched}. {missing_hint}")


def resolve_match_db(explicit: Path | str | None = None) -> Path:
    """Find match_latest.db for current runtime."""
    try:
        return _resolve_data_file(
            MATCH_DB_NAME,
            explicit,
            missing_hint=(
                "Place it at data/match_latest.db next to the exe, "
                "or pass --db explicitly."
            ),
        )
    except FileNotFoundError:
        if explicit is not None or is_frozen():
            raise

    data_dir = project_root() / "data"
    candidates = sorted(
        data_dir.glob("matches_*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0].resolve()

    raise FileNotFoundError(
        f"{MATCH_DB_NAME} not found under data/. "
        "Build match_latest.db or provide --db."
    )


def resolve_meta_json(explicit: Path | str | None = None) -> Path:
    """Find latest_meta_analysis.json for current runtime."""
    return _resolve_data_file(
        META_JSON_NAME,
        explicit,
        missing_hint=(
            "Place it at data/latest_meta_analysis.json next to the exe, "
            "or run analyze_latest_meta.py to regenerate."
        ),
    )


def default_log_dir() -> Path:
    return app_base_dir() / "logs"


def format_mtime(path: Path) -> str:
    if not path.is_file():
        return "未知"
    stamp = path.stat().st_mtime
    from datetime import datetime

    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")


def runtime_build_label(*, entry_script: Path | None = None) -> str:
    """Human-readable runtime source and build timestamp for UI/logging."""
    if is_frozen():
        exe = Path(sys.executable).resolve()
        return f"运行: exe | 构建: {format_mtime(exe)}"
    script = entry_script or (project_root() / "scripts" / "card_pick_recommender.py")
    return f"运行: source | 脚本: {format_mtime(script)}"
