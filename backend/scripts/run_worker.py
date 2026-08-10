"""Run the Temporal worker from a host shell.

Usage: ``python scripts/run_worker.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.worker import main  # noqa: E402

if __name__ == "__main__":
    main()
