import json
import queue
import threading
import time
from types import SimpleNamespace

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
    DELETE_DELAY_SECONDS,
    HISTORY_FILE_NAME,
    MAX_LOG_LINES,
    MAX_HISTORY_ROWS,
    MIN_INTERVAL_SECONDS,
    SHUTDOWN_GRACE_MS,
    TEXT,
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


def make_headless_gui():
    app = object.__new__(ArubaMmCleanupGui)
    app.counter_vars = {
        "queried": FakeVar("7"),
        "deleted": FakeVar("3"),
        "failed": FakeVar("2"),
        "remaining": FakeVar("1"),
        "reappeared": FakeVar("1"),
    }
    app.status_var = FakeVar()
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
    app.history_summaries = []
    app.reappeared_rows = []
    app._set_row_status = lambda mac, status, error: app.rows.append((mac, status, error))
    app._log = lambda message: app.logs.append(message)
    app._set_timer = lambda value, state: app.timers.append((value, state))
    app._sync_settings_visibility = lambda: None
    app._append_history_rows = lambda summary: app.history_summaries.append(summary)
    app._mark_reappeared_rows = lambda macs: app.reappeared_rows.append(macs)
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
    assert DELETE_DELAY_SECONDS == 60
    assert MAX_HISTORY_ROWS == 500
    assert DEFAULT_INTERVAL_SECONDS == 300
    assert MIN_INTERVAL_SECONDS == 60


def test_delete_progress_events_update_counters_immediately():
    app = make_headless_gui()
    app.counter_vars["deleted"].set("0")
    app.counter_vars["failed"].set("0")

    app._handle_progress("delete_done", {"mac": "aa:bb:cc:00:00:01"})
    app._handle_progress("delete_done", {"mac": "aa:bb:cc:00:00:02"})
    app._handle_progress("delete_error", {"mac": "aa:bb:cc:00:00:03", "error": "Error"})
    app._handle_progress("delete_unknown", {"mac": "aa:bb:cc:00:00:04", "error": "timeout"})

    assert app.counter_vars["deleted"].get() == "2"
    assert app.counter_vars["failed"].get() == "2"
    assert app.rows == [
        ("aa:bb:cc:00:00:01", "삭제 완료", ""),
        ("aa:bb:cc:00:00:02", "삭제 완료", ""),
        ("aa:bb:cc:00:00:03", "삭제 실패", "Error"),
        ("aa:bb:cc:00:00:04", "확인 필요", "timeout"),
    ]


def test_running_state_resets_current_run_counters():
    app = make_headless_gui()

    app._set_running(True)

    assert app.counter_vars["deleted"].get() == "0"
    assert app.counter_vars["failed"].get() == "0"
    assert app.counter_vars["remaining"].get() == "0"
    assert app.counter_vars["reappeared"].get() == "0"
    assert app.counter_vars["queried"].get() == "7"
    assert app.timers[-1] == ("실행 중", "조회/삭제 처리")
    assert app.cancel_button.config["state"] == "disabled"


def test_enqueue_event_drops_worker_events_after_closing():
    app = make_headless_gui()

    assert app._enqueue_event("running", True) is True
    app.closing = True
    assert app._enqueue_event("running", False) is False

    assert app.event_queue.get_nowait() == ("running", True)
    assert app.event_queue.empty()


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


def test_log_text_is_capped_to_max_lines():
    app = make_headless_gui()
    app.log_text = FakeLogText()

    for index in range(MAX_LOG_LINES + 5):
        ArubaMmCleanupGui._log(app, f"line {index}")

    assert len(app.log_text.lines) == MAX_LOG_LINES
    assert "line 0" not in app.log_text.lines[0]


def test_summary_overwrites_progress_counters_with_final_values():
    app = make_headless_gui()
    app.counter_vars["deleted"].set("9")
    app.counter_vars["failed"].set("9")
    summary = SimpleNamespace(
        queried_count=4,
        delete_success_count=2,
        delete_failure_count=1,
        remaining_count=1,
        reappeared_count=0,
        error="",
        canceled=False,
        reappeared_macs=[],
        audit_path=None,
        audit_error="",
        history_error="",
    )

    app._handle_summary(summary)

    assert app.counter_vars["queried"].get() == "4"
    assert app.counter_vars["deleted"].get() == "2"
    assert app.counter_vars["failed"].get() == "1"
    assert app.counter_vars["remaining"].get() == "1"
    assert app.counter_vars["reappeared"].get() == "0"
