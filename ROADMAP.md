# LUC Roadmap

## v0.1.0 — Clean project reset
- Reset old codebase to new LUC foundation.
- Create compact base structure.
- Build PySide6 GUI skeleton.
- No trading and no Binance connection.

## v0.1.1 — Binance connection
- API keys wiring.
- Connection checks.
- Server time.
- Account balances.
- Show EURI/USDT balances in GUI.

## v0.1.2 — Market data
- EURUSDT via WebSocket.
- EURIUSDT via HTTP polling.
- Show bid/ask/spread/mid/fair gap.

## v0.1.3 — Manual live orders
- Manual LIMIT BUY/SELL for EURIUSDT.
- Cancel order.
- Open orders table.
- Full action logs.

## v0.1.4 — Accounting core
- Cycles tracking.
- Realized/unrealized PnL.
- Total value.
- Tick capture.
- Win/loss statistics.
- Inventory skew.

## v0.2.0 — Passive Corridor mode
- Automated passive spread capture.
- Inventory control.

## v0.3.0 — Aggressive Trap mode
- BUY_TRAP / SELL_TRAP by fair-value gap.
- Cancel traps when gap disappears.

## v0.4.0 — Risk / Escape core
- Inventory zones.
- Stale order cancellation.
- Reduce-only behavior on dangerous skew.
- Parent impulse block.

## v0.5.0 — Stable live testing
- GUI/log/accounting/settings polish.
- Early live micro-cycle validation.
