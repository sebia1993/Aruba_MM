"""Reusable Aruba MM command session."""

from __future__ import annotations

from typing import Callable, Optional

from .connection import CommandConnection, connect_to_mm, run_command as send_mm_command
from .models import CleanupSettings, MmConnectionConfig


ProgressCallback = Callable[[str, dict[str, object]], None]
ConnectionFactory = Callable[[MmConnectionConfig, int], CommandConnection]


class MmSession:
    """Keep one MM connection alive until config changes or explicit close."""

    def __init__(self, *, connection_factory: Optional[ConnectionFactory] = None) -> None:
        self.connection_factory = connection_factory or (lambda config, timeout: connect_to_mm(config, timeout=timeout))
        self._connection: Optional[CommandConnection] = None
        self._config: Optional[MmConnectionConfig] = None

    @property
    def is_connected(self) -> bool:
        return self._connection is not None

    def run_command(
        self,
        config: MmConnectionConfig,
        settings: CleanupSettings,
        command: str,
        *,
        progress_callback: Optional[ProgressCallback] = None,
        retry_once: bool = True,
    ) -> str:
        connection = self._ensure_connected(config, settings, progress_callback=progress_callback)
        try:
            return send_mm_command(connection, command, timeout=settings.timeout)
        except Exception as exc:
            if not retry_once:
                raise
            self._emit(
                progress_callback,
                "session_reconnect_start",
                host=config.host,
                command=command,
                error=str(exc),
            )
            self.disconnect(progress_callback=progress_callback, reason="reconnect")
            connection = self._ensure_connected(config, settings, progress_callback=progress_callback)
            return send_mm_command(connection, command, timeout=settings.timeout)

    def disconnect(
        self,
        *,
        progress_callback: Optional[ProgressCallback] = None,
        reason: str = "manual",
    ) -> None:
        connection = self._connection
        self._connection = None
        self._config = None
        if connection is None:
            return
        try:
            connection.disconnect()
        except Exception as exc:
            self._emit(progress_callback, "warning", message=f"disconnect failed: {exc}", reason=reason)
        self._emit(progress_callback, "session_disconnected", reason=reason)

    def _ensure_connected(
        self,
        config: MmConnectionConfig,
        settings: CleanupSettings,
        *,
        progress_callback: Optional[ProgressCallback],
    ) -> CommandConnection:
        if self._connection is not None and self._config == config:
            self._emit(progress_callback, "session_reuse", host=config.host)
            return self._connection

        if self._connection is not None:
            self.disconnect(progress_callback=progress_callback, reason="config_changed")

        self._emit(progress_callback, "connect_start", host=config.host)
        self._connection = self.connection_factory(config, settings.timeout)
        self._config = config
        self._emit(progress_callback, "connect_done", host=config.host)
        self._safe_no_paging(settings, progress_callback=progress_callback)
        return self._connection

    def _safe_no_paging(
        self,
        settings: CleanupSettings,
        *,
        progress_callback: Optional[ProgressCallback],
    ) -> None:
        if self._connection is None:
            return
        try:
            send_mm_command(self._connection, "no paging", timeout=settings.timeout)
        except Exception as exc:
            self._emit(progress_callback, "warning", message=f"no paging failed: {exc}")

    @staticmethod
    def _emit(callback: Optional[ProgressCallback], event: str, **payload: object) -> None:
        if callback is not None:
            callback(event, payload)
