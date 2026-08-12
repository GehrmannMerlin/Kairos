"""M-15 retention cleanup CLI（dry-run 安全）。

用法（backend/ 下）：
  .venv/Scripts/python.exe -m app.artifacts.cli --dry-run
  .venv/Scripts/python.exe -m app.artifacts.cli --execute   # 真实清理（生产需人工确认）
"""

from __future__ import annotations

import argparse
import asyncio

from app.artifacts.retention import RetentionService
from app.config import get_settings
from app.infra.deps import get_object_storage, get_session_factory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--days", type=int, default=None)
    args = parser.parse_args()
    days = args.days or get_settings().retention_heavy_days
    session = get_session_factory()()
    storage = get_object_storage()
    result = asyncio.run(
        RetentionService(session, storage, retention_days=days).run(dry_run=args.dry_run)
    )
    print(result)
    session.close()


if __name__ == "__main__":
    main()
