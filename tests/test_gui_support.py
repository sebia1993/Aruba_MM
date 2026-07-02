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
import aruba_mm_cleanup.gui_app as gui_app_module
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


class FailingGetVar(FakeVar):
    def get(self):
        raise tk.TclError("invalid command name")


class FailingSetVar(FakeVar):
    def set(self, _value):
        raise tk.TclError("invalid command name")


class FakeButton:
    def __init__(self):
        self.config = {}

    def configure(self, **kwargs):
        self.config.update(kwargs)


class FailingConfigureButton(FakeButton):
    def configure(self, **_kwargs):
        raise tk.TclError("invalid command name")


class FakeSettingsFrame:
    def __init__(self):
        self.hidden = False
        self.grid_remove_calls = 0
        self.grid_calls = 0

    def grid_remove(self):
        self.grid_remove_calls += 1
        self.hidden = True

    def grid(self):
        self.grid_calls += 1
        self.hidden = False


class DestroyedSettingsFrame(FakeSettingsFrame):
    def grid_remove(self):
        raise tk.TclError("invalid command name")

    def grid(self):
        raise tk.TclError("invalid command name")


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


class DestroyedTreeTable(FakeTreeTable):
    def get_children(self):
        raise tk.TclError("invalid command name")

    def delete(self, *items):
        raise tk.TclError("invalid command name")

    def insert(self, _parent, _index, iid, values, tags=()):
        raise tk.TclError("invalid command name")

    def exists(self, item):
        raise tk.TclError("invalid command name")

    def item(self, item, option=None, **kwargs):
        raise tk.TclError("invalid command name")


class IdentifyFailingTreeTable(FakeTreeTable):
    def identify_column(self, _x):
        raise tk.TclError("invalid command name")

    def identify_row(self, _y):
        raise tk.TclError("invalid command name")


class DestroyedHistoryTable(FakeHistoryTable):
    def get_children(self):
        raise tk.TclError("invalid command name")

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


class InsertFailingLogText(FakeLogText):
    def insert(self, _index, _text):
        raise tk.TclError("invalid command name")


class ConfigureFailingLogText(FakeLogText):
    def configure(self, **_kwargs):
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


class PlacementFailingOverlayFrame(FakeOverlayFrame):
    def place(self, **_kwargs):
        raise tk.TclError("invalid command name")


class HideFailingOverlayFrame(FakeOverlayFrame):
    def place_forget(self):
        raise tk.TclError("invalid command name")


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


@pytest.mark.parametrize(
    "field_name",
    [
        "host_var",
        "username_var",
        "password_var",
        "port_var",
        "timeout_var",
        "role_var",
        "enable_password_var",
        "output_dir_var",
    ],
)
def test_read_inputs_reports_destroyed_input_variables(field_name):
    app = make_input_gui()
    setattr(app, field_name, FailingGetVar(""))

    with pytest.raises(ValueError, match="입력값"):
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


def test_read_interval_reports_destroyed_interval_variable():
    app = make_input_gui()
    app.interval_var = FailingGetVar("1")

    with pytest.raises(ValueError, match="주기\\(초\\)"):
        ArubaMmCleanupGui._read_interval(app)


def test_browse_output_dir_updates_output_dir_and_loads_history(monkeypatch):
    app = make_headless_gui()
    app.output_dir_var = FakeVar("/tmp/current")
    loaded = []
    asked_initial_dirs = []
    app._load_history_from_output_dir = lambda path, force=False: loaded.append((path, force))
    monkeypatch.setattr(
        gui_app_module.filedialog,
        "askdirectory",
        lambda **kwargs: asked_initial_dirs.append(kwargs["initialdir"]) or "/tmp/selected",
    )

    ArubaMmCleanupGui.browse_output_dir(app)

    assert asked_initial_dirs == ["/tmp/current"]
    assert app.output_dir_var.get() == "/tmp/selected"
    assert loaded == [(Path("/tmp/selected"), True)]


def test_browse_output_dir_ignores_dialog_tcl_error(monkeypatch):
    app = make_headless_gui()
    app.output_dir_var = FakeVar("/tmp/current")
    loaded = []
    app._load_history_from_output_dir = lambda path, force=False: loaded.append((path, force))
    monkeypatch.setattr(
        gui_app_module.filedialog,
        "askdirectory",
        lambda **_kwargs: (_ for _ in ()).throw(tk.TclError("invalid command name")),
    )

    ArubaMmCleanupGui.browse_output_dir(app)

    assert app.output_dir_var.get() == "/tmp/current"
    assert loaded == []


