# -*- coding: utf-8 -*-
"""Parse hero labels and screenshot timestamps."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_SCREENSHOT_TS_RE = re.compile(
    r"MuMu-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-(\d+)\.png$",
    re.IGNORECASE,
)
_HERO_LABEL_RE = re.compile(r"^(\d)(.+)$")


def parse_hero_label(label: str) -> tuple[int | None, str]:
    """Parse template label like '3汉堡狗' into (tier, hero_name)."""
    m = _HERO_LABEL_RE.match(label)
    if m:
        return int(m.group(1)), m.group(2)
    return None, label


def parse_screenshot_timestamp(path: Path | str) -> str | None:
    """Parse MuMu-YYYYMMDD-HHMMSS-ms.png filename to ISO8601 local time."""
    name = Path(path).name
    m = _SCREENSHOT_TS_RE.match(name)
    if not m:
        return None
    y, mo, d, h, mi, s, _ms = m.groups()
    dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    return dt.isoformat()
