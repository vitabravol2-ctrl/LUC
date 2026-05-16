import json
import logging
import os
import sys
import threading
import time
import random
from collections import deque
from enum import Enum
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import websocket
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
)

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "settings.json"
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"
SESSION_LOG = LOGS_DIR / f"luc_session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

DEFAULT_SETTINGS = {
    "DRY_RUN": True,
    "order_size": 50.0,
    "min_gap_ticks": 2,
    "max_spread_ticks": 3,
    "cooldown": 2,
    "max_inventory_shift": 300.0,
    "tick_size": 0.0001,
    "euri_poll_interval_sec": 4,
    "symbols": {"parent": "EURUSDT", "child": "EURIUSDT"},
    "history_size": 50,
    "corridor_window": 20,
    "corridor_move_ticks": 3.0,
    "corridor_center_tolerance_ticks": 1.5,
    "corridor_churn_threshold": 0.35,
    "parent_impulse_ticks": 6.0,
    "stale_after_sec": 12,
    "micro_window": 40,
    "refill_recovery_ticks": 1.0,
    "spread_compressed_ticks": 1.2,
    "spread_unstable_ticks": 4.0,
    "regime_enter_threshold": 75,
    "regime_exit_threshold": 60,
    "regime_activation_delay_sec": 6.0,
    "regime_cooldown_sec": 5.0,
    "regime_confidence_jump_log": 12,
    "paper_start_euri": 1000.0,
    "paper_start_usdt": 1000.0,
    "paper_order_size_euri": 50.0,
    "paper_dangerous_skew_pct": 35.0,
    "paper_critical_skew_pct": 55.0,
    "paper_max_hold_sec": 45.0,
    "paper_cancel_if_gap_gone_ticks": 0.6,
    "paper_min_passive_viability": 65,
    "paper_min_trap_survivability": 65,
    "paper_max_open_cycles": 1,
    "paper_min_hold_sec": 4.0,
    "paper_cycle_cooldown_sec": 7.0,
    "paper_max_cycles_per_min": 8,
    "budget_total": 2000.0,
    "budget_passive": 800.0,
    "budget_traps": 700.0,
    "budget_anchor": 500.0,
    "budget_max_one_side": 1000.0,
    "sizing_min_order_size": 10.0,
    "sizing_max_order_size": 200.0,
    "sizing_randomize": True,
    "sizing_random_factor_pct": 20.0,
}




class MarketState(str, Enum):
    WAIT = "WAIT"
    PASSIVE = "PASSIVE"
    BUY_TRAP_READY = "BUY_TRAP_READY"
    SELL_TRAP_READY = "SELL_TRAP_READY"
    CORRIDOR_STABLE = "CORRIDOR_STABLE"
    CORRIDOR_UNSTABLE = "CORRIDOR_UNSTABLE"
    PARENT_IMPULSE = "PARENT_IMPULSE"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    STALE_CHILD = "STALE_CHILD"





class Regime(str, Enum):
    IDEAL_PASSIVE = "IDEAL_PASSIVE"
    IDEAL_TRAP = "IDEAL_TRAP"
    NEUTRAL = "NEUTRAL"
    CAUTION = "CAUTION"
    DANGEROUS = "DANGEROUS"
    ESCAPE = "ESCAPE"



class PaperPositionState(str, Enum):
    FLAT = "FLAT"
    PASSIVE_BUY = "PASSIVE_BUY"
    PASSIVE_SELL = "PASSIVE_SELL"
    BUY_TRAP_ACTIVE = "BUY_TRAP_ACTIVE"
    SELL_TRAP_ACTIVE = "SELL_TRAP_ACTIVE"
    WAIT_EXIT = "WAIT_EXIT"
    ESCAPE_UNLOAD = "ESCAPE_UNLOAD"

class CycleState(str, Enum):
    OPEN = "OPEN"
    ACTIVE = "ACTIVE"
    WAIT_EXIT = "WAIT_EXIT"
    CLOSED = "CLOSED"
    UNLOADED = "UNLOADED"
    FAILED = "FAILED"