def test_browse_output_dir_ignores_destroyed_output_dir_variable(monkeypatch):
    app = make_headless_gui()
    app.output_dir_var = FailingGetVar("/tmp/current")
    loaded = []
    app._load_history_from_output_dir = lambda path, force=False: loaded.append((path, force))
    monkeypatch.setattr(
        gui_app_module.filedialog,
        "askdirectory",
        lambda **_kwargs: "/tmp/selected",
    )

    ArubaMmCleanupGui.browse_output_dir(app)

    assert loaded == []


def test_browse_output_dir_ignores_destroyed_output_dir_set(monkeypatch):
    app = make_headless_gui()
    app.output_dir_var = FailingSetVar("/tmp/current")
    loaded = []
    app._load_history_from_output_dir = lambda path, force=False: loaded.append((path, force))
    monkeypatch.setattr(
        gui_app_module.filedialog,
        "askdirectory",
        lambda **_kwargs: "/tmp/selected",
    )

    ArubaMmCleanupGui.browse_output_dir(app)

    assert loaded == []


def test_manual_run_input_error_dialog_failure_does_not_start_worker(monkeypatch):
    app = make_headless_gui()
    app._read_inputs = lambda: (_ for _ in ()).throw(ValueError("bad input"))
    app._load_history_from_output_dir = lambda *_args, **_kwargs: None
    app._set_running = lambda _running: (_ for _ in ()).throw(AssertionError("worker should not start"))
    monkeypatch.setattr(
        gui_app_module.messagebox,
        "showerror",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(tk.TclError("invalid command name")),
    )

    ArubaMmCleanupGui.start_manual_run(app)

    assert app.is_running is False
    assert app.event_queue.empty()


def test_scheduler_input_error_dialog_failure_does_not_start_scheduler(monkeypatch):
    app = make_headless_gui()
    app._read_inputs = lambda: (_ for _ in ()).throw(ValueError("bad input"))
    app._load_history_from_output_dir = lambda *_args, **_kwargs: None
    app._set_running = lambda _running: (_ for _ in ()).throw(AssertionError("scheduler should not start"))
    monkeypatch.setattr(
        gui_app_module.messagebox,
        "showerror",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(tk.TclError("invalid command name")),
    )

    ArubaMmCleanupGui.start_scheduler(app)

    assert app.scheduler_running is False
    assert app.event_queue.empty()


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


def test_progress_status_update_failure_does_not_skip_followup_work():
    app = make_headless_gui()
    app.status_var = FailingSetVar()

    ArubaMmCleanupGui._handle_progress(app, "connect_start", {"host": "192.0.2.10"})

    assert "CONNECT: 192.0.2.10" in app.logs


def test_disconnect_status_update_failure_does_not_skip_log():
    app = make_headless_gui()
    app.status_var = FailingSetVar()
    close_reasons = []
    app._start_session_close = lambda **kwargs: close_reasons.append(kwargs)

    ArubaMmCleanupGui.disconnect_session(app)

    assert close_reasons == [{"reason": "manual", "enqueue_progress": True}]
    assert "SESSION DISCONNECT REQUEST" in app.logs


def test_delete_canceled_button_failure_does_not_skip_log():
    app = make_headless_gui()
    app.cancel_button = FailingConfigureButton()
    app.table = FakeTreeTable()

    ArubaMmCleanupGui._handle_progress(app, "delete_canceled", {"count": 2})

    assert "CANCELED: 2 pending MAC(s)" in app.logs


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


def test_result_table_updates_ignore_destroyed_table():
    app = make_headless_gui()
    app.table = DestroyedTreeTable()

    ArubaMmCleanupGui._replace_table(app, ["aa:bb:cc:00:00:01"], "삭제 대상")
    ArubaMmCleanupGui._set_row_status(app, "aa:bb:cc:00:00:01", "삭제 완료", "")
    ArubaMmCleanupGui._mark_reappeared_rows(app, ["aa:bb:cc:00:00:01"])
    ArubaMmCleanupGui._set_all_pending_status(app, "취소됨")

    assert app.table.rows == {}


