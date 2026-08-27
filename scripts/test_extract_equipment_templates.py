# -*- coding: utf-8 -*-
"""Tests for equipment template extraction defaults."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.extract_equipment_templates import OUTPUT_DIR  # noqa: E402
from src.layout import EQUIPMENT_TEMPLATE_DIR  # noqa: E402


def test_extraction_uses_runtime_equipment_template_directory() -> None:
    assert OUTPUT_DIR == EQUIPMENT_TEMPLATE_DIR
