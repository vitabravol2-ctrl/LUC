import hashlib
import hmac
import json
import sys
import time
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
    QVBoxLayout,
    QWidget,
)

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "settings.json"


class BinanceClient:
    def __init__(self, api_key: str, api_secret: str, use_testnet: bool = False, timeout: float = 10.0) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self.base_url = "https://testnet.binance.vision" if use_testnet else "https://api.binance.com"

    def _request(self, method: str, path: str, params: dict | None = None, signed: bool = False) -> dict:
        params = params or {}
        headers = {"User-Agent": "LUC/0.1.1"}

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
                "filters": {
                    "tickSize": tick_size,
                    "stepSize": step_size,
                    "minNotional": min_notional,
                },
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
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.api_key = QLineEdit(settings.get("binance_api_key", ""))
        self.api_secret = QLineEdit(settings.get("binance_api_secret", ""))
        self.api_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.use_testnet = QLineEdit(str(bool(settings.get("use_testnet", False))).lower())
        form.addRow("binance_api_key", self.api_key)
        form.addRow("binance_api_secret", self.api_secret)
        form.addRow("use_testnet (true/false)", self.use_testnet)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        save = QPushButton("SAVE")
        cancel = QPushButton("CANCEL")
        buttons.addStretch(1)
        buttons.addWidget(save)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

        save.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

    def payload(self) -> dict:
        return {
            "binance_api_key": self.api_key.text().strip(),
            "binance_api_secret": self.api_secret.text().strip(),
            "use_testnet": self.use_testnet.text().strip().lower() == "true",
        }


class FullConfigDialog(QDialog):
    def __init__(self, settings: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("FULL CONFIG")
        self.resize(800, 600)
        layout = QVBoxLayout(self)

        self.editor = QPlainTextEdit()
        self.editor.setPlainText(json.dumps(settings, indent=2, ensure_ascii=False))
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

        self.setWindowTitle("LUC v0.1.1 — Binance Connection + Real Balances")
        self.resize(1280, 820)
        self._init_ui()

    def _load_settings(self) -> dict:
        if not CONFIG_PATH.exists():
            return {}
        with CONFIG_PATH.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _save_settings(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as file:
            json.dump(self.settings, file, indent=2, ensure_ascii=False)

    def _append_log(self, message: str) -> None:
        max_rows = int(self.settings.get("ui", {}).get("latest_logs_rows", 200))
        lines = self.logs_view.toPlainText().splitlines()
        lines.append(message)
        if len(lines) > max_rows:
            lines = lines[-max_rows:]
        self.logs_view.setPlainText("\n".join(lines))
        self.logs_view.verticalScrollBar().setValue(self.logs_view.verticalScrollBar().maximum())

    def _status_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFrameShape(QFrame.Shape.StyledPanel)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label.setMinimumHeight(30)
        return label

    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        status_bar = QHBoxLayout()
        self.api_status_label = self._status_label("API STATUS: DISCONNECTED")
        status_bar.addWidget(self.api_status_label)
        status_bar.addWidget(self._status_label("EURUSDT STATUS: IDLE"))
        status_bar.addWidget(self._status_label("EURIUSDT STATUS: IDLE"))
        root.addLayout(status_bar)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        balance_block = QGroupBox("Balances")
        balance_layout = QVBoxLayout(balance_block)
        self.euri_label = QLabel("EURI free=0.00000000 / locked=0.00000000 / total=0.00000000")
        self.usdt_label = QLabel("USDT free=0.00000000 / locked=0.00000000 / total=0.00000000")
        self.filters_label = QLabel("EURIUSDT filters: tickSize=N/A / stepSize=N/A / minNotional=N/A")
        balance_layout.addWidget(self.euri_label)
        balance_layout.addWidget(self.usdt_label)
        balance_layout.addWidget(self.filters_label)
        balance_layout.addStretch(1)

        grid.addWidget(balance_block, 0, 0, 1, 2)
        root.addLayout(grid)

        logs_box = QGroupBox("Latest Logs")
        logs_layout = QVBoxLayout(logs_box)
        self.logs_view = QPlainTextEdit()
        self.logs_view.setReadOnly(True)
        self.logs_view.setPlainText("[v0.1.1] LUC initialized. Binance connectivity available. No trading actions.")
        logs_layout.addWidget(self.logs_view)
        root.addWidget(logs_box, stretch=1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.connect_btn = QPushButton("CONNECT")
        self.connect_btn.setMinimumWidth(140)
        self.connect_btn.clicked.connect(self._connect_api)
        self.settings_btn = QPushButton("SETTINGS")
        self.settings_btn.setMinimumWidth(140)
        self.settings_btn.clicked.connect(self._open_settings)
        self.full_config_btn = QPushButton("FULL CONFIG")
        self.full_config_btn.setMinimumWidth(140)
        self.full_config_btn.clicked.connect(self._open_full_config)
        buttons.addWidget(self.connect_btn)
        buttons.addWidget(self.settings_btn)
        buttons.addWidget(self.full_config_btn)
        exit_btn = QPushButton("EXIT")
        exit_btn.setMinimumWidth(140)
        exit_btn.clicked.connect(self.close)
        buttons.addWidget(exit_btn)
        root.addLayout(buttons)

    def _set_api_status(self, status: str) -> None:
        self.api_status_label.setText(f"API STATUS: {status}")

    def _apply_connect_result(self, payload: dict) -> None:
        balances = payload.get("balances", {})
        euri = balances.get("EURI", {"free": 0, "locked": 0, "total": 0})
        usdt = balances.get("USDT", {"free": 0, "locked": 0, "total": 0})
        self.euri_label.setText(
            f"EURI free={euri['free']:.8f} / locked={euri['locked']:.8f} / total={euri['total']:.8f}"
        )
        self.usdt_label.setText(
            f"USDT free={usdt['free']:.8f} / locked={usdt['locked']:.8f} / total={usdt['total']:.8f}"
        )
        f = payload.get("filters", {})
        self.filters_label.setText(
            f"EURIUSDT filters: tickSize={f.get('tickSize', 'N/A')} / stepSize={f.get('stepSize', 'N/A')} / minNotional={f.get('minNotional', 'N/A')}"
        )

    def _connect_api(self) -> None:
        self.connect_btn.setEnabled(False)
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

        self.thread.start()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings.update(dialog.payload())
            self._save_settings()
            self._append_log("[SETTINGS] saved")

    def _open_full_config(self) -> None:
        dialog = FullConfigDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                data = dialog.parsed()
                if not isinstance(data, dict):
                    raise ValueError("settings root must be JSON object")
                self.settings = data
                self._save_settings()
                self._append_log("[CONFIG] full config saved")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(self, "Config error", f"Invalid JSON: {exc}")
                self._append_log(f"[ERROR] config not saved: {exc}")


def main() -> int:
    app = QApplication(sys.argv)
    window = LUCTerminal()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
