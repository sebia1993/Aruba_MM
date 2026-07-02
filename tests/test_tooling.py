import subprocess
import sys
import zipfile
import configparser
import re
from pathlib import Path

from aruba_mm_cleanup.cli import main as cli_main


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
    assert '$changeSummary.Add("- $subject ($shortHash)")' in release_workflow
    assert "CHANGELOG.md" not in release_workflow
    assert "## 검증" in release_workflow
    assert "- 기준 커밋: $sha" in release_workflow
    assert "- 실행한 검증 명령: powershell -NoProfile -ExecutionPolicy Bypass -File .\\tools\\validate.ps1" in release_workflow
    assert "- 실행한 빌드 명령: powershell -NoProfile -ExecutionPolicy Bypass -File .\\build_windows_gui_exe.ps1" in release_workflow
    assert "--smoke-cli --smoke-gui --require-cli-smoke --require-gui-smoke" in release_workflow
    assert "- 실행한 패키지 검증: python .\\tools\\verify_release_package.py --dist .\\dist --smoke-cli --smoke-gui --require-cli-smoke --require-gui-smoke" in release_workflow
    assert "## 첨부 파일" in release_workflow
    assert "- Windows ZIP: $assetName" in release_workflow
    assert "- GUI 실행 파일: ArubaMMCleanupGUI.exe" in release_workflow
    assert "- CLI 실행 파일: ArubaMMCleanupCLI.exe" in release_workflow
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
