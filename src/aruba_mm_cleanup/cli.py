from __future__ import annotations

import argparse
import getpass
from pathlib import Path
from typing import Optional

from .cleanup import MmCleanupRunner, build_query_command
from .models import CleanupSettings, MmConnectionConfig


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="aruba-mm-cleanup", description="Aruba MM profiling-role MAC cleanup.")
    parser.add_argument("--host", required=True, help="Aruba MM host or IP")
    parser.add_argument("--username", required=True, help="SSH username")
    parser.add_argument("--password", help="SSH password; prompts when omitted")
    parser.add_argument("--enable-password", default="", help="optional enable password")
    parser.add_argument("--port", type=int, default=22, help="SSH port")
    parser.add_argument("--role", default="profiling", help="role to query and clean")
    parser.add_argument("--timeout", type=int, default=60, help="device response timeout seconds")
    parser.add_argument("--delay", type=int, default=60, help="countdown seconds between query and delete")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"), help="audit output directory")
    parser.add_argument("--yes", action="store_true", help="run without an interactive pre-countdown confirmation")
    args = parser.parse_args(argv)
    host = args.host.strip()
    if not host:
        parser.error("--host must not be empty")
    username = args.username.strip()
    if not username:
        parser.error("--username must not be empty")
    role = args.role.strip() or "profiling"
    if args.port < 1 or args.port > 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        build_query_command(role)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        password = args.password if args.password is not None else getpass.getpass("Password: ")
    except (EOFError, KeyboardInterrupt):
        print("Canceled before password input.")
        return 1
    if not args.yes:
        try:
            answer = input(
                f"Query role '{args.role}' on {args.host}, then auto-delete after {max(0, args.delay)}s countdown. Continue? [y/N] "
            )
        except (EOFError, KeyboardInterrupt):
            print("Canceled before query.")
            return 1
        if answer.strip().casefold() not in {"y", "yes"}:
            print("Canceled before query.")
            return 1

    config = MmConnectionConfig(
        host=host,
        username=username,
        password=password,
        port=args.port,
        enable_password=args.enable_password,
    )
    settings = CleanupSettings(role=role, timeout=max(5, args.timeout), delete_delay_seconds=max(0, args.delay))
    runner = MmCleanupRunner()

    def progress(event: str, payload: dict[str, object]) -> None:
        if event == "countdown":
            print(f"Delete countdown: {payload.get('remaining')}s")
        elif event in {"query_done", "delete_done", "delete_error", "run_done", "run_error"}:
            print(f"{event}: {payload}")

    summary = runner.run_once(config, settings, output_dir=args.output_dir.expanduser(), progress_callback=progress)
    queried_count = _summary_value(summary, "queried_count", 0)
    delete_success_count = _summary_value(summary, "delete_success_count", 0)
    delete_failure_count = _summary_value(summary, "delete_failure_count", 0)
    remaining_count = _summary_value(summary, "remaining_count", 0)
    reappeared_count = _summary_value(summary, "reappeared_count", 0)
    audit_path = _summary_value(summary, "audit_path", None)
    audit_error = _summary_value(summary, "audit_error", "")
    history_error = _summary_value(summary, "history_error", "")
    error = _summary_value(summary, "error", "summary unavailable")
    print(f"Queried: {queried_count}")
    print(f"Deleted: {delete_success_count}")
    print(f"Failed: {delete_failure_count}")
    print(f"Remaining: {remaining_count}")
    print(f"Reappeared: {reappeared_count}")
    print(f"Audit: {audit_path}")
    if audit_error:
        print(f"Audit warning: {audit_error}")
    if history_error:
        print(f"History warning: {history_error}")
    return 1 if error or delete_failure_count or reappeared_count else 0


def _summary_value(summary: object, name: str, default: object) -> object:
    try:
        return getattr(summary, name, default)
    except Exception:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
