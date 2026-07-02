from __future__ import annotations

import argparse
import getpass
from pathlib import Path
from typing import Optional

from .cleanup import MmCleanupRunner
from .models import CleanupSettings, MmConnectionConfig


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="aruba-mm-cleanup", description="Aruba MM profiling-role MAC cleanup.")
    parser.add_argument("--host", required=True, help="Aruba MM host or IP")
    parser.add_argument("--username", required=True, help="SSH username")
    parser.add_argument("--password", help="SSH password; prompts when omitted")
    parser.add_argument("--enable-password", default="", help="optional enable password")
    parser.add_argument("--port", type=int, default=22, help="SSH port")
    parser.add_argument("--role", default="profiling", help="role to query and clean")
    parser.add_argument("--timeout", type=int, default=60, help="per-command timeout seconds")
    parser.add_argument("--delay", type=int, default=60, help="delete countdown seconds")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"), help="audit output directory")
    parser.add_argument("--yes", action="store_true", help="run without an interactive pre-countdown confirmation")
    args = parser.parse_args(argv)

    password = args.password if args.password is not None else getpass.getpass("Password: ")
    if not args.yes:
        answer = input(
            f"Query role '{args.role}' on {args.host}, then auto-delete after {args.delay}s. Continue? [y/N] "
        )
        if answer.strip().casefold() not in {"y", "yes"}:
            print("Canceled before query.")
            return 1

    config = MmConnectionConfig(
        host=args.host,
        username=args.username,
        password=password,
        port=args.port,
        enable_password=args.enable_password,
    )
    settings = CleanupSettings(role=args.role, timeout=max(5, args.timeout), delete_delay_seconds=max(0, args.delay))
    runner = MmCleanupRunner()

    def progress(event: str, payload: dict[str, object]) -> None:
        if event == "countdown":
            print(f"Delete countdown: {payload.get('remaining')}s")
        elif event in {"query_done", "delete_done", "delete_error", "run_done", "run_error"}:
            print(f"{event}: {payload}")

    summary = runner.run_once(config, settings, output_dir=args.output_dir, progress_callback=progress)
    print(f"Queried: {summary.queried_count}")
    print(f"Deleted: {summary.delete_success_count}")
    print(f"Failed: {summary.delete_failure_count}")
    print(f"Remaining: {summary.remaining_count}")
    print(f"Audit: {summary.audit_path}")
    return 1 if summary.error or summary.delete_failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
