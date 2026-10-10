"""TouchlessX entry point.

Loads configuration, wires the camera -> hand tracking -> gesture engine -> action
controller pipeline together, and starts the desktop UI.

Run with:
    python app.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "settings.json"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    config = load_config()

    try:
        from src.ui import App
    except ImportError as exc:
        print(f"Missing dependency: {exc}\nRun: pip install -r requirements.txt", file=sys.stderr)
        return 1

    app = App(config, CONFIG_PATH)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
