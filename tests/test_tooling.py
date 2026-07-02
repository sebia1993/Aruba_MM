import subprocess
import sys
import zipfile
from pathlib import Path


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


def test_windows_build_and_docs_reference_current_exe_names():
    repo_root = Path(__file__).parents[1]
    build_script = (repo_root / "build_windows_gui_exe.ps1").read_text(encoding="utf-8")
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    release_notes = (repo_root / "RELEASE_NOTES.md").read_text(encoding="utf-8")

    for text in (build_script, readme, release_notes):
        assert "ArubaMMCleanupGUI" in text
        assert "ArubaMMCleanupCLI" in text
    assert "aruba-mm-cleanup_vYYYY.MM.DD-HHMMSS_windows.zip" in readme
    assert "python .\\tools\\verify_release_package.py --dist .\\dist --smoke-cli" in readme


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
    assert "CHANGELOG.md" in release_workflow
    assert "## 변경 내용" in release_workflow
    assert "## 검증" in release_workflow
    assert "- 기준 커밋: $sha" in release_workflow
    assert "- 실행한 검증 명령: powershell -NoProfile -ExecutionPolicy Bypass -File .\\tools\\validate.ps1" in release_workflow
    assert "- 실행한 빌드 명령: powershell -NoProfile -ExecutionPolicy Bypass -File .\\build_windows_gui_exe.ps1" in release_workflow
    assert "- 실행한 패키지 검증: python .\\tools\\verify_release_package.py --dist .\\dist --smoke-cli" in release_workflow
    assert "## 첨부 파일" in release_workflow
    assert "- Windows ZIP: $assetName" in release_workflow
    assert "- GUI 실행 파일: ArubaMMCleanupGUI.exe" in release_workflow
    assert "- CLI 실행 파일: ArubaMMCleanupCLI.exe" in release_workflow
    assert "세부 커밋 및 변경 파일" not in release_workflow
    assert "### 원본 커밋 목록" not in release_workflow
    assert "### 변경 파일" not in release_workflow
