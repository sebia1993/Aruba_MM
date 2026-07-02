import inspect
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
    MAX_HISTORY_ROWS,
    MIN_INTERVAL_SECONDS,
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
    app.scheduler_running = False
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


def test_gui_has_manual_scheduler_and_cancel_controls():
    source = inspect.getsource(ArubaMmCleanupGui)

    assert "start_manual_run" in source
    assert "start_scheduler" in source
    assert "stop_scheduler" in source
    assert "cancel_current_delete" in source
    assert "disconnect_session" in source
    assert "persistent_session=True" in source
    assert "WM_DELETE_WINDOW" in source
    assert "settings_frame" in source
    assert "grid_remove" in source
    assert "_sync_settings_visibility" in source
    assert "_append_history_rows" in source
    assert "_cap_history_rows" in source
    assert "_mark_reappeared_rows" in source
    assert "_action_button" in source
    assert "_timer_card" in source
    assert "_set_timer" in source
    assert "history_row_counter" in source
    assert "runner_lock" in source
    assert "with self.runner_lock" in source
    assert "delete_unknown" in source
    assert "reappeared_macs" in source
    assert "REAPPEARED" in source
    assert "재조회" in source
    assert "재조회됨" in source
    assert "확인 필요" in source
    assert "if self.scheduler_running" in source
    assert "variant=\"secondary\"" in source
    assert "variant=\"danger\"" in source
    assert "variant=\"danger_outline\"" in source
    assert "이번 삭제 취소" in source
    assert "세션 연결 해제" in source
    assert "주기 실행 시작" in source
    assert "최근 삭제 이력" in source
    assert "이력 전체 지우기" in source
    assert "타이머" in source
    assert "삭제 대기" in source
    assert "다음 실행" in source
    assert "조회/삭제 처리" in source
    assert "next_run_var" not in source
    assert "countdown_var" not in source
    assert "조회" in source
    assert "삭제 성공" in source
    assert "삭제 실패" in source
    assert "남은 MAC" in source
    assert "_reset_run_counters" in source
    assert "_increment_counter" in source


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
    )

    app._handle_summary(summary)

    assert app.counter_vars["queried"].get() == "4"
    assert app.counter_vars["deleted"].get() == "2"
    assert app.counter_vars["failed"].get() == "1"
    assert app.counter_vars["remaining"].get() == "1"
    assert app.counter_vars["reappeared"].get() == "0"
