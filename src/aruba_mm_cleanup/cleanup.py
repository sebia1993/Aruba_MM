"""Cleanup runner for Aruba MM profiling-role users."""

from __future__ import annotations

from dataclasses import replace
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .models import CleanupRunSummary, CleanupSettings, DeleteResult, MmConnectionConfig, QueryResult
from .parser import normalize_mac, parse_global_user_table_explained
from .session import ConnectionFactory, MmSession


ProgressCallback = Callable[[str, dict[str, object]], None]
CancelCheck = Callable[[], bool]
SleepFunc = Callable[[float], None]
HISTORY_FILE_NAME = "deletion_history.jsonl"


def build_query_command(role: str) -> str:
    if not isinstance(role, str):
        raise ValueError("Role이 올바르지 않습니다.")
    role_value = role.strip() or "profiling"
    if _has_control_character(role_value):
        raise ValueError("Role에는 제어 문자를 사용할 수 없습니다.")
    return f"show global-user-table list role {role_value}"


def build_delete_command(mac: str) -> str:
    mac_value = normalize_mac(mac)
    if not mac_value:
        raise ValueError("MAC 주소가 올바르지 않습니다.")
    return f"aaa user delete mac {mac_value}"


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
        if not isinstance(output, str):
            raise RuntimeError("장비 조회 응답이 올바르지 않습니다.")
        parsed = parse_global_user_table_explained(output, role_filter=settings.role)
        self._emit(
            progress_callback,
            "query_done",
            command=command,
            count=len(parsed.entries),
            macs=[entry.mac for entry in parsed.entries],
            parse_decisions=[
                {
                    "line_number": item.line_number,
                    "action": item.action,
                    "reason": item.reason,
                    "mac": item.mac,
                    "role": item.role,
                    "user_type": item.user_type,
                    "type_na": item.type_na,
                }
                for item in parsed.decisions
            ],
            type_na_macs=[entry.mac for entry in parsed.entries if entry.type_na],
        )
        return QueryResult(command=command, entries=parsed.entries, parse_decisions=parsed.decisions)

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
            summary.query_parse_decisions = query.parse_decisions
            if not query.entries:
                self._emit(progress_callback, "run_done", queried_count=0, remaining_count=0)
                return self._finalize_summary(summary, output_dir=output_dir, host=config.host, progress_callback=progress_callback)

            if not self._countdown(settings.delete_delay_seconds, progress_callback, cancel_check):
                summary.canceled = True
                summary.remaining_count = summary.queried_count
                self._emit(progress_callback, "delete_canceled", count=summary.queried_count)
                return self._finalize_summary(summary, output_dir=output_dir, host=config.host, progress_callback=progress_callback)

            summary.delete_results = self._delete_macs(
                config,
                settings,
                summary.target_macs,
                progress_callback,
                should_cancel=cancel_check,
            )
            if cancel_check():
                summary.canceled = True
                summary.verification_skipped = True
                summary.delete_success_count = sum(1 for item in summary.delete_results if item.success)
                summary.delete_failure_count = sum(1 for item in summary.delete_results if not item.success)
                summary.remaining_count = max(summary.queried_count - summary.delete_success_count, 0)
                self._emit(progress_callback, "delete_canceled", count=max(len(summary.target_macs) - len(summary.delete_results), 0))
                return self._finalize_summary(summary, output_dir=output_dir, host=config.host, progress_callback=progress_callback)

            # Catch stop/cancel requests that arrive after the delete loop but
            # before the verification query starts.
            if cancel_check():
                summary.canceled = True
                summary.verification_skipped = True
                summary.delete_success_count = sum(1 for item in summary.delete_results if item.success)
                summary.delete_failure_count = sum(1 for item in summary.delete_results if not item.success)
                summary.remaining_count = max(summary.queried_count - summary.delete_success_count, 0)
                self._emit(progress_callback, "delete_canceled", count=0)
                return self._finalize_summary(summary, output_dir=output_dir, host=config.host, progress_callback=progress_callback)

            verify = self._query_users(config, settings, progress_callback=progress_callback)
            summary.verify_parse_decisions = verify.parse_decisions
            summary.remaining_count = len(verify.entries)
            summary.delete_results = _apply_verification(summary.delete_results, verify.macs)
            summary.delete_success_count = sum(1 for item in summary.delete_results if item.success)
            summary.delete_failure_count = sum(1 for item in summary.delete_results if not item.success)
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
        *,
        should_cancel: Optional[CancelCheck] = None,
    ) -> list[DeleteResult]:
        unique_macs = _unique_macs(macs)
        self._emit(progress_callback, "delete_batch_start", count=len(unique_macs))
        results: list[DeleteResult] = []
        for index, mac in enumerate(unique_macs, start=1):
            if should_cancel is not None and should_cancel():
                self._emit(progress_callback, "delete_canceled", count=len(unique_macs) - index + 1)
                break
            try:
                command = build_delete_command(mac)
            except ValueError as exc:
                error = f"확인 필요: 삭제 대상 MAC 오류 - {exc}"
                results.append(
                    DeleteResult(
                        mac=mac,
                        success=False,
                        command="",
                        error=error,
                        status="unknown",
                        response_status="unknown",
                    )
                )
                self._emit(
                    progress_callback,
                    "delete_unknown",
                    index=index,
                    total=len(unique_macs),
                    mac=mac,
                    command="",
                    error=error,
                )
                continue
            self._emit(progress_callback, "delete_start", index=index, total=len(unique_macs), mac=mac, command=command)
            try:
                output = self.session.run_command(
                    config,
                    settings,
                    command,
                    progress_callback=progress_callback,
                    retry_once=False,
                )
                status, error = classify_delete_response(output)
                success = status == "deleted"
                results.append(
                    DeleteResult(
                        mac=mac,
                        success=success,
                        command=command,
                        error=error,
                        status=status,
                        response_status=status,
                    )
                )
                self._emit(
                    progress_callback,
                    _delete_event_name(status),
                    index=index,
                    total=len(unique_macs),
                    mac=mac,
                    command=command,
                    error=error,
                )
            except Exception as exc:
                error = f"확인 필요: 삭제 명령 응답 실패 - {exc}"
                results.append(
                    DeleteResult(
                        mac=mac,
                        success=False,
                        command=command,
                        error=error,
                        status="unknown",
                        response_status="unknown",
                    )
                )
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
        try:
            summary.history_path = append_history_records(summary, output_dir=output_dir, host=host)
        except Exception as exc:
            summary.history_error = str(exc)
            self._emit(progress_callback, "warning", message=f"deletion history save failed: {exc}")
        return summary

    @staticmethod
    def _emit(callback: Optional[ProgressCallback], event: str, **payload: object) -> None:
        if callback is not None:
            try:
                callback(event, payload)
            except Exception:
                pass


