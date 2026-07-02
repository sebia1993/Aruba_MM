import json
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from aruba_mm_cleanup import __version__
from aruba_mm_cleanup.gui_app import (
    ACCENT,
    APP_TITLE,
    BG,
    CARD_BG,
    DANGER_ACTIVE,
    DANGER_SOFT,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_ROLE,
    HISTORY_FILE_NAME,
    MAX_LOG_LINES,
    MAX_HISTORY_ROWS,
    MIN_INTERVAL_SECONDS,
    SHUTDOWN_GRACE_MS,
    TEXT,
    TYPE_NA_MESSAGE,
    ArubaMmCleanupGui,
)


class FakeVar:
    def __init__(self, value="0"):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = str(value)


class FakeButton:
    def __init__(self):
        self.config = {}

    def configure(self, **kwargs):
        self.config.update(kwargs)


class FakeHistoryTable:
    def __init__(self):
        self.rows = {}
        self.order = []

    def insert(self, _parent, _index, iid, values, tags=()):
        self.rows[iid] = {"values": values, "tags": tags}
        self.order.append(iid)

    def delete(self, *items):
        for item in items:
            if item in self.rows:
                del self.rows[item]
            if item in self.order:
                self.order.remove(item)

    def get_children(self):
        return tuple(self.order)


class FakeTreeTable(FakeHistoryTable):
    def __init__(self):
        super().__init__()
        self.click_column = "#1"
        self.click_row = ""

    def exists(self, item):
        return item in self.rows

    def item(self, item, option=None, **kwargs):
        if kwargs:
            self.rows[item].update(kwargs)
            return None
        if option:
            return self.rows[item][option]
        return self.rows[item]

    def identify_column(self, _x):
        return self.click_column

    def identify_row(self, _y):
        if self.click_row:
            return self.click_row
        if self.order:
            return self.order[0]
        return ""


class DestroyedHistoryTable(FakeHistoryTable):
    def delete(self, *items):
        raise tk.TclError("invalid command name")


class InsertFailingHistoryTable(FakeHistoryTable):
    def insert(self, _parent, _index, iid, values, tags=()):
        raise tk.TclError("invalid command name")


class FakeClickEvent:
    x = 1
    y = 1


class FakeLogText:
    def __init__(self):
        self.lines = []
        self.state = "disabled"

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]

    def insert(self, _index, text):
        self.lines.extend(text.splitlines())

    def index(self, _index):
        return f"{max(len(self.lines), 1)}.0"

    def delete(self, start, end):
        if start == "1.0" and end.endswith(".0"):
            count = int(end.split(".")[0]) - 1
            del self.lines[:count]
            return
        self.lines = []

    def see(self, _index):
        pass


class BadIndexLogText(FakeLogText):
    def index(self, _index):
        return "bad-index"


class DeleteFailingLogText(FakeLogText):
    def delete(self, _start, _end):
        raise tk.TclError("invalid command name")


class FakeOverlayFrame:
    def __init__(self):
        self.place_calls = []
        self.lift_calls = 0
        self.hidden = True

    def place(self, **kwargs):
        self.place_calls.append(kwargs)
        self.hidden = False

    def lift(self):
        self.lift_calls += 1

    def place_forget(self):
        self.hidden = True


def make_headless_gui():
    app = object.__new__(ArubaMmCleanupGui)
    app.counter_vars = {
        "queried": FakeVar("7"),
        "deleted": FakeVar("3"),
    }
    app.cumulative_queried_count = 7
    app.cumulative_deleted_count = 3
    app.current_run_queried_count = 0
    app.current_run_query_counted = False
    app.current_run_delete_counted = False
    app.status_var = FakeVar()
    app.copy_notice_title_var = FakeVar("")
    app.copy_notice_mac_var = FakeVar("")
    app.copy_notice_after_id = None
    app.copy_notice_frame = FakeOverlayFrame()
    app.cancel_button = FakeButton()
    app.manual_button = FakeButton()
    app.schedule_button = FakeButton()
    app.stop_schedule_button = FakeButton()
    app.event_queue = queue.Queue()
    app.cancel_event = threading.Event()
    app.scheduler_stop_event = threading.Event()
    app.scheduler_running = False
    app.is_running = False
    app.closing = False
    app.rows = []
    app.logs = []
    app.timers = []
    app.clipboard_values = []
    app.scheduled_callbacks = []
    app.canceled_after_ids = []
    app.history_summaries = []
    app.reappeared_rows = []
    app.clipboard_clear = lambda: app.clipboard_values.clear()
    app.clipboard_append = lambda value: app.clipboard_values.append(value)
    app.after = lambda ms, callback: app.scheduled_callbacks.append((ms, callback)) or f"after-{len(app.scheduled_callbacks)}"
    app.after_cancel = lambda after_id: app.canceled_after_ids.append(after_id)
    app._set_row_status = lambda mac, status, error: app.rows.append((mac, status, error))
    app._log = lambda message: app.logs.append(message)
    app._set_timer = lambda value, state: app.timers.append((value, state))
    app._sync_settings_visibility = lambda: None
    app._append_history_rows = lambda summary: app.history_summaries.append(summary)
    app._mark_reappeared_rows = lambda macs: app.reappeared_rows.append(macs)
    return app


