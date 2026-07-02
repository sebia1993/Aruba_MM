"""Shared models for Aruba MM cleanup runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class MmConnectionConfig:
    host: str
    username: str
    password: str
    port: int = 22
    device_type: str = "aruba_os"
    enable_password: str = ""


@dataclass(frozen=True)
class CleanupSettings:
    role: str = "profiling"
    timeout: int = 60
    delete_delay_seconds: int = 60


@dataclass(frozen=True)
class UserEntry:
    mac: str
    role: str = ""
    username: str = ""
    ip_address: str = ""


@dataclass(frozen=True)
class QueryResult:
    command: str
    entries: list[UserEntry]

    @property
    def macs(self) -> list[str]:
        return [entry.mac for entry in self.entries]


@dataclass(frozen=True)
class DeleteResult:
    mac: str
    success: bool
    command: str
    error: str = ""
    status: str = ""


@dataclass
class CleanupRunSummary:
    started_at: datetime
    role: str
    queried_count: int = 0
    delete_success_count: int = 0
    delete_failure_count: int = 0
    remaining_count: int = 0
    canceled: bool = False
    query_command: str = ""
    target_macs: list[str] = field(default_factory=list)
    delete_results: list[DeleteResult] = field(default_factory=list)
    audit_path: Optional[Path] = None
    audit_error: str = ""
    error: str = ""

    def as_audit_dict(self, *, host: str) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "host": host,
            "role": self.role,
            "query_command": self.query_command,
            "queried_count": self.queried_count,
            "delete_success_count": self.delete_success_count,
            "delete_failure_count": self.delete_failure_count,
            "remaining_count": self.remaining_count,
            "canceled": self.canceled,
            "target_macs": self.target_macs,
            "delete_results": [
                {
                    "mac": item.mac,
                    "success": item.success,
                    "status": item.status or ("deleted" if item.success else "failed"),
                    "command": item.command,
                    "error": item.error,
                }
                for item in self.delete_results
            ],
            "audit_error": self.audit_error,
            "error": self.error,
        }
