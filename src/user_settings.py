# -*- coding: utf-8 -*-
"""Persistent user settings stored next to the exe or project root."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.runtime_paths import app_base_dir

logger = logging.getLogger(__name__)

SETTINGS_FILENAME = "settings.json"
ADB_BIN_KEY = "adb_bin"


def settings_path() -> Path:
    return app_base_dir() / SETTINGS_FILENAME


def load_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read settings file %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Invalid settings file format: %s", path)
        return {}
    return data


def save_settings(data: dict[str, Any]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("Saved settings: %s", path)


def get_saved_adb_bin() -> str | None:
    value = load_settings().get(ADB_BIN_KEY)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def save_adb_bin(path: str) -> None:
    data = load_settings()
    data[ADB_BIN_KEY] = str(Path(path))
    save_settings(data)


def clear_saved_adb_bin() -> None:
    data = load_settings()
    if ADB_BIN_KEY not in data:
        return
    del data[ADB_BIN_KEY]
    if data:
        save_settings(data)
    else:
        path = settings_path()
        if path.is_file():
            path.unlink()
            logger.info("Removed empty settings file: %s", path)
