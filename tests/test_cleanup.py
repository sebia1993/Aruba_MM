import json
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace

from aruba_mm_cleanup.cleanup import (
    HISTORY_FILE_NAME,
    MmCleanupRunner,
    append_history_records,
    build_delete_command,
    build_query_command,
    classify_delete_response,
    write_audit_summary,
)
from aruba_mm_cleanup.connection import connect_to_mm
from aruba_mm_cleanup.models import CleanupRunSummary, CleanupSettings, DeleteResult, MmConnectionConfig, ParseDecision
from aruba_mm_cleanup.session import MmSession


class FakeConnection:
    def __init__(self, responses=None, failures=None):
        self.responses = responses or {}
        self.failures = failures or {}
        self.commands = []
        self.disconnected = False

    def send_command_timing(self, *, command_string, **_kwargs):
        self.commands.append(command_string)
        if command_string in self.failures:
            raise self.failures[command_string]
        response = self.responses.get(command_string, "")
        if isinstance(response, list):
            return response.pop(0)
        return response

    def disconnect(self):
        self.disconnected = True


class FailingDisconnectConnection(FakeConnection):
    def disconnect(self):
        self.disconnected = True
        raise RuntimeError("disconnect failed")


class MissingCommandConnection:
    def __init__(self):
        self.disconnected = False

    def disconnect(self):
        self.disconnected = True


def test_build_commands_use_role_and_mac():
    assert build_query_command("profiling") == "show global-user-table list role profiling"
    assert build_delete_command("aa:bb:cc:00:00:01") == "aaa user delete mac aa:bb:cc:00:00:01"


def test_build_delete_command_rejects_invalid_mac():
    try:
        build_delete_command("not-a-mac")
    except ValueError as exc:
        assert "MAC" in str(exc)
    else:
        raise AssertionError("build_delete_command should reject invalid MAC values")


def test_build_delete_command_rejects_missing_mac_without_attribute_error():
    try:
        build_delete_command(None)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "MAC" in str(exc)
    else:
        raise AssertionError("build_delete_command should reject missing MAC values")


def test_build_query_command_rejects_control_characters_in_role():
    try:
        build_query_command("profiling\nshow version")
    except ValueError as exc:
        assert "Role" in str(exc)
    else:
        raise AssertionError("build_query_command should reject role control characters")


def test_build_query_command_rejects_missing_role_without_attribute_error():
    try:
        build_query_command(None)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "Role" in str(exc)
    else:
        raise AssertionError("build_query_command should reject missing role values")


def test_connect_to_mm_closes_connection_when_enable_fails(monkeypatch):
    class EnableFailingConnection(FakeConnection):
        def enable(self):
            raise RuntimeError("enable failed")

    connection = EnableFailingConnection()
    captured_params = {}

    def fake_connect_handler(**params):
        captured_params.update(params)
        return connection

    monkeypatch.setitem(sys.modules, "netmiko", SimpleNamespace(ConnectHandler=fake_connect_handler))

    config = MmConnectionConfig(
        host="192.0.2.10",
        username="admin",
        password="secret",
        enable_password="enable-secret",
    )

    try:
        connect_to_mm(config, timeout=7)
    except RuntimeError as exc:
        assert str(exc) == "enable failed"
    else:
        raise AssertionError("connect_to_mm should re-raise enable failure")

    assert connection.disconnected is True
    assert captured_params["host"] == "192.0.2.10"
    assert captured_params["secret"] == "enable-secret"


def test_session_disconnect_failure_is_reported_and_session_is_cleared():
    connection = FailingDisconnectConnection()
    events = []
    session = MmSession(connection_factory=lambda _config, _timeout: connection)
    config = MmConnectionConfig(host="192.0.2.10", username="admin", password="secret")
    settings = CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0)

    assert session.run_command(config, settings, "show version") == ""

    session.disconnect(progress_callback=lambda event, payload: events.append((event, payload)), reason="manual")

    assert connection.disconnected is True
    assert session.is_connected is False
    assert ("warning", {"message": "disconnect failed: disconnect failed", "reason": "manual"}) in events
    assert ("session_disconnected", {"reason": "manual"}) in events