def test_history_cap_ignores_destroyed_history_table():
    app = make_headless_gui()
    app.history_table = DestroyedHistoryTable()

    ArubaMmCleanupGui._cap_history_rows(app)

    assert app.history_table.rows == {}


def test_running_state_resets_current_run_counters():
    app = make_headless_gui()

    app._set_running(True)

    assert app.counter_vars["deleted"].get() == "3"
    assert app.counter_vars["queried"].get() == "7"
    assert app.current_run_query_counted is False
    assert app.current_run_delete_counted is False
    assert app.timers[-1] == ("실행 중", "조회/삭제 처리")
    assert app.cancel_button.config["state"] == "disabled"


def test_running_state_button_failure_still_updates_timer():
    app = make_headless_gui()
    app.manual_button = FailingConfigureButton()
    app.schedule_button = FailingConfigureButton()
    app.cancel_button = FailingConfigureButton()

    ArubaMmCleanupGui._set_running(app, True)

    assert app.is_running is True
    assert app.timers[-1] == ("실행 중", "조회/삭제 처리")
    assert app.current_run_query_counted is False


def test_set_timer_ignores_destroyed_timer_variables():
    app = make_headless_gui()
    app.timer_value_var = FailingSetVar()
    app.timer_state_var = FailingSetVar()

    ArubaMmCleanupGui._set_timer(app, "실행 중", "조회/삭제 처리")

    assert app.timer_value_var.value == "0"
    assert app.timer_state_var.value == "0"


def test_sync_counter_vars_ignores_destroyed_counter_variables():
    app = make_headless_gui()
    app.counter_vars = {
        "queried": FailingSetVar("7"),
        "deleted": FailingSetVar("3"),
    }
    app.cumulative_queried_count = 9
    app.cumulative_deleted_count = 5

    ArubaMmCleanupGui._sync_counter_vars(app)

    assert app.counter_vars["queried"].value == "7"
    assert app.counter_vars["deleted"].value == "3"


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


def test_drain_events_logs_malformed_queue_item_and_continues():
    app = make_headless_gui()
    app.scheduler_running = True
    app.event_queue.put(("progress",))
    app.event_queue.put(("scheduler_stopped", None))

    ArubaMmCleanupGui._drain_events(app)

    assert any("이벤트 형식 오류" in message for message in app.logs)
    assert app.scheduler_running is False
    assert app.timers[-1] == ("-", "대기")
    assert app.scheduled_callbacks[-1][0] == 150


def test_scheduler_stopped_button_failure_still_updates_timer():
    app = make_headless_gui()
    app.scheduler_running = True
    app.manual_button = FailingConfigureButton()
    app.schedule_button = FailingConfigureButton()
    app.stop_schedule_button = FailingConfigureButton()
    app.event_queue.put(("scheduler_stopped", None))

    ArubaMmCleanupGui._drain_events(app)

    assert app.scheduler_running is False
    assert app.timers[-1] == ("-", "대기")


def test_sync_settings_visibility_toggles_settings_frame():
    app = make_headless_gui()
    app.settings_frame = FakeSettingsFrame()
    app.is_running = True
    app.scheduler_running = False

    ArubaMmCleanupGui._sync_settings_visibility(app)

    assert app.settings_frame.hidden is True
    assert app.settings_frame.grid_remove_calls == 1

    app.is_running = False
    ArubaMmCleanupGui._sync_settings_visibility(app)

    assert app.settings_frame.hidden is False
    assert app.settings_frame.grid_calls == 1


def test_sync_settings_visibility_ignores_destroyed_settings_frame():
    app = make_headless_gui()
    app.settings_frame = DestroyedSettingsFrame()
    app.is_running = True
    app.scheduler_running = False

    ArubaMmCleanupGui._sync_settings_visibility(app)

    app.is_running = False
    ArubaMmCleanupGui._sync_settings_visibility(app)


def test_drain_events_handles_missing_progress_payload_as_empty_dict():
    app = make_headless_gui()
    app.event_queue.put(("progress", ("connect_start", None)))

    ArubaMmCleanupGui._drain_events(app)

    assert app.status_var.get() == "MM 접속 중"
    assert "CONNECT: None" in app.logs
    assert not any("이벤트 처리 실패(progress)" in message for message in app.logs)


