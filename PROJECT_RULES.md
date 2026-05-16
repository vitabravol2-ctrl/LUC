# LUC Project Rules

## Core principles

1. **Live-first architecture** (no DRY_RUN mode for trading stages).
2. **No market orders at early live phases**; limit orders only.
3. **Transparency first**: every critical action should be visible in GUI and logs.
4. **Compact codebase**: avoid enterprise-style fragmentation.
5. **Financial precision**: use safe numeric handling for accounting in future versions.

## v0.1.0 boundaries

- GUI skeleton only.
- No exchange API integration.
- No auto/manual trading execution.
- No order routing.
