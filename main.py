import hashlib
import hmac
import json
import sys
import time
from copy import deepcopy
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "settings.json"


def default_settings() -> dict:
    return {
        "version": "0.1.2",
        "binance_api_key": "",
        "binance_api_secret": "",
        "use_testnet": False,
        "ui": {"latest_logs_rows": 220, "theme": "dark"},
        "general": {
            "base_symbol": "EURIUSDT",
            "parent_symbol": "EURUSDT",
            "max_open_cycles": 1,
            "default_order_size": 1.0,
            "max_inventory_shift": 0.2,
            "max_hold_sec": 30,
        },
        "passive": {
            "passive_enabled": True,
            "passive_max_spread_ticks": 3,
            "passive_target_ticks": 1,
            "passive_order_size": 1.0,
            "passive_cooldown_sec": 2,
        },
        "trap": {
            "trap_enabled": False,
            "trap_min_gap_ticks": 2,
            "trap_max_spread_ticks": 4,
            "trap_target_ticks": 1,
            "trap_order_size": 1.0,
            "trap_cooldown_sec": 3,
            "cancel_if_gap_gone": True,
        },
        "risk_inventory": {
            "inventory_safe_min": 0.0,
            "inventory_safe_max": 0.3,
            "inventory_danger_max": 0.6,
            "inventory_critical_max": 0.8,
            "no_market_orders": True,
        },
    }


def deep_merge(base: dict, incoming: dict) -> dict:
    out = deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class BinanceClient:
    def __init__(self, api_key: str, api_secret: str, use_testnet: bool = False, timeout: float = 10.0) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self.base_url = "https://testnet.binance.vision" if use_testnet else "https://api.binance.com"

    def _request(self, method: str, path: str, params: dict | None = None, signed: bool = False) -> dict:
        params = params or {}
        headers = {"User-Agent": "LUC/0.1.2"}

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 10000
            query = urlencode(params)
            signature = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            params["signature"] = signature
            headers["X-MBX-APIKEY"] = self.api_key
        elif self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key

        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"

        request = Request(url=url, method=method, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {details}") from exc
        except URLError as exc:
            raise RuntimeError(f"Network error: {exc.reason}") from exc

    def ping(self) -> dict:
        return self._request("GET", "/api/v3/ping")

    def server_time(self) -> dict:
        return self._request("GET", "/api/v3/time")

    def account(self) -> dict:
        return self._request("GET", "/api/v3/account", signed=True)

    def get_balances(self, assets: list[str]) -> dict:
        account_data = self.account()
        indexed = {entry.get("asset", ""): entry for entry in account_data.get("balances", [])}
        out = {}
        for asset in assets:
            b = indexed.get(asset, {"free": "0", "locked": "0"})
            free = float(b.get("free", "0"))
            locked = float(b.get("locked", "0"))
            out[asset] = {"free": free, "locked": locked, "total": free + locked}
        return out

    def get_exchange_info(self, symbol: str) -> dict:
        info = self._request("GET", "/api/v3/exchangeInfo", params={"symbol": symbol})
        symbols = info.get("symbols", [])
        if not symbols:
            raise RuntimeError(f"No exchangeInfo for symbol {symbol}")
        return symbols[0]


class ConnectWorker(QObject):
    log = Signal(str)
    status = Signal(str)
    result = Signal(dict)
    done = Signal()

    def __init__(self, settings: dict) -> None:
        super().__init__()
        self.settings = settings

    def run(self) -> None:
        try:
            api_key = self.settings.get("binance_api_key", "")
            api_secret = self.settings.get("binance_api_secret", "")
            use_testnet = bool(self.settings.get("use_testnet", False))

            if not api_key or not api_secret:
                raise RuntimeError("Binance API key/secret missing in settings.json")

            masked = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) >= 8 else "***"
            self.log.emit(f"[API] connecting with key {masked} (testnet={use_testnet})")
            self.status.emit("CONNECTING")

            client = BinanceClient(api_key=api_key, api_secret=api_secret, use_testnet=use_testnet)
            client.ping()
            self.log.emit("[API] ping ok")

            server_time = client.server_time()
            self.log.emit("[API] server time ok")

            balances = client.get_balances(["EURI", "USDT"])
            self.log.emit("[ACCOUNT] balances loaded")

            symbol_info = client.get_exchange_info("EURIUSDT")
            filters = {f.get("filterType"): f for f in symbol_info.get("filters", [])}
            tick_size = filters.get("PRICE_FILTER", {}).get("tickSize", "N/A")
            step_size = filters.get("LOT_SIZE", {}).get("stepSize", "N/A")
            min_notional = filters.get("NOTIONAL", {}).get("minNotional") or filters.get("MIN_NOTIONAL", {}).get("minNotional", "N/A")
            self.log.emit(f"[FILTERS] EURIUSDT tickSize={tick_size} / stepSize={step_size} / minNotional={min_notional}")

            self.result.emit({
                "server_time": server_time.get("serverTime"),
                "balances": balances,
                "filters": {"tickSize": tick_size, "stepSize": step_size, "minNotional": min_notional},
            })
            self.status.emit("CONNECTED")
        except Exception as exc:  # noqa: BLE001
            self.log.emit(f"[ERROR] {exc}")
            self.status.emit("ERROR")
        finally:
            self.done.emit()


