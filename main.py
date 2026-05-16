import hashlib
import hmac
import json
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, QThread, Qt, QTimer, Signal, QUrl
from PySide6.QtWebSockets import QWebSocket
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
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "settings.json"


def default_settings() -> dict:
        return {
            "version": "0.2.0",
        "binance_api_key": "",
        "binance_api_secret": "",
        "use_testnet": False,
        "ui": {"latest_logs_rows": 220, "theme": "dark"},
        "general": {
            "base_symbol": "EURIUSDT",
            "parent_symbol": "EURUSDT",
            "max_open_cycles": 1,
            "default_order_size": 5.0,
            "max_inventory_shift": 0.2,
            "max_hold_sec": 30,
            "eur_ws_enabled": True,
            "euri_http_poll_sec": 4,
            "max_data_age_sec": 8,
            "eur_stale_ms": 15000,
            "euri_stale_ms": 10000,
            "status_hysteresis_ms": 3000,
            "tick_size": 0.0001,
            "trading_enabled": False,
            "require_order_confirmation": True,
            "max_live_order_size": 10.0,
            "allow_auto_orders": True,
            "execution_cooldown_sec": 15,
            "max_order_lifetime_sec": 60,
            "max_trade_log_rows_gui": 80,
            "max_event_log_rows_gui": 500,
        },
        "passive": {
            "passive_enabled": True,
            "passive_max_spread_ticks": 3,
            "passive_target_ticks": 1,
            "passive_order_size": 5.0,
            "passive_cooldown_sec": 2,
        },
        "trap": {
            "trap_enabled": False,
            "trap_min_gap_ticks": 2,
            "trap_max_spread_ticks": 4,
            "trap_target_ticks": 1,
            "trap_order_size": 5.0,
            "trap_cooldown_sec": 3,
            "cancel_if_gap_gone": True,
        },
            "risk_inventory": {
            "inventory_safe_min": 0.35,
            "inventory_safe_max": 0.65,
            "inventory_danger_max": 0.80,
            "inventory_critical_max": 0.90,
                "no_market_orders": True,
            },
            "inventory_engine": {
                "inventory_target_ratio": 0.5,
                "inventory_recovery_threshold": 0.12,
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




    def place_limit_buy(self, symbol: str, qty: float, price: float) -> dict:
        return self._request("POST", "/api/v3/order", params={"symbol": symbol, "side": "BUY", "type": "LIMIT", "timeInForce": "GTC", "quantity": f"{qty:.8f}", "price": f"{price:.8f}"}, signed=True)

    def place_limit_sell(self, symbol: str, qty: float, price: float) -> dict:
        return self._request("POST", "/api/v3/order", params={"symbol": symbol, "side": "SELL", "type": "LIMIT", "timeInForce": "GTC", "quantity": f"{qty:.8f}", "price": f"{price:.8f}"}, signed=True)

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self._request("DELETE", "/api/v3/order", params={"symbol": symbol, "orderId": order_id}, signed=True)

    def get_open_orders(self, symbol: str) -> list:
        return self._request("GET", "/api/v3/openOrders", params={"symbol": symbol}, signed=True)
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
                ("general.max_live_order_size", "max_live_order_size"),
                ("general.allow_auto_orders", "allow_auto_orders"),
                ("general.execution_cooldown_sec", "execution_cooldown_sec"),
                ("general.max_order_lifetime_sec", "max_order_lifetime_sec"),
                ("general.max_trade_log_rows_gui", "max_trade_log_rows_gui"),
                ("general.max_event_log_rows_gui", "max_event_log_rows_gui"),
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


class AllDataDialog(QDialog):
    def __init__(self, terminal: "LUCTerminal") -> None:
        super().__init__(terminal)
        self.terminal = terminal
        self.setWindowTitle("ALL DATA — LIVE DASHBOARD")
        self.resize(960, 760)
        layout = QVBoxLayout(self)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        layout.addWidget(self.view)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(700)
        self.refresh()

    def refresh(self) -> None:
        self.view.setPlainText(self.terminal._build_all_data_text())


class LUCTerminal(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = self._load_settings()
        self.thread: QThread | None = None
        self.worker: ConnectWorker | None = None
        self.setWindowTitle("LUC v0.1.12 — Balance Refresh Fix + Smart Runtime Logging")
        self.resize(1520, 920)
        self.runtime = self._default_runtime()
        self.log_file = self._open_log_file()
        self.ws: QWebSocket | None = None
        self.ws_connected_logged = False
        self.ws_start_skip_logged = False
        self.api_connect_in_progress = False
        self.api_connected_once = False
        self.market_data_started = False
        self.eur_ws_started = False
        self._init_ui()
        self.market_timer = QTimer(self)
        self.market_timer.timeout.connect(self._poll_euri)
        self.balance_timer = QTimer(self)
        self.balance_timer.timeout.connect(self._refresh_balances_only)
        self.balance_timer.start(20000)
        self.eur_watchdog = QTimer(self)
        self.eur_watchdog.timeout.connect(self._update_market_status)
        self.eur_watchdog.start(1000)
        self.started_at = time.time()
        self.client: BinanceClient | None = None
        self.open_orders_timer = QTimer(self)
        self.open_orders_timer.timeout.connect(self._refresh_open_orders)
        self._update_trading_button()
        self.last_status = {"api": "DISCONNECTED", "eur": "IDLE", "euri": "IDLE"}
        self.runtime.update({"euri_poll_count": 0, "euri_stale_count": 0, "eur_ticks": [], "active_orders_count": 0, "cycles": 0, "wins": 0, "losses": 0, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "tick_capture": 0})
        self._append_log("[v0.1.12] GUI cockpit initialized")
        self._append_log("[STABILITY] hysteresis enabled")
        self._append_log("[THEME] dark theme enabled")
        self._append_log("[SAFETY] No market data yet. No trading actions.")
        QTimer.singleShot(0, self._auto_start)
    def _default_runtime(self) -> dict:
        return {
            "eur": {},
            "euri": {},
            "fair_gap": None,
            "fair_gap_ticks": None,
            "source": {},
            "decision": {
                "data_fresh": False,
                "eur_fresh": False,
                "euri_fresh": False,
                "decision_freshness_source": "stale_ms",
                "euri_spread_ticks": None,
                "fair_gap_ticks": None,
                "parent_impulse": "UNKNOWN",
                "child_delay_ms": None,
                "inventory_zone": "UNKNOWN",
                "passive_status": "IDLE",
                "trap_status": "IDLE",
                "trap_direction": "NONE",
                "planned_action": "WAIT",
                "current_mode": "WAIT",
                "fsm_state": "IDLE",
                "passive_block_reason": "N/A",
                "trap_block_reason": "N/A",
                "block_reason": "N/A",
                "last_decision_time": None,
            },
            "last_logged": {"mode": None, "passive_status": None, "trap_direction": None, "inventory_zone": None},
            "last_order_error": "",
            "execution_cooldown_until": 0.0,
            "last_block_reason": "",
            "last_execution_block_reason": "",
            "last_fair_gap_zone": None,
            "last_balance_refresh_ts": 0.0,
            "last_balance_change_ts": 0.0,
            "last_log_event_key": "",
            "proposed_notional": 0.0,
            "min_notional": 0.0,
            "failed_order_keys": {},
            "last_error_log_times": {},
            "log_events": {},
            "order_status_by_id": {},
            "cycles": {},
            "next_cycle_id": 1,
            "order_to_cycle": {},
            "spread_ownership": "NEUTRAL",
            "inventory": {
                "target_ratio": 0.5,
                "bias": "NEUTRAL",
                "pressure": 0.0,
                "recovery_mode": False,
            },
            "pnl": {
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "spread_captured_ticks": 0,
                "fees_paid": 0.0,
                "cycle_wins": 0,
                "cycle_losses": 0,
                "cycle_winrate": 0.0,
                "avg_cycle_time": 0.0,
            },
        }

    def _open_log_file(self):
        logs_dir = CONFIG_PATH.parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        return {"events": logs_dir / "events.log", "trades": logs_dir / "trades.log"}

    def _rotate_log_if_needed(self, key: str) -> None:
        path = self.log_file[key]
        if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            path.rename(path.with_name(f"{key}_{ts}.log"))

    def _load_settings(self) -> dict:
        base = default_settings()
        if not CONFIG_PATH.exists():
            return base
        with CONFIG_PATH.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
        return deep_merge(base, loaded)

    def _save_settings(self) -> None:
        self.settings["version"] = "0.1.12"
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as file:
            json.dump(self.settings, file, indent=2, ensure_ascii=False)

    def _append_log(self, message: str) -> None:
        max_rows = int(self.settings.get("general", {}).get("max_event_log_rows_gui", 500))
        lines = self.logs_view.toPlainText().splitlines()
        lines.append(message)
        if len(lines) > max_rows:
            lines = lines[-max_rows:]
        self.logs_view.setPlainText("\n".join(lines))
        self.logs_view.verticalScrollBar().setValue(self.logs_view.verticalScrollBar().maximum())
        self._rotate_log_if_needed("events")
        with self.log_file["events"].open("a", encoding="utf-8") as fh:
            fh.write(message + "\n")
            fh.flush()

    def _append_trade_log(self, event: str, payload: str) -> None:
        msg = f"[{event}] {payload}"
        max_rows = int(self.settings.get("general", {}).get("max_trade_log_rows_gui", 80))
        lines = self.trade_logs_view.toPlainText().splitlines()
        lines.append(msg)
        if len(lines) > max_rows:
            lines = lines[-max_rows:]
        self.trade_logs_view.setPlainText("\n".join(lines))
        self.trade_logs_view.verticalScrollBar().setValue(self.trade_logs_view.verticalScrollBar().maximum())
        self._rotate_log_if_needed("trades")
        with self.log_file["trades"].open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
            fh.flush()

    def _log_once_or_changed(self, key: str, message: str, min_interval_sec: int = 10) -> None:
        now = time.time()
        events = self.runtime.setdefault("log_events", {})
        event = events.get(key, {"last_message": None, "last_ts": 0.0})
        message_changed = event.get("last_message") != message
        time_elapsed = now - float(event.get("last_ts", 0.0)) >= float(min_interval_sec)
        if message_changed or time_elapsed:
            self._append_log(message)
            events[key] = {"last_message": message, "last_ts": now}
            self.runtime["last_log_event_key"] = key

    def _status_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFrameShape(QFrame.Shape.StyledPanel)
        label.setMinimumHeight(30)
        label.setProperty("statusType", "idle")
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
        self.version_label = self._status_label("LUC VERSION: 0.1.12")
        self.api_status_label = self._status_label("API STATUS: DISCONNECTED")
        self.eur_status = self._status_label("EURUSDT STATUS: IDLE")
        self.euri_status = self._status_label("EURIUSDT STATUS: IDLE")
        self.mode_label = self._status_label("CURRENT MODE: NONE")
        self.inventory_zone = self._status_label("INVENTORY ZONE: SAFE")
        for w in [self.version_label, self.api_status_label, self.eur_status, self.euri_status, self.mode_label, self.inventory_zone]:
            top.addWidget(w)
        root.addLayout(top)

        modes = QGridLayout()
        self.passive_card = self._make_mode_card("PASSIVE CORRIDOR", [
            ("Status", "IDLE"), ("Spread ticks", "—"), ("Corridor state", "—"), ("Center ownership", "—"),
            ("Recycle readiness", "—"), ("Planned action", "WAIT"), ("Block reason", "N/A"), ("Score", "0"), ("Stability", "LOW"), ("Last decision age", "—")])
        modes.addWidget(self.passive_card, 0, 0)
        self.trap_card = self._make_mode_card("AGGRESSIVE TRAP", [
            ("Status", "IDLE"), ("Fair gap ticks", "—"), ("Trap direction", "NONE"), ("Parent impulse", "—"),
            ("Child delay", "—"), ("Weak side", "—"), ("Planned action", "WAIT"), ("Block reason", "N/A"), ("Score", "0"), ("Stability", "LOW"), ("Last decision age", "—")])
        modes.addWidget(self.trap_card, 0, 1)
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
        ol.addWidget(QLabel("cycle_id | mode | leg | side | price | qty | status | age | orderId"))
        self.active_orders_view = QPlainTextEdit()
        self.active_orders_view.setReadOnly(True)
        self.active_orders_view.setMaximumHeight(130)
        self.active_orders_view.setPlainText("No active orders.")
        ol.addWidget(self.active_orders_view)

        lower.addWidget(market, 0, 0)
        lower.addWidget(acc, 0, 1)
        lower.addWidget(orders, 0, 2)
        root.addLayout(lower)

        logs_box = QGroupBox("Event Log")
        logs_layout = QVBoxLayout(logs_box)
        self.logs_view = QPlainTextEdit()
        self.logs_view.setReadOnly(True)
        self.logs_view.setMaximumHeight(210)
        logs_layout.addWidget(self.logs_view)
        root.addWidget(logs_box)
        trade_box = QGroupBox("Trade Log")
        trade_layout = QVBoxLayout(trade_box)
        self.trade_logs_view = QPlainTextEdit()
        self.trade_logs_view.setReadOnly(True)
        self.trade_logs_view.setMaximumHeight(120)
        trade_layout.addWidget(self.trade_logs_view)
        root.addWidget(trade_box)

        buttons = QHBoxLayout()
        self.trading_btn = QPushButton("START TRADING")
        self.trading_btn.setMinimumWidth(180)
        self.trading_btn.clicked.connect(self._toggle_trading)
        buttons.addWidget(self.trading_btn)
        buttons.addStretch(1)
        self.menu_btn = QPushButton("MENU")
        self.menu_btn.setMinimumWidth(160)
        self.menu_btn.clicked.connect(self._open_tools_menu)
        buttons.addWidget(self.menu_btn)
        root.addLayout(buttons)
        self._apply_theme()

    def _apply_theme(self) -> None:
        if self.settings.get("ui", {}).get("theme", "dark") != "dark":
            return
        self.setStyleSheet("""
        QMainWindow, QWidget { background-color: #1b1f24; color: #d7dde5; }
        QGroupBox { border: 1px solid #4a5360; border-radius: 8px; margin-top: 10px; padding-top: 10px; background-color: #242a31; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #cfd6df; }
        QLabel[statusType="good"] { color: #7CFC8A; font-weight: 700; }
        QLabel[statusType="warn"] { color: #ffb74d; font-weight: 700; }
        QLabel[statusType="bad"] { color: #ff6b6b; font-weight: 700; }
        QLabel[statusType="idle"] { color: #8aa0b8; }
        QLabel[statusType="info"] { color: #6eb5ff; font-weight: 700; }
        QPlainTextEdit { background-color: #14181d; color: #d6e0ea; border: 1px solid #3d4652; border-radius: 6px; }
        QPushButton { background-color: #2c3440; color: #d7dde5; border: 1px solid #4a5360; border-radius: 6px; padding: 6px 10px; }
        """)

    def _open_tools_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("SETTINGS", self._open_settings)
        menu.addAction("FULL CONFIG", self._open_full_config)
        menu.addAction("ALL DATA", self._show_all_data)
        menu.addAction("SAVE SNAPSHOT", self._save_snapshot)
        menu.addAction("CLEAR LOGS", self.logs_view.clear)
        menu.addAction("EXIT", self.close)
        menu.exec(self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomLeft()))

    def _set_api_status(self, status: str) -> None:
        self.api_status_label.setText(f"API STATUS: {status}")
        if self.last_status.get("api") != status:
            self._append_log(f"[STATUS] API -> {status}")
            self.last_status["api"] = status

    def _apply_connect_result(self, payload: dict) -> None:
        if not self.client:
            self.client = BinanceClient(self.settings.get("binance_api_key", ""), self.settings.get("binance_api_secret", ""), bool(self.settings.get("use_testnet", False)))
            self.open_orders_timer.start(4000)
        balances = payload.get("balances", {})
        euri = balances.get("EURI", {"free": 0, "locked": 0, "total": 0})
        usdt = balances.get("USDT", {"free": 0, "locked": 0, "total": 0})
        self.euri_bal.setText(f"free={euri['free']:.8f} / locked={euri['locked']:.8f} / total={euri['total']:.8f}")
        self.usdt_bal.setText(f"free={usdt['free']:.8f} / locked={usdt['locked']:.8f} / total={usdt['total']:.8f}")
        f = payload.get("filters", {})
        self.runtime["filters"] = f
        self.filters_label.setText(f"tickSize={f.get('tickSize', 'N/A')} / stepSize={f.get('stepSize', 'N/A')} / minNotional={f.get('minNotional', 'N/A')}")
        self._start_market_data()
        self.api_connected_once = True
        self._append_log("[ACCOUNT] balances refreshed")

    def _start_market_data(self):
        if self.market_data_started:
            return
        self.market_data_started = True
        self._start_eur_ws()
        sec = float(self.settings.get("general", {}).get("euri_http_poll_sec", 4))
        self.market_timer.start(int(max(3.0, min(5.0, sec)) * 1000))
        self.euri_status.setText("EURIUSDT STATUS: HTTP POLLING")
        self._poll_euri()

    def _start_eur_ws(self):
        if not self.settings.get("general", {}).get("eur_ws_enabled", True):
            return
        ws_alive = self.ws is not None and isValid(self.ws) and self.ws.state() == QWebSocket.SocketState.ConnectedState
        if self.eur_ws_started or ws_alive:
            if not self.ws_start_skip_logged:
                self._append_log("[WS] start skipped: already started")
                self.ws_start_skip_logged = True
            return
        self.eur_ws_started = True
        self.ws_start_skip_logged = False
        self.ws_connected_logged = False
        self.eur_status.setText("EURUSDT STATUS: WS CONNECTING")
        self.ws = QWebSocket()
        self.ws.connected.connect(self._on_ws_connected)
        self.ws.disconnected.connect(self._on_ws_disconnected)
        self.ws.textMessageReceived.connect(self._on_ws_message)
        self.ws.errorOccurred.connect(lambda _: self._set_market_status(self.eur_status, "EURUSDT STATUS: ERROR", "eur"))
        self.ws.open(QUrl("wss://stream.binance.com:9443/ws/eurusdt@bookTicker"))

    def _on_ws_connected(self):
        if not self.ws_connected_logged:
            self._append_log("[WS] EURUSDT connected")
            self.ws_connected_logged = True

    def _on_ws_disconnected(self):
        self._append_log("[WS] EURUSDT disconnected")
        self._set_market_status(self.eur_status, "EURUSDT STATUS: STALE", "eur")
        self.eur_ws_started = False
        self.ws_connected_logged = False
        QTimer.singleShot(2000, self._start_eur_ws)

    def _on_ws_message(self, message: str):
        data = json.loads(message)
        bid, ask = float(data.get("b", 0)), float(data.get("a", 0))
        now = time.time()
        first = not self.runtime.get("eur")
        self.runtime["eur"] = {"bid": bid, "ask": ask, "mid": (bid + ask) / 2, "time": now, "source": "ws:bookTicker"}
        if first:
            self._append_log("[DATA] first EUR tick")
        self.eur_mid.setText(f"{self.runtime['eur']['mid']:.6f}")
        self._set_market_status(self.eur_status, "EURUSDT STATUS: LIVE", "eur")
        self.runtime["eur_ticks"] = [t for t in self.runtime["eur_ticks"] if now - t < 60] + [now]
        self._recompute()

    def _poll_euri(self):
        try:
            client = BinanceClient("", "", bool(self.settings.get("use_testnet", False)))
            ticker = client._request("GET", "/api/v3/ticker/bookTicker", params={"symbol": "EURIUSDT"})
            bid, ask = float(ticker.get("bidPrice", 0)), float(ticker.get("askPrice", 0))
            now = time.time()
            first = not self.runtime["euri"]
            self.runtime["euri"] = {"bid": bid, "ask": ask, "mid": (bid + ask) / 2, "spread_ticks": self._ticks(ask - bid), "time": now, "source": "http:bookTicker"}
            self.runtime["euri_poll_count"] += 1
            if first:
                self._append_log("[DATA] first EURI tick")
            self.euri_bid.setText(f"{bid:.6f}")
            self.euri_ask.setText(f"{ask:.6f}")
            self.euri_spread.setText(str(self.runtime["euri"]["spread_ticks"]))
            self._set_market_status(self.euri_status, "EURIUSDT STATUS: LIVE", "euri")
            self._recompute()
        except Exception as exc:  # noqa: BLE001
            self._set_market_status(self.euri_status, "EURIUSDT STATUS: ERROR", "euri")
            self._append_log(f"[ERROR] EURI poll failed: {exc}")

    def _ticks(self, value: float) -> int:
        tick = float(self.settings.get("general", {}).get("tick_size", 0.0001))
        return int(round(value / tick)) if tick > 0 else 0

    def _recompute(self):
        eur, euri = self.runtime.get("eur", {}), self.runtime.get("euri", {})
        if not eur or not euri:
            return
        gap = eur["mid"] - euri["mid"]
        self.runtime["fair_gap"] = gap
        self.runtime["fair_gap_ticks"] = self._ticks(gap)
        gap_zone = "NONE"
        trap_min_gap = int(self.settings.get("trap", {}).get("trap_min_gap_ticks", 2))
        if self.runtime["fair_gap_ticks"] is not None:
            if self.runtime["fair_gap_ticks"] >= trap_min_gap:
                gap_zone = "BUY_ZONE"
            elif self.runtime["fair_gap_ticks"] <= -trap_min_gap:
                gap_zone = "SELL_ZONE"
        if self.runtime.get("last_fair_gap_zone") != gap_zone:
            self._append_log(f"[DATA] fair_gap zone changed: {self.runtime.get('last_fair_gap_zone')} -> {gap_zone}")
            self.runtime["last_fair_gap_zone"] = gap_zone
        self._update_decisions()

    def _evaluate_inventory_zone(self, euri_total: float, usdt_total: float, euri_mid: float) -> tuple[str, float | None]:
        total_euri_equiv = euri_total + (usdt_total / max(euri_mid, 1e-9) if euri_mid > 0 else 0.0)
        if total_euri_equiv <= 0:
            return "SAFE", None
        skew = euri_total / total_euri_equiv
        risk = self.settings.get("risk_inventory", {})
        safe_min = float(risk.get("inventory_safe_min", 0.0))
        safe_max = float(risk.get("inventory_safe_max", 0.3))
        danger_max = float(risk.get("inventory_danger_max", 0.6))
        critical_max = float(risk.get("inventory_critical_max", 0.8))
        if safe_min <= skew <= safe_max:
            return "SAFE", skew
        if skew < safe_min:
            return "LOW_EURI", skew
        if skew < danger_max:
            return "DANGER", skew
        if skew >= critical_max:
            return "CRITICAL", skew
        return "DANGER", skew

    @staticmethod
    def _score_to_stability(score: int) -> str:
        if score >= 80:
            return "HIGH"
        if score >= 50:
            return "MEDIUM"
        return "LOW"

    def _update_decisions(self) -> None:
        now = time.time()
        eur, euri = self.runtime.get("eur", {}), self.runtime.get("euri", {})
        general = self.settings.get("general", {})
        passive = self.settings.get("passive", {})
        trap = self.settings.get("trap", {})
        eur_stale_ms = int(general.get("eur_stale_ms", 15000))
        euri_stale_ms = int(general.get("euri_stale_ms", 10000))
        max_age = float(general.get("max_data_age_sec", 8))
        eur_age_ms = int((now - eur.get("time", now)) * 1000) if eur else 10**9
        euri_age_ms = int((now - euri.get("time", now)) * 1000) if euri else 10**9
        eur_fresh = bool(eur) and (eur_age_ms <= eur_stale_ms)
        euri_fresh = bool(euri) and (euri_age_ms <= euri_stale_ms)
        freshness_source = "stale_ms"
        if not eur or not euri:
            eur_fresh = bool(eur) and (now - eur.get("time", 0) <= max_age)
            euri_fresh = bool(euri) and (now - euri.get("time", 0) <= max_age)
            freshness_source = "legacy_max_data_age_sec"
        data_fresh = eur_fresh and euri_fresh
        spread_ticks = euri.get("spread_ticks")
        fair_gap_ticks = self.runtime.get("fair_gap_ticks")
        child_delay_ms = int((now - euri.get("time", now)) * 1000) if euri else None
        parent_impulse = "UNKNOWN"
        if eur_fresh and len(self.runtime.get("eur_ticks", [])) >= 6:
            recent = [t for t in self.runtime["eur_ticks"] if now - t <= 2]
            parent_impulse = "UP" if len(recent) >= 4 and fair_gap_ticks is not None and fair_gap_ticks > 0 else "DOWN" if len(recent) >= 4 and fair_gap_ticks is not None and fair_gap_ticks < 0 else "CALM"
        elif eur_fresh:
            parent_impulse = "CALM"

        euri_total = self._parse_total(self.euri_bal.text())
        usdt_total = self._parse_total(self.usdt_bal.text())
        euri_mid = euri.get("mid", 0) if euri else 0
        inventory_zone, _ = self._evaluate_inventory_zone(euri_total, usdt_total, euri_mid)
        active_orders = int(self.runtime.get("active_orders_count", 0))

        passive_status = "READY"
        passive_reason = "N/A"
        if not bool(passive.get("passive_enabled", True)):
            passive_status, passive_reason = "BLOCKED", "DISABLED"
        elif not data_fresh:
            passive_status, passive_reason = "BLOCKED", "DATA_STALE"
        elif spread_ticks is None or spread_ticks > int(passive.get("passive_max_spread_ticks", 3)):
            passive_status, passive_reason = "BLOCKED", "SPREAD_TOO_WIDE"
        elif fair_gap_ticks is None or abs(fair_gap_ticks) >= int(trap.get("trap_min_gap_ticks", 2)):
            passive_status, passive_reason = "BLOCKED", "GAP_TOO_BIG"
        elif inventory_zone != "SAFE":
            passive_status, passive_reason = "BLOCKED", "INVENTORY_NOT_SAFE"

        trap_dir = "NONE"
        trap_status = "IDLE"
        trap_reason = "N/A"
        trap_min_gap = int(trap.get("trap_min_gap_ticks", 2))
        if fair_gap_ticks is not None:
            if fair_gap_ticks >= trap_min_gap:
                trap_dir = "BUY_TRAP"
            elif fair_gap_ticks <= -trap_min_gap:
                trap_dir = "SELL_TRAP"

        if not bool(trap.get("trap_enabled", False)):
            trap_status, trap_reason = "BLOCKED", "DISABLED"
        elif not data_fresh:
            trap_status, trap_reason = "BLOCKED", "DATA_STALE"
        elif trap_dir == "NONE":
            trap_status, trap_reason = "BLOCKED", "GAP_TOO_SMALL"
        elif spread_ticks is None or spread_ticks > int(trap.get("trap_max_spread_ticks", 3)):
            trap_status, trap_reason = "BLOCKED", "SPREAD_TOO_WIDE"
        elif inventory_zone == "CRITICAL":
            trap_status, trap_reason = "BLOCKED", "INVENTORY_CRITICAL"
        elif trap_dir == "SELL_TRAP" and euri_total <= float(trap.get("trap_order_size", 1.0)):
            trap_status, trap_reason = "BLOCKED", "NO_EURI_BALANCE"
        else:
            trap_status = "READY"

        current_mode = "AGGRESSIVE_TRAP" if trap_status == "READY" else "PASSIVE_CORRIDOR" if passive_status == "READY" else "WAIT"
        planned_action = "WAIT"
        if current_mode == "AGGRESSIVE_TRAP" and trap_dir == "BUY_TRAP":
            planned_action = "BUY_EURI_THEN_SELL_PLUS_1"
        elif current_mode == "AGGRESSIVE_TRAP" and trap_dir == "SELL_TRAP":
            planned_action = "SELL_EURI_THEN_BUY_MINUS_1"
        elif current_mode == "PASSIVE_CORRIDOR":
            planned_action = "PASSIVE_BUY_SELL"

        passive_score = 0
        if bool(passive.get("passive_enabled", False)):
            passive_score += 25 if data_fresh else 0
            passive_score += 20 if spread_ticks is not None and spread_ticks <= int(passive.get("passive_max_spread_ticks", 3)) else 0
            passive_score += 20 if fair_gap_ticks is not None and abs(fair_gap_ticks) < int(trap.get("trap_min_gap_ticks", 2)) else 0
            passive_score += 20 if inventory_zone == "SAFE" else 0
            passive_score += 15
        trap_score = 0
        trap_enabled = bool(trap.get("trap_enabled", False))
        if trap_enabled:
            trap_score += 20 if data_fresh else 0
            trap_score += 20 if fair_gap_ticks is not None and abs(fair_gap_ticks) >= int(trap.get("trap_min_gap_ticks", 2)) else 0
            trap_score += 15 if spread_ticks is not None and spread_ticks <= int(trap.get("trap_max_spread_ticks", 4)) else 0
            trap_score += 15 if inventory_zone != "CRITICAL" else 0
            trap_score += 15
            trap_score += 15 if trap_dir != "SELL_TRAP" or euri_total > float(trap.get("trap_order_size", 1.0)) else 0
        inv_cfg = self.settings.get("inventory_engine", {})
        target_ratio = float(inv_cfg.get("inventory_target_ratio", 0.5))
        _, skew = self._evaluate_inventory_zone(euri_total, usdt_total, euri_mid)
        skew = skew if skew is not None else target_ratio
        pressure = skew - target_ratio
        bias = "SELL" if pressure > 0.02 else "BUY" if pressure < -0.02 else "NEUTRAL"
        recovery_mode = abs(pressure) >= float(inv_cfg.get("inventory_recovery_threshold", 0.12))
        self.runtime["inventory"] = {"target_ratio": target_ratio, "bias": bias, "pressure": pressure, "recovery_mode": recovery_mode}
        ownership = "CENTER" if abs(fair_gap_ticks or 0) <= 1 else "ASK_CONTROL" if (fair_gap_ticks or 0) > 1 else "BID_CONTROL"
        self.runtime["spread_ownership"] = ownership
        fsm_state = "TRAP_RUNNING" if current_mode == "AGGRESSIVE_TRAP" else "PASSIVE_RUNNING" if current_mode == "PASSIVE_CORRIDOR" else "IDLE"
        if recovery_mode:
            fsm_state = "INVENTORY_RECOVERY"
        decision = self.runtime["decision"]
        decision.update({
            "data_fresh": data_fresh,
            "eur_fresh": eur_fresh,
            "euri_fresh": euri_fresh,
            "decision_freshness_source": freshness_source,
            "euri_spread_ticks": spread_ticks,
            "fair_gap_ticks": fair_gap_ticks,
            "parent_impulse": parent_impulse,
            "child_delay_ms": child_delay_ms,
            "inventory_zone": inventory_zone,
            "passive_status": passive_status,
            "trap_status": trap_status,
            "trap_direction": trap_dir,
            "planned_action": planned_action,
            "current_mode": current_mode,
            "fsm_state": fsm_state,
            "passive_block_reason": passive_reason,
            "trap_block_reason": trap_reason,
            "block_reason": trap_reason if current_mode == "AGGRESSIVE_TRAP" else passive_reason if current_mode == "PASSIVE_CORRIDOR" else (trap_reason if trap_reason != "N/A" else passive_reason),
            "last_decision_time": now,
            "passive_score": passive_score,
            "trap_score": trap_score,
            "passive_stability": self._score_to_stability(passive_score),
            "trap_stability": "DISABLED" if not trap_enabled else self._score_to_stability(trap_score),
        })
        self._refresh_mode_cards()
        self._log_decision_changes()
        self._execute_auto_order()

    def _refresh_mode_cards(self) -> None:
        d = self.runtime["decision"]
        self.mode_label.setText(f"CURRENT MODE: {d['current_mode']}")
        self.inventory_zone.setText(f"INVENTORY ZONE: {d['inventory_zone']}")
        self._set_card_value(self.passive_card, "Status", d["passive_status"])
        self._set_card_value(self.passive_card, "Spread ticks", str(d["euri_spread_ticks"]) if d["euri_spread_ticks"] is not None else "—")
        corridor_state = "UNKNOWN" if not self.runtime.get("euri") else ("STALE" if not d["data_fresh"] else ("WIDE" if (d["euri_spread_ticks"] or 0) > int(self.settings.get("passive", {}).get("passive_max_spread_ticks", 3)) else "STABLE"))
        self._set_card_value(self.passive_card, "Corridor state", corridor_state)
        self._set_card_value(self.passive_card, "Center ownership", self.runtime.get("spread_ownership", "NEUTRAL"))
        self._set_card_value(self.passive_card, "Recycle readiness", "READY" if d["passive_status"] == "READY" else "BLOCKED")
        self._set_card_value(self.passive_card, "Planned action", "PASSIVE_BUY_SELL" if d["passive_status"] == "READY" else "WAIT")
        self._set_card_value(self.passive_card, "Block reason", d["passive_block_reason"])
        self._set_card_value(self.passive_card, "Score", str(d.get("passive_score", 0)))
        self._set_card_value(self.passive_card, "Stability", d.get("passive_stability", "LOW"))
        self._set_card_value(self.passive_card, "Last decision age", f"{int((time.time() - (d.get('last_decision_time') or time.time()))*1000)} ms")
        self._set_card_value(self.trap_card, "Status", d["trap_status"])
        self._set_card_value(self.trap_card, "Fair gap ticks", str(d["fair_gap_ticks"]) if d["fair_gap_ticks"] is not None else "—")
        self._set_card_value(self.trap_card, "Trap direction", d["trap_direction"])
        self._set_card_value(self.trap_card, "Parent impulse", d["parent_impulse"])
        self._set_card_value(self.trap_card, "Child delay", f"{d['child_delay_ms']} ms" if d["child_delay_ms"] is not None else "—")
        weak_side = "ASK" if d["trap_direction"] == "BUY_TRAP" else "BID" if d["trap_direction"] == "SELL_TRAP" else "NONE"
        self._set_card_value(self.trap_card, "Weak side", weak_side)
        self._set_card_value(self.trap_card, "Planned action", d["planned_action"] if d["trap_status"] == "READY" else "WAIT")
        self._set_card_value(self.trap_card, "Block reason", d["trap_block_reason"])
        self._set_card_value(self.trap_card, "Score", str(d.get("trap_score", 0)))
        self._set_card_value(self.trap_card, "Stability", d.get("trap_stability", "LOW"))
        self._set_card_value(self.trap_card, "Last decision age", f"{int((time.time() - (d.get('last_decision_time') or time.time()))*1000)} ms")
        self._apply_status_colors()

    def _apply_status_colors(self) -> None:
        labels = [self.api_status_label, self.eur_status, self.euri_status, self.mode_label, self.inventory_zone]
        for label in labels:
            text = label.text()
            t = "idle"
            if any(x in text for x in ["LIVE", "READY", "SAFE", "BUY_TRAP"]):
                t = "good"
            if any(x in text for x in ["LOW_EURI"]):
                t = "info"
            if any(x in text for x in ["WAIT", "IDLE"]):
                t = "idle"
            if any(x in text for x in ["BLOCKED", "STALE", "DANGER", "SELL_TRAP"]):
                t = "warn"
            if any(x in text for x in ["ERROR", "CRITICAL"]):
                t = "bad"
            label.setProperty("statusType", t)
            label.style().unpolish(label)
            label.style().polish(label)

    def _set_card_value(self, card: QGroupBox, field_name: str, value: str) -> None:
        form = card.layout()
        if not isinstance(form, QFormLayout):
            return
        for i in range(form.rowCount()):
            label_w = form.itemAt(i, QFormLayout.ItemRole.LabelRole).widget()
            field_w = form.itemAt(i, QFormLayout.ItemRole.FieldRole).widget()
            if isinstance(label_w, QLabel) and isinstance(field_w, QLabel) and label_w.text() == field_name and field_w.text() != value:
                field_w.setText(value)
                return

    def _log_decision_changes(self) -> None:
        d = self.runtime["decision"]
        last = self.runtime["last_logged"]
        if last["mode"] != d["current_mode"]:
            self._append_log(f"[MODE] current mode changed: {last['mode']} -> {d['current_mode']}")
            last["mode"] = d["current_mode"]
        if last["passive_status"] != d["passive_status"]:
            self._append_log(f"[PASSIVE] status changed: {last['passive_status']} -> {d['passive_status']}")
            last["passive_status"] = d["passive_status"]
        if last["trap_direction"] != d["trap_direction"]:
            self._append_log(f"[TRAP] direction changed: {last['trap_direction']} -> {d['trap_direction']}")
            last["trap_direction"] = d["trap_direction"]
        if last["inventory_zone"] != d["inventory_zone"]:
            self._append_log(f"[INVENTORY] zone changed: {last['inventory_zone']} -> {d['inventory_zone']}")
            last["inventory_zone"] = d["inventory_zone"]
        self._log_once_or_changed("decision_passive_block_reason", f"[DECISION] passive block reason: {d['passive_block_reason']}", 30)
        trap_enabled = bool(self.settings.get("trap", {}).get("trap_enabled", False))
        if not trap_enabled:
            self._log_once_or_changed("decision_trap_disabled", "[DECISION] trap block reason: DISABLED", 3600)
        elif d["trap_block_reason"] != "DISABLED":
            self._log_once_or_changed("decision_trap_block_reason", f"[DECISION] trap block reason: {d['trap_block_reason']}", 30)
        score_band = d.get("trap_stability", "DISABLED") if d.get("current_mode") == "AGGRESSIVE_TRAP" else d.get("passive_stability", "LOW")
        self._log_once_or_changed("decision_score_band", f"[DECISION] score band: {score_band}", 30)

    def _update_market_status(self):
        general = self.settings.get("general", {})
        eur_stale_ms = int(general.get("eur_stale_ms", 15000))
        euri_stale_ms = int(general.get("euri_stale_ms", 10000))
        hysteresis_ms = int(general.get("status_hysteresis_ms", 3000))
        now = time.time()
        for key, label, prefix, stale_ms in [("eur", self.eur_status, "EURUSDT STATUS: ", eur_stale_ms), ("euri", self.euri_status, "EURIUSDT STATUS: ", euri_stale_ms)]:
            data = self.runtime.get(key, {})
            age_ms = int((now - data.get("time", now)) * 1000) if data else 10**9
            if "ERROR" in label.text():
                continue
            current = "LIVE" if "LIVE" in label.text() else "STALE" if "STALE" in label.text() else "IDLE"
            if current == "LIVE" and age_ms > stale_ms + hysteresis_ms:
                self.runtime["euri_stale_count"] += 1 if key == "euri" else 0
                self._log_once_or_changed(f"data_{key}_stale_live", f"[DATA] {key.upper()} age became stale", 10)
                self._set_market_status(label, prefix + "STALE", key)
            elif current == "STALE" and age_ms < stale_ms:
                self._log_once_or_changed(f"data_{key}_stale_live", f"[DATA] {key.upper()} age became live", 10)
                self._set_market_status(label, prefix + "LIVE", key)


    def _update_trading_button(self) -> None:
        enabled = bool(self.settings.get("general", {}).get("trading_enabled", False))
        self.trading_btn.setText("STOP TRADING" if enabled else "START TRADING")

    def _toggle_trading(self) -> None:
        general = self.settings.setdefault("general", {})
        general["trading_enabled"] = not bool(general.get("trading_enabled", False))
        self._save_settings()
        state = "enabled" if general["trading_enabled"] else "disabled"
        self._log_once_or_changed("execution_trading_state", f"[EXECUTION] trading {state}", 2)
        self._update_trading_button()

    def _create_cycle(self, mode: str, entry_side: str, entry_price: float, qty: float, target_ticks: int) -> dict:
        cycle_id = str(self.runtime.get("next_cycle_id", 1))
        self.runtime["next_cycle_id"] = int(self.runtime.get("next_cycle_id", 1)) + 1
        tick = float(self.settings.get("general", {}).get("tick_size", 0.0001))
        exit_side = "SELL" if entry_side == "BUY" else "BUY"
        delta = tick * max(1, int(target_ticks))
        exit_price = entry_price + delta if entry_side == "BUY" else max(tick, entry_price - delta)
        cycle = {
            "cycle_id": cycle_id,
            "mode": mode,
            "entry_side": entry_side,
            "exit_side": exit_side,
            "entry_order_id": None,
            "exit_order_id": None,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "qty": qty,
            "state": "ENTRY_PENDING",
            "created_ts": time.time(),
            "updated_ts": time.time(),
        }
        self.runtime.setdefault("cycles", {})[cycle_id] = cycle
        self._append_log(f"[CYCLE] created {mode} #{cycle_id}")
        return cycle

    def _prepare_order(self) -> dict | None:
        d = self.runtime.get("decision", {})
        general = self.settings.get("general", {})
        trap = self.settings.get("trap", {})
        passive = self.settings.get("passive", {})
        risk = self.settings.get("risk_inventory", {})
        euri = self.runtime.get("euri", {})
        now = time.time()
        cooldown_until = float(self.runtime.get("execution_cooldown_until", 0.0))
        if now < cooldown_until:
            self.runtime["last_block_reason"] = "LAST_ERROR_COOLDOWN"
            self.runtime["last_execution_block_reason"] = "LAST_ERROR_COOLDOWN"
            return None
        if not bool(general.get("trading_enabled", False)) or not bool(general.get("allow_auto_orders", True)):
            self.runtime["last_execution_block_reason"] = "TRADING_DISABLED"
            return None
        if d.get("current_mode") == "WAIT" or not d.get("data_fresh"):
            self.runtime["last_execution_block_reason"] = "WAIT_OR_ACTIVE_ORDERS_OR_STALE"
            return None
        if not bool(risk.get("no_market_orders", True)):
            self.runtime["last_execution_block_reason"] = "MARKET_ORDERS_ALLOWED_BLOCK"
            return None
        mode = d.get("current_mode")
        side = None
        qty = 0.0
        price = 0.0
        reason = ""
        target = ""
        cycle_mode = "PASSIVE"
        target_ticks = int(passive.get("passive_target_ticks", 1))
        if mode == "AGGRESSIVE_TRAP":
            if d.get("trap_direction") == "BUY_TRAP":
                side, qty, price, reason, target = "BUY", float(trap.get("trap_order_size", 1.0)), float(euri.get("ask", 0.0)), "BUY_TRAP fair_gap", "SELL +1 tick"
                cycle_mode = "BUY_TRAP"
                target_ticks = int(trap.get("trap_target_ticks", 1))
            elif d.get("trap_direction") == "SELL_TRAP":
                free = self._parse_free(self.euri_bal.text())
                side, qty, price, reason, target = "SELL", min(float(trap.get("trap_order_size", 1.0)), free), float(euri.get("bid", 0.0)), "SELL_TRAP fair_gap", "BUY -1 tick"
                cycle_mode = "SELL_TRAP"
                target_ticks = int(trap.get("trap_target_ticks", 1))
        elif mode == "PASSIVE_CORRIDOR":
            inv_bias = self.runtime.get("inventory", {}).get("bias", "NEUTRAL")
            if inv_bias == "SELL":
                side, qty, price = "SELL", float(passive.get("passive_order_size", 1.0)), float(euri.get("ask", 0.0))
            else:
                side, qty, price = "BUY", float(passive.get("passive_order_size", 1.0)), float(euri.get("bid", 0.0))
            reason, target = "PASSIVE_CORRIDOR recycle", "paired passive recycle"
        if not side or qty <= 0 or price <= 0:
            self.runtime["last_execution_block_reason"] = "INVALID_PROPOSAL"
            return None
        self.runtime["last_execution_block_reason"] = "NONE"
        cycle = self._create_cycle(cycle_mode, side, price, qty, target_ticks)
        return self._preflight_order({"mode": mode, "cycle_id": cycle["cycle_id"], "side": side, "symbol": "EURIUSDT", "price": price, "qty": qty, "reason": reason, "target": target})

    @staticmethod
    def _round_down(value: float, step: float) -> float:
        if step <= 0:
            return value
        return (value // step) * step

    @staticmethod
    def _round_up(value: float, step: float) -> float:
        if step <= 0:
            return value
        units = int((value + step - 1e-12) / step)
        return units * step

    def _preflight_order(self, order: dict) -> dict | None:
        general = self.settings.get("general", {})
        side = order["side"]
        price = float(order["price"])
        qty = float(order["qty"])
        if price <= 0 or qty <= 0:
            self.runtime["last_block_reason"] = "INVALID_PRICE_OR_QTY"
            return None
        tick_size = float(self.runtime.get("filters", {}).get("tickSize", 0.0001) or 0.0001)
        step_size = float(self.runtime.get("filters", {}).get("stepSize", 0.1) or 0.1)
        min_notional = float(self.runtime.get("filters", {}).get("minNotional", 5.0) or 5.0)
        max_live = float(general.get("max_live_order_size", 10.0))
        buffer_mult = 1.02
        price = self._round_down(price, tick_size)
        qty = self._round_down(qty, step_size)
        notional = price * qty
        if notional < min_notional:
            qty = self._round_up((min_notional / max(price, 1e-9)) * buffer_mult, step_size)
            notional = price * qty
        self.runtime["proposed_notional"] = notional
        self.runtime["min_notional"] = min_notional
        if notional < min_notional:
            self.runtime["last_block_reason"] = "NOTIONAL_TOO_LOW"
            self._append_log(f"[ORDER_BLOCKED] NOTIONAL_TOO_LOW qty={qty:.6f} price={price:.6f} notional={notional:.6f} minNotional={min_notional:.6f}")
            return None
        if qty > max_live:
            self.runtime["last_block_reason"] = "MAX_LIVE_ORDER_SIZE"
            self._append_log(f"[ORDER_BLOCKED] MAX_LIVE_ORDER_SIZE qty={qty:.6f} max_live_order_size={max_live:.6f}")
            return None
        usdt_free = self._parse_free(self.usdt_bal.text())
        euri_free = self._parse_free(self.euri_bal.text())
        if side == "BUY" and usdt_free < notional:
            self.runtime["last_block_reason"] = "INSUFFICIENT_USDT"
            self._append_log(f"[ORDER_BLOCKED] INSUFFICIENT_USDT free={usdt_free:.6f} need={notional:.6f}")
            return None
        if side == "SELL" and euri_free < qty:
            self.runtime["last_block_reason"] = "INSUFFICIENT_EURI"
            self._append_log(f"[ORDER_BLOCKED] INSUFFICIENT_EURI free={euri_free:.6f} need={qty:.6f}")
            return None
        out = dict(order)
        out["price"] = price
        out["qty"] = qty
        out["notional"] = notional
        return out

    def _execute_auto_order(self) -> None:
        now = time.time()
        if now < float(self.runtime.get("execution_cooldown_until", 0.0)):
            self._log_once_or_changed("execution_blocked_reason", "[EXECUTION] blocked reason: LAST_ERROR_COOLDOWN", 30)
            return
        order = self._prepare_order()
        if not order:
            reason = self.runtime.get("last_execution_block_reason", "UNKNOWN")
            if reason and reason != "NONE":
                interval = 3600 if reason == "TRADING_DISABLED" else 30
                self._log_once_or_changed("execution_blocked_reason", f"[EXECUTION] blocked reason: {reason}", interval)
            return
        dedup_key = f"{order['side']}|{order['price']:.8f}|{order['qty']:.8f}|{order['reason']}"
        failed_keys = self.runtime.setdefault("failed_order_keys", {})
        if dedup_key in failed_keys and now - float(failed_keys[dedup_key]) <= 30:
            self.runtime["last_block_reason"] = "DUPLICATE_FAILED_PROPOSAL"
            self._log_once_or_changed("order_blocked_preflight", "[ORDER_BLOCKED] duplicate failed proposal cooldown", 10)
            return
        self._append_log(f"[ORDER] order proposal created: {order['side']} {order['qty']:.6f} {order['symbol']} @ {order['price']:.6f} ({order['reason']})")
        self._append_log("[EXECUTION] auto order approved")
        try:
            if not self.client:
                return
            if order["side"] == "BUY":
                resp = self.client.place_limit_buy(order["symbol"], order["qty"], order["price"])
            else:
                resp = self.client.place_limit_sell(order["symbol"], order["qty"], order["price"])
            order_id = resp.get("orderId")
            cycle_id = order.get("cycle_id")
            if cycle_id and cycle_id in self.runtime.get("cycles", {}):
                c = self.runtime["cycles"][cycle_id]
                c["entry_order_id"] = order_id
                c["state"] = "ENTRY_PENDING"
                c["updated_ts"] = time.time()
                self.runtime.setdefault("order_to_cycle", {})[str(order_id)] = cycle_id
            self._append_log(f"[ORDER] order sent id={order_id}")
            self._append_trade_log("ORDER_SENT", f"id={resp.get('orderId')} side={order['side']} qty={order['qty']:.6f} price={order['price']:.6f}")
            self._refresh_open_orders()
            self._refresh_balances_only()
        except Exception as exc:
            err = str(exc)
            self.runtime["last_order_error"] = err
            self.runtime["last_block_reason"] = "ORDER_ERROR"
            self.runtime["execution_cooldown_until"] = now + float(self.settings.get("general", {}).get("execution_cooldown_sec", 15))
            self.runtime.setdefault("failed_order_keys", {})[dedup_key] = now
            key = f"order_error::{err}"
            last_t = float(self.runtime.setdefault("last_error_log_times", {}).get(key, 0.0))
            if now - last_t >= 10:
                self.runtime["last_error_log_times"][key] = now
                self._append_log(f"[ORDER] order error: {err}")

    @staticmethod
    def _parse_free(text: str) -> float:
        try:
            return float(text.split("free=")[1].split(" /")[0])
        except Exception:
            return 0.0

    def _refresh_open_orders(self) -> None:
        if not self.client:
            return
        try:
            orders = self.client.get_open_orders("EURIUSDT")
            prev_count = int(self.runtime.get("active_orders_count", 0))
            self.runtime["active_orders_count"] = len(orders)
            now_ms = int(time.time() * 1000)
            lines = []
            for o in orders:
                age = max(0, (now_ms - int(o.get("time", now_ms))) // 1000)
                status = o.get("status")
                if status in {"FILLED", "CANCELED"} and age > 10:
                    continue
                order_id = str(o.get("orderId"))
                cycle_id = self.runtime.get("order_to_cycle", {}).get(order_id, "-")
                cycle = self.runtime.get("cycles", {}).get(cycle_id, {})
                mode = cycle.get("mode", "-")
                leg = "ENTRY" if cycle.get("entry_order_id") == o.get("orderId") else "EXIT" if cycle.get("exit_order_id") == o.get("orderId") else "-"
                lines.append(f"{cycle_id} | {mode} | {leg} | {o.get('side')} | {o.get('price')} | {o.get('origQty')} | {status} | {age}s | {o.get('orderId')}")
            self.active_orders_view.setPlainText("\n".join(lines) if lines else "No active orders.")
            if prev_count != len(orders):
                self._append_log(f"[ORDER] open orders count changed: {prev_count} -> {len(orders)}")
            prev_statuses = self.runtime.setdefault("order_status_by_id", {})
            current_statuses = {str(o.get("orderId")): str(o.get("status")) for o in orders if o.get("orderId") is not None}
            for order_id, status in current_statuses.items():
                if prev_statuses.get(order_id) != status:
                    self._append_log(f"[ORDER] status changed: id={order_id} {prev_statuses.get(order_id)} -> {status}")
            self.runtime["order_status_by_id"] = current_statuses
        except Exception as exc:
            self._append_log(f"[ORDER] open orders refresh error: {exc}")

    def _show_all_data(self):
        AllDataDialog(self).exec()

    def _connect_api(self) -> None:
        if self.api_connect_in_progress:
            return
        if self.api_connected_once:
            return
        thread = self.thread
        if thread is not None:
            if not isValid(thread):
                self.thread = None
            else:
                try:
                    if thread.isRunning():
                        return
                except RuntimeError:
                    self.thread = None

        worker = self.worker
        if worker is not None and not isValid(worker):
            self.worker = None
        self.api_connect_in_progress = True
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
        self.thread.finished.connect(lambda: setattr(self, "thread", None))
        self.thread.finished.connect(lambda: setattr(self, "worker", None))
        self.thread.finished.connect(lambda: setattr(self, "api_connect_in_progress", False))
        self.thread.start()

    def _refresh_balances_only(self) -> None:
        if not self.client:
            return
        try:
            balances = self.client.get_balances(["EURI", "USDT"])
            euri = balances.get("EURI", {"free": 0, "locked": 0, "total": 0})
            usdt = balances.get("USDT", {"free": 0, "locked": 0, "total": 0})
            prev_euri_total = self._parse_total(self.euri_bal.text())
            prev_usdt_total = self._parse_total(self.usdt_bal.text())
            self.euri_bal.setText(f"free={euri['free']:.8f} / locked={euri['locked']:.8f} / total={euri['total']:.8f}")
            self.usdt_bal.setText(f"free={usdt['free']:.8f} / locked={usdt['locked']:.8f} / total={usdt['total']:.8f}")
            self.runtime["last_balance_refresh_ts"] = time.time()
            inventory_zone, _ = self._evaluate_inventory_zone(euri["total"], usdt["total"], self.runtime.get("euri", {}).get("mid", 0.0))
            self.inventory_zone.setText(f"INVENTORY ZONE: {inventory_zone}")
            changed = abs(prev_euri_total - float(euri["total"])) > 1e-9 or abs(prev_usdt_total - float(usdt["total"])) > 1e-9
            if changed:
                self.runtime["last_balance_change_ts"] = time.time()
                self._append_log(f"[ACCOUNT] balances changed euri={euri['total']:.8f} usdt={usdt['total']:.8f}")
            else:
                self._log_once_or_changed("balances_ok", f"[ACCOUNT] balances ok euri={euri['total']:.8f} usdt={usdt['total']:.8f}", 60)
        except Exception as exc:
            self._append_log(f"[ACCOUNT] balances refresh error: {exc}")

    def _auto_start(self) -> None:
        if self.api_connected_once or self.api_connect_in_progress:
            return
        self._append_log("[AUTO] auto connect started")
        self._connect_api()

    def _set_market_status(self, label: QLabel, value: str, key: str) -> None:
        if label.text() != value:
            label.setText(value)
            short = value.split(": ", 1)[-1]
            if self.last_status.get(key) != short:
                self._append_log(f"[STATUS] {key.upper()} -> {short}")
                self.last_status[key] = short

    def _build_all_data_text(self) -> str:
        now = time.time()
        eur = self.runtime.get("eur", {})
        euri = self.runtime.get("euri", {})
        tick = float(self.settings.get("general", {}).get("tick_size", 0.0001))
        api_key = self.settings.get("binance_api_key", "")
        masked = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) >= 8 else "***"
        def age_ms(d): return int((now - d.get("time", now)) * 1000) if d else -1
        eur_spread = (eur.get("ask", 0) - eur.get("bid", 0)) if eur else 0
        euri_spread = (euri.get("ask", 0) - euri.get("bid", 0)) if euri else 0
        euri_total = self._parse_total(self.euri_bal.text())
        usdt_total = self._parse_total(self.usdt_bal.text())
        total_est = usdt_total + euri_total * (euri.get("mid") or 0)
        skew = (euri_total / (euri_total + usdt_total / max((euri.get("mid") or 1), 1e-9))) if (euri_total + usdt_total) > 0 else 0
        return f"""EURUSDT Parent
bid={eur.get('bid', 'N/A')} ask={eur.get('ask', 'N/A')} mid={eur.get('mid', 'N/A')} spread={eur_spread:.6f}
source={eur.get('source', 'N/A')} update={eur.get('time', 0):.3f} age_ms={age_ms(eur)} status={self.eur_status.text()} ticks/min={len(self.runtime['eur_ticks'])}

EURIUSDT Child
bid={euri.get('bid', 'N/A')} ask={euri.get('ask', 'N/A')} mid={euri.get('mid', 'N/A')} spread={euri_spread:.6f} spread_ticks={self._ticks(euri_spread)}
source={euri.get('source', 'N/A')} update={euri.get('time', 0):.3f} age_ms={age_ms(euri)} status={self.euri_status.text()} http_poll_count={self.runtime['euri_poll_count']} stale_count={self.runtime['euri_stale_count']}

Fair Value / Edge
fair_gap={self.runtime.get('fair_gap')} fair_gap_ticks={self.runtime.get('fair_gap_ticks')} trap_direction={self.runtime['decision']['trap_direction']}
passive_readiness={self.runtime['decision']['passive_status']} corridor_state=N/A child_delay={age_ms(euri)} parent_impulse=N/A weak_side=N/A block_reason={self.runtime['decision']['block_reason']}

Balances
EURI {self.euri_bal.text()}
USDT {self.usdt_bal.text()}
total_value_estimate={total_est:.6f} inventory_skew={skew:.4f} inventory_zone={self.inventory_zone.text()}

Filters
{self.filters_label.text()}

Runtime
app_version=0.1.12 uptime={int(now-self.started_at)}s current_mode={self.mode_label.text()} active_orders_count={self.runtime['active_orders_count']}
cycles={self.runtime['cycles']} wins/losses={self.runtime['wins']}/{self.runtime['losses']} realized/unrealized_pnl={self.runtime['realized_pnl']}/{self.runtime['unrealized_pnl']} tick_capture={self.runtime['tick_capture']}
last_order_error={self.runtime.get('last_order_error')}
execution_cooldown_until={self.runtime.get('execution_cooldown_until')}
last_block_reason={self.runtime.get('last_block_reason')}
last_execution_block_reason={self.runtime.get('last_execution_block_reason')}
last_balance_refresh_ts={self.runtime.get('last_balance_refresh_ts')}
last_balance_change_ts={self.runtime.get('last_balance_change_ts')}
last_fair_gap_zone={self.runtime.get('last_fair_gap_zone')}
last_log_event_key={self.runtime.get('last_log_event_key')}
proposed_notional={self.runtime.get('proposed_notional')}
min_notional={self.runtime.get('min_notional')}

Decision Engine
passive_status={self.runtime['decision']['passive_status']}
passive_block_reason={self.runtime['decision']['passive_block_reason']}
trap_status={self.runtime['decision']['trap_status']}
trap_direction={self.runtime['decision']['trap_direction']}
trap_block_reason={self.runtime['decision']['trap_block_reason']}
current_mode={self.runtime['decision']['current_mode']}
inventory_zone={self.runtime['decision']['inventory_zone']}
planned_action={self.runtime['decision']['planned_action']}
last_decision_time={self.runtime['decision']['last_decision_time']}
eur_age_ms={age_ms(eur)} euri_age_ms={age_ms(euri)}
eur_stale_ms={self.settings.get('general',{}).get('eur_stale_ms')} euri_stale_ms={self.settings.get('general',{}).get('euri_stale_ms')}
eur_fresh={self.runtime['decision'].get('eur_fresh')} euri_fresh={self.runtime['decision'].get('euri_fresh')} decision_freshness_source={self.runtime['decision'].get('decision_freshness_source')}
passive_score={self.runtime['decision'].get('passive_score')}
trap_score={self.runtime['decision'].get('trap_score')}
passive_stability={self.runtime['decision'].get('passive_stability')}
trap_stability={self.runtime['decision'].get('trap_stability')}

Settings summary
api_key={masked} api_secret=HIDDEN use_testnet={self.settings.get('use_testnet', False)} parent_symbol={self.settings.get('general',{}).get('parent_symbol')} base_symbol={self.settings.get('general',{}).get('base_symbol')} tick_size={tick}
"""

    @staticmethod
    def _parse_total(text: str) -> float:
        try:
            return float(text.split("total=")[-1])
        except Exception:
            return 0.0

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