def test_drain_events_ignores_reschedule_failure():
    app = make_headless_gui()
    app._drain_after_id = "old-after"
    app.after = lambda _ms, _callback: (_ for _ in ()).throw(tk.TclError("invalid command name"))

    ArubaMmCleanupGui._drain_events(app)

    assert app._drain_after_id is None


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


def test_on_close_destroys_window_when_shutdown_after_fails():
    app = make_headless_gui()
    app._drain_after_id = None
    app.copy_notice_after_id = None
    close_calls = []
    destroy_calls = []
    app.after = lambda _ms, _callback: (_ for _ in ()).throw(tk.TclError("invalid command name"))
    app._start_session_close = lambda **kwargs: close_calls.append(kwargs)
    app._destroy_window = lambda: destroy_calls.append("destroyed")

    ArubaMmCleanupGui.on_close(app)

    assert app.closing is True
    assert close_calls == [{"reason": "app_close", "enqueue_progress": False}]
    assert destroy_calls == ["destroyed"]


def test_gui_smoke_main_uses_safe_destroy_when_destroy_raises(monkeypatch):
    class SmokeApp:
        def __init__(self):
            self._drain_after_id = "after-1"
            self.closing = False
            self.canceled_after_ids = []
            self.safe_destroy_calls = 0

        def update_idletasks(self):
            pass

        def after_cancel(self, after_id):
            self.canceled_after_ids.append(after_id)

        def destroy(self):
            raise tk.TclError("invalid command name")

        def _destroy_window(self):
            self.safe_destroy_calls += 1
            try:
                self.destroy()
            except tk.TclError:
                pass

    app = SmokeApp()
    monkeypatch.setenv("ARUBA_MM_CLEANUP_GUI_SMOKE", "1")
    monkeypatch.setattr(gui_app_module, "ArubaMmCleanupGui", lambda: app)

    assert gui_app_module.main() == 0
    assert app.closing is True
    assert app.canceled_after_ids == ["after-1"]
    assert app.safe_destroy_calls == 1


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


def test_close_runner_session_reports_manual_close_failure():
    app = make_headless_gui()
    app.runner_lock = threading.Lock()
    app.runner = SimpleNamespace(
        close_session=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("close failed"))
    )

    ArubaMmCleanupGui._close_runner_session(app, reason="manual", enqueue_progress=True)

    assert app.event_queue.get_nowait() == (
        "progress",
        ("warning", {"message": "session close failed: close failed", "reason": "manual"}),
    )
    assert app.event_queue.empty()


def test_close_runner_session_ignores_app_close_failure_without_progress():
    app = make_headless_gui()
    app.runner_lock = threading.Lock()
    app.runner = SimpleNamespace(
        close_session=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("close failed"))
    )

    ArubaMmCleanupGui._close_runner_session(app, reason="app_close", enqueue_progress=False)

    assert app.event_queue.empty()


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


