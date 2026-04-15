"""Compatibility entrypoint for existing cron jobs and direct script usage."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from arc_bot.cli import main_cli


if __name__ == "__main__":
    sys.exit(main_cli())