def test_session_rejects_invalid_connection_object_and_clears_state():
    connection = MissingCommandConnection()
    events = []
    session = MmSession(connection_factory=lambda _config, _timeout: connection)  # type: ignore[arg-type]
    config = MmConnectionConfig(host="192.0.2.10", username="admin", password="secret")
    settings = CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0)

    try:
        session.run_command(
            config,
            settings,
            "show version",
            progress_callback=lambda event, payload: events.append((event, payload)),
        )
    except RuntimeError as exc:
        assert "MM 연결 객체" in str(exc)
    else:
        raise AssertionError("session should reject invalid connection objects")

    assert connection.disconnected is True
    assert session.is_connected is False
    assert ("connect_start", {"host": "192.0.2.10"}) in events
    assert not any(event == "connect_done" for event, _payload in events)


def test_run_once_deletes_snapshot_and_verifies_remaining(tmp_path):
    first_query = "10.1.1.10 aa:bb:cc:00:00:01 user-a profiling\n10.1.1.11 aa:bb:cc:00:00:02 user-b profiling"
    verify_query = ""
    connection = FakeConnection(
        responses={
            "no paging": "",
            "show global-user-table list role profiling": [first_query, verify_query],
            "aaa user delete mac aa:bb:cc:00:00:01": "User deleted",
            "aaa user delete mac aa:bb:cc:00:00:02": "User deleted",
        }
    )
    connections = [connection]
    events = []
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connections.pop(0),
        sleep_func=lambda _seconds: None,
    )

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=1),
        output_dir=tmp_path,
        progress_callback=lambda event, payload: events.append((event, payload)),
    )

    assert summary.queried_count == 2
    assert summary.delete_success_count == 2
    assert summary.delete_failure_count == 0
    assert summary.remaining_count == 0
    assert [item.status for item in summary.delete_results] == ["verified_deleted", "verified_deleted"]
    assert summary.audit_path and summary.audit_path.exists()
    assert summary.history_path and summary.history_path.exists()
    assert connections == []
    assert connection.disconnected is True
    assert connection.commands == [
        "no paging",
        "show global-user-table list role profiling",
        "aaa user delete mac aa:bb:cc:00:00:01",
        "aaa user delete mac aa:bb:cc:00:00:02",
        "show global-user-table list role profiling",
    ]
    assert any(event == "countdown" and payload["remaining"] == 1 for event, payload in events)


def test_run_once_records_partial_delete_failure(tmp_path):
    first_query = "10.1.1.10 aa:bb:cc:00:00:01 user-a profiling\n10.1.1.11 aa:bb:cc:00:00:02 user-b profiling"
    connection = FakeConnection(
        responses={
            "no paging": "",
            "show global-user-table list role profiling": [
                first_query,
                "10.1.1.11 aa:bb:cc:00:00:02 user-b profiling",
            ],
            "aaa user delete mac aa:bb:cc:00:00:01": "User deleted",
            "aaa user delete mac aa:bb:cc:00:00:02": "Error: not found",
        }
    )
    connections = [connection]
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connections.pop(0),
        sleep_func=lambda _seconds: None,
    )

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        output_dir=tmp_path,
    )

    assert summary.delete_success_count == 1
    assert summary.delete_failure_count == 1
    assert summary.remaining_count == 1
    assert summary.delete_results[1].error == "Error: not found"
    assert connections == []
    assert connection.disconnected is True