def test_history_read_keeps_only_recent_jsonl_records(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    history_path = output_dir / HISTORY_FILE_NAME
    records = [
        json.dumps(
            {
                "run_at": f"2026-07-02T13:{index % 60:02d}:00",
                "mac": f"aa:bb:cc:00:{index // 256:02x}:{index % 256:02x}",
                "status": "verified_deleted",
            },
            ensure_ascii=False,
        )
        for index in range(MAX_HISTORY_ROWS + 3)
    ]
    history_path.write_text("\n".join(records), encoding="utf-8")
    app = make_headless_gui()

    loaded = app._read_history_records(output_dir)

    assert len(loaded) == MAX_HISTORY_ROWS
    assert loaded[0]["mac"] == "aa:bb:cc:00:00:03"
    assert loaded[-1]["mac"] == "aa:bb:cc:00:01:f6"


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


def test_log_restores_disabled_state_when_insert_fails():
    app = make_headless_gui()
    app.log_text = InsertFailingLogText()

    ArubaMmCleanupGui._log(app, "line cannot be inserted")

    assert app.log_text.state == "disabled"
    assert app.log_text.lines == []


def test_log_ignores_destroyed_log_widget():
    app = make_headless_gui()
    app.log_text = ConfigureFailingLogText()

    ArubaMmCleanupGui._log(app, "line cannot be written")

    assert app.log_text.lines == []


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


def test_summary_status_update_failure_does_not_skip_audit_or_history():
    app = make_headless_gui()
    app.status_var = FailingSetVar()
    summary = SimpleNamespace(
        queried_count=1,
        target_macs=["aa:bb:cc:00:00:01"],
        delete_success_count=1,
        reappeared_count=0,
        verification_skipped=False,
        error="",
        canceled=False,
        reappeared_macs=[],
        audit_path="/tmp/audit.json",
        audit_error="",
        history_error="",
    )

    ArubaMmCleanupGui._handle_summary(app, summary)

    assert "AUDIT: /tmp/audit.json" in app.logs
    assert app.history_summaries == [summary]


def test_summary_button_failure_does_not_skip_audit_or_history():
    app = make_headless_gui()
    app.cancel_button = FailingConfigureButton()
    summary = SimpleNamespace(
        queried_count=1,
        target_macs=["aa:bb:cc:00:00:01"],
        delete_success_count=1,
        reappeared_count=0,
        verification_skipped=False,
        error="",
        canceled=False,
        reappeared_macs=[],
        audit_path="/tmp/audit.json",
        audit_error="",
        history_error="",
    )

    ArubaMmCleanupGui._handle_summary(app, summary)

    assert "AUDIT: /tmp/audit.json" in app.logs
    assert app.history_summaries == [summary]


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


def test_mac_copy_notice_ignores_hide_timer_schedule_failure():
    app = make_headless_gui()
    table = FakeTreeTable()
    table.insert(
        "",
        "end",
        iid="aa:bb:cc:00:00:01",
        values=("aa:bb:cc:00:00:01", "삭제 대상", "2026-07-02 13:00:00", "", ""),
    )
    app.after = lambda _ms, _callback: (_ for _ in ()).throw(tk.TclError("invalid command name"))

    ArubaMmCleanupGui._copy_mac_from_table_event(app, FakeClickEvent(), table, "#1")

    assert app.clipboard_values == ["aa:bb:cc:00:00:01"]
    assert app.copy_notice_title_var.get() == "복사 완료"
    assert app.copy_notice_mac_var.get() == "aa:bb:cc:00:00:01"
    assert app.copy_notice_frame.hidden is False
    assert app.copy_notice_after_id is None


def test_mac_copy_notice_ignores_overlay_place_failure():
    app = make_headless_gui()
    app.copy_notice_frame = PlacementFailingOverlayFrame()
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
    assert app.scheduled_callbacks[0][0] == 1000


def test_mac_copy_ignores_destroyed_table_identify_failure():
    app = make_headless_gui()
    table = IdentifyFailingTreeTable()

    ArubaMmCleanupGui._copy_mac_from_table_event(app, FakeClickEvent(), table, "#1")

    assert app.clipboard_values == []
    assert app.copy_notice_title_var.get() == ""
    assert app.copy_notice_mac_var.get() == ""
    assert app.scheduled_callbacks == []


def test_show_copy_notice_ignores_destroyed_notice_variables():
    app = make_headless_gui()
    app.copy_notice_title_var = FailingSetVar("")
    app.copy_notice_mac_var = FailingSetVar("")

    ArubaMmCleanupGui._show_copy_notice(app, "aa:bb:cc:00:00:01")

    assert app.copy_notice_frame.hidden is False
    assert app.copy_notice_after_id == "after-1"


def test_hide_copy_notice_clears_state_when_overlay_hide_fails():
    app = make_headless_gui()
    app.copy_notice_frame = HideFailingOverlayFrame()
    app.copy_notice_title_var.set("복사 완료")
    app.copy_notice_mac_var.set("aa:bb:cc:00:00:01")
    app.copy_notice_after_id = "after-1"

    ArubaMmCleanupGui._hide_copy_notice(app)

    assert app.copy_notice_title_var.get() == ""
    assert app.copy_notice_mac_var.get() == ""
    assert app.copy_notice_after_id is None


def test_hide_copy_notice_ignores_destroyed_notice_variables():
    app = make_headless_gui()
    app.copy_notice_title_var = FailingSetVar("복사 완료")
    app.copy_notice_mac_var = FailingSetVar("aa:bb:cc:00:00:01")
    app.copy_notice_after_id = "after-1"
    app.copy_notice_frame.hidden = False

    ArubaMmCleanupGui._hide_copy_notice(app)

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
