# LUC — EURI/EUR Equilibrium Corridor Harvester

Version: **v0.1.0**

LUC is a **live-first desktop terminal skeleton** for future trading operations on **EURIUSDT** with **EURUSDT** as parent/fair-value stream.

## Scope of v0.1.0

This version is a clean reset and foundation release:
- old project logic removed;
- compact project structure created;
- PySide6 GUI skeleton implemented;
- no Binance API integration yet;
- no trading logic yet.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Project structure

- `main.py`
- `requirements.txt`
- `README.md`
- `PROJECT_RULES.md`
- `ROADMAP.md`
- `config/settings.json`
- `logs/.gitkeep`
