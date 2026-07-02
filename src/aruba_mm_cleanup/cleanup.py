"""Cleanup runner for Aruba MM profiling-role users."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .models import CleanupRunSummary, CleanupSettings, DeleteResult, MmConnectionConfig, QueryResult
from .parser import normalize_mac, parse_global_user_table
from .session import ConnectionFactory, MmSession


ProgressCallback = Callable[[str, dict[str, object]], None]
CancelCheck = Callable[[], bool]
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
        session: Optional[MmSession] = None,
        persistent_session: bool = False,
        sleep_func: Optional[SleepFunc] = None,
    ) -> None:
        self.session = session or MmSession(connection_factory=connection_factory)
        self.persistent_session = persistent_session
        self.sleep_func = sleep_func or time.sleep

    def query_users(
        self,
        config: MmConnectionConfig,
        settings: CleanupSettings,
        *,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> QueryResult:
        try:
            return self._query_users(config, settings, progress_callback=progress_callback)
        finally:
            if not self.persistent_session:
                self.close_session(progress_callback=progress_callback, reason="run_complete")

    def _query_users(
        self,
        config: MmConnectionConfig,
        settings: CleanupSettings,
        *,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> QueryResult:
        command = build_query_command(settings.role)
        self._emit(progress_callback, "query_start", command=command, role=settings.role)
        output = self.session.run_command(config, settings, command, progress_callback=progress_callback)
        entries = parse_global_user_table(output, role_filter=settings.role)
        self._emit(
            progress_callback,
            "query_done",
            command=command,
            count=len(entries),
            macs=[entry.mac for entry in entries],
        )
        return QueryResult(command=command, entries=entries)

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
            query = self._query_users(config, settings, progress_callback=progress_callback)
            summary.query_command = query.command
            summary.queried_count = len(query.entries)
            summary.target_macs = _unique_macs(query.macs)
            if not query.entries:
                self._emit(progress_callback, "run_done", queried_count=0, remaining_count=0)
                return self._finalize_summary(summary, output_dir=output_dir, host=config.host, progress_callback=progress_callback)

            if not self._countdown(settings.delete_delay_seconds, progress_callback, cancel_check):
                summary.canceled = True
                summary.remaining_count = summary.queried_count
                self._emit(progress_callback, "delete_canceled", count=summary.queried_count)
                return self._finalize_summary(summary, output_dir=output_dir, host=config.host, progress_callback=progress_callback)

            summary.delete_results = self._delete_macs(config, settings, summary.target_macs, progress_callback)
            summary.delete_success_count = sum(1 for item in summary.delete_results if item.success)
            summary.delete_failure_count = sum(1 for item in summary.delete_results if not item.success)
            verify = self._query_users(config, settings, progress_callback=progress_callback)
            summary.remaining_count = len(verify.entries)
            summary.reappeared_macs = _reappeared_deleted_macs(summary.delete_results, verify.macs)
            summary.reappeared_count = len(summary.reappeared_macs)
            if summary.reappeared_macs:
                self._emit(
                    progress_callback,
                    "reappeared_macs",
                    count=summary.reappeared_count,
                    macs=summary.reappeared_macs,
                )
            self._emit(
                progress_callback,
                "run_done",
                queried_count=summary.queried_count,
                delete_success_count=summary.delete_success_count,
                delete_failure_count=summary.delete_failure_count,
                remaining_count=summary.remaining_count,
                reappeared_count=summary.reappeared_count,
            )
        except Exception as exc:
            summary.error = str(exc)
            self._emit(progress_callback, "run_error", error=str(exc))
        return self._finalize_summary(summary, output_dir=output_dir, host=config.host, progress_callback=progress_callback)

    def _delete_macs(
        self,
        config: MmConnectionConfig,
        settings: CleanupSettings,
        macs: list[str],
        progress_callback: Optional[ProgressCallback],
    ) -> list[DeleteResult]:
        unique_macs = _unique_macs(macs)
        self._emit(progress_callback, "delete_batch_start", count=len(unique_macs))
        results: list[DeleteResult] = []
        for index, mac in enumerate(unique_macs, start=1):
            command = build_delete_command(mac)
            self._emit(progress_callback, "delete_start", index=index, total=len(unique_macs), mac=mac, command=command)
            try:
                output = self.session.run_command(
                    config,
                    settings,
                    command,
                    progress_callback=progress_callback,
                    retry_once=False,
                )
                error = _delete_error_from_output(output)
                success = not error
                status = "deleted" if success else "failed"
                results.append(DeleteResult(mac=mac, success=success, command=command, error=error, status=status))
                self._emit(
                    progress_callback,
                    "delete_done" if success else "delete_error",
                    index=index,
                    total=len(unique_macs),
                    mac=mac,
                    command=command,
                    error=error,
                )
            except Exception as exc:
                error = f"확인 필요: 삭제 명령 응답 실패 - {exc}"
                results.append(DeleteResult(mac=mac, success=False, command=command, error=error, status="unknown"))
                self._emit(
                    progress_callback,
                    "delete_unknown",
                    index=index,
                    total=len(unique_macs),
                    mac=mac,
                    command=command,
                    error=error,
                )
        return results

    def close_session(
        self,
        *,
        progress_callback: Optional[ProgressCallback] = None,
        reason: str = "manual",
    ) -> None:
        self.session.disconnect(progress_callback=progress_callback, reason=reason)

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

    def _finalize_summary(
        self,
        summary: CleanupRunSummary,
        *,
        output_dir: Path,
        host: str,
        progress_callback: Optional[ProgressCallback],
    ) -> CleanupRunSummary:
        if not self.persistent_session:
            self.close_session(progress_callback=progress_callback, reason="run_complete")
        try:
            summary.audit_path = write_audit_summary(summary, output_dir=output_dir, host=host)
        except Exception as exc:
            summary.audit_error = str(exc)
            self._emit(progress_callback, "warning", message=f"audit summary save failed: {exc}")
        return summary

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


def _unique_macs(macs: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for mac in macs:
        normalized = normalize_mac(mac) or mac.strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _reappeared_deleted_macs(delete_results: list[DeleteResult], verify_macs: list[str]) -> list[str]:
    remaining = set(_unique_macs(verify_macs))
    deleted_success_macs = [item.mac for item in delete_results if item.success]
    return [mac for mac in _unique_macs(deleted_success_macs) if mac in remaining]
