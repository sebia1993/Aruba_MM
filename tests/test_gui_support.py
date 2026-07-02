import inspect

from aruba_mm_cleanup import __version__
from aruba_mm_cleanup.gui_app import (
    APP_TITLE,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_ROLE,
    DELETE_DELAY_SECONDS,
    MIN_INTERVAL_SECONDS,
    ArubaMmCleanupGui,
)


def test_version_and_gui_constants():
    assert __version__ == "0.1.0"
    assert APP_TITLE == "Aruba MM Cleanup Dashboard"
    assert DEFAULT_ROLE == "profiling"
    assert DELETE_DELAY_SECONDS == 60
    assert DEFAULT_INTERVAL_SECONDS == 300
    assert MIN_INTERVAL_SECONDS == 60


def test_gui_has_manual_scheduler_and_cancel_controls():
    source = inspect.getsource(ArubaMmCleanupGui)

    assert "start_manual_run" in source
    assert "start_scheduler" in source
    assert "stop_scheduler" in source
    assert "cancel_current_delete" in source
    assert "이번 삭제 취소" in source
    assert "주기 실행 시작" in source
    assert "조회" in source
    assert "삭제 성공" in source
    assert "삭제 실패" in source
    assert "남은 MAC" in source

