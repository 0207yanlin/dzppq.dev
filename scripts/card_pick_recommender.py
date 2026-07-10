# -*- coding: utf-8 -*-
"""Background card-pick recommender for DZPPQ (MuMu + ADB + OCR)."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adb_capture import AdbClient, DEFAULT_ADB_BIN, OcrHelper, make_mumu_filename  # noqa: E402
from src.card_pick_recommend import (  # noqa: E402
    CARD_PREFIXES,
    CardMatchResult,
    CardStatsIndex,
    RecommendationResult,
    build_recommendation,
    format_recommendation,
    recognize_hand_cards,
)
from src.layout import HAND_CARD_BOXES  # noqa: E402
from src.runtime_paths import (  # noqa: E402
    app_base_dir,
    default_log_dir,
    format_mtime,
    is_frozen,
    resolve_match_db,
    resolve_meta_json,
    runtime_build_label,
)
from src.user_settings import clear_saved_adb_bin, get_saved_adb_bin, save_adb_bin  # noqa: E402

logger = logging.getLogger(__name__)

PREFIX_COLORS = {
    "白": "#f5f5f5",
    "蓝": "#d6ebff",
    "黄": "#fff3bf",
    "彩": "#ffe0f0",
}


@dataclass
class AdbRuntimeOptions:
    adb_bin: str | None = None
    serial: str | None = None
    auto_connect: bool = True
    mumu_host: str = "127.0.0.1"
    mumu_port: int = 16384
    skip_resolution_check: bool = False
    connect_at_start: bool = False


def resolve_adb_bin(adb_bin: str | None) -> str | None:
    """Return adb.exe path if found, else None."""
    resolved, _source = resolve_adb_bin_with_source(adb_bin)
    return resolved


def resolve_adb_bin_with_source(adb_bin: str | None) -> tuple[str | None, str]:
    """Resolve adb.exe using CLI > saved settings > default MuMu path."""
    if adb_bin:
        path = Path(adb_bin)
        if path.is_file():
            return str(path.resolve()), "命令行"
        return None, "命令行（无效）"

    saved = get_saved_adb_bin()
    if saved:
        path = Path(saved)
        if path.is_file():
            return str(path.resolve()), "已保存配置"
        return None, "已保存配置（无效）"

    default = Path(DEFAULT_ADB_BIN)
    if default.is_file():
        return str(default.resolve()), "默认路径"
    return None, "未找到"


def build_adb_client(options: AdbRuntimeOptions, *, verbose: bool = False) -> AdbClient:
    kwargs: dict[str, object] = {"verbose_commands": verbose}
    if options.adb_bin:
        kwargs["adb_bin"] = options.adb_bin
    if options.serial:
        kwargs["device_serial"] = options.serial
    return AdbClient(**kwargs)


def ensure_adb_session(adb: AdbClient, options: AdbRuntimeOptions) -> str:
    """Connect/check MuMu ADB before screenshot. Returns connected serial."""
    if options.auto_connect:
        target = adb.connect(host=options.mumu_host, port=options.mumu_port)
        logger.info("adb auto-connect: %s", target)
    adb.check_device(prefer=options.serial)
    serial = adb.device_serial or "<unknown>"
    logger.info("adb session ready: %s", serial)
    return serial


class PrefixPickerDialog(tk.Toplevel):
    """Modal dialog to pick card prefix after clicking 开始推荐."""

    def __init__(self, parent: tk.Tk | tk.Toplevel) -> None:
        super().__init__(parent)
        self.title("选择卡牌类别")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.selected_prefix: str | None = None

        ttk.Label(
            self,
            text="请选择当前三选一卡牌的类别：",
            padding=(16, 12, 16, 8),
        ).pack()

        btn_row = ttk.Frame(self)
        btn_row.pack(padx=16, pady=8)
        for prefix in CARD_PREFIXES:
            tk.Button(
                btn_row,
                text=prefix,
                width=8,
                height=2,
                command=lambda p=prefix: self._choose(p),
                bg=PREFIX_COLORS.get(prefix, "#eeeeee"),
                activebackground=PREFIX_COLORS.get(prefix, "#eeeeee"),
            ).pack(side="left", padx=6)

        ttk.Button(self, text="取消", command=self._cancel).pack(pady=(4, 12))

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.update_idletasks()
        self._center_over_parent(parent)

    def _center_over_parent(self, parent: tk.Misc) -> None:
        parent.update_idletasks()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        w = self.winfo_width()
        h = self.winfo_height()
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
        self.geometry(f"+{x}+{y}")

    def _choose(self, prefix: str) -> None:
        self.selected_prefix = prefix
        self.destroy()

    def _cancel(self) -> None:
        self.selected_prefix = None
        self.destroy()


class CardPickRecommenderApp:
    def __init__(
        self,
        *,
        adb: AdbClient,
        adb_options: AdbRuntimeOptions,
        ocr: OcrHelper,
        stats: CardStatsIndex,
        data_path: Path,
        data_mode: str,
        adb_bin_path: str | None,
        adb_source: str,
        adb_config_locked: bool = False,
        save_debug_roi: Path | None = None,
    ) -> None:
        self.adb = adb
        self.adb_options = adb_options
        self.ocr = ocr
        self.stats = stats
        self.data_path = data_path
        self.data_mode = data_mode
        self.adb_bin_path = adb_bin_path
        self.adb_source = adb_source
        self.adb_config_locked = adb_config_locked
        self.save_debug_roi = save_debug_roi
        self.build_label = runtime_build_label(
            entry_script=Path(__file__).resolve(),
        )

        self.current_prefix: str | None = None
        self.last_image: np.ndarray | None = None
        self.last_cards: list[CardMatchResult] = []
        self.last_result: RecommendationResult | None = None
        self._busy = False
        self._ocr_status = "OCR 预加载中..."

        self.root = tk.Tk()
        self.root.title("DZPPQ 卡牌推荐")
        self.root.geometry("820x720")
        self.root.attributes("-topmost", True)
        self._build_ui()
        self._poll_ocr_warmup()

    def _poll_ocr_warmup(self) -> None:
        if self.ocr.warmup_finished:
            result = self.ocr.warmup_result
            if result is not None and result.success:
                self._ocr_status = f"OCR 已就绪 ({result.backend}, {result.elapsed_ms:.0f} ms)"
            elif result is not None:
                self._ocr_status = f"OCR 预加载失败: {result.error or 'unknown'}"
            else:
                self._ocr_status = "OCR 已就绪"
            if not self._busy:
                self.status_var.set("就绪")
            self.ocr_label_var.set(self._ocr_status)
            return
        self.ocr_label_var.set(self._ocr_status)
        self.root.after(200, self._poll_ocr_warmup)

    def _build_ui(self) -> None:
        header = ttk.Label(
            self.root,
            text="遇到三选一卡牌时，点击「开始推荐」，再选择卡牌类别。",
            wraplength=720,
        )
        header.pack(padx=12, pady=(12, 6), anchor="w")

        action_frame = ttk.Frame(self.root)
        action_frame.pack(fill="x", padx=12, pady=6)

        self.start_btn = ttk.Button(
            action_frame,
            text="开始推荐",
            command=self.on_start_clicked,
        )
        self.start_btn.pack(side="left")

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(action_frame, textvariable=self.status_var).pack(side="left", padx=12)

        self.prefix_var = tk.StringVar(value="尚未开始")
        ttk.Label(self.root, textvariable=self.prefix_var).pack(anchor="w", padx=12)

        self._build_adb_config_ui()

        meta = self.stats
        data_kind = "数据库" if self.data_mode == "db" else "JSON"
        meta_text = (
            f"数据: {self.data_path.name} ({data_kind}) | "
            f"更新: {format_mtime(self.data_path)} | "
            f"来源: {meta.data_source or self.data_path.name} | "
            f"生成: {meta.generated_at[:19] if meta.generated_at else '未知'} | "
            f"对局: {meta.total_matches} | 卡牌记录: {meta.total_card_records}"
        )
        ttk.Label(self.root, text=meta_text, wraplength=720).pack(anchor="w", padx=12)

        self.ocr_label_var = tk.StringVar(value=self._ocr_status)
        ttk.Label(self.root, textvariable=self.ocr_label_var, wraplength=720).pack(anchor="w", padx=12)

        ttk.Label(
            self.root,
            text=self.build_label,
            wraplength=720,
        ).pack(anchor="w", padx=12)

        result_frame = ttk.LabelFrame(self.root, text="推荐结果")
        result_frame.pack(fill="both", expand=True, padx=12, pady=8)

        self.result_text = scrolledtext.ScrolledText(
            result_frame,
            wrap="word",
            font=("Consolas", 11),
            height=22,
        )
        self.result_text.pack(fill="both", expand=True, padx=8, pady=8)
        self.result_text.tag_configure("recommend", font=("Consolas", 11, "bold"))
        self.result_text.tag_configure("warning", foreground="#b45309")
        self.result_text.tag_configure("header", font=("Consolas", 11, "bold"))
        self.result_text.insert("1.0", "点击「开始推荐」，然后选择卡牌类别。\n")
        self.result_text.configure(state="disabled")

    def _build_adb_config_ui(self) -> None:
        adb_frame = ttk.LabelFrame(self.root, text="ADB 配置")
        adb_frame.pack(fill="x", padx=12, pady=(0, 6))

        path_row = ttk.Frame(adb_frame)
        path_row.pack(fill="x", padx=8, pady=(8, 4))

        ttk.Label(path_row, text="adb.exe:").pack(side="left")
        initial_path = self.adb_bin_path or get_saved_adb_bin() or DEFAULT_ADB_BIN
        self.adb_path_var = tk.StringVar(value=initial_path)
        self.adb_path_entry = ttk.Entry(path_row, textvariable=self.adb_path_var)
        self.adb_path_entry.pack(side="left", fill="x", expand=True, padx=(8, 8))

        btn_row = ttk.Frame(adb_frame)
        btn_row.pack(fill="x", padx=8, pady=(0, 8))

        self.adb_browse_btn = ttk.Button(btn_row, text="浏览...", command=self._on_browse_adb)
        self.adb_browse_btn.pack(side="left")
        self.adb_apply_btn = ttk.Button(btn_row, text="应用", command=self._on_apply_adb)
        self.adb_apply_btn.pack(side="left", padx=(6, 0))
        self.adb_save_btn = ttk.Button(btn_row, text="保存", command=self._on_save_adb)
        self.adb_save_btn.pack(side="left", padx=(6, 0))
        self.adb_reset_btn = ttk.Button(btn_row, text="恢复默认", command=self._on_reset_adb)
        self.adb_reset_btn.pack(side="left", padx=(6, 0))

        self.adb_status_var = tk.StringVar(value=self._format_adb_status())
        ttk.Label(adb_frame, textvariable=self.adb_status_var, wraplength=760).pack(
            anchor="w",
            padx=8,
            pady=(0, 8),
        )

        if self.adb_config_locked:
            self.adb_path_entry.configure(state="disabled")
            for widget in (
                self.adb_browse_btn,
                self.adb_apply_btn,
                self.adb_save_btn,
                self.adb_reset_btn,
            ):
                widget.configure(state="disabled")

    def _format_adb_status(self) -> str:
        connect_hint = (
            f"来源: {self.adb_source} | 推荐前自动连接 "
            f"{self.adb_options.mumu_host}:{self.adb_options.mumu_port}"
        )
        if self.adb_config_locked:
            connect_hint += " | 当前由命令行参数锁定"
        elif self.adb_bin_path:
            connect_hint += " | 路径有效"
        else:
            connect_hint += " | 未找到 adb.exe，请浏览选择 MuMu 自带的 adb.exe"
        return connect_hint

    def _refresh_adb_status(self) -> None:
        self.adb_status_var.set(self._format_adb_status())

    def _apply_adb_path(self, path_text: str, *, source: str) -> bool:
        path = Path(path_text.strip())
        if not path.is_file():
            self.adb_bin_path = None
            self.adb_source = f"{source}（无效）"
            self.adb.adb_bin = DEFAULT_ADB_BIN
            self.adb_options.adb_bin = None
            self._refresh_adb_status()
            return False

        resolved = str(path.resolve())
        self.adb_bin_path = resolved
        self.adb_source = source
        self.adb.adb_bin = resolved
        self.adb_options.adb_bin = resolved
        self.adb_path_var.set(resolved)
        self._refresh_adb_status()
        logger.info("ADB path applied: %s (%s)", resolved, source)
        return True

    def _on_browse_adb(self) -> None:
        initial = self.adb_path_var.get().strip() or DEFAULT_ADB_BIN
        initial_dir = str(Path(initial).parent) if Path(initial).parent.is_dir() else str(app_base_dir())
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="选择 adb.exe",
            initialdir=initial_dir,
            filetypes=[("ADB executable", "adb.exe"), ("Executable", "*.exe"), ("All files", "*.*")],
        )
        if not selected:
            return
        self.adb_path_var.set(selected)
        if self._apply_adb_path(selected, source="手动选择"):
            self.status_var.set("ADB 路径已更新")

    def _on_apply_adb(self) -> None:
        path_text = self.adb_path_var.get()
        if self._apply_adb_path(path_text, source="手动输入"):
            self.status_var.set("ADB 路径已应用")
        else:
            messagebox.showerror(
                "无效 ADB 路径",
                f"找不到 adb.exe:\n{path_text}\n\n"
                "请选择 MuMu 安装目录下的 adb.exe，例如：\n"
                f"{DEFAULT_ADB_BIN}",
            )

    def _on_save_adb(self) -> None:
        path_text = self.adb_path_var.get()
        if not self._apply_adb_path(path_text, source="已保存配置"):
            messagebox.showerror(
                "无法保存",
                f"找不到 adb.exe:\n{path_text}\n\n请先选择有效路径后再保存。",
            )
            return
        save_adb_bin(self.adb_bin_path or path_text)
        self.status_var.set("ADB 路径已保存")

    def _on_reset_adb(self) -> None:
        if self.adb_config_locked:
            return
        clear_saved_adb_bin()
        self.adb_path_var.set(DEFAULT_ADB_BIN)
        if self._apply_adb_path(DEFAULT_ADB_BIN, source="默认路径"):
            self.status_var.set("已恢复默认 ADB 路径")
        else:
            self.adb_source = "未找到"
            self.adb_bin_path = None
            self._refresh_adb_status()
            self.status_var.set("默认 ADB 路径不存在，请手动选择")

    def run(self) -> None:
        self.root.mainloop()

    def _set_busy(self, busy: bool, message: str) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.start_btn.configure(state=state)
        self.status_var.set(message)
        self.root.update_idletasks()

    def on_start_clicked(self) -> None:
        if self._busy:
            return
        if not self.adb_bin_path:
            messagebox.showerror(
                "缺少 ADB",
                f"未找到 adb.exe。\n\n"
                f"默认路径: {DEFAULT_ADB_BIN}\n\n"
                "请在上方「ADB 配置」中浏览选择 MuMu 自带的 adb.exe，然后点击「保存」。",
            )
            return
        dialog = PrefixPickerDialog(self.root)
        self.root.wait_window(dialog)
        prefix = dialog.selected_prefix
        if prefix is None:
            self.status_var.set("已取消")
            return
        self.current_prefix = prefix
        self.prefix_var.set(f"当前类别: {prefix}")
        threading.Thread(
            target=self._capture_and_recognize,
            args=(prefix,),
            daemon=True,
        ).start()

    def _capture_and_recognize(self, prefix: str) -> None:
        import time

        t0 = time.perf_counter()
        try:
            self.root.after(0, lambda: self._set_busy(True, "正在连接 ADB..."))
            serial = ensure_adb_session(self.adb, self.adb_options)
            t_connect = time.perf_counter()
            self.root.after(
                0,
                lambda: self.status_var.set(f"已连接 {serial}，正在截图..."),
            )
            if self.adb_options.skip_resolution_check:
                img = self.adb.capture_bgr()
            else:
                img = self.adb.capture_bgr_validated()
            t_capture = time.perf_counter()
            self.last_image = img
            if self.save_debug_roi is not None:
                self._save_debug_rois(img, self.save_debug_roi)
            self.root.after(0, lambda: self.status_var.set("正在识别..."))
            if not self.ocr.warmup_finished:
                self.root.after(0, lambda: self.status_var.set("等待 OCR 预加载..."))
            self.ocr.ensure_ready()
            cards = self.ocr.run_on_ocr_thread(
                lambda: recognize_hand_cards(img, self.ocr, prefix, self.stats)
            )
            t_ocr = time.perf_counter()
            self.last_cards = cards
            result = build_recommendation(prefix, cards, self.stats)
            self.last_result = result
            text = format_recommendation(result)
            total_ms = (time.perf_counter() - t0) * 1000
            ocr_ms = (t_ocr - t_capture) * 1000
            timing_summary = f"完成：总 {total_ms / 1000:.1f}s / OCR {ocr_ms / 1000:.1f}s"
            logger.info(
                "Timing ms: connect=%.0f capture=%.0f ocr=%.0f match=%.0f total=%.0f",
                (t_connect - t0) * 1000,
                (t_capture - t_connect) * 1000,
                ocr_ms,
                (time.perf_counter() - t_ocr) * 1000,
                total_ms,
            )
            self.root.after(
                0,
                lambda: self._show_result(text, timing_summary),
            )
        except Exception as exc:
            logger.exception("capture failed")
            err_text = str(exc)
            self.root.after(
                0,
                lambda: self._show_result(
                    f"ADB/截图/识别失败:\n{err_text}\n\n"
                    "请确认 MuMu 已启动，并已开启 ADB 调试。",
                    "失败",
                ),
            )
            self.root.after(0, lambda: messagebox.showerror("推荐失败", err_text))
            self.root.after(0, lambda: self._set_busy(False, "失败"))
            return
        self.root.after(0, lambda: self._set_busy(False, "就绪"))

    def _show_result(self, text: str, status: str) -> None:
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", text)
        if text.startswith("建议选取:"):
            end = self.result_text.index("1.end")
            self.result_text.tag_add("recommend", "1.0", end)
        for marker in ("低置信", "样本少", "匹配置信度偏低", "样本偏少"):
            start = "1.0"
            while True:
                idx = self.result_text.search(marker, start, stopindex="end")
                if not idx:
                    break
                line_end = self.result_text.index(f"{idx} lineend")
                self.result_text.tag_add("warning", idx, line_end)
                start = line_end
        for header in ("对比速览", "多维排序", "诊断信息"):
            start = "1.0"
            while True:
                idx = self.result_text.search(header, start, stopindex="end")
                if not idx:
                    break
                line_end = self.result_text.index(f"{idx} lineend")
                self.result_text.tag_add("header", idx, line_end)
                start = line_end
        self.result_text.configure(state="disabled")
        self.status_var.set(status)

    def _save_debug_rois(self, img: np.ndarray, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = make_mumu_filename().replace(".png", "")
        for slot, box in enumerate(HAND_CARD_BOXES):
            x1, y1, x2, y2 = box
            roi = img[y1:y2, x1:x2]
            path = out_dir / f"{ts}_card{slot + 1}.png"
            cv2.imwrite(str(path), roi)


def setup_logging(*, verbose: bool, log_file: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DZPPQ card pick recommender (MuMu ADB + OCR)")
    parser.add_argument(
        "--connect",
        action="store_true",
        help="Pre-connect MuMu ADB at startup (device check still deferred until 开始推荐)",
    )
    parser.add_argument(
        "--no-auto-connect",
        action="store_true",
        help="Disable auto adb connect before screenshot",
    )
    parser.add_argument(
        "--mumu-port",
        type=int,
        default=16384,
        help="MuMu ADB TCP port for auto connect (default: 16384)",
    )
    parser.add_argument("--serial", type=str, default=None, help="Preferred adb device serial")
    parser.add_argument("--adb-bin", type=str, default=None, help="Path to adb executable")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to match_latest.db (default: auto-detect data/match_latest.db)",
    )
    parser.add_argument(
        "--data-json",
        type=Path,
        default=None,
        help="Fallback path to latest_meta_analysis.json when DB is unavailable",
    )
    parser.add_argument(
        "--save-debug-roi",
        type=Path,
        default=None,
        help="Directory to save cropped card ROIs for debugging",
    )
    parser.add_argument(
        "--skip-resolution-check",
        action="store_true",
        help="Skip wm size / screenshot resolution validation",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Optional log file path (default: logs/card_pick_recommender.log next to exe)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser


def adb_options_from_args(args: argparse.Namespace) -> AdbRuntimeOptions:
    return AdbRuntimeOptions(
        adb_bin=args.adb_bin,
        serial=args.serial,
        auto_connect=not args.no_auto_connect,
        mumu_port=args.mumu_port,
        skip_resolution_check=args.skip_resolution_check,
        connect_at_start=args.connect,
    )


def load_card_stats(
    args: argparse.Namespace,
) -> tuple[CardStatsIndex, Path, str]:
    """Load recommender stats from DB, falling back to JSON when needed."""
    import sqlite3

    db_error: Exception | None = None
    try:
        db_path = resolve_match_db(args.db)
        logger.info("Loading card stats from DB: %s", db_path)
        return CardStatsIndex.from_db_path(db_path), db_path, "db"
    except FileNotFoundError as exc:
        db_error = exc
        logger.warning("Match DB unavailable, will try JSON fallback: %s", exc)
    except (sqlite3.Error, ValueError) as exc:
        db_error = exc
        logger.exception("Failed to load card stats from DB")
        if args.db is not None:
            message = f"无法读取数据库: {exc}"
            if is_frozen():
                messagebox.showerror("数据库错误", message)
            raise SystemExit(message) from exc

    try:
        meta_json = resolve_meta_json(args.data_json)
    except FileNotFoundError as exc:
        if db_error is not None:
            message = f"{db_error}\nJSON fallback also unavailable: {exc}"
        else:
            message = str(exc)
        if is_frozen():
            messagebox.showerror("缺少数据文件", message)
            raise SystemExit(message) from exc
        raise SystemExit(message) from exc

    logger.warning("Using JSON fallback for card stats: %s", meta_json)
    return CardStatsIndex.from_json_path(meta_json), meta_json, "json"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log_file = args.log_file
    if log_file is None and is_frozen():
        log_file = default_log_dir() / "card_pick_recommender.log"
    setup_logging(verbose=args.verbose, log_file=log_file)

    try:
        stats, data_path, data_mode = load_card_stats(args)
    except SystemExit:
        return 1

    adb_options = adb_options_from_args(args)
    cli_adb_bin = args.adb_bin
    adb_bin_path, adb_source = resolve_adb_bin_with_source(cli_adb_bin)
    if adb_bin_path:
        adb_options.adb_bin = adb_bin_path
    adb = build_adb_client(adb_options, verbose=args.verbose)

    if adb_options.connect_at_start and adb_bin_path:
        try:
            adb.connect(host=adb_options.mumu_host, port=adb_options.mumu_port)
            logger.info("Startup pre-connect completed")
        except Exception as exc:
            logger.warning("Startup pre-connect failed (will retry on 开始推荐): %s", exc)

    ocr = OcrHelper(use_cls=False)
    ocr.start_warmup_async()

    app = CardPickRecommenderApp(
        adb=adb,
        adb_options=adb_options,
        ocr=ocr,
        stats=stats,
        data_path=data_path,
        data_mode=data_mode,
        adb_bin_path=adb_bin_path,
        adb_source=adb_source,
        adb_config_locked=cli_adb_bin is not None,
        save_debug_roi=args.save_debug_roi,
    )
    logger.info(
        "Card stats loaded from %s (%s, matches=%s, cards=%s)",
        data_path,
        data_mode,
        stats.total_matches,
        stats.total_card_records,
    )
    logger.info("Started from %s (frozen=%s)", app_base_dir(), is_frozen())
    logger.info("%s", runtime_build_label(entry_script=Path(__file__).resolve()))
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
