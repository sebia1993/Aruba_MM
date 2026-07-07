import subprocess
import sys
import zipfile
import configparser
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from aruba_mm_cleanup.cli import main as cli_main
from tools.verify_release_package import (
    _find_latest_zip,
    _read_zip_names,
    _smoke_cli_help,
    _smoke_gui,
    main as verifier_main,
)


def test_release_zip_verifier_checks_required_files(tmp_path):
    repo_root = Path(__file__).parents[1]
    verifier = repo_root / "tools" / "verify_release_package.py"
    zip_path = tmp_path / "ArubaMMCleanupGUI_v0.1.0.zip"
    names = [
        "ArubaMMCleanupGUI.exe",
        "ArubaMMCleanupCLI.exe",
        "README.md",
        "USER_GUIDE_KO.md",
        "config/mock_scenarios/profiling_users.txt",
    ]
    with zipfile.ZipFile(zip_path, "w") as archive:
        for name in names:
            archive.writestr(name, "sample")

    completed = subprocess.run(
        [sys.executable, str(verifier), "--zip", str(zip_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_release_zip_verifier_ignores_disappearing_zip_candidates(tmp_path, monkeypatch):
    stale_zip = tmp_path / "stale.zip"
    latest_zip = tmp_path / "latest.zip"
    stale_zip.write_text("stale", encoding="utf-8")
    latest_zip.write_text("latest", encoding="utf-8")
    original_stat = Path.stat

    def flaky_stat(path, *args, **kwargs):
        if path == stale_zip:
            raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    assert _find_latest_zip(tmp_path) == latest_zip


def test_release_zip_verifier_reports_inaccessible_dist_directory(tmp_path, monkeypatch):
    dist_dir = tmp_path / "dist"
    original_glob = Path.glob

    def inaccessible_glob(path, pattern):
        if path == dist_dir and pattern == "*.zip":
            raise PermissionError("dist access denied")
        return original_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", inaccessible_glob)

    with pytest.raises(SystemExit) as exc_info:
        _find_latest_zip(dist_dir)

    assert "Release ZIP directory is not accessible" in str(exc_info.value)
    assert str(dist_dir) in str(exc_info.value)


def test_release_zip_verifier_reports_inaccessible_zip_path(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    original_exists = Path.exists

    def inaccessible_exists(path):
        if path == zip_path:
            raise PermissionError("access denied")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", inaccessible_exists)

    with pytest.raises(SystemExit) as exc_info:
        verifier_main(["--zip", str(zip_path)])

    assert "Release ZIP is not accessible" in str(exc_info.value)


def test_release_zip_verifier_reports_zip_open_permission_failure(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    zip_path.write_text("placeholder", encoding="utf-8")
    original_zip_file = zipfile.ZipFile

    def locked_zip_file(path, *args, **kwargs):
        if path == zip_path:
            raise PermissionError("locked by scanner")
        return original_zip_file(path, *args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", locked_zip_file)

    with pytest.raises(SystemExit) as exc_info:
        _read_zip_names(zip_path)

    assert "Release ZIP could not be read" in str(exc_info.value)
    assert "locked by scanner" in str(exc_info.value)


def test_release_zip_verifier_reports_duplicate_zip_entries(tmp_path):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupGUI.exe", "first")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("ArubaMMCleanupGUI.exe", "second")

    with pytest.raises(SystemExit) as exc_info:
        _read_zip_names(zip_path)

    assert "Release ZIP contains duplicate entries" in str(exc_info.value)
    assert "ArubaMMCleanupGUI.exe" in str(exc_info.value)


def test_release_zip_verifier_reports_zip_inspection_runtime_failure(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupGUI.exe", "sample")

    def failing_testzip(self):
        raise RuntimeError("encrypted zip entry")

    monkeypatch.setattr(zipfile.ZipFile, "testzip", failing_testzip)

    with pytest.raises(SystemExit) as exc_info:
        _read_zip_names(zip_path)

    assert "Release ZIP could not be inspected" in str(exc_info.value)
    assert "encrypted zip entry" in str(exc_info.value)


def test_release_zip_verifier_does_not_accept_directory_as_required_file(tmp_path):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupGUI.exe/", "")
        archive.writestr("ArubaMMCleanupCLI.exe", "sample")
        archive.writestr("README.md", "sample")
        archive.writestr("USER_GUIDE_KO.md", "sample")
        archive.writestr("config/mock_scenarios/profiling_users.txt", "sample")

    with pytest.raises(SystemExit) as exc_info:
        verifier_main(["--zip", str(zip_path)])

    assert "Release ZIP is missing required files" in str(exc_info.value)
    assert "ArubaMMCleanupGUI.exe" in str(exc_info.value)


def test_release_zip_verifier_reports_unsafe_zip_entry_paths(tmp_path):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupGUI.exe", "sample")
        archive.writestr("ArubaMMCleanupCLI.exe", "sample")
        archive.writestr("README.md", "sample")
        archive.writestr("USER_GUIDE_KO.md", "sample")
        archive.writestr("config/mock_scenarios/profiling_users.txt", "sample")
        archive.writestr("../outside.txt", "bad")

    with pytest.raises(SystemExit) as exc_info:
        verifier_main(["--zip", str(zip_path)])

    assert "Release ZIP contains unsafe paths" in str(exc_info.value)
    assert "../outside.txt" in str(exc_info.value)


def test_release_zip_verifier_reports_cli_smoke_timeout(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupCLI.exe", "sample")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")

    def timeout_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["ArubaMMCleanupCLI.exe", "--help"], 60)

    monkeypatch.setattr("tools.verify_release_package.subprocess.run", timeout_run)

    with pytest.raises(SystemExit) as exc_info:
        _smoke_cli_help(zip_path, require=True)

    assert "CLI smoke command timed out" in str(exc_info.value)


def test_cli_smoke_rejects_unsafe_zip_paths_before_extract(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupCLI.exe", "sample")
        archive.writestr("../outside.txt", "bad")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "tools.verify_release_package.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="--host --role", stderr=""),
    )

    with pytest.raises(SystemExit) as exc_info:
        _smoke_cli_help(zip_path, require=True)

    assert "Release ZIP contains unsafe paths" in str(exc_info.value)
    assert "../outside.txt" in str(exc_info.value)


def test_cli_smoke_rechecks_unsafe_zip_paths_during_extract(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupCLI.exe", "sample")
        archive.writestr("../outside.txt", "bad")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")
    monkeypatch.setattr("tools.verify_release_package._read_zip_names", lambda _zip_path: {"ArubaMMCleanupCLI.exe"})

    def fail_if_extractall_runs(self, _path):
        raise AssertionError("extractall should not run for unsafe paths")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", fail_if_extractall_runs)

    with pytest.raises(SystemExit) as exc_info:
        _smoke_cli_help(zip_path, require=True)

    assert "CLI smoke ZIP contains unsafe paths" in str(exc_info.value)
    assert "../outside.txt" in str(exc_info.value)


def test_release_zip_verifier_reports_cli_smoke_launch_failure(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupCLI.exe", "sample")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")

    def failing_run(*_args, **_kwargs):
        raise OSError("launch denied")

    monkeypatch.setattr("tools.verify_release_package.subprocess.run", failing_run)

    with pytest.raises(SystemExit) as exc_info:
        _smoke_cli_help(zip_path, require=True)

    assert "CLI smoke command could not start" in str(exc_info.value)


def test_release_zip_verifier_reports_gui_smoke_launch_failure(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupGUI.exe", "sample")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")

    def failing_run(*_args, **_kwargs):
        raise OSError("launch denied")

    monkeypatch.setattr("tools.verify_release_package.subprocess.run", failing_run)

    with pytest.raises(SystemExit) as exc_info:
        _smoke_gui(zip_path, require=True)

    assert "GUI smoke command could not start" in str(exc_info.value)


def test_release_zip_verifier_reports_gui_smoke_timeout(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupGUI.exe", "sample")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")

    def timeout_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["ArubaMMCleanupGUI.exe"], 60)

    monkeypatch.setattr("tools.verify_release_package.subprocess.run", timeout_run)

    with pytest.raises(SystemExit) as exc_info:
        _smoke_gui(zip_path, require=True)

    assert "GUI smoke command timed out" in str(exc_info.value)


def test_release_zip_verifier_reports_cli_smoke_temp_directory_failure(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupCLI.exe", "sample")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "tools.verify_release_package.tempfile.TemporaryDirectory",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("temp denied")),
    )

    with pytest.raises(SystemExit) as exc_info:
        _smoke_cli_help(zip_path, require=True)

    assert "CLI smoke temporary directory could not be created" in str(exc_info.value)
    assert "temp denied" in str(exc_info.value)


def test_release_zip_verifier_reports_gui_smoke_temp_directory_failure(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupGUI.exe", "sample")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "tools.verify_release_package.tempfile.TemporaryDirectory",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("temp denied")),
    )

    with pytest.raises(SystemExit) as exc_info:
        _smoke_gui(zip_path, require=True)

    assert "GUI smoke temporary directory could not be created" in str(exc_info.value)
    assert "temp denied" in str(exc_info.value)


def test_release_zip_verifier_reports_temp_directory_runtime_failure(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupCLI.exe", "sample")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "tools.verify_release_package.tempfile.TemporaryDirectory",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temp runtime failure")),
    )

    with pytest.raises(SystemExit) as exc_info:
        _smoke_cli_help(zip_path, require=True)

    assert "CLI smoke temporary directory could not be created" in str(exc_info.value)
    assert "temp runtime failure" in str(exc_info.value)


def test_release_zip_verifier_ignores_cli_smoke_temp_cleanup_failure(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupCLI.exe", "sample")

    smoke_dir = tmp_path / "smoke"
    smoke_dir.mkdir()

    class CleanupFailingTempDir:
        name = str(smoke_dir)

        def cleanup(self):
            raise PermissionError("cleanup denied")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "tools.verify_release_package.tempfile.TemporaryDirectory",
        lambda *args, **kwargs: CleanupFailingTempDir(),
    )
    monkeypatch.setattr(
        "tools.verify_release_package.subprocess.run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="--host --role", stderr=""),
    )

    _smoke_cli_help(zip_path, require=True)


def test_release_zip_verifier_ignores_temp_cleanup_runtime_failure(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupCLI.exe", "sample")

    smoke_dir = tmp_path / "smoke"
    smoke_dir.mkdir()

    class CleanupRuntimeFailingTempDir:
        name = str(smoke_dir)

        def cleanup(self):
            raise RuntimeError("cleanup runtime failure")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "tools.verify_release_package.tempfile.TemporaryDirectory",
        lambda *args, **kwargs: CleanupRuntimeFailingTempDir(),
    )
    monkeypatch.setattr(
        "tools.verify_release_package.subprocess.run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="--host --role", stderr=""),
    )

    _smoke_cli_help(zip_path, require=True)


def test_release_zip_verifier_runs_gui_smoke_with_smoke_environment(tmp_path, monkeypatch):
    release_dir = tmp_path / "release package"
    release_dir.mkdir()
    zip_path = release_dir / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupGUI.exe", "sample")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.verify_release_package.subprocess.run", fake_run)

    _smoke_gui(zip_path, require=True)

    assert Path(captured["args"][0]).name == "ArubaMMCleanupGUI.exe"
    assert captured["env"]["ARUBA_MM_CLEANUP_GUI_SMOKE"] == "1"


def test_release_zip_verifier_reports_cli_smoke_extract_failure(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupCLI.exe", "sample")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")

    def failing_extractall(self, _path):
        raise OSError("extract denied")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", failing_extractall)

    with pytest.raises(SystemExit) as exc_info:
        _smoke_cli_help(zip_path, require=True)

    assert "CLI smoke ZIP extraction failed" in str(exc_info.value)


def test_release_zip_verifier_reports_gui_smoke_extract_failure(tmp_path, monkeypatch):
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("ArubaMMCleanupGUI.exe", "sample")

    monkeypatch.setattr("tools.verify_release_package.platform.system", lambda: "Windows")

    def failing_extractall(self, _path):
        raise RuntimeError("encrypted zip")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", failing_extractall)

    with pytest.raises(SystemExit) as exc_info:
        _smoke_gui(zip_path, require=True)

    assert "GUI smoke ZIP extraction failed" in str(exc_info.value)


def test_cli_help_distinguishes_timeout_from_delete_delay():
    completed = subprocess.run(
        [sys.executable, "-m", "aruba_mm_cleanup.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "--timeout" in output
    assert "device response timeout seconds" in output
    assert "--delay" in output
    assert "countdown seconds between query and delete" in output


def test_cli_rejects_out_of_range_port_before_connecting():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "aruba_mm_cleanup.cli",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
            "--port",
            "0",
            "--yes",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 2, output
    assert "--port must be between 1 and 65535" in output


def test_cli_rejects_non_positive_timeout_before_connecting():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "aruba_mm_cleanup.cli",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
            "--timeout",
            "0",
            "--yes",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 2, output
    assert "--timeout must be at least 1" in output


def test_cli_uses_actual_one_second_timeout(monkeypatch):
    captured = {}

    class FakeRunner:
        def run_once(self, _config, settings, **_kwargs):
            captured["timeout"] = settings.timeout
            return SimpleNamespace(
                queried_count=0,
                delete_success_count=0,
                delete_failure_count=0,
                remaining_count=0,
                reappeared_count=0,
                audit_path=None,
                audit_error="",
                history_error="",
                error="",
            )

    monkeypatch.setattr("aruba_mm_cleanup.cli.MmCleanupRunner", lambda: FakeRunner())

    result = cli_main(
        [
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
            "--timeout",
            "1",
            "--yes",
        ]
    )

    assert result == 0
    assert captured["timeout"] == 1


def test_cli_rejects_negative_delay_before_connecting():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "aruba_mm_cleanup.cli",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
            "--delay",
            "-1",
            "--yes",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 2, output
    assert "--delay must be at least 0" in output


def test_cli_uses_actual_zero_delete_delay(monkeypatch):
    captured = {}

    class FakeRunner:
        def run_once(self, _config, settings, **_kwargs):
            captured["delete_delay_seconds"] = settings.delete_delay_seconds
            return SimpleNamespace(
                queried_count=0,
                delete_success_count=0,
                delete_failure_count=0,
                remaining_count=0,
                reappeared_count=0,
                audit_path=None,
                audit_error="",
                history_error="",
                error="",
            )

    monkeypatch.setattr("aruba_mm_cleanup.cli.MmCleanupRunner", lambda: FakeRunner())

    result = cli_main(
        [
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
            "--delay",
            "0",
            "--yes",
        ]
    )

    assert result == 0
    assert captured["delete_delay_seconds"] == 0


def test_cli_rejects_empty_host_before_connecting(monkeypatch, capsys):
    def fail_runner():
        raise AssertionError("runner should not be created for empty host")

    monkeypatch.setattr("aruba_mm_cleanup.cli.MmCleanupRunner", fail_runner)

    try:
        cli_main(
            [
                "--host",
                " ",
                "--username",
                "admin",
                "--password",
                "secret",
                "--yes",
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("CLI should reject empty host")

    assert "--host must not be empty" in capsys.readouterr().err


def test_cli_rejects_empty_username_before_connecting(monkeypatch, capsys):
    def fail_runner():
        raise AssertionError("runner should not be created for empty username")

    monkeypatch.setattr("aruba_mm_cleanup.cli.MmCleanupRunner", fail_runner)

    try:
        cli_main(
            [
                "--host",
                "192.0.2.10",
                "--username",
                " ",
                "--password",
                "secret",
                "--yes",
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("CLI should reject empty username")

    assert "--username must not be empty" in capsys.readouterr().err


def test_cli_rejects_role_control_characters_before_connecting():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "aruba_mm_cleanup.cli",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
            "--role",
            "profiling\nshow version",
            "--yes",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 2, output
    assert "Role" in output


def test_cli_treats_missing_confirmation_input_as_cancel(monkeypatch, capsys):
    def raise_eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    result = cli_main(
        [
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
        ]
    )

    assert result == 1
    assert "Canceled before query." in capsys.readouterr().out


def test_cli_treats_missing_password_input_as_cancel(monkeypatch, capsys):
    def raise_eof(_prompt):
        raise EOFError

    monkeypatch.setattr("aruba_mm_cleanup.cli.getpass.getpass", raise_eof)

    result = cli_main(
        [
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--yes",
        ]
    )

    assert result == 1
    assert "Canceled before password input." in capsys.readouterr().out


def test_cli_reports_history_save_warning(monkeypatch, capsys, tmp_path):
    class FakeRunner:
        def run_once(self, *_args, **_kwargs):
            return SimpleNamespace(
                queried_count=0,
                delete_success_count=0,
                delete_failure_count=0,
                remaining_count=0,
                reappeared_count=0,
                audit_path=tmp_path / "cleanup_summary.json",
                audit_error="",
                history_error="history write failed",
                error="",
            )

    monkeypatch.setattr("aruba_mm_cleanup.cli.MmCleanupRunner", lambda: FakeRunner())

    result = cli_main(
        [
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
            "--yes",
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "History warning: history write failed" in output


def test_cli_handles_malformed_summary_without_attribute_error(monkeypatch, capsys):
    class MalformedSummary:
        queried_count = 0
        remaining_count = 0
        audit_path = None

        @property
        def delete_success_count(self):
            raise RuntimeError("bad delete success count")

        @property
        def delete_failure_count(self):
            raise RuntimeError("bad delete failure count")

        @property
        def reappeared_count(self):
            raise RuntimeError("bad reappeared count")

        @property
        def audit_error(self):
            raise RuntimeError("bad audit error")

        @property
        def history_error(self):
            raise RuntimeError("bad history error")

        @property
        def error(self):
            raise RuntimeError("bad summary error")

    class FakeRunner:
        def run_once(self, *_args, **_kwargs):
            return MalformedSummary()

    monkeypatch.setattr("aruba_mm_cleanup.cli.MmCleanupRunner", lambda: FakeRunner())

    result = cli_main(
        [
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
            "--yes",
        ]
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "Queried: 0" in output
    assert "Deleted: 0" in output
    assert "Failed: 0" in output
    assert "Remaining: 0" in output
    assert "Reappeared: 0" in output


def test_cli_reports_unexpected_runner_failure(monkeypatch, capsys):
    class FakeRunner:
        def run_once(self, *_args, **_kwargs):
            raise RuntimeError("runner exploded")

    monkeypatch.setattr("aruba_mm_cleanup.cli.MmCleanupRunner", lambda: FakeRunner())

    result = cli_main(
        [
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
            "--yes",
        ]
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "Run error: runner exploded" in output


def test_cli_handles_unprintable_summary_values(monkeypatch, capsys):
    class BadText:
        def __str__(self):
            raise RuntimeError("bad text")

        def __repr__(self):
            raise RuntimeError("bad repr")

    class FakeRunner:
        def run_once(self, *_args, **_kwargs):
            return SimpleNamespace(
                queried_count=0,
                delete_success_count=0,
                delete_failure_count=0,
                remaining_count=0,
                reappeared_count=0,
                audit_path=BadText(),
                audit_error=BadText(),
                history_error=BadText(),
                error="",
            )

    monkeypatch.setattr("aruba_mm_cleanup.cli.MmCleanupRunner", lambda: FakeRunner())

    result = cli_main(
        [
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
            "--yes",
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "Audit:" in output


def test_cli_handles_unreadable_summary_truthiness(monkeypatch, capsys):
    class BadBool:
        def __bool__(self):
            raise RuntimeError("bad bool")

        def __str__(self):
            return "bad-bool"

    class FakeRunner:
        def run_once(self, *_args, **_kwargs):
            return SimpleNamespace(
                queried_count=0,
                delete_success_count=0,
                delete_failure_count=BadBool(),
                remaining_count=0,
                reappeared_count=BadBool(),
                audit_path=None,
                audit_error=BadBool(),
                history_error=BadBool(),
                error=BadBool(),
            )

    monkeypatch.setattr("aruba_mm_cleanup.cli.MmCleanupRunner", lambda: FakeRunner())

    result = cli_main(
        [
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
            "--yes",
        ]
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "Failed: bad-bool" in output


def test_cli_expands_user_home_output_dir(monkeypatch):
    captured = {}

    class FakeRunner:
        def run_once(self, *_args, **kwargs):
            captured["output_dir"] = kwargs["output_dir"]
            return SimpleNamespace(
                queried_count=0,
                delete_success_count=0,
                delete_failure_count=0,
                remaining_count=0,
                reappeared_count=0,
                audit_path=None,
                audit_error="",
                history_error="",
                error="",
            )

    monkeypatch.setattr("aruba_mm_cleanup.cli.MmCleanupRunner", lambda: FakeRunner())

    result = cli_main(
        [
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
            "--output-dir",
            "~/aruba-cli-output",
            "--yes",
        ]
    )

    assert result == 0
    assert captured["output_dir"] == Path.home() / "aruba-cli-output"


def test_cli_strips_host_before_connecting(monkeypatch):
    captured = {}

    class FakeRunner:
        def run_once(self, config, *_args, **_kwargs):
            captured["host"] = config.host
            return SimpleNamespace(
                queried_count=0,
                delete_success_count=0,
                delete_failure_count=0,
                remaining_count=0,
                reappeared_count=0,
                audit_path=None,
                audit_error="",
                history_error="",
                error="",
            )

    monkeypatch.setattr("aruba_mm_cleanup.cli.MmCleanupRunner", lambda: FakeRunner())

    result = cli_main(
        [
            "--host",
            " 192.0.2.10 ",
            "--username",
            "admin",
            "--password",
            "secret",
            "--yes",
        ]
    )

    assert result == 0
    assert captured["host"] == "192.0.2.10"


def test_cli_strips_username_before_connecting(monkeypatch):
    captured = {}

    class FakeRunner:
        def run_once(self, config, *_args, **_kwargs):
            captured["username"] = config.username
            return SimpleNamespace(
                queried_count=0,
                delete_success_count=0,
                delete_failure_count=0,
                remaining_count=0,
                reappeared_count=0,
                audit_path=None,
                audit_error="",
                history_error="",
                error="",
            )

    monkeypatch.setattr("aruba_mm_cleanup.cli.MmCleanupRunner", lambda: FakeRunner())

    result = cli_main(
        [
            "--host",
            "192.0.2.10",
            "--username",
            " admin ",
            "--password",
            "secret",
            "--yes",
        ]
    )

    assert result == 0
    assert captured["username"] == "admin"


def test_cli_strips_role_before_running(monkeypatch):
    captured = {}

    class FakeRunner:
        def run_once(self, _config, settings, **_kwargs):
            captured["role"] = settings.role
            return SimpleNamespace(
                queried_count=0,
                delete_success_count=0,
                delete_failure_count=0,
                remaining_count=0,
                reappeared_count=0,
                audit_path=None,
                audit_error="",
                history_error="",
                error="",
            )

    monkeypatch.setattr("aruba_mm_cleanup.cli.MmCleanupRunner", lambda: FakeRunner())

    result = cli_main(
        [
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password",
            "secret",
            "--role",
            " profiling ",
            "--yes",
        ]
    )

    assert result == 0
    assert captured["role"] == "profiling"


def test_windows_build_and_docs_reference_current_exe_names():
    repo_root = Path(__file__).parents[1]
    build_script = (repo_root / "build_windows_gui_exe.ps1").read_text(encoding="utf-8")
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    release_notes = (repo_root / "RELEASE_NOTES.md").read_text(encoding="utf-8")

    for text in (build_script, readme, release_notes):
        assert "ArubaMMCleanupGUI" in text
        assert "ArubaMMCleanupCLI" in text
    assert "aruba-mm-cleanup_vYYYY.MM.DD-HHMMSS_windows.zip" in readme
    assert "python .\\tools\\verify_release_package.py --dist .\\dist --smoke-cli --smoke-gui" in readme
    assert '-c ".\\constraints.txt"' in build_script
    assert "-m pip check" in build_script
    assert "pip install failed with exit code $LASTEXITCODE" in build_script
    assert "pip check failed with exit code $LASTEXITCODE" in build_script
    assert "version lookup failed with exit code $LASTEXITCODE" in build_script


def test_github_actions_release_contract():
    repo_root = Path(__file__).parents[1]
    pr_workflow = (repo_root / ".github" / "workflows" / "pr-validation.yml").read_text(encoding="utf-8")
    release_workflow = (repo_root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "pull_request:" in pr_workflow
    assert "gh release" not in pr_workflow
    assert "push:" in release_workflow
    assert "branches: [main]" in release_workflow
    assert "Korea Standard Time" in release_workflow
    assert "yyyy.MM.dd-HHmmss" in release_workflow
    assert 'aruba-mm-cleanup_${candidate}_windows.zip' in release_workflow
    assert "Get-FileHash -Algorithm SHA256" not in release_workflow
    assert ".sha256" not in release_workflow
    assert "--sha256" not in release_workflow
    assert "gh release create" in release_workflow
    assert '--title "Aruba MM Cleanup ${{ steps.metadata.outputs.tag }}"' in release_workflow
    assert "--draft=false" in release_workflow
    assert "# Aruba MM Cleanup $tag" in release_workflow
    assert "## 변경 내용" in release_workflow
    assert 'git log --format="%H"' in release_workflow
    assert "Release-Note-KO:" in release_workflow
    assert "Korean release note is required" in release_workflow
    assert '$releaseNote -notmatch "[가-힣]"' in release_workflow
    assert '$changeSummary.Add("- $releaseNote ($shortHash)")' in release_workflow
    assert "CHANGELOG.md" not in release_workflow
    assert "--smoke-cli --smoke-gui --require-cli-smoke --require-gui-smoke" in release_workflow
    assert "## 검증" not in release_workflow
    assert "## 첨부 파일" not in release_workflow
    assert "- Windows ZIP: $assetName" not in release_workflow
    assert "- GUI 실행 파일: ArubaMMCleanupGUI.exe" not in release_workflow
    assert "- CLI 실행 파일: ArubaMMCleanupCLI.exe" not in release_workflow
    assert "세부 커밋 및 변경 파일" not in release_workflow
    assert "### 원본 커밋 목록" not in release_workflow
    assert "### 변경 파일" not in release_workflow


def test_package_metadata_versions_and_dependencies_do_not_drift():
    repo_root = Path(__file__).parents[1]
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    init_py = (repo_root / "src" / "aruba_mm_cleanup" / "__init__.py").read_text(encoding="utf-8")
    setup_cfg = configparser.ConfigParser()
    setup_cfg.read(repo_root / "setup.cfg", encoding="utf-8")
    constraints = (repo_root / "constraints.txt").read_text(encoding="utf-8")

    pyproject_version = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE).group(1)
    init_version = re.search(r'^__version__ = "([^"]+)"$', init_py, re.MULTILINE).group(1)

    assert pyproject_version == setup_cfg["metadata"]["version"] == init_version
    assert '"netmiko>=4.3.0"' in pyproject
    assert "netmiko>=4.3.0" in setup_cfg["options"]["install_requires"]
    assert '"pyinstaller>=6.0"' in pyproject
    assert "pyinstaller>=6.0" in setup_cfg["options.extras_require"]["dev"]
    assert "netmiko==4.6.0" in constraints
    assert "pyinstaller==6.21.0" in constraints
    assert "pytest==8.4.2" in constraints