def test_run_once_zero_delete_delay_starts_delete_after_countdown_zero(tmp_path):
    first_query = "10.1.1.10 aa:bb:cc:00:00:01 user-a profiling"
    connection = FakeConnection(
        responses={
            "no paging": "",
            "show global-user-table list role profiling": [first_query, ""],
            "aaa user delete mac aa:bb:cc:00:00:01": "User deleted",
        }
    )
    events = []
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connection,
        sleep_func=lambda _seconds: None,
    )

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        output_dir=tmp_path,
        progress_callback=lambda event, payload: events.append((event, payload)),
    )

    assert summary.delete_success_count == 1
    countdown_events = [payload["remaining"] for event, payload in events if event == "countdown"]
    assert countdown_events == [0]
    assert "aaa user delete mac aa:bb:cc:00:00:01" in connection.commands


def test_run_once_reports_type_na_macs_without_blocking_delete(tmp_path):
    header = f"{'IP':<16}{'MAC Address':<21}{'User':<14}{'Role':<12}{'Type':<8}{'BSSID'}"
    first_query = "\n".join(
        [
            header,
            f"{'10.1.1.10':<16}{'aa:bb:cc:00:00:01':<21}{'user-a':<14}{'profiling':<12}{'N/A':<8}{'11:22:33:44:55:66'}",
        ]
    )
    connection = FakeConnection(
        responses={
            "no paging": "",
            "show global-user-table list role profiling": [first_query, ""],
            "aaa user delete mac aa:bb:cc:00:00:01": "User deleted",
        }
    )
    events = []
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connection,
        sleep_func=lambda _seconds: None,
    )

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        output_dir=tmp_path,
        progress_callback=lambda event, payload: events.append((event, payload)),
    )

    assert "aaa user delete mac aa:bb:cc:00:00:01" in connection.commands
    query_done_payload = next(payload for event, payload in events if event == "query_done" and payload["macs"])
    assert query_done_payload["type_na_macs"] == ["aa:bb:cc:00:00:01"]
    assert any(item.mac == "aa:bb:cc:00:00:01" and item.type_na for item in summary.query_parse_decisions)
    audit = json.loads(summary.audit_path.read_text(encoding="utf-8"))
    selected = [item for item in audit["query_parse_decisions"] if item["action"] == "selected"]
    assert selected[0]["user_type"] == "N/A"
    assert selected[0]["type_na"] is True


def test_run_once_flags_successfully_deleted_mac_that_reappears(tmp_path):
    first_query = "10.1.1.10 aa:bb:cc:00:00:01 user-a profiling\n10.1.1.11 aa:bb:cc:00:00:02 user-b profiling"
    verify_query = "10.1.1.10 aa:bb:cc:00:00:01 user-a profiling\n10.1.1.11 aa:bb:cc:00:00:02 user-b profiling"
    connection = FakeConnection(
        responses={
            "no paging": "",
            "show global-user-table list role profiling": [first_query, verify_query],
            "aaa user delete mac aa:bb:cc:00:00:01": "User deleted",
            "aaa user delete mac aa:bb:cc:00:00:02": "Error: not found",
        }
    )
    events = []
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connection,
        sleep_func=lambda _seconds: None,
    )

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        output_dir=tmp_path,
        progress_callback=lambda event, payload: events.append((event, payload)),
    )

    assert summary.delete_success_count == 0
    assert summary.delete_failure_count == 2
    assert summary.remaining_count == 2
    assert summary.reappeared_count == 1
    assert summary.delete_results[0].status == "reappeared"
    assert summary.reappeared_macs == ["aa:bb:cc:00:00:01"]
    assert any(
        event == "reappeared_macs" and payload["macs"] == ["aa:bb:cc:00:00:01"]
        for event, payload in events
    )

    audit = json.loads(summary.audit_path.read_text(encoding="utf-8"))
    assert audit["reappeared_count"] == 1
    assert audit["reappeared_macs"] == ["aa:bb:cc:00:00:01"]