class SettingsDialog(QDialog):
    def __init__(self, settings: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SETTINGS")
        self.resize(760, 600)
        self.inputs: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        groups = {
            "API": [("binance_api_key", "binance_api_key"), ("binance_api_secret", "binance_api_secret"), ("use_testnet", "use_testnet")],
            "General Trading": [
                ("general.base_symbol", "base_symbol"),
                ("general.parent_symbol", "parent_symbol"),
                ("general.max_open_cycles", "max_open_cycles"),
                ("general.default_order_size", "default_order_size"),
                ("general.max_inventory_shift", "max_inventory_shift"),
                ("general.max_hold_sec", "max_hold_sec"),
            ],
            "Passive Corridor": [
                ("passive.passive_enabled", "passive_enabled"),
                ("passive.passive_max_spread_ticks", "passive_max_spread_ticks"),
                ("passive.passive_target_ticks", "passive_target_ticks"),
                ("passive.passive_order_size", "passive_order_size"),
                ("passive.passive_cooldown_sec", "passive_cooldown_sec"),
            ],
            "Aggressive Trap": [
                ("trap.trap_enabled", "trap_enabled"),
                ("trap.trap_min_gap_ticks", "trap_min_gap_ticks"),
                ("trap.trap_max_spread_ticks", "trap_max_spread_ticks"),
                ("trap.trap_target_ticks", "trap_target_ticks"),
                ("trap.trap_order_size", "trap_order_size"),
                ("trap.trap_cooldown_sec", "trap_cooldown_sec"),
                ("trap.cancel_if_gap_gone", "cancel_if_gap_gone"),
            ],
            "Risk / Inventory": [
                ("risk_inventory.inventory_safe_min", "inventory_safe_min"),
                ("risk_inventory.inventory_safe_max", "inventory_safe_max"),
                ("risk_inventory.inventory_danger_max", "inventory_danger_max"),
                ("risk_inventory.inventory_critical_max", "inventory_critical_max"),
                ("risk_inventory.no_market_orders", "no_market_orders"),
            ],
        }

        for tab_name, fields in groups.items():
            page = QWidget()
            form = QFormLayout(page)
            for path, label in fields:
                value = self._get_path(settings, path)
                editor = QLineEdit(str(value))
                form.addRow(label, editor)
                self.inputs[path] = editor
            tabs.addTab(page, tab_name)

        buttons = QHBoxLayout()
        save = QPushButton("SAVE")
        cancel = QPushButton("CANCEL")
        buttons.addStretch(1)
        buttons.addWidget(save)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        save.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

    def _get_path(self, data: dict, path: str):
        current = data
        for key in path.split("."):
            if not isinstance(current, dict):
                return ""
            current = current.get(key)
        return current

    @staticmethod
    def _parse_value(raw: str):
        text = raw.strip()
        if text.lower() in {"true", "false"}:
            return text.lower() == "true"
        try:
            if "." in text:
                return float(text)
            return int(text)
        except ValueError:
            return text

    def payload(self) -> dict:
        out: dict = {}
        for path, editor in self.inputs.items():
            keys = path.split(".")
            cursor = out
            for key in keys[:-1]:
                cursor = cursor.setdefault(key, {})
            cursor[keys[-1]] = self._parse_value(editor.text())
        return out


class FullConfigDialog(QDialog):
    def __init__(self, settings: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("FULL CONFIG")
        self.resize(900, 640)
        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit(json.dumps(settings, indent=2, ensure_ascii=False))
        layout.addWidget(self.editor)
        buttons = QHBoxLayout()
        save = QPushButton("SAVE")
        close = QPushButton("CLOSE")
        buttons.addStretch(1)
        buttons.addWidget(save)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        save.clicked.connect(self.accept)
        close.clicked.connect(self.reject)

    def parsed(self) -> dict:
        return json.loads(self.editor.toPlainText())


class LUCTerminal(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = self._load_settings()
        self.thread: QThread | None = None
        self.worker: ConnectWorker | None = None
        self.setWindowTitle("LUC v0.1.2 — GUI Cockpit + Mode Cards + Structured Settings")
        self.resize(1520, 920)
        self._init_ui()
        self._append_log("[v0.1.2] GUI cockpit initialized")
        self._append_log("[SAFETY] No market data yet. No trading actions.")

    def _load_settings(self) -> dict:
        base = default_settings()
        if not CONFIG_PATH.exists():
            return base
        with CONFIG_PATH.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
        return deep_merge(base, loaded)

    def _save_settings(self) -> None:
        self.settings["version"] = "0.1.2"
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as file:
            json.dump(self.settings, file, indent=2, ensure_ascii=False)

    def _append_log(self, message: str) -> None:
        max_rows = int(self.settings.get("ui", {}).get("latest_logs_rows", 220))
        lines = self.logs_view.toPlainText().splitlines()
        lines.append(message)
        if len(lines) > max_rows:
            lines = lines[-max_rows:]
        self.logs_view.setPlainText("\n".join(lines))
        self.logs_view.verticalScrollBar().setValue(self.logs_view.verticalScrollBar().maximum())

    def _status_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFrameShape(QFrame.Shape.StyledPanel)
        label.setMinimumHeight(30)
        return label

    def _make_mode_card(self, title: str, rows: list[tuple[str, str]]) -> QGroupBox:
        box = QGroupBox(title)
        form = QFormLayout(box)
        for name, value in rows:
            form.addRow(QLabel(name), QLabel(value))
        return box

    def _init_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        self.setCentralWidget(central)

        top = QHBoxLayout()
        self.version_label = self._status_label("LUC VERSION: 0.1.2")
        self.api_status_label = self._status_label("API STATUS: DISCONNECTED")
        self.eur_status = self._status_label("EURUSDT STATUS: IDLE")
        self.euri_status = self._status_label("EURIUSDT STATUS: IDLE")
        self.mode_label = self._status_label("CURRENT MODE: NONE")
        self.inventory_zone = self._status_label("INVENTORY ZONE: SAFE")
        for w in [self.version_label, self.api_status_label, self.eur_status, self.euri_status, self.mode_label, self.inventory_zone]:
            top.addWidget(w)
        root.addLayout(top)

        modes = QGridLayout()
        modes.addWidget(self._make_mode_card("PASSIVE CORRIDOR", [
            ("Status", "IDLE"), ("Spread ticks", "—"), ("Corridor state", "—"), ("Center ownership", "—"),
            ("Recycle readiness", "—"), ("Queue position placeholder", "—"), ("Planned action", "NONE"), ("Block reason", "N/A")]), 0, 0)
        modes.addWidget(self._make_mode_card("AGGRESSIVE TRAP", [
            ("Status", "IDLE"), ("Fair gap ticks", "—"), ("Trap direction", "NONE"), ("Parent impulse", "—"),
            ("Child delay", "—"), ("Weak side", "—"), ("Planned action", "NONE"), ("Block reason", "N/A")]), 0, 1)
        root.addLayout(modes)

        lower = QGridLayout()
        market = QGroupBox("Market / Balances")
        ml = QFormLayout(market)
        self.eur_mid = QLabel("N/A")
        self.euri_bid = QLabel("N/A")
        self.euri_ask = QLabel("N/A")
        self.euri_spread = QLabel("N/A")
        self.euri_bal = QLabel("free=0.00000000 / locked=0.00000000 / total=0.00000000")
        self.usdt_bal = QLabel("free=0.00000000 / locked=0.00000000 / total=0.00000000")
        self.filters_label = QLabel("tickSize=N/A / stepSize=N/A / minNotional=N/A")
        for k, v in [("EUR mid", self.eur_mid), ("EURI bid", self.euri_bid), ("EURI ask", self.euri_ask), ("EURI spread", self.euri_spread), ("EURI", self.euri_bal), ("USDT", self.usdt_bal), ("filters", self.filters_label)]:
            ml.addRow(k, v)

        acc = self._make_mode_card("Accounting", [
            ("cycles", "0"), ("wins/losses", "0/0"), ("realized PnL", "0.00"), ("unrealized PnL", "0.00"),
            ("total value", "0.00"), ("tick capture", "0"), ("inventory skew", "0.00")])

        orders = QGroupBox("Active Orders")
        ol = QVBoxLayout(orders)
        ol.addWidget(QLabel("side | price | qty | status | age | reason"))
        ol.addWidget(QLabel("No active orders (placeholder)."))

        lower.addWidget(market, 0, 0)
        lower.addWidget(acc, 0, 1)
        lower.addWidget(orders, 0, 2)
        root.addLayout(lower)

        logs_box = QGroupBox("Logs")
        logs_layout = QVBoxLayout(logs_box)
        self.logs_view = QPlainTextEdit()
        self.logs_view.setReadOnly(True)
        self.logs_view.setMaximumHeight(210)
        logs_layout.addWidget(self.logs_view)
        log_btns = QHBoxLayout()
        clear_btn = QPushButton("Clear Logs")
        clear_btn.clicked.connect(self.logs_view.clear)
        snap_btn = QPushButton("Save Snapshot")
        snap_btn.clicked.connect(self._save_snapshot)
        log_btns.addStretch(1)
        log_btns.addWidget(clear_btn)
        log_btns.addWidget(snap_btn)
        logs_layout.addLayout(log_btns)
        root.addWidget(logs_box)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.connect_btn = QPushButton("CONNECT")
        self.connect_btn.clicked.connect(self._connect_api)
        self.refresh_btn = QPushButton("REFRESH BALANCES")
        self.refresh_btn.clicked.connect(self._connect_api)
        self.settings_btn = QPushButton("SETTINGS")
        self.settings_btn.clicked.connect(self._open_settings)
        self.full_config_btn = QPushButton("FULL CONFIG")
        self.full_config_btn.clicked.connect(self._open_full_config)
        exit_btn = QPushButton("EXIT")
        exit_btn.clicked.connect(self.close)
        for btn in [self.connect_btn, self.refresh_btn, self.settings_btn, self.full_config_btn, exit_btn]:
            btn.setMinimumWidth(160)
            buttons.addWidget(btn)
        root.addLayout(buttons)

    def _set_api_status(self, status: str) -> None:
        self.api_status_label.setText(f"API STATUS: {status}")

    def _apply_connect_result(self, payload: dict) -> None:
        balances = payload.get("balances", {})
        euri = balances.get("EURI", {"free": 0, "locked": 0, "total": 0})
        usdt = balances.get("USDT", {"free": 0, "locked": 0, "total": 0})
        self.euri_bal.setText(f"free={euri['free']:.8f} / locked={euri['locked']:.8f} / total={euri['total']:.8f}")
        self.usdt_bal.setText(f"free={usdt['free']:.8f} / locked={usdt['locked']:.8f} / total={usdt['total']:.8f}")
        f = payload.get("filters", {})
        self.filters_label.setText(f"tickSize={f.get('tickSize', 'N/A')} / stepSize={f.get('stepSize', 'N/A')} / minNotional={f.get('minNotional', 'N/A')}")

    def _connect_api(self) -> None:
        self.connect_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self._set_api_status("CONNECTING")
        self.thread = QThread()
        self.worker = ConnectWorker(self.settings)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self._append_log)
        self.worker.status.connect(self._set_api_status)
        self.worker.result.connect(self._apply_connect_result)
        self.worker.done.connect(self.thread.quit)
        self.worker.done.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(lambda: self.connect_btn.setEnabled(True))
        self.thread.finished.connect(lambda: self.refresh_btn.setEnabled(True))
        self.thread.start()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = deep_merge(self.settings, dialog.payload())
            self._save_settings()
            self._append_log("[SETTINGS] grouped settings saved")

    def _open_full_config(self) -> None:
        dialog = FullConfigDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                data = dialog.parsed()
                if not isinstance(data, dict):
                    raise ValueError("settings root must be JSON object")
                self.settings = deep_merge(default_settings(), data)
                self._save_settings()
                self._append_log("[CONFIG] full config saved")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(self, "Config error", f"Invalid JSON: {exc}")
                self._append_log(f"[ERROR] config not saved: {exc}")

    def _save_snapshot(self) -> None:
        snapshot = {
            "ts": int(time.time()),
            "api_status": self.api_status_label.text(),
            "balances": {"euri": self.euri_bal.text(), "usdt": self.usdt_bal.text()},
            "filters": self.filters_label.text(),
        }
        path = CONFIG_PATH.parent / "snapshot.json"
        with path.open("w", encoding="utf-8") as file:
            json.dump(snapshot, file, indent=2, ensure_ascii=False)
        self._append_log(f"[SNAPSHOT] saved: {path}")


def main() -> int:
    app = QApplication(sys.argv)
    window = LUCTerminal()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