def make_input_gui():
    app = object.__new__(ArubaMmCleanupGui)
    app.host_var = FakeVar("192.0.2.10")
    app.port_var = FakeVar("22")
    app.username_var = FakeVar("admin")
    app.password_var = FakeVar("secret")
    app.enable_password_var = FakeVar("")
    app.role_var = FakeVar("profiling")
    app.timeout_var = FakeVar("15")
    app.interval_var = FakeVar("1")
    app.output_dir_var = FakeVar("/tmp/aruba-mm-cleanup")
    return app


def test_version_and_gui_constants():
    assert __version__ == "0.1.0"
    assert APP_TITLE == "Aruba MM Cleanup Dashboard"
    assert ACCENT == "#3e6ae1"
    assert DANGER_ACTIVE == "#8f1d14"
    assert DANGER_SOFT == "#fff4f2"
    assert BG == "#f4f4f4"
    assert TEXT == "#171a20"
    assert CARD_BG == "#ffffff"
    assert DEFAULT_ROLE == "profiling"
    assert MAX_HISTORY_ROWS == 500
    assert DEFAULT_INTERVAL_SECONDS == 300
    assert MIN_INTERVAL_SECONDS == 1


def test_read_inputs_uses_immediate_delete_and_device_timeout():
    app = make_input_gui()

    config, settings, output_dir = ArubaMmCleanupGui._read_inputs(app)

    assert config.host == "192.0.2.10"
    assert settings.timeout == 15
    assert settings.delete_delay_seconds == 0
    assert str(output_dir) == "/tmp/aruba-mm-cleanup"


def test_read_inputs_expands_user_home_output_dir():
    app = make_input_gui()
    app.output_dir_var.set("~/aruba-mm-cleanup")

    _config, _settings, output_dir = ArubaMmCleanupGui._read_inputs(app)

    assert output_dir == Path.home() / "aruba-mm-cleanup"


def test_read_inputs_reports_clear_timeout_errors():
    app = make_input_gui()
    app.timeout_var.set("slow")

    with pytest.raises(ValueError, match="장비 응답 대기"):
        ArubaMmCleanupGui._read_inputs(app)


def test_read_inputs_rejects_role_control_characters():
    app = make_input_gui()
    app.role_var.set("profiling\nshow version")

    with pytest.raises(ValueError, match="Role"):
        ArubaMmCleanupGui._read_inputs(app)


@pytest.mark.parametrize("value", ["0", "-1", "65536"])
def test_read_inputs_rejects_out_of_range_ports(value):
    app = make_input_gui()
    app.port_var.set(value)

    with pytest.raises(ValueError, match="Port"):
        ArubaMmCleanupGui._read_inputs(app)


def test_read_interval_uses_actual_input_value():
    app = make_input_gui()
    app.interval_var.set("1")

    assert ArubaMmCleanupGui._read_interval(app) == 1


@pytest.mark.parametrize("value", ["0", "-1", "soon"])
def test_read_interval_rejects_invalid_values(value):
    app = make_input_gui()
    app.interval_var.set(value)

    with pytest.raises(ValueError, match="주기\\(초\\)"):
        ArubaMmCleanupGui._read_interval(app)


def test_delete_progress_events_update_rows_without_confirmed_delete_count():
    app = make_headless_gui()

    app._handle_progress("delete_done", {"mac": "aa:bb:cc:00:00:01"})
    app._handle_progress("delete_done", {"mac": "aa:bb:cc:00:00:02"})
    app._handle_progress("delete_error", {"mac": "aa:bb:cc:00:00:03", "error": "Error"})
    app._handle_progress("delete_unknown", {"mac": "aa:bb:cc:00:00:04", "error": "timeout"})

    assert app.counter_vars["deleted"].get() == "3"
    assert app.rows == [
        ("aa:bb:cc:00:00:01", "삭제 완료", ""),
        ("aa:bb:cc:00:00:02", "삭제 완료", ""),
        ("aa:bb:cc:00:00:03", "삭제 실패", "Error"),
        ("aa:bb:cc:00:00:04", "확인 필요", "timeout"),
    ]