def test_run_once_can_cancel_during_countdown(tmp_path):
    first_query = "10.1.1.10 aa:bb:cc:00:00:01 user-a profiling"
    query_conn = FakeConnection(
        responses={"no paging": "", "show global-user-table list role profiling": [first_query]}
    )
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: query_conn,
        sleep_func=lambda _seconds: None,
    )
    checks = iter([False, True])

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=3),
        output_dir=tmp_path,
        should_cancel=lambda: next(checks, True),
    )

    assert summary.canceled is True
    assert summary.delete_results == []
    assert summary.remaining_count == 1
    assert query_conn.disconnected is True


def test_run_once_can_cancel_during_delete_loop_before_next_mac(tmp_path):
    first_query = "10.1.1.10 aa:bb:cc:00:00:01 user-a profiling\n10.1.1.11 aa:bb:cc:00:00:02 user-b profiling"
    connection = FakeConnection(
        responses={
            "no paging": "",
            "show global-user-table list role profiling": [first_query],
            "aaa user delete mac aa:bb:cc:00:00:01": "User deleted",
        }
    )
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connection,
        sleep_func=lambda _seconds: None,
    )
    checks = iter([False, False, True])

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        output_dir=tmp_path,
        should_cancel=lambda: next(checks, True),
    )

    assert summary.canceled is True
    assert summary.verification_skipped is True
    assert len(summary.delete_results) == 1
    assert connection.commands.count("aaa user delete mac aa:bb:cc:00:00:01") == 1
    assert "aaa user delete mac aa:bb:cc:00:00:02" not in connection.commands
    assert connection.commands.count("show global-user-table list role profiling") == 1


def test_run_once_skips_verify_when_canceled_after_delete_loop(tmp_path):
    first_query = "10.1.1.10 aa:bb:cc:00:00:01 user-a profiling"
    connection = FakeConnection(
        responses={
            "no paging": "",
            "show global-user-table list role profiling": [first_query],
            "aaa user delete mac aa:bb:cc:00:00:01": "User deleted",
        }
    )
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connection,
        sleep_func=lambda _seconds: None,
    )
    checks = iter([False, False, True])

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        output_dir=tmp_path,
        should_cancel=lambda: next(checks, True),
    )

    assert summary.canceled is True
    assert summary.verification_skipped is True
    assert connection.commands.count("show global-user-table list role profiling") == 1


def test_zero_query_writes_audit_without_delete(tmp_path):
    query_conn = FakeConnection(responses={"no paging": "", "show global-user-table list role profiling": ""})
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: query_conn,
        sleep_func=lambda _seconds: None,
    )

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        output_dir=Path(tmp_path),
    )

    assert summary.queried_count == 0
    assert summary.delete_success_count == 0
    assert summary.audit_path and summary.audit_path.exists()
    assert query_conn.disconnected is True


def test_non_string_query_response_is_reported_without_delete(tmp_path):
    query_conn = FakeConnection(responses={"no paging": "", "show global-user-table list role profiling": None})
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: query_conn,
        sleep_func=lambda _seconds: None,
    )

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        output_dir=Path(tmp_path),
    )

    assert "장비 조회 응답" in summary.error
    assert summary.delete_results == []
    assert query_conn.commands == ["no paging", "show global-user-table list role profiling"]
    assert query_conn.disconnected is True


def test_progress_callback_failure_does_not_abort_run(tmp_path):
    query_conn = FakeConnection(responses={"no paging": "", "show global-user-table list role profiling": ""})
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: query_conn,
        sleep_func=lambda _seconds: None,
    )

    def failing_progress(_event, _payload):
        raise RuntimeError("progress failed")

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        output_dir=tmp_path,
        progress_callback=failing_progress,
    )

    assert summary.error == ""
    assert summary.queried_count == 0
    assert query_conn.commands == ["no paging", "show global-user-table list role profiling"]
    assert query_conn.disconnected is True


