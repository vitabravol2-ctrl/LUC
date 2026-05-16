import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)


class LUCTerminal(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LUC v0.1.0 — Live Terminal Skeleton")
        self.resize(1280, 820)
        self._init_ui()

    def _status_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFrameShape(QFrame.Shape.StyledPanel)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label.setMinimumHeight(30)
        return label

    def _metric(self, name: str, value: str = "N/A") -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        left = QLabel(name)
        right = QLabel(value)
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        right.setStyleSheet("font-weight: 600;")
        layout.addWidget(left)
        layout.addWidget(right)
        return row

    def _build_group(self, title: str, metrics: list[tuple[str, str]]) -> QGroupBox:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        for k, v in metrics:
            layout.addWidget(self._metric(k, v))
        layout.addStretch(1)
        return box

    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        status_bar = QHBoxLayout()
        status_bar.addWidget(self._status_label("API STATUS: DISCONNECTED"))
        status_bar.addWidget(self._status_label("EURUSDT STATUS: IDLE"))
        status_bar.addWidget(self._status_label("EURIUSDT STATUS: IDLE"))
        root.addLayout(status_bar)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        eur_block = self._build_group(
            "EURUSDT Parent",
            [("EUR Mid", "0.00000"), ("Parent Stream", "OFF")],
        )
        euri_block = self._build_group(
            "EURIUSDT Child",
            [
                ("Bid", "0.00000"),
                ("Ask", "0.00000"),
                ("Spread", "0 ticks"),
                ("EURI Mid", "0.00000"),
                ("Fair Gap", "0 ticks"),
            ],
        )
        balance_block = self._build_group(
            "Balances",
            [("EURI", "0.0000"), ("USDT", "0.0000")],
        )
        mode_block = self._build_group(
            "Mode / Runtime",
            [("Current Mode", "WAIT"), ("Active Orders", "0"), ("Inventory Zone", "SAFE")],
        )
        accounting_block = self._build_group(
            "Accounting",
            [
                ("Cycles", "0"),
                ("Wins/Losses", "0 / 0"),
                ("Realized PnL", "0.0000"),
                ("Unrealized PnL", "0.0000"),
                ("Total Value", "0.0000"),
                ("Tick Capture", "0"),
            ],
        )

        grid.addWidget(eur_block, 0, 0)
        grid.addWidget(euri_block, 0, 1)
        grid.addWidget(balance_block, 1, 0)
        grid.addWidget(mode_block, 1, 1)
        grid.addWidget(accounting_block, 2, 0, 1, 2)
        root.addLayout(grid)

        logs_box = QGroupBox("Latest Logs")
        logs_layout = QVBoxLayout(logs_box)
        self.logs_view = QPlainTextEdit()
        self.logs_view.setReadOnly(True)
        self.logs_view.setPlainText("[v0.1.0] LUC skeleton initialized.\nNo API calls. No trading actions.")
        logs_layout.addWidget(self.logs_view)
        root.addWidget(logs_box, stretch=1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for caption in ["CONNECT", "SETTINGS", "FULL CONFIG"]:
            btn = QPushButton(caption)
            btn.setMinimumWidth(140)
            buttons.addWidget(btn)
        exit_btn = QPushButton("EXIT")
        exit_btn.setMinimumWidth(140)
        exit_btn.clicked.connect(self.close)
        buttons.addWidget(exit_btn)
        root.addLayout(buttons)


def main() -> int:
    app = QApplication(sys.argv)
    window = LUCTerminal()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