class BasicSettingsDialog(QDialog):
    def __init__(self, settings: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LUC Базовые настройки")
        self.setModal(True)
        self.resize(520, 620)

        root = QVBoxLayout(self)

        self.dry_run = QCheckBox()
        self.dry_run.setChecked(bool(settings.get("DRY_RUN", True)))

        self.order_size = QDoubleSpinBox()
        self.order_size.setRange(0.0, 1_000_000.0)
        self.order_size.setDecimals(4)
        self.order_size.setValue(float(settings.get("order_size", 50.0)))

        self.min_gap_ticks = QSpinBox()
        self.min_gap_ticks.setRange(0, 10_000)
        self.min_gap_ticks.setValue(int(settings.get("min_gap_ticks", 2)))

        self.max_spread_ticks = QSpinBox()
        self.max_spread_ticks.setRange(1, 10_000)
        self.max_spread_ticks.setValue(int(settings.get("max_spread_ticks", 3)))

        self.budget_total = QDoubleSpinBox()
        self.budget_total.setRange(0.0, 10_000_000.0)
        self.budget_total.setDecimals(2)
        self.budget_total.setValue(float(settings.get("budget_total", 2000.0)))

        self.budget_max_one_side = QDoubleSpinBox()
        self.budget_max_one_side.setRange(0.0, 10_000_000.0)
        self.budget_max_one_side.setDecimals(2)
        self.budget_max_one_side.setValue(float(settings.get("budget_max_one_side", 1000.0)))

        self.sizing_min_order_size = QDoubleSpinBox()
        self.sizing_min_order_size.setRange(0.0, 1_000_000.0)
        self.sizing_min_order_size.setDecimals(4)
        self.sizing_min_order_size.setValue(float(settings.get("sizing_min_order_size", 10.0)))

        self.sizing_max_order_size = QDoubleSpinBox()
        self.sizing_max_order_size.setRange(0.0, 1_000_000.0)
        self.sizing_max_order_size.setDecimals(4)
        self.sizing_max_order_size.setValue(float(settings.get("sizing_max_order_size", 200.0)))

        self.sizing_randomize = QCheckBox()
        self.sizing_randomize.setChecked(bool(settings.get("sizing_randomize", True)))

        self.sizing_random_factor_pct = QDoubleSpinBox()
        self.sizing_random_factor_pct.setRange(0.0, 100.0)
        self.sizing_random_factor_pct.setDecimals(1)
        self.sizing_random_factor_pct.setValue(float(settings.get("sizing_random_factor_pct", 20.0)))
        self.paper_min_hold_sec = QDoubleSpinBox()
        self.paper_min_hold_sec.setRange(0.0, 10_000.0)
        self.paper_min_hold_sec.setValue(float(settings.get("paper_min_hold_sec", 4.0)))
        self.paper_cycle_cooldown_sec = QDoubleSpinBox()
        self.paper_cycle_cooldown_sec.setRange(0.0, 10_000.0)
        self.paper_cycle_cooldown_sec.setValue(float(settings.get("paper_cycle_cooldown_sec", 7.0)))
        self.paper_max_cycles_per_min = QSpinBox()
        self.paper_max_cycles_per_min.setRange(1, 10_000)
        self.paper_max_cycles_per_min.setValue(int(settings.get("paper_max_cycles_per_min", 8)))

        sections = [
            ("Основное", [("Сухой режим", self.dry_run), ("Размер ордера", self.order_size), ("Мин. gap", self.min_gap_ticks), ("Макс. spread", self.max_spread_ticks)]),
            ("Бюджет", [("Общий бюджет", self.budget_total), ("Макс. в одной стороне", self.budget_max_one_side)]),
            ("Размер ставки", [("Мин. ордер", self.sizing_min_order_size), ("Макс. ордер", self.sizing_max_order_size), ("Random size", self.sizing_randomize), ("Random %", self.sizing_random_factor_pct)]),
            ("Paper", [("Min hold", self.paper_min_hold_sec), ("Cooldown", self.paper_cycle_cooldown_sec), ("Max cycles/min", self.paper_max_cycles_per_min)]),
        ]
        for title, rows in sections:
            box = QGroupBox(title)
            form = QFormLayout(box)
            for name, widget in rows:
                form.addRow(name, widget)
            root.addWidget(box)

        buttons = QHBoxLayout()
        apply_btn = QPushButton("Применить")
        cancel_btn = QPushButton("Отмена")
        apply_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(apply_btn)
        buttons.addWidget(cancel_btn)
        root.addLayout(buttons)

    def values(self) -> dict[str, Any]:
        return {
            "DRY_RUN": self.dry_run.isChecked(),
            "order_size": self.order_size.value(),
            "min_gap_ticks": self.min_gap_ticks.value(),
            "max_spread_ticks": self.max_spread_ticks.value(),
            "budget_total": self.budget_total.value(),
            "budget_max_one_side": self.budget_max_one_side.value(),
            "sizing_min_order_size": self.sizing_min_order_size.value(),
            "sizing_max_order_size": self.sizing_max_order_size.value(),
            "sizing_randomize": self.sizing_randomize.isChecked(),
            "sizing_random_factor_pct": self.sizing_random_factor_pct.value(),
            "paper_min_hold_sec": self.paper_min_hold_sec.value(),
            "paper_cycle_cooldown_sec": self.paper_cycle_cooldown_sec.value(),
            "paper_max_cycles_per_min": self.paper_max_cycles_per_min.value(),
        }


class JsonSettingsDialog(QDialog):
    def __init__(self, settings: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LUC Full JSON Settings")
        self.resize(720, 520)

        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(json.dumps(settings, indent=2, ensure_ascii=False))
        layout.addWidget(self.editor)

        row = QHBoxLayout()
        import_btn = QPushButton("Import")
        export_btn = QPushButton("Export")
        apply_btn = QPushButton("Apply")
        cancel_btn = QPushButton("Cancel")
        row.addWidget(import_btn)
        row.addWidget(export_btn)
        row.addStretch(1)
        row.addWidget(apply_btn)
        row.addWidget(cancel_btn)
        layout.addLayout(row)

        import_btn.clicked.connect(self.import_json)
        export_btn.clicked.connect(self.export_json)
        apply_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

    def import_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import JSON", str(BASE_DIR), "JSON (*.json)")
        if not path:
            return
        try:
            self.editor.setPlainText(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            QMessageBox.critical(self, "Import error", str(exc))

    def export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export JSON", str(BASE_DIR / "settings_export.json"), "JSON (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")
        except Exception as exc:
            QMessageBox.critical(self, "Export error", str(exc))

    def values(self) -> dict[str, Any]:
        return json.loads(self.editor.toPlainText())


class LUCWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LUC — EURIUSDT Equilibrium Corridor Harvester")
        self.resize(1280, 840)

        self.settings = self.load_settings()

        self.parent_bid = 0.0
        self.parent_ask = 0.0
        self.child_bid = 0.0
        self.child_ask = 0.0

        self.ws_status = "DISCONNECTED"
        self.http_status = "IDLE"
        self.app_status = "RUNNING"
        self.mode = "STOPPED"
        self.paper_engine_enabled = False
        self.paper_entry_block_reason = "STOPPED"

        self.log_buffer: deque[str] = deque(maxlen=300)
        history_size = int(self.settings.get("history_size", 50))
        self.parent_mid_hist: deque[float] = deque(maxlen=history_size)
        self.child_mid_hist: deque[float] = deque(maxlen=history_size)
        self.gap_ticks_hist: deque[float] = deque(maxlen=history_size)
        micro_window = int(self.settings.get("micro_window", 40))
        self.spread_ticks_hist: deque[float] = deque(maxlen=micro_window)
        self.refill_timings_hist: deque[float] = deque(maxlen=micro_window)
        self.center_dev_hist: deque[float] = deque(maxlen=micro_window)
        self.corridor_age_hist: deque[int] = deque(maxlen=micro_window)
        self.state_age_hist: deque[int] = deque(maxlen=micro_window)
        self._refill_drop_started_at: float | None = None
        self._last_gap_sign: int = 0
        self._state_duration = 0
        self._last_micro_log: dict[str, str] = {}
        self.current_regime = Regime.NEUTRAL
        self._regime_candidate = Regime.NEUTRAL
        self._regime_candidate_since = time.time()
        self._regime_since = time.time()
        self._regime_cooldown_until = 0.0
        self._regime_confidence = 50
        self._regime_transition_count = 0
        self._regime_last_transition = "INIT -> NEUTRAL"
        self._regime_confidence_bucket = 5
        self.last_child_update_ts = 0.0
        self.current_state = MarketState.WAIT
        self.corridor_state = MarketState.WAIT
        self.parent_state = MarketState.WAIT
        self.trap_readiness = MarketState.WAIT
        self.stale_state = MarketState.WAIT
        self.stop_ws = threading.Event()
        self.ws_thread: threading.Thread | None = None

        self.paper_state = PaperPositionState.FLAT
        self.paper_euri = float(self.settings.get("paper_start_euri", 1000.0))
        self.paper_usdt = float(self.settings.get("paper_start_usdt", 1000.0))
        self.paper_realized_pnl = 0.0
        self.paper_entry_price = 0.0
        self.paper_entry_ts = 0.0
        self.paper_cycle_side = "NONE"
        self.paper_pending_exit_price = 0.0
        self.paper_trapped_euri = 0.0
        self.paper_cycles = 0
        self.paper_wins = 0
        self.paper_losses = 0
        self.paper_total_ticks = 0.0
        self.paper_total_hold_sec = 0.0
        self.paper_trap_attempts = 0
        self.paper_trap_success = 0
        self.paper_last_cycle = "-"
        self._paper_last_log: dict[str, float] = {}
        self.paper_lots: deque[dict[str, Any]] = deque()
        self.paper_ledger: deque[dict[str, Any]] = deque(maxlen=300)
        self.paper_cycle_state = CycleState.CLOSED
        self.paper_cycle_open_ts = 0.0
        self.paper_cycle_mae = 0.0
        self.paper_cycle_partial = "NONE"
        self.paper_trap_open_duration = 0.0
        self.paper_trap_mae = 0.0
        self.paper_trap_decay = 0.0
        self.paper_trap_regime_changes = 0
        self.paper_trap_survived = "N/A"
        self.paper_avg_recycle_pnl = 0.0
        self.paper_avg_trap_pnl = 0.0
        self.paper_recycle_count = 0
        self.paper_trap_close_count = 0
        self.paper_passive_gate = 0
        self.paper_cycle_source = "NONE"
        self.paper_max_open_cycles = max(1, int(self.settings.get("paper_max_open_cycles", 1)))
        self.euri_snapshot_id = 0
        self.last_entry_snapshot_id = -1
        self.last_exit_snapshot_id = -1
        self.paper_cycle_entry_snapshot_id = -1
        self.paper_cycle_snapshots = 0
        self.paper_last_close_ts = 0.0
        self.paper_fill_delay_left = 0
        self.pending_paper_entry: dict[str, Any] | None = None
        self._paper_step_last_snapshot_id = -1
        self._sizing_random_by_snapshot: dict[int, float] = {}
        self.paper_overtrade_block_until = 0.0
        self.paper_fill_quality = 0
        self.paper_cycle_timestamps: deque[float] = deque(maxlen=120)
        self._last_warn_log: dict[str, float] = {}
        self.current_sizing: dict[str, Any] = {}
        self._last_sizing_logged: dict[str, float] = {}

        self.setup_logger()
        self.build_ui()
        self.apply_theme()

        self.http_timer = QTimer(self)
        self.http_timer.timeout.connect(self.poll_euri)
        self.http_timer.start(max(1000, int(float(self.settings.get("euri_poll_interval_sec", 4)) * 1000)))

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.refresh_view)
        self.ui_timer.start(500)

        self.start_parent_ws()
        self.log("LUC started")

    def setup_logger(self) -> None:
        LOGS_DIR.mkdir(exist_ok=True)
        DATA_DIR.mkdir(exist_ok=True)
        self.logger = logging.getLogger("luc")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        handler = logging.FileHandler(SESSION_LOG, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        self.logger.addHandler(handler)

    def build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        top = QHBoxLayout()
        self.start_btn = QPushButton("START")
        self.stop_btn = QPushButton("STOP")
        self.reset_btn = QPushButton("RESET PAPER")
        self.settings_btn = QPushButton("SETTINGS")
        self.full_json_btn = QPushButton("FULL JSON")
        self.start_btn.clicked.connect(self.start_paper_engine)
        self.stop_btn.clicked.connect(self.stop_paper_engine)
        self.reset_btn.clicked.connect(self.reset_paper_engine)
        self.settings_btn.clicked.connect(self.open_basic_settings)
        self.full_json_btn.clicked.connect(self.open_json_settings)
        for btn in (self.start_btn, self.stop_btn, self.reset_btn, self.settings_btn, self.full_json_btn):
            btn.setMinimumHeight(34)
            top.addWidget(btn)
        self.lbl_app = QLabel()
        self.lbl_ws = QLabel()
        self.lbl_http = QLabel()
        self.lbl_mode = QLabel()
        self.lbl_symbols = QLabel(f"PAIR: {self.settings['symbols']['parent']} → {self.settings['symbols']['child']}")
        for w in (self.lbl_app, self.lbl_ws, self.lbl_http, self.lbl_mode, self.lbl_symbols):
            top.addWidget(w)
        top.addStretch(1)
        layout.addLayout(top)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.market_labels = self.make_panel(grid, 0, 0, "MARKET", [
            "EUR bid", "EUR ask", "EUR mid", "EURI bid", "EURI ask", "EURI mid", "EURI spread", "fair gap ticks", "child stale sec",
        ])
        self.signal_labels = self.make_panel(grid, 1, 0, "SIGNAL", ["current state", "regime", "trap readiness"])
        self.inventory_labels = self.make_panel(grid, 2, 0, "INVENTORY / PNL", [
            "EURI", "USDT", "total value", "realized pnl", "unrealized pnl", "skew", "open lots",
        ])
        self.engine_labels = self.make_panel(grid, 0, 1, "PAPER ENGINE", [
            "engine status", "paper state", "active cycle", "entry block reason", "fill quality", "cooldown", "min hold", "cycles/min",
        ])
        self.session_labels = self.make_panel(grid, 1, 1, "SESSION", [
            "cycles", "wins", "losses", "winrate", "avg pnl", "avg ticks", "avg hold",
        ])
        self.sizing_labels = self.make_panel(grid, 2, 1, "SIZING", [
            "passive size", "trap size", "anchor layer 1", "anchor layer 2", "anchor layer 3",
            "budget used %", "random factor",
        ])
        self.ledger_labels = self.make_panel(grid, 3, 1, "LEDGER LAST EVENTS", [
            "event 1", "event 2", "event 3", "event 4", "event 5",
        ])
        layout.addLayout(grid)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(300)
        self.log_box.setMinimumHeight(170)
        layout.addWidget(self.log_box, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 2)
        grid.setRowStretch(1, 3)
        grid.setRowStretch(2, 2)
        grid.setRowStretch(3, 2)
        grid.setRowStretch(4, 0)

    def start_paper_engine(self) -> None:
        if self.paper_engine_enabled:
            return
        self.paper_engine_enabled = True
        self.mode = "PAPER"
        self.paper_entry_block_reason = "waiting_new_snapshot"
        self.log("[CONTROL] paper engine START")

    def stop_paper_engine(self) -> None:
        self.paper_engine_enabled = False
        self.mode = "STOPPED"
        self.paper_entry_block_reason = "STOPPED"
        self.log("[CONTROL] paper engine STOP (no new entries)")

    def reset_paper_engine(self) -> None:
        self.paper_state = PaperPositionState.FLAT
        self.paper_euri = float(self.settings.get("paper_start_euri", 1000.0))
        self.paper_usdt = float(self.settings.get("paper_start_usdt", 1000.0))
        self.paper_realized_pnl = 0.0
        self.paper_cycles = 0
        self.paper_wins = 0
        self.paper_losses = 0
        self.paper_total_ticks = 0.0
        self.paper_total_hold_sec = 0.0
        self.paper_last_cycle = "-"
        self.paper_lots.clear()
        self.paper_ledger.clear()
        self.paper_cycle_state = CycleState.CLOSED
        self.paper_cycle_side = "NONE"
        self.paper_cycle_source = "NONE"
        self.paper_trapped_euri = 0.0
        self.paper_entry_ts = 0.0
        self.paper_cycle_timestamps.clear()
        self.pending_paper_entry = None
        self.paper_fill_delay_left = 0
        self._sizing_random_by_snapshot.clear()
        self.log("[CONTROL] paper engine RESET")

    def make_panel(self, grid: QGridLayout, row: int, col: int, title: str, fields: list[str]) -> dict[str, QLabel]:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setVerticalSpacing(3)
        box.setMinimumHeight(170 if row in (0, 1) else 140)
        labels: dict[str, QLabel] = {}
        for name in fields:
            val = QLabel("-")
            form.addRow(name + ":", val)
            labels[name] = val
        grid.addWidget(box, row, col)
        return labels

    def apply_theme(self) -> None:
        self.setStyleSheet("""
            QWidget { background-color: #0f1115; color: #d4d7de; font-family: Consolas, Menlo, monospace; font-size: 12px; }
            QGroupBox { border: 1px solid #2b2f3a; margin-top: 6px; padding-top: 6px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #8ab4f8; }
            QPushButton { background: #1f2530; border: 1px solid #3a4252; padding: 3px 8px; }
            QPushButton:hover { background: #263142; }
            QPlainTextEdit { border: 1px solid #2b2f3a; background: #0b0d12; }
        """)

    def load_settings(self) -> dict[str, Any]:
        if not SETTINGS_PATH.exists():
            SETTINGS_PATH.write_text(json.dumps(DEFAULT_SETTINGS, indent=2, ensure_ascii=False), encoding="utf-8")
            return DEFAULT_SETTINGS.copy()
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            loaded = DEFAULT_SETTINGS.copy()
        merged = DEFAULT_SETTINGS.copy()
        merged.update(loaded)
        return merged

    def save_settings(self) -> None:
        SETTINGS_PATH.write_text(json.dumps(self.settings, indent=2, ensure_ascii=False), encoding="utf-8")

    def open_basic_settings(self) -> None:
        dlg = BasicSettingsDialog(self.settings, self)
        if dlg.exec() != QDialog.Accepted:
            return
        self.settings.update(dlg.values())
        self.mode = "DRY_RUN" if self.settings.get("DRY_RUN", True) else "LIVE_DISABLED"
        self.save_settings()
        self.log("Basic settings applied")

    def open_json_settings(self) -> None:
        dlg = JsonSettingsDialog(self.settings, self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.settings = dlg.values()
            self.mode = "DRY_RUN" if self.settings.get("DRY_RUN", True) else "LIVE_DISABLED"
            self.save_settings()
            self.log("JSON settings applied")
        except Exception as exc:
            QMessageBox.critical(self, "Settings error", f"Invalid JSON: {exc}")

    def start_parent_ws(self) -> None:
        if self.ws_thread and self.ws_thread.is_alive():
            return

        def run_ws() -> None:
            symbol = self.settings["symbols"]["parent"].lower()
            url = f"wss://stream.binance.com:9443/ws/{symbol}@bookTicker"
            self.ws_status = "CONNECTING"
            while not self.stop_ws.is_set():
                try:
                    ws = websocket.WebSocketApp(
                        url,
                        on_message=self.on_parent_message,
                        on_open=lambda _ws: self._set_ws_status("CONNECTED"),
                        on_close=lambda _ws, *_: self._set_ws_status("DISCONNECTED"),
                        on_error=lambda _ws, err: self.log(f"WS error: {err}"),
                    )
                    ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as exc:
                    self.log(f"WS reconnect error: {exc}")
                    self._set_ws_status("ERROR")
                time.sleep(2)

        self.ws_thread = threading.Thread(target=run_ws, daemon=True)
        self.ws_thread.start()

    def _set_ws_status(self, status: str) -> None:
        self.ws_status = status

    def on_parent_message(self, _ws: Any, message: str) -> None:
        try:
            payload = json.loads(message)
            self.parent_bid = float(payload.get("b", 0.0))
            self.parent_ask = float(payload.get("a", 0.0))
        except Exception as exc:
            self.log(f"WS parse error: {exc}")

    def poll_euri(self) -> None:
        try:
            self.http_status = "POLLING"
            symbol = self.settings["symbols"]["child"]
            url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={symbol}"
            resp = requests.get(url, timeout=4)
            resp.raise_for_status()
            payload = resp.json()
            self.child_bid = float(payload.get("bidPrice", 0.0))
            self.child_ask = float(payload.get("askPrice", 0.0))
            self.euri_snapshot_id += 1
            if self.paper_state != PaperPositionState.FLAT and self.paper_cycle_entry_snapshot_id >= 0:
                self.paper_cycle_snapshots = max(1, self.euri_snapshot_id - self.paper_cycle_entry_snapshot_id + 1)
            self.http_status = "OK"
            self.last_child_update_ts = time.time()
        except Exception as exc:
            self.http_status = "ERROR"
            self.log(f"HTTP error: {exc}")

    def _direction(self, values: deque[float], tick_size: float) -> str:
        if len(values) < 2 or not tick_size:
            return "FLAT"
        delta_ticks = (values[-1] - values[0]) / tick_size
        if delta_ticks > 0.5:
            return "UP"
        if delta_ticks < -0.5:
            return "DOWN"
        return "FLAT"

    def _state_color(self, state: MarketState) -> str:
        if state in (MarketState.PASSIVE, MarketState.BUY_TRAP_READY):
            return "#7CFC8A"
        if state in (MarketState.SELL_TRAP_READY,):
            return "#FF6E6E"
        if state in (MarketState.CORRIDOR_STABLE,):
            return "#6FA8FF"
        if state in (MarketState.PARENT_IMPULSE, MarketState.STALE_CHILD):
            return "#FFB366"
        if state in (MarketState.SPREAD_TOO_WIDE,):
            return "#FF6E6E"
        return "#b7bdc8"

    def _set_state_label(self, name: str, state: MarketState) -> None:
        label = self.signal_labels[name]
        label.setText(state.value)
        label.setStyleSheet(f"color: {self._state_color(state)}; font-weight: bold;")

    def _log_state_change(self, state: MarketState) -> None:
        if state != self.current_state:
            self.log(f"[STATE] {state.value}")
            self.current_state = state

    def _micro_color(self, level: str) -> str:
        level = level.upper()
        if level in {"IDEAL", "HIGH", "GREEN", "SURVIVABLE", "CALM", "COMPRESSED", "STABLE", "GOOD"}:
            return "#7CFC8A"
        if level in {"NORMAL", "BLUE", "RECYCLING"}:
            return "#6FA8FF"
        if level in {"CAUTION", "MEDIUM", "ORANGE", "EXPANDING"}:
            return "#FFB366"
        if level in {"DANGEROUS", "LOW", "RED", "UNSTABLE", "BROKEN", "AGGRESSIVE"}:
            return "#FF6E6E"
        return "#b7bdc8"

    def _regime_color(self, regime: Regime) -> str:
        if regime in {Regime.IDEAL_PASSIVE, Regime.IDEAL_TRAP}:
            return "#7CFC8A"
        if regime == Regime.NEUTRAL:
            return "#6FA8FF"
        if regime == Regime.CAUTION:
            return "#FFB366"
        return "#FF6E6E"

    def _set_regime_label(self, name: str, value: str, regime: Regime | None = None) -> None:
        label = self.signal_labels[name]
        color = self._regime_color(regime) if regime else "#b7bdc8"
        label.setText(value)
        label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _pick_regime_candidate(self, equilibrium_score: int, passive_viability: int, trap_survivability: int, corridor_stable: bool, spread_state: str, refill_strength: str, churn_quality: str, parent_impulse: bool, stale_state: bool, mean_reversion_quality: int) -> tuple[Regime, int]:
        ideal_passive_score = int(equilibrium_score * 0.35 + passive_viability * 0.45 + (100 if refill_strength == "WEAK_REFILL" else 55 if refill_strength == "NORMAL_REFILL" else 20) * 0.20)
        ideal_trap_score = int(trap_survivability * 0.50 + (100 if corridor_stable else 45) * 0.30 + mean_reversion_quality * 0.20)
        danger_score = int((100 if parent_impulse else 40) * 0.30 + (100 if spread_state == "UNSTABLE" else 55 if spread_state == "EXPANDING" else 20) * 0.30 + (100 if churn_quality == "CHAOTIC" else 45) * 0.20 + (100 if stale_state else 35) * 0.20)
        if stale_state and (spread_state == "UNSTABLE" or mean_reversion_quality < 35):
            return Regime.ESCAPE, max(80, danger_score)
        if ideal_passive_score >= ideal_trap_score and ideal_passive_score >= 70:
            return Regime.IDEAL_PASSIVE, ideal_passive_score
        if ideal_trap_score >= 70:
            return Regime.IDEAL_TRAP, ideal_trap_score
        if danger_score >= 70:
            return Regime.DANGEROUS, danger_score
        if danger_score >= 55 or spread_state in {"EXPANDING", "UNSTABLE"}:
            return Regime.CAUTION, max(danger_score, 55)
        return Regime.NEUTRAL, int((equilibrium_score + mean_reversion_quality) / 2)

    def _update_regime(self, candidate: Regime, score: int, now_ts: float, stability: int) -> None:
        enter_thr = int(self.settings.get("regime_enter_threshold", 75))
        exit_thr = int(self.settings.get("regime_exit_threshold", 60))
        hold_sec = float(self.settings.get("regime_activation_delay_sec", 6.0))
        cooldown_sec = float(self.settings.get("regime_cooldown_sec", 5.0))
        if candidate != self._regime_candidate:
            self._regime_candidate = candidate
            self._regime_candidate_since = now_ts
        if now_ts < self._regime_cooldown_until and candidate != self.current_regime:
            return
        if candidate == self.current_regime:
            return
        threshold = enter_thr
        if self.current_regime in {Regime.IDEAL_PASSIVE, Regime.IDEAL_TRAP} and candidate in {Regime.NEUTRAL, Regime.CAUTION, Regime.DANGEROUS}:
            threshold = exit_thr
        if score < threshold:
            return
        if now_ts - self._regime_candidate_since < hold_sec:
            return
        prev = self.current_regime
        self.current_regime = candidate
        self._regime_since = now_ts
        self._regime_transition_count += 1
        self._regime_cooldown_until = now_ts + cooldown_sec
        self._regime_last_transition = f"{prev.value} -> {candidate.value}"
        self.log(f"[REGIME] transition {self._regime_last_transition}")
        self.log(f"[REGIME] {candidate.value} activated confidence={self._regime_confidence}")
        if abs(stability - 50) >= 30:
            self.log(f"[REGIME] {'stable' if stability >= 70 else 'violent'} transition")

    def _set_micro_label(self, name: str, value: str, level: str) -> None:
        label = self.signal_labels[name]
        label.setText(value)
        label.setStyleSheet(f"color: {self._micro_color(level)}; font-weight: bold;")

    def _log_micro_change(self, key: str, message: str) -> None:
        prev = self._last_micro_log.get(key)
        if prev != message:
            self._last_micro_log[key] = message
            self.log(f"[MICRO] {message}")


    def _paper_log(self, key: str, message: str, cooldown_sec: float = 4.0) -> None:
        now = time.time()
        if now - self._paper_last_log.get(key, 0.0) >= cooldown_sec:
            self._paper_last_log[key] = now
            self.log(message)

    def _warn_once(self, key: str, message: str, cooldown_sec: float = 12.0) -> None:
        now = time.time()
        if now - self._last_warn_log.get(key, 0.0) >= cooldown_sec:
            self._last_warn_log[key] = now
            self.log(message)

    def _inventory_metrics(self, mark_price: float) -> tuple[float, float, float, float]:
        total_value = self.paper_usdt + self.paper_euri * mark_price
        skew = 0.0 if total_value <= 0 else ((self.paper_euri * mark_price) - self.paper_usdt) / total_value * 100
        start_value = float(self.settings.get("paper_start_usdt", 1000.0)) + float(self.settings.get("paper_start_euri", 1000.0)) * mark_price
        unrealized = sum((mark_price - lot["entry_price"]) * lot["qty"] for lot in self.paper_lots if lot["side"] == "BUY")
        unrealized += sum((lot["entry_price"] - mark_price) * lot["qty"] for lot in self.paper_lots if lot["side"] == "SELL")
        return total_value, skew, start_value, unrealized

    def _ledger_add(self, event_type: str, side: str, qty: float, price: float, realized_pnl: float, now_ts: float) -> None:
        self.paper_ledger.appendleft({
            "timestamp": datetime.utcfromtimestamp(now_ts).strftime("%H:%M:%S"),
            "type": event_type, "side": side, "qty": qty, "price": price, "realized_pnl": realized_pnl,
            "inventory_after": self.paper_euri, "regime": self.current_regime.value, "paper_state": self.paper_state.value,
        })

    def _open_lot(self, *, side: str, qty: float, price: float, now_ts: float, source: str) -> bool:
        if qty <= 1e-9:
            self._warn_once("open_zero_qty", "[WARN] open_lot blocked: zero qty")
            return False
        if len(self.paper_lots) >= self.paper_max_open_cycles:
            self._warn_once("open_lot_blocked", "[WARN] open_lot blocked: active cycle or max_open_cycles reached")
            return False
        self.paper_lots.append({"side": side, "qty": qty, "entry_price": price, "entry_time": now_ts, "source": source, "state": "OPEN"})
        self._ledger_add("OPEN_LOT", side, qty, price, 0.0, now_ts)
        self.log(f"[LEDGER] OPEN_LOT {side} qty={qty:.2f} price={price:.4f}")
        return True

    def _can_open_new_cycle(self) -> bool:
        if not (self.paper_state == PaperPositionState.FLAT and len(self.paper_lots) < self.paper_max_open_cycles):
            return False
        if self.euri_snapshot_id <= self.last_entry_snapshot_id:
            self._paper_log("entry_same_snapshot", "[PAPER] entry blocked same_snapshot")
            return False
        now_ts = time.time()
        cooldown_sec = float(self.settings.get("paper_cycle_cooldown_sec", 7.0))
        if now_ts < self.paper_last_close_ts + cooldown_sec:
            self._paper_log("cooldown", "[PAPER] cooldown active")
            return False
        if now_ts < self.paper_overtrade_block_until:
            self._paper_log("overtrade", "[WARN] paper overtrade guard active")
            return False
        return True

    def _paper_fill_delay_snapshots(self, spread_ticks: float, passive_viability: int, refill_strength: str, churn_quality: str, is_fresh: bool) -> int:
        if refill_strength == "AGGRESSIVE_REFILL":
            return -1
        if spread_ticks > float(self.settings.get("max_spread_ticks", 3)):
            return -1
        delay = 2
        if spread_ticks <= 1.2 and passive_viability >= 85 and churn_quality in {"CALM", "NORMAL"} and refill_strength == "WEAK_REFILL" and is_fresh:
            delay = 1
        elif passive_viability < 70 or churn_quality == "CHAOTIC" or not is_fresh:
            delay = 3
        if spread_ticks > 2.2:
            delay += 1
        return max(1, min(4, delay))

    def _close_lots(self, *, side: str, qty: float, price: float, now_ts: float, event_type: str) -> tuple[float, float]:
        if qty <= 1e-9:
            self._warn_once("zero_close_qty", "[WARN] zero close qty blocked")
            return 0.0, 0.0
        remain = qty
        realized = 0.0
        closed_qty = 0.0
        close_side = "SELL" if side == "BUY" else "BUY"
        for lot in list(self.paper_lots):
            if remain <= 1e-9 or lot["side"] != side or lot["qty"] <= 0:
                continue
            chunk = min(remain, lot["qty"])
            pnl = (price - lot["entry_price"]) * chunk if side == "BUY" else (lot["entry_price"] - price) * chunk
            realized += pnl
            lot["qty"] -= chunk
            remain -= chunk
            closed_qty += chunk
            lot["state"] = "CLOSED" if lot["qty"] <= 1e-9 else "PARTIAL"
        self.paper_lots = deque([x for x in self.paper_lots if x["qty"] > 1e-9])
        self.paper_realized_pnl += realized
        if closed_qty <= 1e-9:
            self._warn_once("zero_close_qty", "[WARN] zero close qty blocked")
            return 0.0, 0.0
        self._ledger_add(event_type, close_side, closed_qty, price, realized, now_ts)
        self.log(f"[LEDGER] {event_type} {close_side} qty={closed_qty:.2f} price={price:.4f} pnl={realized:+.4f}")
        return realized, closed_qty

    def _finalize_cycle_close(self, *, now_ts: float, close_price: float, side: str, source_state: PaperPositionState) -> None:
        tracked_qty = self.paper_trapped_euri
        pnl, closed_qty = self._close_lots(side=side, qty=tracked_qty, price=close_price, now_ts=now_ts, event_type="RECYCLE_CLOSE")
        if closed_qty <= 1e-9:
            return
        tick = float(self.settings.get("tick_size", 0.0001))
        source_name = self.paper_cycle_source
        if side == "BUY":
            self.paper_usdt += close_price * closed_qty
            self.paper_euri -= closed_qty
            ticks = (close_price - self.paper_entry_price) / tick if tick else 0.0
            direction = "LONG"
            close_side = "SELL"
        else:
            self.paper_usdt -= close_price * closed_qty
            self.paper_euri += closed_qty
            ticks = (self.paper_entry_price - close_price) / tick if tick else 0.0
            direction = "SHORT"
            close_side = "BUY"
        self.paper_cycles += 1
        self.paper_wins += 1 if pnl > 0 else 0
        self.paper_losses += 1 if pnl < 0 else 0
        self.paper_total_ticks += ticks
        self.paper_total_hold_sec += max(0.0, now_ts - self.paper_entry_ts)
        self.paper_avg_recycle_pnl = ((self.paper_avg_recycle_pnl * self.paper_recycle_count) + pnl) / max(1, self.paper_recycle_count + 1)
        self.paper_recycle_count += 1
        if source_state in {PaperPositionState.BUY_TRAP_ACTIVE, PaperPositionState.SELL_TRAP_ACTIVE}:
            self.paper_trap_success += 1
            self._paper_log("trap", "[PAPER] trap survived")
        self.paper_last_cycle = f"{direction} {ticks:+.2f}t pnl={pnl:+.4f}"
        self.paper_state = PaperPositionState.FLAT
        self.paper_cycle_state = CycleState.CLOSED
        self.paper_cycle_side = "NONE"
        self.paper_cycle_source = "NONE"
        self.paper_trapped_euri = 0.0
        self.paper_entry_ts = 0.0
        self.paper_cycle_partial = "NONE"
        self.last_exit_snapshot_id = self.euri_snapshot_id
        self.paper_last_close_ts = now_ts
        self.paper_cycle_timestamps.append(now_ts)
        self.paper_cycle_entry_snapshot_id = -1
        self.paper_cycle_snapshots = 0
        self.log(f"[PAPER] close {source_name} {close_side} qty={closed_qty:.2f} price={close_price:.4f} pnl={pnl:+.4f}")
        self._paper_log("recycle", f"[PAPER] cycle closed pnl={pnl:+.4f} ticks={ticks:+.2f}")


    def _calc_inventory_factor(self, skew: float, side: str, danger_skew: float, critical_skew: float) -> float:
        abs_skew = abs(skew)
        if abs_skew >= critical_skew:
            if (side == "BUY" and skew > 0) or (side == "SELL" and skew < 0):
                return 0.0
            return 0.35
        if abs_skew >= danger_skew:
            return 0.5
        return 1.0

    def _compute_sizing(self, *, mode: str, side: str, regime: Regime, gap_ticks: float, trap_survivability: int, skew: float, danger_skew: float, critical_skew: float) -> dict[str, float]:
        base_size = float(self.settings.get("paper_order_size_euri", self.settings.get("order_size", 50.0)))
        regime_factor_map = {Regime.IDEAL_PASSIVE: 1.0, Regime.IDEAL_TRAP: 1.2, Regime.NEUTRAL: 0.5, Regime.CAUTION: 0.25, Regime.DANGEROUS: 0.0, Regime.ESCAPE: 0.0}
        regime_factor = regime_factor_map.get(regime, 0.5)
        ag = abs(gap_ticks)
        gap_factor = 0.7 if ag < 1.5 else 1.0 if ag < 4.0 else 1.3
        mode_factor = 0.7 if mode == "PASSIVE" else 1.0 if mode in {"BUY_TRAP", "SELL_TRAP"} else 1.2
        inventory_factor = self._calc_inventory_factor(skew, side, danger_skew, critical_skew)
        trap_factor = max(0.6, min(1.2, trap_survivability / 100.0 + 0.2)) if "TRAP" in mode else 1.0
        random_pct = float(self.settings.get("sizing_random_factor_pct", 20.0)) / 100.0
        if self.settings.get("sizing_randomize", True):
            random_factor = self._sizing_random_by_snapshot.get(self.euri_snapshot_id)
            if random_factor is None:
                random_factor = random.uniform(1.0 - random_pct, 1.0 + random_pct)
                self._sizing_random_by_snapshot[self.euri_snapshot_id] = random_factor
        else:
            random_factor = 1.0
        size = base_size * regime_factor * gap_factor * mode_factor * inventory_factor * trap_factor * random_factor
        min_size = float(self.settings.get("sizing_min_order_size", 10.0))
        max_size = float(self.settings.get("sizing_max_order_size", 200.0))
        size = max(min_size, min(max_size, size))
        return {"size": size, "inventory_factor": inventory_factor, "random_factor": random_factor, "mode": mode}

    def _paper_step(self, *, now_ts: float, child_mid: float, gap_ticks: float, spread_ticks: float, parent_dir: str, regime: Regime, passive_viability: int, trap_survivability: int, refill_strength: str, churn_quality: str, child_stale_sec: float) -> None:
        self.paper_entry_block_reason = "-"
        if child_mid <= 0:
            return
        qty = float(self.settings.get("paper_order_size_euri", self.settings.get("order_size", 50.0)))
        min_gap_ticks = float(self.settings.get("min_gap_ticks", 2))
        max_spread_ticks = float(self.settings.get("max_spread_ticks", 3))
        cancel_gap = float(self.settings.get("paper_cancel_if_gap_gone_ticks", 0.6))
        max_hold_sec = float(self.settings.get("paper_max_hold_sec", 45.0))
        min_hold_sec = float(self.settings.get("paper_min_hold_sec", 4.0))
        danger_skew = float(self.settings.get("paper_dangerous_skew_pct", 35.0))
        critical_skew = float(self.settings.get("paper_critical_skew_pct", 55.0))
        max_cycles_per_min = int(self.settings.get("paper_max_cycles_per_min", 8))
        total_value, skew, start_value, unrealized = self._inventory_metrics(child_mid)
        sizing_passive = self._compute_sizing(mode="PASSIVE", side="BUY", regime=regime, gap_ticks=gap_ticks, trap_survivability=trap_survivability, skew=skew, danger_skew=danger_skew, critical_skew=critical_skew)
        sizing_trap_buy = self._compute_sizing(mode="BUY_TRAP", side="BUY", regime=regime, gap_ticks=gap_ticks, trap_survivability=trap_survivability, skew=skew, danger_skew=danger_skew, critical_skew=critical_skew)
        sizing_trap_sell = self._compute_sizing(mode="SELL_TRAP", side="SELL", regime=regime, gap_ticks=gap_ticks, trap_survivability=trap_survivability, skew=skew, danger_skew=danger_skew, critical_skew=critical_skew)
        self.current_sizing = {"passive": sizing_passive, "trap_buy": sizing_trap_buy, "trap_sell": sizing_trap_sell}
        for key, value in {
            "passive": sizing_passive["size"],
            "trap": max(sizing_trap_buy["size"], sizing_trap_sell["size"]),
            "anchor_layers": float(self.settings.get("budget_anchor", 0.0)),
        }.items():
            prev = self._last_sizing_logged.get(key)
            if prev is None or (prev > 0 and abs(value - prev) / prev >= 0.25):
                if key == "anchor_layers":
                    a1 = min(value * 0.20, float(self.settings.get("sizing_max_order_size", 200.0)))
                    a2 = min(value * 0.30, float(self.settings.get("sizing_max_order_size", 200.0)))
                    a3 = min(value * 0.50, float(self.settings.get("sizing_max_order_size", 200.0)))
                    self._paper_log("sizing_anchor", f"[SIZING] anchor_layers={a1:.2f}/{a2:.2f}/{a3:.2f}", cooldown_sec=12.0)
                else:
                    self._paper_log(f"sizing_{key}", f"[SIZING] {key}={value:.2f}", cooldown_sec=12.0)
                self._last_sizing_logged[key] = value
        self.paper_cycle_mae = min(self.paper_cycle_mae, unrealized)
        self.paper_fill_quality = max(0, min(100, int(
            (100 if child_stale_sec <= 2.5 else 45) * 0.25
            + (100 if spread_ticks <= max_spread_ticks else 35) * 0.2
            + passive_viability * 0.2
            + (85 if refill_strength == "WEAK_REFILL" else 55 if refill_strength == "NORMAL_REFILL" else 20) * 0.2
            + (95 if regime in {Regime.IDEAL_PASSIVE, Regime.IDEAL_TRAP, Regime.NEUTRAL} else 40) * 0.15
        )))
        while self.paper_cycle_timestamps and now_ts - self.paper_cycle_timestamps[0] > 60:
            self.paper_cycle_timestamps.popleft()
        if len(self.paper_cycle_timestamps) >= max_cycles_per_min:
            self.paper_overtrade_block_until = max(self.paper_overtrade_block_until, now_ts + 10.0)
            self._paper_log("overtrade", "[WARN] paper overtrade guard active", cooldown_sec=8.0)

        if abs(skew) >= danger_skew and self.paper_state == PaperPositionState.FLAT:
            self._paper_log("skew", "[PAPER] skew danger")
        if abs(skew) >= critical_skew and self.paper_state != PaperPositionState.FLAT:
            self.paper_state = PaperPositionState.ESCAPE_UNLOAD

        hold_sec = now_ts - self.paper_entry_ts if self.paper_entry_ts else 0.0
        can_exit_snapshot = self.euri_snapshot_id > self.paper_cycle_entry_snapshot_id and self.euri_snapshot_id > self.last_exit_snapshot_id
        if self.paper_state in {PaperPositionState.BUY_TRAP_ACTIVE, PaperPositionState.PASSIVE_BUY} and (self.child_ask >= self.paper_pending_exit_price > 0):
            if not can_exit_snapshot:
                return
            if hold_sec < min_hold_sec:
                self._paper_log("min_hold", "[PAPER] exit waiting min_hold")
                return
            source_state = self.paper_state
            self._finalize_cycle_close(now_ts=now_ts, close_price=self.paper_pending_exit_price, side="BUY", source_state=source_state)

        if self.paper_state in {PaperPositionState.SELL_TRAP_ACTIVE, PaperPositionState.PASSIVE_SELL} and (self.child_bid <= self.paper_pending_exit_price < 10):
            if not can_exit_snapshot:
                return
            if hold_sec < min_hold_sec:
                self._paper_log("min_hold", "[PAPER] exit waiting min_hold")
                return
            source_state = self.paper_state
            self._finalize_cycle_close(now_ts=now_ts, close_price=self.paper_pending_exit_price, side="SELL", source_state=source_state)
        elif self.paper_state in {PaperPositionState.BUY_TRAP_ACTIVE, PaperPositionState.PASSIVE_BUY, PaperPositionState.SELL_TRAP_ACTIVE, PaperPositionState.PASSIVE_SELL}:
            self._paper_log("price_touch", "[PAPER] exit waiting price_touch")

        if self.paper_state in {PaperPositionState.BUY_TRAP_ACTIVE, PaperPositionState.SELL_TRAP_ACTIVE} and (abs(gap_ticks) < cancel_gap or now_ts - self.paper_entry_ts > max_hold_sec or regime in {Regime.DANGEROUS, Regime.ESCAPE}):
            self._paper_log("unload", "[PAPER] virtual unload")
            self.paper_state = PaperPositionState.WAIT_EXIT
            self.paper_cycle_state = CycleState.UNLOADED

        if self.paper_state == PaperPositionState.WAIT_EXIT:
            self.paper_pending_exit_price = child_mid
            partial_qty = max(0.1, self.paper_trapped_euri * 0.5)
            self.paper_trapped_euri -= partial_qty
            self.paper_cycle_partial = f"PARTIAL {partial_qty:.2f}"
            if self.paper_cycle_side == "LONG":
                self.paper_usdt += child_mid * partial_qty
                self.paper_euri -= partial_qty
                self._close_lots(side="BUY", qty=partial_qty, price=child_mid, now_ts=now_ts, event_type="PARTIAL_CLOSE")
            elif self.paper_cycle_side == "SHORT":
                self.paper_usdt -= child_mid * partial_qty
                self.paper_euri += partial_qty
                self._close_lots(side="SELL", qty=partial_qty, price=child_mid, now_ts=now_ts, event_type="PARTIAL_CLOSE")
            self.log("[LEDGER] partial close")
            if self.paper_trapped_euri <= 0.1:
                self.paper_state = PaperPositionState.FLAT
                self.paper_cycle_state = CycleState.CLOSED
                self.paper_cycle_source = "NONE"

        if self.paper_state != PaperPositionState.FLAT:
            if self.paper_cycle_state in {CycleState.OPEN, CycleState.ACTIVE, CycleState.WAIT_EXIT} and len(self.paper_lots) == 0:
                self._warn_once("ghost_active_cycle", "[WARN] ghost active cycle repaired")
                self.paper_state = PaperPositionState.FLAT
                self.paper_cycle_state = CycleState.CLOSED
                self.paper_cycle_side = "NONE"
                self.paper_cycle_source = "NONE"
                self.paper_trapped_euri = 0.0
            return
        if not self.paper_engine_enabled:
            self.paper_entry_block_reason = "STOPPED"
            return
        if len(self.paper_lots) >= self.paper_max_open_cycles:
            self.paper_entry_block_reason = "max_cycles"
            self._warn_once("max_cycles", "[WARN] max_open_cycles reached")
            return
        if regime in {Regime.DANGEROUS, Regime.ESCAPE, Regime.CAUTION} or abs(skew) >= critical_skew:
            self.paper_entry_block_reason = "regime_not_allowed"
            return
        if spread_ticks > max_spread_ticks:
            self.paper_entry_block_reason = "spread_too_wide"
            return

        tick = float(self.settings.get("tick_size", 0.0001))
        min_passive = int(self.settings.get("paper_min_passive_viability", 65))
        min_trap = int(self.settings.get("paper_min_trap_survivability", 65))
        is_new_snapshot = self.euri_snapshot_id > self._paper_step_last_snapshot_id
        if is_new_snapshot:
            self._paper_step_last_snapshot_id = self.euri_snapshot_id

        if self.pending_paper_entry:
            pending = self.pending_paper_entry
            if is_new_snapshot:
                pending["delay_left"] -= 1
            self.paper_fill_delay_left = max(0, int(pending["delay_left"]))
            if not (regime == Regime.IDEAL_PASSIVE and passive_viability >= min_passive and abs(skew) < danger_skew and spread_ticks <= max_spread_ticks):
                self.paper_entry_block_reason = "pending_invalid:conditions"
                self.log("[PAPER] pending entry dropped reason=conditions")
                self.pending_paper_entry = None
                return
            if pending["delay_left"] > 0:
                self.paper_entry_block_reason = "fill_delay"
                return
            qty = float(pending["qty"])
            if self.paper_usdt < qty * self.child_bid:
                self.paper_entry_block_reason = "pending_invalid:insufficient_usdt"
                self.log("[PAPER] pending entry dropped reason=insufficient_usdt")
                self.pending_paper_entry = None
                return
            self.paper_entry_price = self.child_bid
            self.paper_usdt -= qty * self.paper_entry_price
            self.paper_euri += qty
            self.paper_pending_exit_price = self.child_ask
            self.paper_entry_ts = now_ts
            self.paper_trapped_euri = qty
            self.paper_cycle_side = "LONG"
            self.paper_state = PaperPositionState.PASSIVE_BUY
            self.paper_cycle_state = CycleState.OPEN
            self.paper_cycle_source = "PASSIVE"
            self.last_entry_snapshot_id = self.euri_snapshot_id
            self.paper_cycle_entry_snapshot_id = self.euri_snapshot_id
            self.paper_cycle_snapshots = 1
            if self._open_lot(side="BUY", qty=qty, price=self.paper_entry_price, now_ts=now_ts, source="PASSIVE"):
                self.paper_entry_block_reason = "-"
                self.log(f"[PAPER] open PASSIVE BUY qty={qty:.2f} price={self.paper_entry_price:.4f}")
            self.pending_paper_entry = None
            return

        if regime == Regime.IDEAL_PASSIVE and passive_viability >= min_passive and abs(skew) < danger_skew:
            if self.paper_usdt >= qty * self.child_bid and self._can_open_new_cycle():
                fill_delay = self._paper_fill_delay_snapshots(spread_ticks, passive_viability, refill_strength, churn_quality, child_stale_sec <= 2.5)
                if fill_delay < 0:
                    self.paper_entry_block_reason = "no_price_touch"
                    return
                qty = sizing_passive["size"]
                self.paper_fill_delay_left = fill_delay
                self.pending_paper_entry = {
                    "side": "BUY",
                    "mode": "PASSIVE",
                    "qty": qty,
                    "entry_price": self.child_bid,
                    "target_price": self.child_ask,
                    "created_snapshot": self.euri_snapshot_id,
                    "delay_left": fill_delay,
                }
                self.paper_entry_block_reason = "fill_delay"
        elif regime == Regime.IDEAL_TRAP and trap_survivability >= min_trap and gap_ticks >= min_gap_ticks and parent_dir == "UP":
            if self.paper_usdt >= qty * self.child_ask and self._can_open_new_cycle():
                qty = sizing_trap_buy["size"]
                self.paper_entry_price = self.child_ask
                self.paper_usdt -= qty * self.paper_entry_price
                self.paper_euri += qty
                self.paper_pending_exit_price = self.paper_entry_price + tick
                self.paper_entry_ts = now_ts
                self.paper_trapped_euri = qty
                self.paper_cycle_side = "LONG"
                self.paper_state = PaperPositionState.BUY_TRAP_ACTIVE
                self.paper_cycle_state = CycleState.ACTIVE
                self.paper_trap_attempts += 1
                self.paper_cycle_source = "BUY_TRAP"
                self.last_entry_snapshot_id = self.euri_snapshot_id
                self.paper_cycle_entry_snapshot_id = self.euri_snapshot_id
                self.paper_cycle_snapshots = 1
                self._open_lot(side="BUY", qty=qty, price=self.paper_entry_price, now_ts=now_ts, source="BUY_TRAP")
                self.paper_entry_block_reason = "-"
                self._paper_log("open_buy", "[PAPER] BUY_TRAP opened")
        elif regime == Regime.IDEAL_TRAP and trap_survivability >= min_trap and gap_ticks <= -min_gap_ticks and parent_dir == "DOWN":
            if self.paper_euri >= qty and self._can_open_new_cycle():
                self.paper_entry_price = self.child_bid
                self.paper_euri -= qty
                self.paper_usdt += qty * self.paper_entry_price
                self.paper_pending_exit_price = self.paper_entry_price - tick
                self.paper_entry_ts = now_ts
                self.paper_trapped_euri = qty
                self.paper_cycle_side = "SHORT"
                self.paper_state = PaperPositionState.SELL_TRAP_ACTIVE
                self.paper_cycle_state = CycleState.ACTIVE
                self.paper_trap_attempts += 1
                self.paper_cycle_source = "SELL_TRAP"
                self.last_entry_snapshot_id = self.euri_snapshot_id
                self.paper_cycle_entry_snapshot_id = self.euri_snapshot_id
                self.paper_cycle_snapshots = 1
                self._open_lot(side="SELL", qty=qty, price=self.paper_entry_price, now_ts=now_ts, source="SELL_TRAP")
                self.paper_entry_block_reason = "-"
                self._paper_log("open_sell", "[PAPER] SELL_TRAP opened")
        elif self.euri_snapshot_id <= self.last_entry_snapshot_id:
            self.paper_entry_block_reason = "same_snapshot"
        elif not self._can_open_new_cycle():
            self.paper_entry_block_reason = "cooldown"
        elif abs(skew) >= danger_skew:
            self.paper_entry_block_reason = "skew_limit"
        elif regime not in {Regime.IDEAL_PASSIVE, Regime.IDEAL_TRAP}:
            self.paper_entry_block_reason = "waiting_new_snapshot"
        else:
            self.paper_entry_block_reason = "no_price_touch"
        if abs((self.paper_realized_pnl + unrealized) - (total_value - start_value)) > 0.8:
            self._warn_once("accounting_mismatch", "[WARN] accounting mismatch")

    def refresh_view(self) -> None:
        parent_mid = (self.parent_bid + self.parent_ask) / 2 if self.parent_bid and self.parent_ask else 0.0
        child_mid = (self.child_bid + self.child_ask) / 2 if self.child_bid and self.child_ask else 0.0
        spread = self.child_ask - self.child_bid if self.child_bid and self.child_ask else 0.0
        tick_size = float(self.settings.get("tick_size", 0.0001))
        spread_ticks = spread / tick_size if tick_size else 0.0
        gap = parent_mid - child_mid
        gap_ticks = gap / tick_size if tick_size else 0.0

        if parent_mid:
            self.parent_mid_hist.append(parent_mid)
        if child_mid:
            self.child_mid_hist.append(child_mid)
        self.gap_ticks_hist.append(gap_ticks)

        corridor_window = int(self.settings.get("corridor_window", 20))
        min_gap_ticks = float(self.settings.get("min_gap_ticks", 2))
        max_spread_ticks = float(self.settings.get("max_spread_ticks", 3))
        now_ts = time.time()
        child_stale_sec = max(0.0, now_ts - self.last_child_update_ts) if self.last_child_update_ts else 9999.0

        parent_dir = self._direction(self.parent_mid_hist, tick_size)
        child_dir = self._direction(self.child_mid_hist, tick_size)
        parent_vol_ticks = 0.0
        if len(self.parent_mid_hist) >= 5 and tick_size:
            recent_parent = list(self.parent_mid_hist)[-5:]
            parent_vol_ticks = (max(recent_parent) - min(recent_parent)) / tick_size

        recent_gap = list(self.gap_ticks_hist)[-corridor_window:]
        corridor_age = len(recent_gap)
        gap_center = sum(recent_gap) / len(recent_gap) if recent_gap else 0.0
        gap_amplitude = (max(recent_gap) - min(recent_gap)) if recent_gap else 0.0
        sign_changes = sum(1 for i in range(1, len(recent_gap)) if recent_gap[i - 1] * recent_gap[i] < 0)
        churn = sign_changes / max(1, len(recent_gap) - 1)

        self.spread_ticks_hist.append(spread_ticks)
        self.center_dev_hist.append(abs(gap_center))
        self.corridor_age_hist.append(corridor_age)
        if gap_ticks > 0:
            gap_sign = 1
        elif gap_ticks < 0:
            gap_sign = -1
        else:
            gap_sign = 0
        if self._last_gap_sign and gap_sign and gap_sign != self._last_gap_sign:
            self._refill_drop_started_at = now_ts
        if self._refill_drop_started_at is not None and abs(gap_ticks) <= float(self.settings.get("refill_recovery_ticks", 1.0)):
            self.refill_timings_hist.append(now_ts - self._refill_drop_started_at)
            self._refill_drop_started_at = None
        self._last_gap_sign = gap_sign

        stable_move = float(self.settings.get("corridor_move_ticks", 3.0))
        stable_center = float(self.settings.get("corridor_center_tolerance_ticks", 1.5))
        churn_threshold = float(self.settings.get("corridor_churn_threshold", 0.35))

        corridor_stable = (
            spread_ticks <= max_spread_ticks
            and gap_amplitude <= stable_move
            and abs(gap_center) <= stable_center
            and churn <= churn_threshold
            and corridor_age >= min(5, corridor_window)
        )
        self.corridor_state = MarketState.CORRIDOR_STABLE if corridor_stable else MarketState.CORRIDOR_UNSTABLE

        self.stale_state = MarketState.WAIT
        stale_after_sec = float(self.settings.get("stale_after_sec", 12))
        if child_stale_sec >= stale_after_sec or spread <= 0.0:
            self.stale_state = MarketState.STALE_CHILD

        self.parent_state = MarketState.WAIT
        if parent_vol_ticks >= float(self.settings.get("parent_impulse_ticks", 6.0)):
            self.parent_state = MarketState.PARENT_IMPULSE

        self.trap_readiness = MarketState.WAIT
        if corridor_stable and spread_ticks <= max_spread_ticks:
            if gap_ticks >= min_gap_ticks and parent_dir == "UP":
                self.trap_readiness = MarketState.BUY_TRAP_READY
            elif gap_ticks <= -min_gap_ticks and parent_dir == "DOWN":
                self.trap_readiness = MarketState.SELL_TRAP_READY
            elif abs(gap_ticks) < min_gap_ticks and spread_ticks <= 2:
                self.trap_readiness = MarketState.PASSIVE

        state = self.corridor_state if corridor_stable else MarketState.WAIT
        if spread_ticks > max_spread_ticks:
            state = MarketState.SPREAD_TOO_WIDE
        if self.parent_state == MarketState.PARENT_IMPULSE:
            state = MarketState.PARENT_IMPULSE
        if self.stale_state == MarketState.STALE_CHILD:
            state = MarketState.STALE_CHILD
        if self.trap_readiness in (MarketState.BUY_TRAP_READY, MarketState.SELL_TRAP_READY, MarketState.PASSIVE):
            state = self.trap_readiness

        avg_spread = sum(self.spread_ticks_hist) / len(self.spread_ticks_hist) if self.spread_ticks_hist else spread_ticks
        spread_var = (max(self.spread_ticks_hist) - min(self.spread_ticks_hist)) if self.spread_ticks_hist else 0.0
        center_stability = max(0, min(100, int(100 - (abs(gap_center) * 20 + gap_amplitude * 10))))
        mean_reversion_quality = max(0, min(100, int(100 - min(100, abs(gap_ticks) * 15) - churn * 25)))
        equilibrium_score = max(0, min(100, int((center_stability * 0.45) + ((100 - min(100, gap_amplitude * 18)) * 0.35) + ((100 - min(100, churn * 160)) * 0.20))))
        passive_viability = max(0, min(100, int((100 - min(100, avg_spread * 18)) * 0.4 + equilibrium_score * 0.35 + (100 - min(100, parent_vol_ticks * 8)) * 0.25)))
        trap_survivability = max(0, min(100, int((100 if corridor_stable else 35) * 0.35 + (100 - min(100, spread_ticks * 22)) * 0.2 + (100 - min(100, parent_vol_ticks * 10)) * 0.2 + mean_reversion_quality * 0.25)))

        refill_avg = sum(self.refill_timings_hist) / len(self.refill_timings_hist) if self.refill_timings_hist else 2.4
        if refill_avg <= 0.9:
            refill_strength = "AGGRESSIVE_REFILL"
        elif refill_avg <= 1.9:
            refill_strength = "NORMAL_REFILL"
        else:
            refill_strength = "WEAK_REFILL"

        if spread_var >= 2.0 or spread_ticks >= float(self.settings.get("spread_unstable_ticks", 4.0)):
            spread_state = "UNSTABLE"
        elif spread_ticks <= float(self.settings.get("spread_compressed_ticks", 1.2)):
            spread_state = "COMPRESSED"
        elif spread_ticks > max_spread_ticks:
            spread_state = "EXPANDING"
        else:
            spread_state = "NORMAL"

        if churn >= 0.55:
            churn_quality = "CHAOTIC"
        elif churn >= 0.25:
            churn_quality = "RECYCLING"
        else:
            churn_quality = "CALM"

        market_class = "IDEAL_MARKET" if (spread_state in {"COMPRESSED", "NORMAL"} and refill_strength == "WEAK_REFILL" and equilibrium_score >= 70 and passive_viability >= 70) else "DANGEROUS_MARKET" if (spread_state == "UNSTABLE" or self.parent_state == MarketState.PARENT_IMPULSE or refill_strength == "AGGRESSIVE_REFILL" or mean_reversion_quality < 45) else "CAUTION_MARKET"
        regime_candidate, regime_score = self._pick_regime_candidate(equilibrium_score, passive_viability, trap_survivability, corridor_stable, spread_state, refill_strength, churn_quality, self.parent_state == MarketState.PARENT_IMPULSE, self.stale_state == MarketState.STALE_CHILD, mean_reversion_quality)
        regime_duration = now_ts - self._regime_since
        metric_agreement = max(0, 100 - int(abs(equilibrium_score - passive_viability) * 0.7 + abs(passive_viability - trap_survivability) * 0.3))
        duration_bonus = min(100, int(regime_duration * 5))
        regime_stability = max(0, 100 - min(90, self._regime_transition_count * 6))
        self._regime_confidence = max(0, min(100, int(regime_score * 0.45 + metric_agreement * 0.30 + duration_bonus * 0.15 + regime_stability * 0.10)))
        self._update_regime(regime_candidate, regime_score, now_ts, regime_stability)

        self._paper_step(
            now_ts=now_ts,
            child_mid=child_mid,
            gap_ticks=gap_ticks,
            spread_ticks=spread_ticks,
            parent_dir=parent_dir,
            regime=self.current_regime,
            passive_viability=passive_viability,
            trap_survivability=trap_survivability,
            refill_strength=refill_strength,
            churn_quality=churn_quality,
            child_stale_sec=child_stale_sec,
        )

        self._state_duration = self._state_duration + 1 if state == self.current_state else 1
        self.state_age_hist.append(self._state_duration)

        self._log_state_change(state)

        self.lbl_app.setText(f"APP: {self.app_status}")
        self.lbl_ws.setText(f"WS: {'OK' if self.ws_status == 'CONNECTED' else self.ws_status}")
        self.lbl_http.setText(f"HTTP: {self.http_status}")
        self.lbl_mode.setText(f"MODE: {self.mode}")

        self.market_labels["EUR bid"].setText(f"{self.parent_bid:.6f}")
        self.market_labels["EUR ask"].setText(f"{self.parent_ask:.6f}")
        self.market_labels["EUR mid"].setText(f"{parent_mid:.6f}")
        self.market_labels["EURI bid"].setText(f"{self.child_bid:.6f}")
        self.market_labels["EURI ask"].setText(f"{self.child_ask:.6f}")
        self.market_labels["EURI mid"].setText(f"{child_mid:.6f}")
        self.market_labels["EURI spread"].setText(f"{spread:.6f}")
        self.market_labels["fair gap ticks"].setText(f"{gap_ticks:.2f}")
        self.market_labels["child stale sec"].setText(f"{child_stale_sec:.1f}")

        self._set_state_label("current state", state)
        self._set_state_label("trap readiness", self.trap_readiness)
        self._set_regime_label("regime", self.current_regime.value, self.current_regime)

        self._log_micro_change("eq", f"equilibrium={equilibrium_score} passive={passive_viability} class={market_class}")
        self._log_micro_change("refill", f"refill={refill_strength}")
        self._log_micro_change("trap", f"trap_survivability={trap_survivability}")
        if market_class == "DANGEROUS_MARKET":
            self._log_micro_change("danger", "dangerous_market")

        confidence_bucket = self._regime_confidence // max(1, int(self.settings.get("regime_confidence_jump_log", 12)))
        if confidence_bucket != self._regime_confidence_bucket:
            self._regime_confidence_bucket = confidence_bucket
            self.log(f"[REGIME] {self.current_regime.value} confidence={self._regime_confidence}")
        cooldown_left = max(0.0, self._regime_cooldown_until - now_ts)
        transition_quality = "stable transition" if regime_stability >= 70 else "violent transition" if regime_stability <= 40 else "mixed transition"

        total_value, skew, _, unrealized = self._inventory_metrics(child_mid)
        avg_ticks = self.paper_total_ticks / self.paper_cycles if self.paper_cycles else 0.0
        avg_hold = self.paper_total_hold_sec / self.paper_cycles if self.paper_cycles else 0.0
        trap_success = (self.paper_trap_success / self.paper_trap_attempts * 100.0) if self.paper_trap_attempts else 0.0

        self.inventory_labels["EURI"].setText(f"{self.paper_euri:.2f}")
        self.inventory_labels["USDT"].setText(f"{self.paper_usdt:.2f}")
        self.inventory_labels["total value"].setText(f"{total_value:.2f}")
        self.inventory_labels["skew"].setText(f"{skew:+.2f}%")
        self.inventory_labels["realized pnl"].setText(f"{self.paper_realized_pnl:+.4f}")
        self.inventory_labels["unrealized pnl"].setText(f"{unrealized:+.4f}")
        self.inventory_labels["open lots"].setText(str(len(self.paper_lots)))

        self.engine_labels["engine status"].setText("STARTED" if self.paper_engine_enabled else "STOPPED")
        self.engine_labels["paper state"].setText(self.paper_state.value)
        self.engine_labels["active cycle"].setText(f"{self.paper_last_cycle} [{self.paper_cycle_source}]")
        self.session_labels["cycles"].setText(str(self.paper_cycles))
        self.session_labels["wins"].setText(str(self.paper_wins))
        self.session_labels["losses"].setText(str(self.paper_losses))
        self.session_labels["winrate"].setText(f"{(self.paper_wins / self.paper_cycles * 100.0) if self.paper_cycles else 0.0:.1f}%")
        self.session_labels["avg pnl"].setText(f"{(self.paper_realized_pnl / self.paper_cycles) if self.paper_cycles else 0.0:+.4f}")
        self.session_labels["avg ticks"].setText(f"{avg_ticks:+.2f}")
        self.session_labels["avg hold"].setText(f"{avg_hold:.1f}s")
        entry_age = max(0, self.euri_snapshot_id - self.last_entry_snapshot_id) if self.last_entry_snapshot_id >= 0 else 0
        cool_left = max(0.0, (self.paper_last_close_ts + float(self.settings.get("paper_cycle_cooldown_sec", 7.0))) - now_ts)
        hold_left = max(0.0, (self.paper_entry_ts + float(self.settings.get("paper_min_hold_sec", 4.0))) - now_ts) if self.paper_entry_ts else 0.0
        cycles_min = len(self.paper_cycle_timestamps)
        self.engine_labels["entry block reason"].setText(self.paper_entry_block_reason)
        self.engine_labels["fill quality"].setText(f"{self.paper_fill_quality}")
        self.engine_labels["cooldown"].setText(f"{cool_left:.1f}s")
        self.engine_labels["min hold"].setText(f"{hold_left:.1f}s")
        self.engine_labels["cycles/min"].setText(str(cycles_min))
        cp = getattr(self, "current_sizing", {})
        passive_rec = cp.get("passive", {}).get("size", 0.0)
        trap_rec = max(cp.get("trap_buy", {}).get("size", 0.0), cp.get("trap_sell", {}).get("size", 0.0))
        anchor1 = min(float(self.settings.get("budget_anchor", 0.0)) * 0.20, float(self.settings.get("sizing_max_order_size", 200.0)))
        anchor2 = min(float(self.settings.get("budget_anchor", 0.0)) * 0.30, float(self.settings.get("sizing_max_order_size", 200.0)))
        anchor3 = min(float(self.settings.get("budget_anchor", 0.0)) * 0.50, float(self.settings.get("sizing_max_order_size", 200.0)))
        used_budget = 0.0 if float(self.settings.get("budget_total", 1.0)) <= 0 else min(100.0, (self.paper_trapped_euri * child_mid) / float(self.settings.get("budget_total", 1.0)) * 100.0)
        inv_factor = cp.get("passive", {}).get("inventory_factor", 1.0)
        rnd_factor = cp.get("passive", {}).get("random_factor", 1.0)
        active_mode = self.paper_cycle_source if self.paper_cycle_source != "NONE" else "IDLE"
        self.sizing_labels["passive size"].setText(f"{passive_rec:.2f}")
        self.sizing_labels["trap size"].setText(f"{trap_rec:.2f}")
        self.sizing_labels["anchor layer 1"].setText(f"{anchor1:.2f}")
        self.sizing_labels["anchor layer 2"].setText(f"{anchor2:.2f}")
        self.sizing_labels["anchor layer 3"].setText(f"{anchor3:.2f}")
        self.sizing_labels["budget used %"].setText(f"{used_budget:.1f}%")
        self.sizing_labels["random factor"].setText(f"{rnd_factor:.2f}")
        for idx, key in enumerate(["event 1", "event 2", "event 3", "event 4", "event 5"]):
            if idx < len(self.paper_ledger):
                evt = self.paper_ledger[idx]
                self.ledger_labels[key].setText(f"{evt['timestamp']} {evt['type']} {evt['side']} {evt['qty']:.2f} pnl={evt['realized_pnl']:+.4f}")
            else:
                if idx == 0 and not self.paper_engine_enabled:
                    self.ledger_labels[key].setText("Paper stopped")
                elif idx == 0:
                    self.ledger_labels[key].setText("Нет событий — нажмите START или ждём условия входа")
                else:
                    self.ledger_labels[key].setText("-")

    def log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        self.log_buffer.append(line)
        self.log_box.appendPlainText(line)
        self.logger.info(message)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.stop_ws.set()
        self.app_status = "STOPPING"
        self.log("LUC shutting down")
        super().closeEvent(event)


if __name__ == "__main__":
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    win = LUCWindow()
    win.show()
    sys.exit(app.exec())
