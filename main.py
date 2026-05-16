import json
import logging
import os
import sys
import threading
import time
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

class BasicSettingsDialog(QDialog):
    def __init__(self, settings: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LUC Basic Settings")
        self.setModal(True)
        self.resize(380, 280)

        form = QFormLayout(self)

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

        self.cooldown = QSpinBox()
        self.cooldown.setRange(0, 10_000)
        self.cooldown.setValue(int(settings.get("cooldown", 2)))

        self.max_inventory_shift = QDoubleSpinBox()
        self.max_inventory_shift.setRange(0.0, 1_000_000.0)
        self.max_inventory_shift.setDecimals(4)
        self.max_inventory_shift.setValue(float(settings.get("max_inventory_shift", 300.0)))

        form.addRow("DRY_RUN", self.dry_run)
        form.addRow("order_size", self.order_size)
        form.addRow("min_gap_ticks", self.min_gap_ticks)
        form.addRow("max_spread_ticks", self.max_spread_ticks)
        form.addRow("cooldown", self.cooldown)
        form.addRow("max_inventory_shift", self.max_inventory_shift)

        buttons = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        cancel_btn = QPushButton("Cancel")
        apply_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(apply_btn)
        buttons.addWidget(cancel_btn)
        form.addRow(buttons)

    def values(self) -> dict[str, Any]:
        return {
            "DRY_RUN": self.dry_run.isChecked(),
            "order_size": self.order_size.value(),
            "min_gap_ticks": self.min_gap_ticks.value(),
            "max_spread_ticks": self.max_spread_ticks.value(),
            "cooldown": self.cooldown.value(),
            "max_inventory_shift": self.max_inventory_shift.value(),
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
        self.resize(1080, 760)

        self.settings = self.load_settings()

        self.parent_bid = 0.0
        self.parent_ask = 0.0
        self.child_bid = 0.0
        self.child_ask = 0.0

        self.ws_status = "DISCONNECTED"
        self.http_status = "IDLE"
        self.app_status = "RUNNING"
        self.mode = "DRY_RUN" if self.settings.get("DRY_RUN", True) else "LIVE_DISABLED"

        self.log_buffer: deque[str] = deque(maxlen=500)
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
        self.lbl_app = QLabel()
        self.lbl_ws = QLabel()
        self.lbl_http = QLabel()
        self.lbl_mode = QLabel()
        self.lbl_symbols = QLabel(f"SYMBOLS: {self.settings['symbols']['parent']} / {self.settings['symbols']['child']}")
        for w in (self.lbl_app, self.lbl_ws, self.lbl_http, self.lbl_mode, self.lbl_symbols):
            top.addWidget(w)
        top.addStretch(1)
        basic_btn = QPushButton("Basic Settings")
        json_btn = QPushButton("Full JSON Settings")
        basic_btn.clicked.connect(self.open_basic_settings)
        json_btn.clicked.connect(self.open_json_settings)
        top.addWidget(basic_btn)
        top.addWidget(json_btn)
        layout.addLayout(top)

        grid = QGridLayout()
        self.market_labels = self.make_panel(grid, 0, 0, "Market", [
            "EUR bid", "EUR ask", "EUR mid", "EURI bid", "EURI ask", "EURI mid", "EURI spread",
            "spread ticks", "EUR dir", "EURI dir", "parent vol ticks", "child stale sec",
        ])
        self.fair_labels = self.make_panel(grid, 0, 1, "Fair Value", [
            "EUR fair-value", "EURI mid", "fair gap", "fair gap ticks",
            "corridor age", "corridor stability", "refill/churn",
        ])
        self.inventory_labels = self.make_panel(grid, 1, 0, "Inventory (demo)", [
            "EURI balance", "USDT balance", "total value", "inventory skew",
        ])
        self.session_labels = self.make_panel(grid, 1, 1, "Session (demo)", [
            "cycles", "wins", "losses", "pnl", "ticks harvested",
        ])
        self.state_labels = self.make_panel(grid, 2, 0, "Market State", [
            "current state", "corridor state", "trap readiness", "parent state", "stale state",
        ])
        self.micro_labels = self.make_panel(grid, 2, 1, "Microstructure", [
            "equilibrium score", "passive viability", "trap survivability", "refill strength",
            "spread state", "churn quality", "center stability", "mean reversion quality", "market class",
        ])
        self.regime_labels = self.make_panel(grid, 3, 0, "Regime Panel", [
            "current regime", "regime confidence", "regime duration", "last transition", "transition cooldown", "regime stability",
        ])
        layout.addLayout(grid)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(600)
        layout.addWidget(self.log_box, 1)

    def make_panel(self, grid: QGridLayout, row: int, col: int, title: str, fields: list[str]) -> dict[str, QLabel]:
        box = QGroupBox(title)
        form = QFormLayout(box)
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
        label = self.state_labels[name]
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
        label = self.regime_labels[name]
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
        label = self.micro_labels[name]
        label.setText(value)
        label.setStyleSheet(f"color: {self._micro_color(level)}; font-weight: bold;")

    def _log_micro_change(self, key: str, message: str) -> None:
        prev = self._last_micro_log.get(key)
        if prev != message:
            self._last_micro_log[key] = message
            self.log(f"[MICRO] {message}")

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

        self._state_duration = self._state_duration + 1 if state == self.current_state else 1
        self.state_age_hist.append(self._state_duration)

        self._log_state_change(state)

        self.lbl_app.setText(f"APP STATUS: {self.app_status}")
        self.lbl_ws.setText(f"WS STATUS: {self.ws_status}")
        self.lbl_http.setText(f"HTTP STATUS: {self.http_status}")
        self.lbl_mode.setText(f"MODE: {self.mode}")

        self.market_labels["EUR bid"].setText(f"{self.parent_bid:.6f}")
        self.market_labels["EUR ask"].setText(f"{self.parent_ask:.6f}")
        self.market_labels["EUR mid"].setText(f"{parent_mid:.6f}")
        self.market_labels["EURI bid"].setText(f"{self.child_bid:.6f}")
        self.market_labels["EURI ask"].setText(f"{self.child_ask:.6f}")
        self.market_labels["EURI mid"].setText(f"{child_mid:.6f}")
        self.market_labels["EURI spread"].setText(f"{spread:.6f}")
        self.market_labels["spread ticks"].setText(f"{spread_ticks:.2f}")
        self.market_labels["EUR dir"].setText(parent_dir)
        self.market_labels["EURI dir"].setText(child_dir)
        self.market_labels["parent vol ticks"].setText(f"{parent_vol_ticks:.2f}")
        self.market_labels["child stale sec"].setText(f"{child_stale_sec:.1f}")

        self.fair_labels["EUR fair-value"].setText(f"{parent_mid:.6f}")
        self.fair_labels["EURI mid"].setText(f"{child_mid:.6f}")
        self.fair_labels["fair gap"].setText(f"{gap:.6f}")
        self.fair_labels["fair gap ticks"].setText(f"{gap_ticks:.2f}")
        self.fair_labels["corridor age"].setText(str(corridor_age))
        self.fair_labels["corridor stability"].setText("stable" if corridor_stable else "unstable")
        self.fair_labels["refill/churn"].setText(f"{churn:.2f}")

        self._set_state_label("current state", state)
        self._set_state_label("corridor state", self.corridor_state)
        self._set_state_label("trap readiness", self.trap_readiness)
        self._set_state_label("parent state", self.parent_state)
        self._set_state_label("stale state", self.stale_state)

        self._set_micro_label("equilibrium score", f"{equilibrium_score}", "HIGH" if equilibrium_score >= 70 else "MEDIUM" if equilibrium_score >= 45 else "LOW")
        self._set_micro_label("passive viability", f"{passive_viability}", "HIGH" if passive_viability >= 70 else "MEDIUM" if passive_viability >= 45 else "LOW")
        self._set_micro_label("trap survivability", f"{trap_survivability}", "SURVIVABLE" if trap_survivability >= 65 else "CAUTION" if trap_survivability >= 45 else "DANGEROUS")
        self._set_micro_label("refill strength", refill_strength, "RED" if refill_strength == "AGGRESSIVE_REFILL" else "NORMAL" if refill_strength == "NORMAL_REFILL" else "GREEN")
        self._set_micro_label("spread state", spread_state, spread_state)
        self._set_micro_label("churn quality", churn_quality, "CALM" if churn_quality == "CALM" else "RECYCLING" if churn_quality == "RECYCLING" else "DANGEROUS")
        self._set_micro_label("center stability", f"{center_stability}", "HIGH" if center_stability >= 70 else "MEDIUM" if center_stability >= 45 else "LOW")
        self._set_micro_label("mean reversion quality", f"{mean_reversion_quality}", "HIGH" if mean_reversion_quality >= 70 else "MEDIUM" if mean_reversion_quality >= 45 else "BROKEN")
        self._set_micro_label("market class", market_class, "GREEN" if market_class == "IDEAL_MARKET" else "RED" if market_class == "DANGEROUS_MARKET" else "ORANGE")

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
        self._set_regime_label("current regime", self.current_regime.value, self.current_regime)
        self._set_regime_label("regime confidence", f"{self._regime_confidence}", self.current_regime)
        self._set_regime_label("regime duration", f"{regime_duration:.1f}s", self.current_regime)
        self._set_regime_label("last transition", self._regime_last_transition, self.current_regime)
        self._set_regime_label("transition cooldown", f"{cooldown_left:.1f}s", self.current_regime)
        self._set_regime_label("regime stability", f"{regime_stability} ({transition_quality})", self.current_regime)

        self.inventory_labels["EURI balance"].setText("1000.00")
        self.inventory_labels["USDT balance"].setText("1000.00")
        self.inventory_labels["total value"].setText("2000.00")
        self.inventory_labels["inventory skew"].setText("0.00%")

        self.session_labels["cycles"].setText("0")
        self.session_labels["wins"].setText("0")
        self.session_labels["losses"].setText("0")
        self.session_labels["pnl"].setText("0.00")
        self.session_labels["ticks harvested"].setText("0")

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