def test_query_done_adds_unique_display_macs_to_cumulative_total():
    app = make_headless_gui()
    replaced = []
    app._replace_table = lambda macs, status, **kwargs: replaced.append((macs, status, kwargs))

    app._handle_progress(
        "query_done",
        {
            "count": 3,
            "macs": ["aa-bb-cc-00-00-01", "aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02"],
        },
    )

    assert app.counter_vars["queried"].get() == "9"
    assert replaced == [
        (["aa-bb-cc-00-00-01", "aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02"], "삭제 대상", {"type_na_macs": []})
    ]


def test_query_done_ignores_string_macs_payload_without_character_rows():
    app = make_headless_gui()
    replaced = []
    app._replace_table = lambda macs, status, **kwargs: replaced.append((macs, status, kwargs))

    app._handle_progress("query_done", {"count": 1, "macs": "aa:bb:cc:00:00:01"})

    assert app.counter_vars["queried"].get() == "7"
    assert replaced == [([], "삭제 대상", {"type_na_macs": []})]


def test_query_done_ignores_non_string_mac_items_without_table_rows():
    app = make_headless_gui()
    app.table = FakeTreeTable()

    app._handle_progress(
        "query_done",
        {
            "count": 2,
            "macs": [["aa:bb:cc:00:00:01"], "aa:bb:cc:00:00:02"],
        },
    )

    assert app.counter_vars["queried"].get() == "8"
    assert app.table.get_children() == ("aa:bb:cc:00:00:02",)


def test_query_done_marks_type_na_rows_and_logs_admin_guidance():
    app = make_headless_gui()
    app.table = FakeTreeTable()

    app._handle_progress(
        "query_done",
        {
            "count": 2,
            "macs": ["aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02"],
            "type_na_macs": ["aa:bb:cc:00:00:02"],
        },
    )

    assert app.table.rows["aa:bb:cc:00:00:01"]["values"][4] == ""
    assert app.table.rows["aa:bb:cc:00:00:02"]["values"][4] == TYPE_NA_MESSAGE
    assert "TYPE N/A: aa:bb:cc:00:00:02 - 관리자 직접 장비 지정 필요" in app.logs


def test_reappeared_macs_ignores_string_payload_without_character_rows():
    app = make_headless_gui()

    app._handle_progress("reappeared_macs", {"macs": "aa:bb:cc:00:00:01"})

    assert app.reappeared_rows == [[]]
    assert not any(message.startswith("REAPPEARED:") for message in app.logs)


def test_type_na_message_survives_delete_status_updates():
    app = make_headless_gui()
    app.table = FakeTreeTable()
    app.table.insert(
        "",
        "end",
        iid="aa:bb:cc:00:00:01",
        values=("aa:bb:cc:00:00:01", "삭제 대상", "2026-07-02 13:00:00", "", TYPE_NA_MESSAGE),
    )

    ArubaMmCleanupGui._set_row_status(app, "aa:bb:cc:00:00:01", "삭제 완료", "")
    assert app.table.rows["aa:bb:cc:00:00:01"]["values"][4] == TYPE_NA_MESSAGE

    ArubaMmCleanupGui._set_row_status(app, "aa:bb:cc:00:00:01", "확인 필요", "timeout")
    assert app.table.rows["aa:bb:cc:00:00:01"]["values"][4] == f"{TYPE_NA_MESSAGE} | timeout"


def test_set_row_status_ignores_malformed_table_row_values():
    app = make_headless_gui()
    app.table = FakeTreeTable()
    app.table.insert(
        "",
        "end",
        iid="aa:bb:cc:00:00:01",
        values=("aa:bb:cc:00:00:01",),
    )

    ArubaMmCleanupGui._set_row_status(app, "aa:bb:cc:00:00:01", "삭제 완료", "")

    assert app.table.rows["aa:bb:cc:00:00:01"]["values"] == ("aa:bb:cc:00:00:01",)


def test_set_all_pending_status_skips_malformed_rows_and_updates_valid_rows():
    app = make_headless_gui()
    app.table = FakeTreeTable()
    app.table.insert("", "end", iid="bad-row", values=("bad-row",))
    app.table.insert(
        "",
        "end",
        iid="aa:bb:cc:00:00:01",
        values=("aa:bb:cc:00:00:01", "삭제 대상", "2026-07-02 13:00:00", "", ""),
    )

    ArubaMmCleanupGui._set_all_pending_status(app, "취소됨")

    assert app.table.rows["bad-row"]["values"] == ("bad-row",)
    assert app.table.rows["aa:bb:cc:00:00:01"]["values"][1] == "취소됨"


def test_running_state_resets_current_run_counters():
    app = make_headless_gui()

    app._set_running(True)

    assert app.counter_vars["deleted"].get() == "3"
    assert app.counter_vars["queried"].get() == "7"
    assert app.current_run_query_counted is False
    assert app.current_run_delete_counted is False
    assert app.timers[-1] == ("실행 중", "조회/삭제 처리")
    assert app.cancel_button.config["state"] == "disabled"


