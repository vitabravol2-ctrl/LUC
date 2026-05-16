import json
import logging
import os
import sys
import threading
import time
from collections import deque
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
}


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
        ])
        self.fair_labels = self.make_panel(grid, 0, 1, "Fair Value", [
            "EUR fair-value", "EURI mid", "fair gap", "fair gap ticks",
        ])
        self.inventory_labels = self.make_panel(grid, 1, 0, "Inventory (demo)", [
            "EURI balance", "USDT balance", "total value", "inventory skew",
        ])
        self.session_labels = self.make_panel(grid, 1, 1, "Session (demo)", [
            "cycles", "wins", "losses", "pnl", "ticks harvested",
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
            QGroupBox { border: 1px solid #2b2f3a; margin-top: 8px; padding-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #8ab4f8; }
            QPushButton { background: #1f2530; border: 1px solid #3a4252; padding: 4px 10px; }
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
        except Exception as exc:
            self.http_status = "ERROR"
            self.log(f"HTTP error: {exc}")

    def refresh_view(self) -> None:
        parent_mid = (self.parent_bid + self.parent_ask) / 2 if self.parent_bid and self.parent_ask else 0.0
        child_mid = (self.child_bid + self.child_ask) / 2 if self.child_bid and self.child_ask else 0.0
        spread = self.child_ask - self.child_bid if self.child_bid and self.child_ask else 0.0
        gap = parent_mid - child_mid
        tick_size = float(self.settings.get("tick_size", 0.0001))
        gap_ticks = gap / tick_size if tick_size else 0.0

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

        self.fair_labels["EUR fair-value"].setText(f"{parent_mid:.6f}")
        self.fair_labels["EURI mid"].setText(f"{child_mid:.6f}")
        self.fair_labels["fair gap"].setText(f"{gap:.6f}")
        self.fair_labels["fair gap ticks"].setText(f"{gap_ticks:.2f}")

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
