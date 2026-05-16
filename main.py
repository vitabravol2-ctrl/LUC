import hashlib
import hmac
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
)

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "settings.json"
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

DEFAULT_SETTINGS = {
    "dry_run": False,
    "order_size_euri": 25.0,
    "target_ticks": 1,
    "buy_offset_ticks": 0,
    "sell_offset_ticks": 0,
    "max_spread_ticks": 4,
    "max_active_cycle": 1,
    "order_timeout_sec": 30,
    "live_armed": False,
    "symbol": "EURIUSDT",
}


class CycleState(str, Enum):
    IDLE = "IDLE"
    PLACE_BUY = "PLACE_BUY"
    WAIT_BUY_FILL = "WAIT_BUY_FILL"
    PLACE_SELL = "PLACE_SELL"
    WAIT_SELL_FILL = "WAIT_SELL_FILL"
    CANCELING = "CANCELING"
    CLOSED = "CLOSED"
    ERROR = "ERROR"


@dataclass
class FillInfo:
    qty: float = 0.0
    quote: float = 0.0
    avg_price: float = 0.0


class ApiSettingsDialog(QDialog):
    def __init__(self, api: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("API SETTINGS")
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.key = QLineEdit(api.get("api_key", ""))
        self.secret = QLineEdit(api.get("api_secret", ""))
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API Key", self.key)
        form.addRow("API Secret", self.secret)
        root.addLayout(form)
        row = QHBoxLayout()
        ok = QPushButton("Save")
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        root.addLayout(row)

    def values(self) -> dict[str, str]:
        return {"api_key": self.key.text().strip(), "api_secret": self.secret.text().strip()}


class BasicSettingsDialog(QDialog):
    def __init__(self, s: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SETTINGS")
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.order_size = QDoubleSpinBox(); self.order_size.setRange(0.01, 1_000_000); self.order_size.setDecimals(4); self.order_size.setValue(float(s["order_size_euri"]))
        self.target_ticks = QSpinBox(); self.target_ticks.setRange(1, 1000); self.target_ticks.setValue(int(s["target_ticks"]))
        self.buy_offset = QSpinBox(); self.buy_offset.setRange(0, 1000); self.buy_offset.setValue(int(s["buy_offset_ticks"]))
        self.sell_offset = QSpinBox(); self.sell_offset.setRange(0, 1000); self.sell_offset.setValue(int(s["sell_offset_ticks"]))
        self.max_spread = QSpinBox(); self.max_spread.setRange(1, 1000); self.max_spread.setValue(int(s["max_spread_ticks"]))
        self.timeout = QSpinBox(); self.timeout.setRange(5, 3600); self.timeout.setValue(int(s["order_timeout_sec"]))
        self.live_armed = QCheckBox(); self.live_armed.setChecked(bool(s["live_armed"]))
        self.dry_run = QCheckBox(); self.dry_run.setChecked(bool(s["dry_run"]))
        form.addRow("order_size_euri", self.order_size)
        form.addRow("target_ticks", self.target_ticks)
        form.addRow("buy_offset_ticks", self.buy_offset)
        form.addRow("sell_offset_ticks", self.sell_offset)
        form.addRow("max_spread_ticks", self.max_spread)
        form.addRow("max_active_cycle", QLabel("1 (fixed)"))
        form.addRow("order_timeout_sec", self.timeout)
        form.addRow("live_armed", self.live_armed)
        form.addRow("dry_run", self.dry_run)
        root.addLayout(form)
        row = QHBoxLayout()
        ok = QPushButton("Apply"); ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        row.addWidget(ok); row.addWidget(cancel)
        root.addLayout(row)

    def values(self) -> dict[str, Any]:
        return {
            "order_size_euri": self.order_size.value(), "target_ticks": self.target_ticks.value(),
            "buy_offset_ticks": self.buy_offset.value(), "sell_offset_ticks": self.sell_offset.value(),
            "max_spread_ticks": self.max_spread.value(), "max_active_cycle": 1,
            "order_timeout_sec": self.timeout.value(), "live_armed": self.live_armed.isChecked(),
            "dry_run": self.dry_run.isChecked(),
        }


class LUCWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LUC v0.3.0 Live Terminal")
        self.resize(1150, 820)
        self.settings = self._load_settings()
        self.api = self.settings.setdefault("api", {"api_key": "", "api_secret": ""})

        self.base_url = "https://api.binance.com"
        self.tick_size = 0.0001
        self.step_size = 0.1
        self.min_qty = 0.1
        self.min_notional = 5.0
        self.filters_loaded = False

        self.eur_bid = self.eur_ask = self.euri_bid = self.euri_ask = 0.0
        self.api_connected = False
        self.can_trade = False
        self.euri_free = self.euri_locked = self.usdt_free = self.usdt_locked = 0.0
        self.open_luc_orders = 0
        self.last_error = "-"
        self.block_reason = "-"
        self.trading_on = False

        self.cycle_state = CycleState.IDLE
        self.current_order_id = "-"
        self.current_side = "-"
        self.current_qty = 0.0
        self.entry_price = 0.0
        self.exit_price = 0.0
        self.last_order_status = "-"
        self.buy_order_started = 0.0
        self.sell_order_started = 0.0
        self.buy_client_id = ""
        self.sell_client_id = ""
        self.buy_fill = FillInfo()
        self.sell_fill = FillInfo()
        self.real_cycles = self.wins = self.losses = 0
        self.realized_pnl = 0.0
        self.last_closed_cycle = "-"
        self.logs: list[str] = []

        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start(1500)

    def _load_settings(self) -> dict[str, Any]:
        if SETTINGS_PATH.exists():
            with SETTINGS_PATH.open("r", encoding="utf-8") as f:
                s = json.load(f)
        else:
            s = {}
        for k, v in DEFAULT_SETTINGS.items():
            s.setdefault(k, v)
        return s

    def _save_settings(self) -> None:
        with SETTINGS_PATH.open("w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=2, ensure_ascii=False)

    def _build_group(self, title: str, fields: list[str], store: dict[str, QLabel]) -> QGroupBox:
        g = QGroupBox(title)
        grid = QGridLayout(g)
        for i, key in enumerate(fields):
            grid.addWidget(QLabel(key), i, 0)
            lbl = QLabel("-")
            lbl.setTextInteractionFlags(lbl.textInteractionFlags())
            grid.addWidget(lbl, i, 1)
            store[key] = lbl
        return g

    def _build_ui(self) -> None:
        root = QWidget(); self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        top = QHBoxLayout()
        self.start_btn = QPushButton("START TRADING")
        self.start_btn.clicked.connect(self.toggle_trading)
        api_btn = QPushButton("API SETTINGS"); api_btn.clicked.connect(self.open_api_settings)
        st_btn = QPushButton("SETTINGS"); st_btn.clicked.connect(self.open_settings)
        ref_btn = QPushButton("REFRESH BALANCES"); ref_btn.clicked.connect(self.refresh_account)
        cancel_btn = QPushButton("CANCEL LUC ORDERS"); cancel_btn.clicked.connect(self.cancel_luc_orders)
        for b in (self.start_btn, api_btn, st_btn, ref_btn, cancel_btn): top.addWidget(b)
        layout.addLayout(top)

        self.market_labels = {}
        self.signal_labels = {}
        self.account_labels = {}
        self.cycle_labels = {}
        self.session_labels = {}

        layout.addWidget(self._build_group("MARKET", ["EUR bid/ask/mid", "EURI bid/ask/mid", "EURI spread ticks", "fair gap ticks"], self.market_labels))
        layout.addWidget(self._build_group("SIGNAL", ["regime", "trap readiness", "action suggestion", "BLOCK REASON"], self.signal_labels))
        layout.addWidget(self._build_group("ACCOUNT", ["API status", "canTrade", "EURI free/locked", "USDT free/locked", "open LUC orders"], self.account_labels))
        layout.addWidget(self._build_group("LIVE CYCLE", ["trading status", "active cycle state", "current order id", "side", "qty", "entry price", "exit price", "last order status", "last error"], self.cycle_labels))
        layout.addWidget(self._build_group("SESSION", ["real cycles", "wins/losses", "realized pnl", "open exposure", "last closed cycle"], self.session_labels))

        self.logs_box = QLabel("LOGS: -")
        self.logs_box.setWordWrap(True)
        layout.addWidget(self.logs_box)

    def log(self, msg: str) -> None:
        line = f"{datetime.utcnow().strftime('%H:%M:%S')} {msg}"
        self.logs.append(line)
        self.logs = self.logs[-12:]
        self.logs_box.setText("LOGS:\n" + "\n".join(self.logs))
        logging.info(line)

    def _public(self, path: str, params: dict[str, Any] | None = None) -> Any:
        r = requests.get(self.base_url + path, params=params or {}, timeout=8)
        r.raise_for_status()
        return r.json()

    def _signed(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.api.get("api_key") or not self.api.get("api_secret"):
            raise RuntimeError("API keys missing")
        p = dict(params or {})
        p["timestamp"] = int(time.time() * 1000)
        q = urlencode(p)
        sig = hmac.new(self.api["api_secret"].encode(), q.encode(), hashlib.sha256).hexdigest()
        p["signature"] = sig
        headers = {"X-MBX-APIKEY": self.api["api_key"]}
        r = requests.request(method, self.base_url + path, params=p, headers=headers, timeout=8)
        r.raise_for_status()
        return r.json()

    def load_filters(self) -> None:
        info = self._public("/api/v3/exchangeInfo", {"symbol": self.settings["symbol"]})
        sym = info["symbols"][0]
        self.can_trade = bool(sym.get("isSpotTradingAllowed", True))
        for f in sym.get("filters", []):
            if f["filterType"] == "PRICE_FILTER": self.tick_size = float(f["tickSize"])
            if f["filterType"] == "LOT_SIZE": self.step_size, self.min_qty = float(f["stepSize"]), float(f["minQty"])
            if f["filterType"] == "MIN_NOTIONAL": self.min_notional = float(f.get("minNotional", self.min_notional))
        self.filters_loaded = True

    def open_api_settings(self) -> None:
        d = ApiSettingsDialog(self.api, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.api.update(d.values())
            self._save_settings()
            self.log("[API] keys saved")

    def open_settings(self) -> None:
        d = BasicSettingsDialog(self.settings, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.settings.update(d.values())
            self._save_settings()
            self.log("[SETTINGS] updated")

    def refresh_account(self) -> None:
        acc = self._signed("GET", "/api/v3/account")
        self.api_connected = True
        self.can_trade = bool(acc.get("canTrade", False))
        for b in acc.get("balances", []):
            if b["asset"] == "EURI": self.euri_free, self.euri_locked = float(b["free"]), float(b["locked"])
            if b["asset"] == "USDT": self.usdt_free, self.usdt_locked = float(b["free"]), float(b["locked"])
        oo = self._signed("GET", "/api/v3/openOrders", {"symbol": self.settings["symbol"]})
        self.open_luc_orders = len([o for o in oo if str(o.get("clientOrderId", "")).startswith("LUC_LIVE_")])
        self.log("[ACCOUNT] refreshed")

    def cancel_luc_orders(self) -> None:
        oo = self._signed("GET", "/api/v3/openOrders", {"symbol": self.settings["symbol"]})
        for o in oo:
            cid = str(o.get("clientOrderId", ""))
            if cid.startswith("LUC_LIVE_"):
                self._signed("DELETE", "/api/v3/order", {"symbol": self.settings["symbol"], "origClientOrderId": cid})
        self.log("[ORDERS] canceled LUC_LIVE_ orders only")

    def _norm_price(self, p: float) -> float:
        return (int(p / self.tick_size)) * self.tick_size

    def _norm_qty(self, q: float) -> float:
        return max(self.min_qty, (int(q / self.step_size)) * self.step_size)

    def _validate_start(self) -> str | None:
        if not self.settings.get("live_armed", False): return "live_armed false"
        if self.settings.get("dry_run", False): return "dry_run enabled"
        if not self.api_connected: return "API not connected"
        if not self.can_trade: return "canTrade false"
        if not self.filters_loaded: return "filters not loaded"
        if self.cycle_state not in {CycleState.IDLE, CycleState.CLOSED}: return "active cycle exists"
        if self.open_luc_orders > 0: return "open LUC order exists"
        if self.usdt_free <= 0: return "balance insufficient"
        return None

    def toggle_trading(self) -> None:
        if self.trading_on:
            self.trading_on = False
            self.start_btn.setText("START TRADING")
            self.log("[CONTROL] STOP TRADING")
            return
        reason = self._validate_start()
        if reason:
            self.block_reason = reason
            self.log(f"[BLOCK] {reason}")
            return
        self.block_reason = "-"
        self.trading_on = True
        self.start_btn.setText("STOP TRADING")
        self.log("[CONTROL] START TRADING")

    def _place_limit(self, side: str, qty: float, price: float, cid: str) -> dict[str, Any]:
        return self._signed("POST", "/api/v3/order", {
            "symbol": self.settings["symbol"], "side": side, "type": "LIMIT", "timeInForce": "GTC",
            "quantity": f"{qty:.8f}", "price": f"{price:.8f}", "newClientOrderId": cid,
        })

    def _poll_order(self, cid: str) -> dict[str, Any]:
        return self._signed("GET", "/api/v3/order", {"symbol": self.settings["symbol"], "origClientOrderId": cid})

    def _ensure_min_notional(self, qty: float, price: float) -> float:
        if qty * price >= self.min_notional:
            return qty
        boosted = (self.min_notional * 1.02) / max(price, 1e-12)
        return self._norm_qty(boosted)

    def _on_tick(self) -> None:
        try:
            eur = self._public("/api/v3/ticker/bookTicker", {"symbol": "EURUSDT"})
            euri = self._public("/api/v3/ticker/bookTicker", {"symbol": self.settings["symbol"]})
            self.eur_bid, self.eur_ask = float(eur["bidPrice"]), float(eur["askPrice"])
            self.euri_bid, self.euri_ask = float(euri["bidPrice"]), float(euri["askPrice"])
            if not self.filters_loaded:
                self.load_filters()
            self.refresh_account()
            self.run_cycle_logic()
        except Exception as e:
            self.last_error = str(e)
            self.log(f"[ERROR] {self.last_error}")
        self.render()

    def run_cycle_logic(self) -> None:
        spread_ticks = (self.euri_ask - self.euri_bid) / self.tick_size if self.tick_size else 999
        if self.trading_on and self.cycle_state == CycleState.IDLE:
            reason = self._validate_start()
            if reason: self.block_reason = reason; return
            if spread_ticks > float(self.settings["max_spread_ticks"]): self.block_reason = "spread too wide"; return
            if self.euri_bid <= 0 or self.euri_ask <= 0: self.block_reason = "invalid bid/ask"; return
            self.cycle_state = CycleState.PLACE_BUY

        if self.cycle_state == CycleState.PLACE_BUY:
            qty = self._norm_qty(float(self.settings["order_size_euri"]))
            buy_price = self._norm_price(self.euri_bid - self.settings["buy_offset_ticks"] * self.tick_size)
            qty = self._ensure_min_notional(qty, buy_price)
            if self.usdt_free < qty * buy_price: self.block_reason = "balance insufficient"; self.cycle_state = CycleState.ERROR; return
            self.buy_client_id = f"LUC_LIVE_BUY_{int(time.time()*1000)}"
            res = self._place_limit("BUY", qty, buy_price, self.buy_client_id)
            self.current_order_id = str(res.get("orderId", "-")); self.current_side = "BUY"; self.current_qty = qty; self.entry_price = buy_price
            self.buy_order_started = time.time(); self.last_order_status = str(res.get("status", "NEW")); self.cycle_state = CycleState.WAIT_BUY_FILL
            self.log(f"[LIVE] BUY LIMIT placed qty={qty:.4f} price={buy_price:.6f}")

        elif self.cycle_state == CycleState.WAIT_BUY_FILL:
            o = self._poll_order(self.buy_client_id); st = str(o.get("status", "NEW")); self.last_order_status = st
            if st == "FILLED":
                self.buy_fill.qty = float(o.get("executedQty", 0.0)); self.buy_fill.quote = float(o.get("cummulativeQuoteQty", 0.0))
                self.buy_fill.avg_price = self.buy_fill.quote / self.buy_fill.qty if self.buy_fill.qty else 0.0
                self.cycle_state = CycleState.PLACE_SELL
            elif time.time() - self.buy_order_started > float(self.settings["order_timeout_sec"]):
                self._signed("DELETE", "/api/v3/order", {"symbol": self.settings["symbol"], "origClientOrderId": self.buy_client_id})
                self.cycle_state = CycleState.IDLE; self.log("[LIVE] BUY timeout cancel -> IDLE")

        elif self.cycle_state == CycleState.PLACE_SELL:
            target_price = max(self.euri_ask + self.settings["sell_offset_ticks"] * self.tick_size, self.buy_fill.avg_price + self.settings["target_ticks"] * self.tick_size)
            sell_price = self._norm_price(target_price)
            qty = self._norm_qty(self.buy_fill.qty)
            self.sell_client_id = f"LUC_LIVE_SELL_{int(time.time()*1000)}"
            res = self._place_limit("SELL", qty, sell_price, self.sell_client_id)
            self.current_order_id = str(res.get("orderId", "-")); self.current_side = "SELL"; self.exit_price = sell_price
            self.sell_order_started = time.time(); self.last_order_status = str(res.get("status", "NEW")); self.cycle_state = CycleState.WAIT_SELL_FILL
            self.log(f"[LIVE] SELL LIMIT placed qty={qty:.4f} price={sell_price:.6f}")

        elif self.cycle_state == CycleState.WAIT_SELL_FILL:
            o = self._poll_order(self.sell_client_id); st = str(o.get("status", "NEW")); self.last_order_status = st
            if st == "FILLED":
                self.sell_fill.qty = float(o.get("executedQty", 0.0)); self.sell_fill.quote = float(o.get("cummulativeQuoteQty", 0.0))
                self.sell_fill.avg_price = self.sell_fill.quote / self.sell_fill.qty if self.sell_fill.qty else 0.0
                pnl = self.sell_fill.quote - self.buy_fill.quote
                self.realized_pnl += pnl; self.real_cycles += 1; self.wins += 1 if pnl > 0 else 0; self.losses += 1 if pnl < 0 else 0
                ticks = (self.sell_fill.avg_price - self.buy_fill.avg_price) / self.tick_size if self.tick_size else 0.0
                self.last_closed_cycle = f"buy {self.buy_fill.qty:.4f}@{self.buy_fill.avg_price:.6f} -> sell {self.sell_fill.qty:.4f}@{self.sell_fill.avg_price:.6f}, pnl {pnl:+.6f}, ticks {ticks:+.2f}"
                self.cycle_state = CycleState.CLOSED
                self.log(f"[LIVE] cycle closed pnl={pnl:+.6f}")
            elif time.time() - self.sell_order_started > float(self.settings["order_timeout_sec"]):
                self._signed("DELETE", "/api/v3/order", {"symbol": self.settings["symbol"], "origClientOrderId": self.sell_client_id})
                self.cycle_state = CycleState.IDLE; self.log("[LIVE] SELL timeout cancel -> IDLE")

        elif self.cycle_state == CycleState.CLOSED:
            self.cycle_state = CycleState.IDLE

    def render(self) -> None:
        eur_mid = (self.eur_bid + self.eur_ask) / 2 if self.eur_bid and self.eur_ask else 0
        euri_mid = (self.euri_bid + self.euri_ask) / 2 if self.euri_bid and self.euri_ask else 0
        spread = (self.euri_ask - self.euri_bid) / self.tick_size if self.tick_size and self.euri_ask else 0
        fair_gap = (euri_mid - eur_mid) / self.tick_size if self.tick_size and eur_mid else 0
        regime = "NEUTRAL" if spread <= self.settings["max_spread_ticks"] else "CAUTION"
        action = "WAIT"
        if self.block_reason != "-": action = "WAIT"
        elif spread <= self.settings["max_spread_ticks"]: action = "PASSIVE"

        self.market_labels["EUR bid/ask/mid"].setText(f"{self.eur_bid:.6f} / {self.eur_ask:.6f} / {eur_mid:.6f}")
        self.market_labels["EURI bid/ask/mid"].setText(f"{self.euri_bid:.6f} / {self.euri_ask:.6f} / {euri_mid:.6f}")
        self.market_labels["EURI spread ticks"].setText(f"{spread:.2f}")
        self.market_labels["fair gap ticks"].setText(f"{fair_gap:+.2f}")

        self.signal_labels["regime"].setText(regime)
        self.signal_labels["trap readiness"].setText("LOW")
        self.signal_labels["action suggestion"].setText(action)
        self.signal_labels["BLOCK REASON"].setText(self.block_reason)

        self.account_labels["API status"].setText("CONNECTED" if self.api_connected else "DISCONNECTED")
        self.account_labels["canTrade"].setText(str(self.can_trade))
        self.account_labels["EURI free/locked"].setText(f"{self.euri_free:.4f}/{self.euri_locked:.4f}")
        self.account_labels["USDT free/locked"].setText(f"{self.usdt_free:.4f}/{self.usdt_locked:.4f}")
        self.account_labels["open LUC orders"].setText(str(self.open_luc_orders))

        self.cycle_labels["trading status"].setText("ON" if self.trading_on else "OFF")
        self.cycle_labels["active cycle state"].setText(self.cycle_state.value)
        self.cycle_labels["current order id"].setText(self.current_order_id)
        self.cycle_labels["side"].setText(self.current_side)
        self.cycle_labels["qty"].setText(f"{self.current_qty:.4f}")
        self.cycle_labels["entry price"].setText(f"{self.entry_price:.6f}")
        self.cycle_labels["exit price"].setText(f"{self.exit_price:.6f}")
        self.cycle_labels["last order status"].setText(self.last_order_status)
        self.cycle_labels["last error"].setText(self.last_error)

        self.session_labels["real cycles"].setText(str(self.real_cycles))
        self.session_labels["wins/losses"].setText(f"{self.wins}/{self.losses}")
        self.session_labels["realized pnl"].setText(f"{self.realized_pnl:+.6f} USDT")
        self.session_labels["open exposure"].setText(f"{self.buy_fill.qty - self.sell_fill.qty:+.4f} EURI")
        self.session_labels["last closed cycle"].setText(self.last_closed_cycle)


if __name__ == "__main__":
    log_file = LOGS_DIR / f"luc_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(filename=log_file, level=logging.INFO, format="%(asctime)s %(message)s")
    app = QApplication(sys.argv)
    win = LUCWindow()
    win.show()
    sys.exit(app.exec())