def test_persistent_runner_reuses_session_until_closed(tmp_path):
    first_query = "10.1.1.10 aa:bb:cc:00:00:01 user-a profiling"
    connection = FakeConnection(
        responses={
            "no paging": "",
            "show global-user-table list role profiling": [first_query, "", first_query, ""],
            "aaa user delete mac aa:bb:cc:00:00:01": "User deleted",
        }
    )
    factory_calls = []
    runner = MmCleanupRunner(
        connection_factory=lambda config, _timeout: factory_calls.append(config) or connection,
        persistent_session=True,
        sleep_func=lambda _seconds: None,
    )
    config = MmConnectionConfig(host="192.0.2.10", username="admin", password="secret")
    settings = CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0)

    first_summary = runner.run_once(config, settings, output_dir=tmp_path)
    second_summary = runner.run_once(config, settings, output_dir=tmp_path)

    assert first_summary.delete_success_count == 1
    assert second_summary.delete_success_count == 1
    assert len(factory_calls) == 1
    assert connection.disconnected is False

    runner.close_session()

    assert connection.disconnected is True


def test_stale_session_reconnects_and_retries_command_once(tmp_path):
    query_command = build_query_command("profiling")
    stale_connection = FakeConnection(responses={"no paging": ""}, failures={query_command: RuntimeError("socket closed")})
    fresh_connection = FakeConnection(responses={"no paging": "", query_command: ""})
    connections = [stale_connection, fresh_connection]
    events = []
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connections.pop(0),
        sleep_func=lambda _seconds: None,
    )

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        output_dir=tmp_path,
        progress_callback=lambda event, payload: events.append((event, payload)),
    )

    assert summary.error == ""
    assert summary.queried_count == 0
    assert connections == []
    assert stale_connection.disconnected is True
    assert fresh_connection.disconnected is True
    assert stale_connection.commands == ["no paging", query_command]
    assert fresh_connection.commands == ["no paging", query_command]
    assert any(event == "session_reconnect_start" for event, _payload in events)


def test_reconnect_failure_reports_initial_and_retry_errors(tmp_path):
    query_command = build_query_command("profiling")
    stale_connection = FakeConnection(responses={"no paging": ""}, failures={query_command: RuntimeError("socket closed")})
    factory_calls = []
    events = []

    def failing_factory(config, _timeout):
        factory_calls.append(config)
        if len(factory_calls) == 1:
            return stale_connection
        raise RuntimeError("reconnect denied")

    runner = MmCleanupRunner(
        connection_factory=failing_factory,
        sleep_func=lambda _seconds: None,
    )

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        output_dir=tmp_path,
        progress_callback=lambda event, payload: events.append((event, payload)),
    )

    assert "socket closed" in summary.error
    assert "reconnect denied" in summary.error
    assert stale_connection.disconnected is True
    assert len(factory_calls) == 2
    assert any(
        event == "session_reconnect_start" and payload["error"] == "socket closed"
        for event, payload in events
    )


def test_session_disconnects_retry_connection_after_retry_command_failure():
    command = "show version"
    stale_connection = FakeConnection(responses={"no paging": ""}, failures={command: RuntimeError("socket closed")})
    retry_connection = FakeConnection(responses={"no paging": ""}, failures={command: RuntimeError("socket still closed")})
    connections = [stale_connection, retry_connection]
    events = []
    session = MmSession(connection_factory=lambda _config, _timeout: connections.pop(0))
    config = MmConnectionConfig(host="192.0.2.10", username="admin", password="secret")
    settings = CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0)

    try:
        session.run_command(
            config,
            settings,
            command,
            progress_callback=lambda event, payload: events.append((event, payload)),
        )
    except RuntimeError as exc:
        assert "socket closed" in str(exc)
        assert "socket still closed" in str(exc)
    else:
        raise AssertionError("session retry command failure should be reported")

    assert stale_connection.disconnected is True
    assert retry_connection.disconnected is True
    assert session.is_connected is False
    assert ("session_disconnected", {"reason": "command_failed"}) in events


