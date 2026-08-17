"""Terminal reconciliation for lost/abandoned TaskWorkflows (dry-run by default).

Usage::

    python scripts/reconcile_runs.py                 # dry-run: print what would change
    python scripts/reconcile_runs.py --apply         # apply terminal commands
    python scripts/reconcile_runs.py --stale-after-seconds 7200

Only touches ``running`` Runs whose Temporal workflow is provably terminal (or absent).
Each command is re-applied through the existing CAS + DomainService path, so a still-alive
workflow that wins the terminal claim is never overwritten.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.infra.temporal import create_temporal_client  # noqa: E402
from app.reconciliation.service import (  # noqa: E402
    DEFAULT_STALE_AFTER_SECONDS,
    reconcile_stale_runs,
)


async def _workflow_status_fn(client):
    from temporalio.service import RPCError, RPCStatusCode

    async def get_status(workflow_id: str) -> str | None:
        try:
            handle = client.get_workflow_handle(workflow_id)
            description = await handle.describe()
            return description.status.name
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return None
            raise

    return get_status


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    client = await create_temporal_client(settings)
    results = await reconcile_stale_runs(
        workflow_status_fn=await _workflow_status_fn(client),
        stale_after_seconds=args.stale_after_seconds,
        dry_run=not args.apply,
    )
    for row in results:
        print(
            f"run={row['run_id']} task={row.get('task_id')} "
            f"temporal={row['temporal_status']} action={row['action']} "
            f"applied={row['applied']}"
        )
    print(f"{'APPLIED' if args.apply else 'DRY-RUN'}: {len(results)} stale run(s) inspected")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="apply terminal commands (default dry-run)"
    )
    parser.add_argument(
        "--stale-after-seconds",
        type=int,
        default=DEFAULT_STALE_AFTER_SECONDS,
        help="staleness cutoff in seconds (default %(default)s)",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