def write_audit_summary(summary: CleanupRunSummary, *, output_dir: Path, host: str) -> Path:
    run_dir = output_dir / summary.started_at.strftime("%Y%m%d_%H%M%S_%f")
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "cleanup_summary.json"
    path.write_text(
        json.dumps(summary.as_audit_dict(host=host), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def append_history_records(summary: CleanupRunSummary, *, output_dir: Path, host: str) -> Optional[Path]:
    if not summary.delete_results:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / HISTORY_FILE_NAME
    lines: list[str] = []
    for item in summary.delete_results:
        record = {
            "run_at": summary.started_at.isoformat(timespec="seconds"),
            "host": host,
            "role": summary.role,
            "mac": item.mac,
            "result": _history_result_label(item),
            "success": item.success,
            "status": item.status or ("deleted" if item.success else "failed"),
            "response_status": item.response_status,
            "verified_absent": item.verified_absent,
            "error": item.error,
            "reappeared": item.status == "reappeared",
        }
        lines.append(json.dumps(record, ensure_ascii=False) + "\n")
    with path.open("a", encoding="utf-8") as handle:
        handle.writelines(lines)
    return path


def classify_delete_response(output: str) -> tuple[str, str]:
    if output is not None and not isinstance(output, str):
        return "unknown", f"확인 필요: 삭제 명령 응답 판정 불가 - {output}"
    text = (output or "").strip()
    normalized = text.casefold()
    if not text:
        return "unknown", "확인 필요: 삭제 명령 응답이 비어 있음"

    failure_markers = (
        "invalid input",
        "permission denied",
        "not authorized",
        "authorization failed",
        "authentication failed",
        "access denied",
        "not found",
        "no such",
        "does not exist",
        "error",
        "failed",
    )
    for marker in failure_markers:
        if marker in normalized:
            return "failed", text

    unknown_markers = (
        "incomplete",
        "ambiguous",
        "timed out",
        "timeout",
        "try again",
        "confirm",
        "continue",
    )
    for marker in unknown_markers:
        if marker in normalized:
            return "unknown", f"확인 필요: 삭제 명령 응답 판정 불가 - {text}"

    success_markers = (
        "user deleted",
        "deleted",
        "delete successful",
        "successfully deleted",
        "removed",
        "success",
    )
    for marker in success_markers:
        if marker in normalized:
            return "deleted", ""

    return "unknown", f"확인 필요: 삭제 명령 응답 판정 불가 - {text}"


def _delete_error_from_output(output: str) -> str:
    status, error = classify_delete_response(output)
    return "" if status == "deleted" else error


def _has_control_character(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _unique_macs(macs: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for mac in macs:
        text = mac.strip() if isinstance(mac, str) else ""
        normalized = normalize_mac(text) or text.casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _reappeared_deleted_macs(delete_results: list[DeleteResult], verify_macs: list[str]) -> list[str]:
    return [item.mac for item in delete_results if item.status == "reappeared"]


def _apply_verification(delete_results: list[DeleteResult], verify_macs: list[str]) -> list[DeleteResult]:
    remaining = set(_unique_macs(verify_macs))
    verified: list[DeleteResult] = []
    for item in delete_results:
        absent = item.mac not in remaining
        response_status = item.response_status or item.status
        if response_status == "deleted" and absent:
            verified.append(replace(item, success=True, status="verified_deleted", verified_absent=True))
        elif response_status == "deleted":
            error = item.error or "삭제 응답은 성공이었지만 검증 조회에서 다시 발견"
            verified.append(replace(item, success=False, status="reappeared", error=error, verified_absent=False))
        else:
            verified.append(replace(item, success=False, status=response_status or "unknown", verified_absent=absent))
    return verified


def _delete_event_name(status: str) -> str:
    if status == "deleted":
        return "delete_done"
    if status == "failed":
        return "delete_error"
    return "delete_unknown"


def _history_result_label(item: DeleteResult) -> str:
    if item.status == "reappeared":
        return "재조회됨"
    if item.status == "unknown":
        return "확인 필요"
    if item.success:
        return "삭제 완료"
    return "삭제 실패"
