"""Live Aruba MM command boundary."""

from __future__ import annotations

from typing import Protocol

from .models import MmConnectionConfig


class CommandConnection(Protocol):
    def send_command_timing(self, *, command_string: str, **kwargs) -> str: ...

    def disconnect(self) -> None: ...


def connect_to_mm(config: MmConnectionConfig, *, timeout: int):
    try:
        from netmiko import ConnectHandler
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("netmiko is required for live Aruba MM access") from exc

    params = {
        "device_type": config.device_type,
        "host": config.host,
        "port": config.port,
        "username": config.username,
        "password": config.password,
        "secret": config.enable_password or None,
        "timeout": timeout,
        "conn_timeout": timeout,
        "auth_timeout": timeout,
        "banner_timeout": timeout,
        "fast_cli": False,
    }
    connection = ConnectHandler(**params)
    try:
        if config.enable_password:
            connection.enable()
    except Exception:
        try:
            connection.disconnect()
        except Exception:
            pass
        raise
    return connection


def run_command(connection: CommandConnection, command: str, *, timeout: int) -> str:
    return connection.send_command_timing(
        command_string=command,
        strip_prompt=False,
        strip_command=False,
        cmd_verify=False,
        read_timeout=timeout,
    )
