import inspect

from aruba_mm_cleanup import __version__
from aruba_mm_cleanup.gui_app import (
    ACCENT,
    APP_TITLE,
    BG,
    CARD_BG,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_ROLE,
    DELETE_DELAY_SECONDS,
    MAX_HISTORY_ROWS,
    MIN_INTERVAL_SECONDS,
    TEXT,
    ArubaMmCleanupGui,
)


def test_version_and_gui_constants():
    assert __version__ == "0.1.0"
    assert APP_TITLE == "Aruba MM Cleanup Dashboard"
    assert ACCENT == "#3e6ae1"
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
    assert "이번 삭제 취소" in source
    assert "세션 연결 해제" in source
    assert "주기 실행 시작" in source
    assert "최근 삭제 이력" in source
    assert "이력 지우기" in source
    assert "조회" in source
    assert "삭제 성공" in source
    assert "삭제 실패" in source
    assert "남은 MAC" in source
