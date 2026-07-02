"""Cleanup runner for Aruba MM profiling-role users."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .connection import CommandConnection, connect_to_mm, run_command
from .models import CleanupRunSummary, CleanupSettings, DeleteResult, MmConnectionConfig, QueryResult
from .parser import parse_global_user_table


ProgressCallback = Callable[[str, dict[str, object]], None]
CancelCheck = Callable[[], bool]
ConnectionFactory = Callable[[MmConnectionConfig, int], CommandConnection]
SleepFunc = Callable[[float], None]


def build_query_command(role: str) -> str:
    role_value = role.strip() or "profiling"
    return f"show global-user-table list role {role_value}"


def build_delete_command(mac: str) -> str:
    return f"aaa user delete mac {mac}"


class MmCleanupRunner:
    def __init__(
        self,
        *,
        connection_factory: Optional[ConnectionFactory] = None,
        sleep_func: Optional[SleepFunc] = None,
    ) -> None:
        self.connection_factory = connection_factory or (lambda config, timeout: connect_to_mm(config, timeout=timeout))
        self.sleep_func = sleep_func or time.sleep

    def query_users(
        self,
        config: MmConnectionConfig,
        settings: CleanupSettings,
        *,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> QueryResult:
        command = build_query_command(settings.role)
        self._emit(progress_callback, "connect_start", host=config.host)
        connection = self.connection_factory(config, settings.timeout)
        try:
            self._emit(progress_callback, "connect_done", host=config.host)
            self._safe_no_paging(connection, settings, progress_callback=progress_callback)
            self._emit(progress_callback, "query_start", command=command, role=settings.role)
            output = run_command(connection, command, timeout=settings.timeout)
            entries = parse_global_user_table(output, role_filter=settings.role)
            self._emit(
                progress_callback,
                "query_done",
                command=command,
                count=len(entries),
                macs=[entry.mac for entry in entries],
            )
            return QueryResult(command=command, entries=entries)
        finally:
            self._disconnect(connection)

    def run_once(
        self,
        config: MmConnectionConfig,
        settings: CleanupSettings,
        *,
        output_dir: Path,
        progress_callback: Optional[ProgressCallback] = None,
        should_cancel: Optional[CancelCheck] = None,
    ) -> CleanupRunSummary:
        started_at = datetime.now()
        summary = CleanupRunSummary(started_at=started_at, role=settings.role)
        cancel_check = should_cancel or (lambda: False)
        try:
            query = self.query_users(config, settings, progress_callback=progress_callback)
            summary.query_command = query.command
            summary.queried_count = len(query.entries)
            summary.target_macs = query.macs
            if not query.entries:
                self._emit(progress_callback, "run_done", queried_count=0, remaining_count=0)
                summary.audit_path = write_audit_summary(summary, output_dir=output_dir, host=config.host)
                return summary

            if not self._countdown(settings.delete_delay_seconds, progress_callback, cancel_check):
                summary.canceled = True
                summary.remaining_count = summary.queried_count
                self._emit(progress_callback, "delete_canceled", count=summary.queried_count)
                summary.audit_path = write_audit_summary(summary, output_dir=output_dir, host=config.host)
                return summary

            summary.delete_results = self._delete_macs(config, settings, query.macs, progress_callback)
            summary.delete_success_count = sum(1 for item in summary.delete_results if item.success)
            summary.delete_failure_count = sum(1 for item in summary.delete_results if not item.success)
            verify = self.query_users(config, settings, progress_callback=progress_callback)
            summary.remaining_count = len(verify.entries)
            self._emit(
                progress_callback,
                "run_done",
                queried_count=summary.queried_count,
                delete_success_count=summary.delete_success_count,
                delete_failure_count=summary.delete_failure_count,
                remaining_count=summary.remaining_count,
            )
        except Exception as exc:
            summary.error = str(exc)
            self._emit(progress_callback, "run_error", error=str(exc))
        summary.audit_path = write_audit_summary(summary, output_dir=output_dir, host=config.host)
        return summary

    def _delete_macs(
        self,
        config: MmConnectionConfig,
        settings: CleanupSettings,
        macs: list[str],
        progress_callback: Optional[ProgressCallback],
    ) -> list[DeleteResult]:
        self._emit(progress_callback, "delete_connect_start", count=len(macs))
        connection = self.connection_factory(config, settings.timeout)
        results: list[DeleteResult] = []
        try:
            self._emit(progress_callback, "delete_connect_done", count=len(macs))
            self._safe_no_paging(connection, settings, progress_callback=progress_callback)
            for index, mac in enumerate(macs, start=1):
                command = build_delete_command(mac)
                self._emit(progress_callback, "delete_start", index=index, total=len(macs), mac=mac, command=command)
                try:
                    output = run_command(connection, command, timeout=settings.timeout)
                    error = _delete_error_from_output(output)
                    success = not error
                    results.append(DeleteResult(mac=mac, success=success, command=command, error=error))
                    self._emit(
                        progress_callback,
                        "delete_done" if success else "delete_error",
                        index=index,
                        total=len(macs),
                        mac=mac,
                        command=command,
                        error=error,
                    )
                except Exception as exc:
                    error = str(exc)
                    results.append(DeleteResult(mac=mac, success=False, command=command, error=error))
                    self._emit(
                        progress_callback,
                        "delete_error",
                        index=index,
                        total=len(macs),
                        mac=mac,
                        command=command,
                        error=error,
                    )
        finally:
            self._disconnect(connection)
        return results

    def _safe_no_paging(
        self,
        connection: CommandConnection,
        settings: CleanupSettings,
        *,
        progress_callback: Optional[ProgressCallback],
    ) -> None:
        try:
            run_command(connection, "no paging", timeout=settings.timeout)
        except Exception as exc:
            self._emit(progress_callback, "warning", message=f"no paging failed: {exc}")

    def _countdown(
        self,
        seconds: int,
        progress_callback: Optional[ProgressCallback],
        should_cancel: CancelCheck,
    ) -> bool:
        remaining = max(0, int(seconds))
        while remaining > 0:
            if should_cancel():
                return False
            self._emit(progress_callback, "countdown", remaining=remaining)
            self.sleep_func(1)
            remaining -= 1
        if should_cancel():
            return False
        self._emit(progress_callback, "countdown", remaining=0)
        return True

    @staticmethod
    def _disconnect(connection: CommandConnection) -> None:
        try:
            connection.disconnect()
        except Exception:
            pass

    @staticmethod
    def _emit(callback: Optional[ProgressCallback], event: str, **payload: object) -> None:
        if callback is not None:
            callback(event, payload)


def write_audit_summary(summary: CleanupRunSummary, *, output_dir: Path, host: str) -> Path:
    run_dir = output_dir / summary.started_at.strftime("%Y%m%d_%H%M%S_%f")
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "cleanup_summary.json"
    path.write_text(
        json.dumps(summary.as_audit_dict(host=host), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _delete_error_from_output(output: str) -> str:
    normalized = (output or "").casefold()
    for marker in ("invalid input", "permission denied", "not authorized", "error", "failed"):
        if marker in normalized:
            return output.strip() or marker
    return ""
