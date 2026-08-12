"""Deployment-facing retention cleanup entry（python infra/scripts/retention_cleanup.py --dry-run）。"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.artifacts.cli import main  # noqa: E402

main()