def test_enqueue_event_drops_worker_events_after_closing():
    app = make_headless_gui()

    assert app._enqueue_event("running", True) is True
    app.closing = True
    assert app._enqueue_event("running", False) is False

    assert app.event_queue.get_nowait() == ("running", True)
    assert app.event_queue.empty()


def test_drain_events_logs_bad_event_and_continues():
    app = make_headless_gui()
    app.event_queue.put(("progress", ("countdown", {"remaining": "bad"})))
    app.event_queue.put(("scheduler_stopped", None))

    ArubaMmCleanupGui._drain_events(app)

    assert any("이벤트 처리 실패(progress)" in message for message in app.logs)
    assert app.scheduler_running is False
    assert app.stop_schedule_button.config["state"] == "disabled"
    assert app.timers[-1] == ("-", "대기")
    assert app.scheduled_callbacks[-1][0] == 150


def test_drain_events_handles_missing_progress_payload_as_empty_dict():
    app = make_headless_gui()
    app.event_queue.put(("progress", ("connect_start", None)))

    ArubaMmCleanupGui._drain_events(app)

    assert app.status_var.get() == "MM 접속 중"
    assert "CONNECT: None" in app.logs
    assert not any("이벤트 처리 실패(progress)" in message for message in app.logs)


def test_on_close_sets_flags_and_schedules_bounded_destroy_without_direct_close():
    app = make_headless_gui()
    app._drain_after_id = "drain-id"
    canceled = []
    scheduled = []
    close_calls = []
    app.after_cancel = lambda after_id: canceled.append(after_id)
    app.after = lambda ms, callback: scheduled.append((ms, callback)) or "shutdown-id"
    app._start_session_close = lambda **kwargs: close_calls.append(kwargs)

    ArubaMmCleanupGui.on_close(app)

    assert app.closing is True
    assert app.scheduler_stop_event.is_set()
    assert app.cancel_event.is_set()
    assert canceled == ["drain-id"]
    assert close_calls == [{"reason": "app_close", "enqueue_progress": False}]
    assert scheduled[0][0] == SHUTDOWN_GRACE_MS


def test_start_session_close_returns_without_waiting_for_runner_lock():
    app = make_headless_gui()
    app.runner_lock = threading.Lock()
    app.runner_lock.acquire()
    close_calls = []
    app.runner = SimpleNamespace(close_session=lambda **_kwargs: close_calls.append("closed"))
    app.session_close_worker = None

    started = time.monotonic()
    app._start_session_close(reason="manual", enqueue_progress=False)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert close_calls == []
    app.runner_lock.release()
    app.session_close_worker.join(timeout=2)
    assert close_calls == ["closed"]


def test_history_load_restores_jsonl_rows(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    history_path = output_dir / HISTORY_FILE_NAME
    history_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "run_at": "2026-07-02T13:00:00",
                        "mac": "aa:bb:cc:00:00:01",
                        "result": "삭제 완료",
                        "status": "verified_deleted",
                        "success": True,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "run_at": "2026-07-02T13:01:00",
                        "mac": "aa:bb:cc:00:00:02",
                        "status": "reappeared",
                        "success": False,
                        "error": "",
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )
    app = make_headless_gui()
    app.history_table = FakeHistoryTable()
    app.history_row_counter = 0
    app.loaded_history_dir = None

    app._load_history_from_output_dir(output_dir)

    rows = [app.history_table.rows[item]["values"] for item in app.history_table.get_children()]
    assert rows == [
        ("2026-07-02 13:00:00", "aa:bb:cc:00:00:01", "삭제 완료", ""),
        ("2026-07-02 13:01:00", "aa:bb:cc:00:00:02", "재조회됨", "삭제 성공 후 검증 조회에서 다시 발견"),
    ]


def test_clear_history_ignores_destroyed_history_table():
    app = make_headless_gui()
    app.history_table = DestroyedHistoryTable()
    app.history_table.insert("", "end", iid="history-0", values=("run", "mac", "result", ""))
    app.history_row_counter = 1

    ArubaMmCleanupGui.clear_history(app)

    assert app.history_row_counter == 1


def test_history_load_ignores_destroyed_history_table(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / HISTORY_FILE_NAME).write_text(
        json.dumps({"run_at": "2026-07-02T13:00:00", "mac": "aa:bb:cc:00:00:01"}),
        encoding="utf-8",
    )
    app = make_headless_gui()
    app.history_table = DestroyedHistoryTable()
    app.history_table.insert("", "end", iid="history-0", values=("run", "mac", "result", ""))
    app.history_row_counter = 1
    app.loaded_history_dir = None

    app._load_history_from_output_dir(output_dir)

    assert app.history_row_counter == 1