def test_delete_macs_sends_one_command_per_normalized_mac():
    connection = FakeConnection(
        responses={
            "no paging": "",
            "aaa user delete mac aa:bb:cc:00:00:01": "User deleted",
        }
    )
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connection,
        sleep_func=lambda _seconds: None,
    )

    results = runner._delete_macs(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        ["AA-BB-CC-00-00-01", "aa:bb:cc:00:00:01", "aabb.cc00.0001"],
        None,
    )

    assert len(results) == 1
    assert results[0].success is True
    assert connection.commands.count("aaa user delete mac aa:bb:cc:00:00:01") == 1


def test_delete_macs_records_invalid_mac_without_sending_command():
    connection = FakeConnection(responses={"no paging": ""})
    events = []
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connection,
        sleep_func=lambda _seconds: None,
    )

    results = runner._delete_macs(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        ["not-a-mac"],
        lambda event, payload: events.append((event, payload)),
    )

    assert len(results) == 1
    assert results[0].success is False
    assert results[0].status == "unknown"
    assert "MAC" in results[0].error
    assert connection.commands == []
    assert any(event == "delete_unknown" for event, _payload in events)


def test_delete_macs_skips_missing_mac_values_without_sending_command():
    connection = FakeConnection(responses={"no paging": ""})
    events = []
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connection,
        sleep_func=lambda _seconds: None,
    )

    results = runner._delete_macs(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        [None, ""],  # type: ignore[list-item]
        lambda event, payload: events.append((event, payload)),
    )

    assert results == []
    assert connection.commands == []
    assert ("delete_batch_start", {"count": 0}) in events


def test_delete_command_exception_is_unknown_without_retry():
    command = "aaa user delete mac aa:bb:cc:00:00:01"
    connection = FakeConnection(responses={"no paging": ""}, failures={command: RuntimeError("socket timeout")})
    events = []
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connection,
        sleep_func=lambda _seconds: None,
    )

    results = runner._delete_macs(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        ["aa:bb:cc:00:00:01"],
        lambda event, payload: events.append((event, payload)),
    )

    assert len(results) == 1
    assert results[0].success is False
    assert results[0].status == "unknown"
    assert "확인 필요" in results[0].error
    assert connection.commands.count(command) == 1
    assert not any(event == "session_reconnect_start" for event, _payload in events)
    assert any(event == "delete_unknown" for event, _payload in events)


def test_delete_command_exception_closes_session_before_next_mac_without_retry():
    failed_command = "aaa user delete mac aa:bb:cc:00:00:01"
    next_command = "aaa user delete mac aa:bb:cc:00:00:02"
    stale_connection = FakeConnection(
        responses={"no paging": ""},
        failures={failed_command: RuntimeError("socket timeout")},
    )
    fresh_connection = FakeConnection(responses={"no paging": "", next_command: "User deleted"})
    connections = [stale_connection, fresh_connection]
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connections.pop(0),
        sleep_func=lambda _seconds: None,
    )

    results = runner._delete_macs(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        ["aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02"],
        None,
    )

    assert [item.status for item in results] == ["unknown", "deleted"]
    assert stale_connection.commands.count(failed_command) == 1
    assert stale_connection.disconnected is True
    assert fresh_connection.commands == ["no paging", next_command]
    assert connections == []


def test_classify_delete_response_handles_failure_unknown_and_success():
    assert classify_delete_response("User deleted") == ("deleted", "")
    assert classify_delete_response("Permission denied") == ("failed", "Permission denied")
    assert classify_delete_response("Invalid input detected at '^' marker.") == (
        "failed",
        "Invalid input detected at '^' marker.",
    )
    assert classify_delete_response("") == ("unknown", "확인 필요: 삭제 명령 응답이 비어 있음")
    status, error = classify_delete_response("aaa user delete mac aa:bb:cc:00:00:01")
    assert status == "unknown"
    assert "판정 불가" in error


