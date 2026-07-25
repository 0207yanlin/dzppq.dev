# -*- coding: utf-8 -*-
"""Build DZPPQCardRecommender Windows exe with PyInstaller (slim profile)."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "scripts" / "card_pick_recommender.py"
META_JSON = ROOT / "data" / "latest_meta_analysis.json"
MATCH_DB = ROOT / "data" / "match_latest.db"
DIST_DIR = ROOT / "dist" / "DZPPQCardRecommender"
BUILD_DIR = ROOT / "build" / "DZPPQCardRecommender"
EXE_NAME = "DZPPQCardRecommender"

# Avoid --collect-all onnxruntime: it pulls tools/transformers and can drag torch/scipy in.

EXCLUDED_MODULES = [
    "torch",
    "torchvision",
    "tensorflow",
    "scipy",
    "matplotlib",
    "pandas",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "easyocr",
    "skimage",
    "onnxruntime.tools",
    "onnxruntime.transformers",
    "onnxruntime.datasets",
    "onnxruntime.quantization",
]

OCR_HIDDEN_IMPORTS = [
    "rapidocr_onnxruntime",
    "onnxruntime",
    "onnxruntime.capi.onnxruntime_pybind11_state",
    "PIL",
    "PIL.Image",
]

EXPECTED_ONNX_MODELS = 3
REQUIRED_SPLIT_CARD_KEYS = frozenset(
    {
        "蓝·一起刷刷刷",
        "蓝·天降啾啾pro",
        "蓝·重质也重量pro",
        "蓝·拍档支援",
        "黄·快速成型",
        "黄·吸吸宝pro",
        "黄·巨神兵",
        "黄·迅迅迅捷双剑",
        "黄·死亡摇滚",
        "黄·摇盒高手",
    }
)
STALE_SPLIT_RANKING_KEYS = frozenset({"蓝·一起刷刷刷+天降啾啾pro"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build DZPPQ card recommender exe")
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Build a single exe file instead of onedir (slower startup)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove previous build/dist output before building",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Use legacy collect-all profile (larger, not recommended)",
    )
    return parser


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "PyInstaller is required. Install with: pip install pyinstaller"
        ) from exc


def copy_runtime_data(output_dir: Path) -> None:
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    if MATCH_DB.is_file():
        shutil.copy2(MATCH_DB, data_dir / MATCH_DB.name)
        print(f"Copied DB: {MATCH_DB} -> {data_dir / MATCH_DB.name}")
    else:
        print(f"Warning: missing runtime DB (exe will need data/{MATCH_DB.name}): {MATCH_DB}")

    if META_JSON.is_file():
        shutil.copy2(META_JSON, data_dir / META_JSON.name)
        print(f"Copied JSON fallback: {META_JSON}")
    else:
        print(f"Note: JSON fallback not bundled: {META_JSON}")


def _add_data_arg(cmd: list[str]) -> None:
    sep = ";" if sys.platform == "win32" else ":"
    if MATCH_DB.is_file():
        cmd.extend(["--add-data", f"{MATCH_DB}{sep}data"])
    if META_JSON.is_file():
        cmd.extend(["--add-data", f"{META_JSON}{sep}data"])


def _add_exclude_modules(cmd: list[str]) -> None:
    for name in EXCLUDED_MODULES:
        cmd.extend(["--exclude-module", name])


def _add_hidden_imports(cmd: list[str]) -> None:
    for name in OCR_HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", name])


def _rapidocr_model_data_args() -> list[str]:
    """Explicitly bundle rapidocr config and ONNX models (--collect-data may skip .onnx)."""
    try:
        import rapidocr_onnxruntime
    except ImportError as exc:
        raise SystemExit(
            "rapidocr-onnxruntime is required to build. "
            "Install with: pip install rapidocr-onnxruntime"
        ) from exc

    sep = ";" if sys.platform == "win32" else ":"
    pkg_dir = Path(rapidocr_onnxruntime.__file__).resolve().parent
    args: list[str] = []

    config = pkg_dir / "config.yaml"
    if config.is_file():
        args.extend(["--add-data", f"{config}{sep}rapidocr_onnxruntime"])

    models_dir = pkg_dir / "models"
    if models_dir.is_dir():
        for onnx in sorted(models_dir.glob("*.onnx")):
            args.extend(["--add-data", f"{onnx}{sep}rapidocr_onnxruntime/models"])

    return args


def build_command(*, onefile: bool, full: bool) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--name",
        EXE_NAME,
        "--windowed",
        "--paths",
        str(ROOT),
        "--paths",
        str(ROOT / "src"),
        "--collect-submodules",
        "PIL",
        "--collect-data",
        "rapidocr_onnxruntime",
        "--collect-binaries",
        "onnxruntime",
    ]
    _add_hidden_imports(cmd)
    _add_exclude_modules(cmd)
    _add_data_arg(cmd)
    cmd.extend(_rapidocr_model_data_args())

    if full:
        cmd.extend(
            [
                "--collect-all",
                "rapidocr_onnxruntime",
                "--collect-all",
                "onnxruntime",
            ]
        )

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    cmd.append(str(ENTRY))
    return cmd


def _bundle_internal_dir(output_dir: Path, *, onefile: bool) -> Path:
    return output_dir if onefile else output_dir / "_internal"


def validate_ocr_bundle(output_dir: Path, *, onefile: bool) -> None:
    """Fail fast if OCR runtime assets were not bundled."""
    internal = _bundle_internal_dir(output_dir, onefile=onefile)
    errors: list[str] = []

    pil_found = (internal / "PIL").is_dir() or any(
        p.name == "_imaging.pyd" for p in internal.rglob("_imaging*.pyd")
    )
    if not pil_found:
        errors.append("PIL/Pillow not found in bundle")

    rapidocr_dir = internal / "rapidocr_onnxruntime"
    if not (rapidocr_dir / "config.yaml").is_file():
        errors.append(f"missing {rapidocr_dir / 'config.yaml'}")

    onnx_files = list(rapidocr_dir.rglob("*.onnx"))
    if len(onnx_files) < EXPECTED_ONNX_MODELS:
        errors.append(
            f"expected >= {EXPECTED_ONNX_MODELS} .onnx models under rapidocr_onnxruntime, "
            f"found {len(onnx_files)}"
        )

    if errors:
        raise SystemExit("OCR bundle validation failed:\n  - " + "\n  - ".join(errors))

    print(
        f"OCR bundle OK: PIL={'yes' if pil_found else 'no'}, "
        f"onnx_models={len(onnx_files)}"
    )


def _ranking_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        key = value.get("key")
        if isinstance(key, str):
            keys.add(key)
        for child in value.values():
            keys.update(_ranking_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_ranking_keys(child))
    return keys


def validate_runtime_data(output_dir: Path, *, check_bundled: bool = False) -> None:
    """Verify copied runtime data is current and logical ranking keys are split."""
    data_dirs = [output_dir / "data"]
    if check_bundled:
        data_dirs.append(output_dir / "_internal" / "data")
    errors: list[str] = []
    for data_dir in data_dirs:
        for source in (MATCH_DB, META_JSON):
            if not source.is_file():
                continue
            copied = data_dir / source.name
            if not copied.is_file():
                errors.append(f"missing copied runtime data: {copied}")
            elif copied.read_bytes() != source.read_bytes():
                errors.append(f"stale copied runtime data differs from source: {copied}")

    copied_json = data_dirs[0] / META_JSON.name
    if copied_json.is_file():
        try:
            payload = json.loads(copied_json.read_text(encoding="utf-8"))
            cards = payload["rankings"]["cards"]
            rows_by_prefix = cards["single_cards_by_prefix"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            errors.append(f"invalid card ranking JSON {copied_json}: {exc}")
        else:
            catalog_keys = {
                str(row.get("key"))
                for rows in rows_by_prefix.values()
                if isinstance(rows, list)
                for row in rows
                if isinstance(row, dict) and row.get("key")
            }
            missing = sorted(REQUIRED_SPLIT_CARD_KEYS - catalog_keys)
            if missing:
                errors.append("missing split logical card keys: " + ", ".join(missing))

            stale = sorted(STALE_SPLIT_RANKING_KEYS & _ranking_keys(cards))
            if stale:
                errors.append("stale merged ranking keys remain: " + ", ".join(stale))

    if errors:
        raise SystemExit("Runtime data validation failed:\n  - " + "\n  - ".join(errors))
    print(
        "Runtime data OK: source files match dist; "
        f"split logical keys={len(REQUIRED_SPLIT_CARD_KEYS)}"
    )


def summarize_dist(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    files = [p for p in output_dir.rglob("*") if p.is_file()]
    total_bytes = sum(p.stat().st_size for p in files)
    print(f"Output files: {len(files)}")
    print(f"Output size: {total_bytes / (1024 * 1024):.1f} MB")
    suspicious = sorted(
        {
            str(p.relative_to(output_dir))
            for p in files
            if any(
                token in str(p).lower()
                for token in ("torch", "scipy", "matplotlib", "pandas", "jupyter")
            )
        }
    )
    if suspicious:
        print("Warning: suspicious bundled paths detected:")
        for name in suspicious[:20]:
            print(f"  - {name}")


def run_build(*, onefile: bool, clean: bool, full: bool) -> Path:
    ensure_pyinstaller()
    if clean:
        shutil.rmtree(DIST_DIR, ignore_errors=True)
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
        spec = ROOT / f"{EXE_NAME}.spec"
        if spec.exists():
            spec.unlink()

    cmd = build_command(onefile=onefile, full=full)
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)

    if onefile:
        output_dir = ROOT / "dist"
        exe_path = output_dir / f"{EXE_NAME}.exe"
        copy_runtime_data(output_dir)
    else:
        output_dir = DIST_DIR
        exe_path = output_dir / f"{EXE_NAME}.exe"
        copy_runtime_data(output_dir)

    summarize_dist(output_dir if not onefile else output_dir)
    validate_ocr_bundle(output_dir if not onefile else output_dir, onefile=onefile)
    validate_runtime_data(output_dir, check_bundled=not onefile)
    return exe_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exe_path = run_build(onefile=args.onefile, clean=args.clean, full=args.full)
    print(f"Built: {exe_path}")
    print(f"Runtime data dir: {exe_path.parent / 'data'}")
    if MATCH_DB.is_file():
        print(f"DB copied to: {exe_path.parent / 'data' / MATCH_DB.name}")
    if META_JSON.is_file():
        print(f"JSON fallback copied to: {exe_path.parent / 'data' / META_JSON.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