def test_history_load_ignores_history_row_insert_failure(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / HISTORY_FILE_NAME).write_text(
        json.dumps({"run_at": "2026-07-02T13:00:00", "mac": "aa:bb:cc:00:00:01"}),
        encoding="utf-8",
    )
    app = make_headless_gui()
    app.history_table = InsertFailingHistoryTable()
    app.history_row_counter = 0
    app.loaded_history_dir = None

    app._load_history_from_output_dir(output_dir)

    assert app.history_row_counter == 0


def test_history_load_ignores_non_string_jsonl_mac(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    history_path = output_dir / HISTORY_FILE_NAME
    history_path.write_text(
        json.dumps(
            {
                "run_at": "2026-07-02T13:00:00",
                "mac": ["aa:bb:cc:00:00:01"],
                "result": "삭제 완료",
                "status": "verified_deleted",
                "success": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    app = make_headless_gui()
    app.history_table = FakeHistoryTable()
    app.history_row_counter = 0
    app.loaded_history_dir = None

    app._load_history_from_output_dir(output_dir)

    assert app.history_table.get_children() == ()


def test_history_load_ignores_unreadable_jsonl_path(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / HISTORY_FILE_NAME).mkdir()
    app = make_headless_gui()
    app.history_table = FakeHistoryTable()
    app.history_row_counter = 0
    app.loaded_history_dir = None

    app._load_history_from_output_dir(output_dir)

    assert app.history_table.get_children() == ()


def test_history_load_ignores_invalid_encoding_jsonl(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / HISTORY_FILE_NAME).write_bytes(b"\xff\xfeinvalid history")
    app = make_headless_gui()
    app.history_table = FakeHistoryTable()
    app.history_row_counter = 0
    app.loaded_history_dir = None

    app._load_history_from_output_dir(output_dir)

    assert app.history_table.get_children() == ()


def test_history_load_ignores_invalid_encoding_audit_fallback(tmp_path):
    output_dir = tmp_path / "outputs"
    run_dir = output_dir / "20260702_130000_000000"
    run_dir.mkdir(parents=True)
    (run_dir / "cleanup_summary.json").write_bytes(b"\xff\xfeinvalid audit")
    app = make_headless_gui()
    app.history_table = FakeHistoryTable()
    app.history_row_counter = 0
    app.loaded_history_dir = None

    app._load_history_from_output_dir(output_dir)

    assert app.history_table.get_children() == ()


def test_history_load_ignores_non_object_audit_fallback(tmp_path):
    output_dir = tmp_path / "outputs"
    run_dir = output_dir / "20260702_130000_000000"
    run_dir.mkdir(parents=True)
    (run_dir / "cleanup_summary.json").write_text("[]", encoding="utf-8")
    app = make_headless_gui()
    app.history_table = FakeHistoryTable()
    app.history_row_counter = 0
    app.loaded_history_dir = None

    app._load_history_from_output_dir(output_dir)

    assert app.history_table.get_children() == ()


def test_history_load_ignores_invalid_reappeared_macs_type(tmp_path):
    output_dir = tmp_path / "outputs"
    run_dir = output_dir / "20260702_130000_000000"
    run_dir.mkdir(parents=True)
    (run_dir / "cleanup_summary.json").write_text(
        json.dumps(
            {
                "started_at": "2026-07-02T13:00:00",
                "reappeared_macs": 1,
                "delete_results": [
                    {
                        "mac": "aa:bb:cc:00:00:01",
                        "status": "verified_deleted",
                        "success": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    app = make_headless_gui()
    app.history_table = FakeHistoryTable()
    app.history_row_counter = 0
    app.loaded_history_dir = None

    app._load_history_from_output_dir(output_dir)

    rows = [app.history_table.rows[item]["values"] for item in app.history_table.get_children()]
    assert rows == [("2026-07-02 13:00:00", "aa:bb:cc:00:00:01", "삭제 완료", "")]


def test_history_load_ignores_invalid_reappeared_mac_items(tmp_path):
    output_dir = tmp_path / "outputs"
    run_dir = output_dir / "20260702_130000_000000"
    run_dir.mkdir(parents=True)
    (run_dir / "cleanup_summary.json").write_text(
        json.dumps(
            {
                "started_at": "2026-07-02T13:00:00",
                "reappeared_macs": [["aa:bb:cc:00:00:01"]],
                "delete_results": [
                    {
                        "mac": "aa:bb:cc:00:00:01",
                        "status": "verified_deleted",
                        "success": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    app = make_headless_gui()
    app.history_table = FakeHistoryTable()
    app.history_row_counter = 0
    app.loaded_history_dir = None

    app._load_history_from_output_dir(output_dir)

    rows = [app.history_table.rows[item]["values"] for item in app.history_table.get_children()]
    assert rows == [("2026-07-02 13:00:00", "aa:bb:cc:00:00:01", "삭제 완료", "")]


def test_history_load_ignores_invalid_delete_results_type(tmp_path):
    output_dir = tmp_path / "outputs"
    run_dir = output_dir / "20260702_130000_000000"
    run_dir.mkdir(parents=True)
    (run_dir / "cleanup_summary.json").write_text(
        json.dumps(
            {
                "started_at": "2026-07-02T13:00:00",
                "delete_results": 1,
            }
        ),
        encoding="utf-8",
    )
    app = make_headless_gui()
    app.history_table = FakeHistoryTable()
    app.history_row_counter = 0
    app.loaded_history_dir = None

    app._load_history_from_output_dir(output_dir)

    assert app.history_table.get_children() == ()


def test_history_load_ignores_invalid_audit_mac_type(tmp_path):
    output_dir = tmp_path / "outputs"
    run_dir = output_dir / "20260702_130000_000000"
    run_dir.mkdir(parents=True)
    (run_dir / "cleanup_summary.json").write_text(
        json.dumps(
            {
                "started_at": "2026-07-02T13:00:00",
                "reappeared_macs": ["aa:bb:cc:00:00:01"],
                "delete_results": [
                    {
                        "mac": ["aa:bb:cc:00:00:01"],
                        "status": "verified_deleted",
                        "success": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    app = make_headless_gui()
    app.history_table = FakeHistoryTable()
    app.history_row_counter = 0
    app.loaded_history_dir = None

    app._load_history_from_output_dir(output_dir)

    assert app.history_table.get_children() == ()


def test_log_text_is_capped_to_max_lines():
    app = make_headless_gui()
    app.log_text = FakeLogText()

    for index in range(MAX_LOG_LINES + 5):
        ArubaMmCleanupGui._log(app, f"line {index}")

    assert len(app.log_text.lines) == MAX_LOG_LINES
    assert "line 0" not in app.log_text.lines[0]


def test_log_keeps_message_when_line_index_is_unexpected():
    app = make_headless_gui()
    app.log_text = BadIndexLogText()

    ArubaMmCleanupGui._log(app, "line still recorded")

    assert len(app.log_text.lines) == 1
    assert app.log_text.lines[0].endswith("line still recorded")
    assert app.log_text.state == "disabled"


def test_log_stays_disabled_when_cap_delete_fails():
    app = make_headless_gui()
    app.log_text = DeleteFailingLogText()
    app.log_text.lines = [f"line {index}" for index in range(MAX_LOG_LINES + 1)]

    ArubaMmCleanupGui._log(app, "line still recorded")

    assert app.log_text.lines[-1].endswith("line still recorded")
    assert app.log_text.state == "disabled"


def test_clear_log_restores_disabled_state_when_delete_fails():
    app = make_headless_gui()
    app.log_text = DeleteFailingLogText()

    ArubaMmCleanupGui.clear_log(app)

    assert app.log_text.state == "disabled"


def test_append_history_rows_ignores_missing_delete_results():
    app = make_headless_gui()
    app.history_table = FakeHistoryTable()
    app.history_row_counter = 0
    summary = SimpleNamespace(started_at=datetime(2026, 7, 2, 13, 0, 0), reappeared_macs=[])

    ArubaMmCleanupGui._append_history_rows(app, summary)

    assert app.history_table.get_children() == ()


def test_append_history_rows_ignores_invalid_reappeared_mac_items():
    app = make_headless_gui()
    app.history_table = FakeHistoryTable()
    app.history_row_counter = 0
    summary = SimpleNamespace(
        started_at=datetime(2026, 7, 2, 13, 0, 0),
        reappeared_macs=[["aa:bb:cc:00:00:01"]],
        delete_results=[
            SimpleNamespace(
                mac="aa:bb:cc:00:00:01",
                status="verified_deleted",
                success=True,
                error="",
            )
        ],
    )

    ArubaMmCleanupGui._append_history_rows(app, summary)

    row_id = app.history_table.get_children()[0]
    assert app.history_table.rows[row_id]["values"] == (
        "2026-07-02 13:00:00",
        "aa:bb:cc:00:00:01",
        "삭제 완료",
        "",
    )


def test_summary_updates_simple_dashboard_cards_with_final_values():
    app = make_headless_gui()
    summary = SimpleNamespace(
        queried_count=4,
        target_macs=["aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02", "aa:bb:cc:00:00:03"],
        delete_success_count=2,
        delete_failure_count=1,
        remaining_count=1,
        reappeared_count=0,
        verification_skipped=False,
        error="",
        canceled=False,
        reappeared_macs=[],
        audit_path=None,
        audit_error="",
        history_error="",
    )

    app._handle_summary(summary)

    assert app.counter_vars["queried"].get() == "10"
    assert app.counter_vars["deleted"].get() == "5"


def test_summary_does_not_double_count_query_progress():
    app = make_headless_gui()
    app._replace_table = lambda *_args, **_kwargs: None
    app._handle_progress(
        "query_done",
        {
            "count": 1,
            "macs": ["aa:bb:cc:00:00:01"],
        },
    )
    summary = SimpleNamespace(
        queried_count=1,
        target_macs=["aa:bb:cc:00:00:01"],
        delete_success_count=1,
        reappeared_count=0,
        verification_skipped=False,
        error="",
        canceled=False,
        reappeared_macs=[],
        audit_path=None,
        audit_error="",
        history_error="",
    )

    app._handle_summary(summary)

    assert app.counter_vars["queried"].get() == "8"
    assert app.counter_vars["deleted"].get() == "4"


def test_summary_handles_missing_queried_count_as_zero():
    app = make_headless_gui()
    summary = SimpleNamespace(
        target_macs=[],
        delete_success_count=0,
        reappeared_count=0,
        verification_skipped=False,
        error="",
        canceled=False,
        reappeared_macs=[],
        audit_path=None,
        audit_error="",
        history_error="",
    )

    app._handle_summary(summary)

    assert app.counter_vars["queried"].get() == "7"
    assert app.counter_vars["deleted"].get() == "3"
    assert app.status_var.get() == "완료"


def test_summary_handles_missing_status_and_count_fields_as_defaults():
    app = make_headless_gui()
    summary = SimpleNamespace(target_macs=[])

    app._handle_summary(summary)

    assert app.counter_vars["queried"].get() == "7"
    assert app.counter_vars["deleted"].get() == "3"
    assert app.status_var.get() == "완료"
    assert app.reappeared_rows == []
    assert app.logs == []


def test_summary_handles_invalid_delete_success_count_as_zero():
    app = make_headless_gui()
    summary = SimpleNamespace(
        target_macs=[],
        queried_count=0,
        delete_success_count="bad-count",
        reappeared_count=0,
        verification_skipped=False,
        error="",
        canceled=False,
        reappeared_macs=[],
        audit_path=None,
        audit_error="",
        history_error="",
    )

    app._handle_summary(summary)

    assert app.counter_vars["deleted"].get() == "3"
    assert app.status_var.get() == "완료"


def test_summary_ignores_string_target_macs_without_character_counting():
    app = make_headless_gui()
    summary = SimpleNamespace(
        target_macs="aa:bb:cc:00:00:01",
        queried_count=0,
        delete_success_count=0,
        reappeared_count=0,
        verification_skipped=False,
        error="",
        canceled=False,
        reappeared_macs=[],
        audit_path=None,
        audit_error="",
        history_error="",
    )

    app._handle_summary(summary)

    assert app.counter_vars["queried"].get() == "7"
    assert app.status_var.get() == "완료"


def test_summary_ignores_string_reappeared_macs_without_character_rows():
    app = make_headless_gui()
    summary = SimpleNamespace(
        target_macs=[],
        queried_count=0,
        delete_success_count=0,
        reappeared_count=1,
        verification_skipped=False,
        error="",
        canceled=False,
        reappeared_macs="aa:bb:cc:00:00:01",
        audit_path=None,
        audit_error="",
        history_error="",
    )

    app._handle_summary(summary)

    assert app.status_var.get() == "삭제 MAC 재조회됨"
    assert app.reappeared_rows == []


@pytest.mark.parametrize(
    ("error", "canceled", "verification_skipped"),
    [("boom", False, False), ("", True, False), ("", False, True)],
)
def test_summary_leaves_confirmed_delete_unknown_without_verification(error, canceled, verification_skipped):
    app = make_headless_gui()
    summary = SimpleNamespace(
        queried_count=4,
        target_macs=["aa:bb:cc:00:00:01"],
        delete_success_count=1,
        reappeared_count=0,
        verification_skipped=verification_skipped,
        error=error,
        canceled=canceled,
        reappeared_macs=[],
        audit_path=None,
        audit_error="",
        history_error="",
    )

    app._handle_summary(summary)

    assert app.counter_vars["queried"].get() == "8"
    assert app.counter_vars["deleted"].get() == "3"


def test_result_mac_column_click_copies_mac_and_hides_notice():
    app = make_headless_gui()
    table = FakeTreeTable()
    table.insert(
        "",
        "end",
        iid="aa:bb:cc:00:00:01",
        values=("aa:bb:cc:00:00:01", "삭제 대상", "2026-07-02 13:00:00", "", ""),
    )

    ArubaMmCleanupGui._copy_mac_from_table_event(app, FakeClickEvent(), table, "#1")

    assert app.clipboard_values == ["aa:bb:cc:00:00:01"]
    assert app.copy_notice_title_var.get() == "복사 완료"
    assert app.copy_notice_mac_var.get() == "aa:bb:cc:00:00:01"
    assert app.copy_notice_frame.place_calls == [{"relx": 0.5, "rely": 0.5, "anchor": "center"}]
    assert app.copy_notice_frame.lift_calls == 1
    assert app.copy_notice_frame.hidden is False
    assert app.scheduled_callbacks[0][0] == 1000

    app.scheduled_callbacks[0][1]()

    assert app.copy_notice_title_var.get() == ""
    assert app.copy_notice_mac_var.get() == ""
    assert app.copy_notice_frame.hidden is True
    assert app.copy_notice_after_id is None


def test_repeated_mac_copy_replaces_center_notice_timer():
    app = make_headless_gui()
    table = FakeTreeTable()
    table.insert(
        "",
        "end",
        iid="aa:bb:cc:00:00:01",
        values=("aa:bb:cc:00:00:01", "삭제 대상", "2026-07-02 13:00:00", "", ""),
    )

    ArubaMmCleanupGui._copy_mac_from_table_event(app, FakeClickEvent(), table, "#1")
    table.rows["aa:bb:cc:00:00:01"]["values"] = (
        "aa:bb:cc:00:00:09",
        "삭제 대상",
        "2026-07-02 13:00:00",
        "",
        "",
    )
    ArubaMmCleanupGui._copy_mac_from_table_event(app, FakeClickEvent(), table, "#1")

    assert app.clipboard_values == ["aa:bb:cc:00:00:09"]
    assert app.copy_notice_mac_var.get() == "aa:bb:cc:00:00:09"
    assert app.canceled_after_ids == ["after-1"]
    assert len(app.scheduled_callbacks) == 2


def test_history_mac_column_click_copies_second_column_mac():
    app = make_headless_gui()
    table = FakeTreeTable()
    table.click_column = "#2"
    table.insert(
        "",
        "end",
        iid="history-0",
        values=("2026-07-02 13:00:00", "aa:bb:cc:00:00:02", "삭제 완료", ""),
    )

    ArubaMmCleanupGui._copy_mac_from_table_event(app, FakeClickEvent(), table, "#2")

    assert app.clipboard_values == ["aa:bb:cc:00:00:02"]
    assert app.copy_notice_title_var.get() == "복사 완료"
    assert app.copy_notice_mac_var.get() == "aa:bb:cc:00:00:02"


def test_non_mac_column_click_does_not_copy_mac():
    app = make_headless_gui()
    table = FakeTreeTable()
    table.click_column = "#2"
    table.insert(
        "",
        "end",
        iid="aa:bb:cc:00:00:01",
        values=("aa:bb:cc:00:00:01", "삭제 대상", "2026-07-02 13:00:00", "", ""),
    )

    ArubaMmCleanupGui._copy_mac_from_table_event(app, FakeClickEvent(), table, "#1")

    assert app.clipboard_values == []
    assert app.copy_notice_title_var.get() == ""
    assert app.copy_notice_mac_var.get() == ""
    assert app.copy_notice_frame.hidden is True
    assert app.scheduled_callbacks == []


def test_invalid_mac_column_identifier_does_not_copy_mac():
    app = make_headless_gui()
    table = FakeTreeTable()
    table.click_column = "MAC"
    table.insert(
        "",
        "end",
        iid="aa:bb:cc:00:00:01",
        values=("aa:bb:cc:00:00:01", "삭제 대상", "2026-07-02 13:00:00", "", ""),
    )

    ArubaMmCleanupGui._copy_mac_from_table_event(app, FakeClickEvent(), table, "MAC")

    assert app.clipboard_values == []
    assert app.copy_notice_title_var.get() == ""
    assert app.copy_notice_mac_var.get() == ""
    assert app.copy_notice_frame.hidden is True
    assert app.scheduled_callbacks == []


def test_mac_copy_ignores_malformed_row_values():
    app = make_headless_gui()
    table = FakeTreeTable()
    table.insert("", "end", iid="bad-row", values=())
    table.rows["bad-row"]["values"] = None

    ArubaMmCleanupGui._copy_mac_from_table_event(app, FakeClickEvent(), table, "#1")

    assert app.clipboard_values == []
    assert app.copy_notice_title_var.get() == ""
    assert app.copy_notice_mac_var.get() == ""
    assert app.copy_notice_frame.hidden is True
    assert app.scheduled_callbacks == []