def test_classify_delete_response_handles_non_string_output():
    status, error = classify_delete_response({"unexpected": "response"})  # type: ignore[arg-type]

    assert status == "unknown"
    assert "삭제 명령 응답 판정 불가" in error


def test_audit_save_failure_does_not_break_summary(tmp_path):
    blocked_output_dir = tmp_path / "not-a-directory"
    blocked_output_dir.write_text("file blocks directory creation", encoding="utf-8")
    connection = FakeConnection(responses={"no paging": "", "show global-user-table list role profiling": ""})
    events = []
    runner = MmCleanupRunner(
        connection_factory=lambda _config, _timeout: connection,
        sleep_func=lambda _seconds: None,
    )

    summary = runner.run_once(
        MmConnectionConfig(host="192.0.2.10", username="admin", password="secret"),
        CleanupSettings(role="profiling", timeout=5, delete_delay_seconds=0),
        output_dir=blocked_output_dir,
        progress_callback=lambda event, payload: events.append((event, payload)),
    )

    assert summary.error == ""
    assert summary.queried_count == 0
    assert summary.audit_path is None
    assert summary.audit_error
    assert any(event == "warning" and "audit summary save failed" in payload["message"] for event, payload in events)


def test_audit_summary_tolerates_malformed_internal_items(tmp_path):
    summary = CleanupRunSummary(started_at=datetime(2026, 7, 2, 13, 0, 0), role="profiling")
    summary.queried_count = 1
    summary.query_parse_decisions = [
        ParseDecision(1, "selected", "selected_identity_mac_before_role", mac="aa:bb:cc:00:00:01"),
        object(),  # type: ignore[list-item]
    ]
    summary.verify_parse_decisions = [
        {"line_number": "bad", "action": object(), "type_na": "false"},  # type: ignore[list-item]
    ]
    summary.delete_results = [
        DeleteResult(mac="aa:bb:cc:00:00:01", success=False, command="cmd", error=object()),  # type: ignore[arg-type]
        {"mac": "aa:bb:cc:00:00:02", "success": "true", "command": object(), "verified_absent": "true"},  # type: ignore[list-item]
    ]

    path = write_audit_summary(summary, output_dir=tmp_path, host="192.0.2.10")

    audit = json.loads(path.read_text(encoding="utf-8"))
    assert audit["queried_count"] == 1
    assert audit["query_parse_decisions"][0]["mac"] == "aa:bb:cc:00:00:01"
    assert audit["query_parse_decisions"][1]["line_number"] == 0
    assert audit["verify_parse_decisions"][0]["line_number"] == 0
    assert audit["verify_parse_decisions"][0]["type_na"] is False
    assert audit["delete_results"][0]["status"] == "failed"
    assert audit["delete_results"][0]["error"]
    assert audit["delete_results"][1]["success"] is True
    assert audit["delete_results"][1]["verified_absent"] is True


def test_history_append_serialization_failure_does_not_partially_append(tmp_path):
    history_path = tmp_path / HISTORY_FILE_NAME
    original_content = json.dumps({"run_at": "existing", "mac": "aa:bb:cc:00:00:ff"}) + "\n"
    history_path.write_text(original_content, encoding="utf-8")
    summary = CleanupRunSummary(started_at=datetime(2026, 7, 2, 13, 0, 0), role="profiling")
    summary.delete_results = [
        DeleteResult(mac="aa:bb:cc:00:00:01", success=True, command="cmd"),
        DeleteResult(mac="aa:bb:cc:00:00:02", success=False, command="cmd", error=object()),  # type: ignore[arg-type]
    ]

    try:
        append_history_records(summary, output_dir=tmp_path, host="192.0.2.10")
    except TypeError:
        pass
    else:
        raise AssertionError("append_history_records should report JSON serialization failure")

    assert history_path.read_text(encoding="utf-8") == original_content
