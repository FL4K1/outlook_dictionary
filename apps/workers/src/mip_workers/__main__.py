"""Executable entrypoint for python -m mip_workers.

Starts the ARQ worker runtime using WorkerSettings.
"""

from __future__ import annotations

import sys

from arq.worker import run_worker

from mip_workers.worker import WorkerSettings


def main() -> None:
    """Run ARQ worker process."""
    run_worker(WorkerSettings)  # type: ignore[arg-type]


if __name__ == "__main__":
    sys.exit(main())
