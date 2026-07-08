# Release Notes 운영 규칙

이 파일은 저장소에 커밋하는 릴리즈 준비 점검 문서입니다. 실제 GitHub Release 본문은 `.github/workflows/release.yml`에서 `main` push 시 한국어로 자동 생성합니다.

## GitHub Release 본문 형식

자동 Release notes는 한국어로 생성하며 아래 정보를 포함합니다.

- 변경 내용
- 변경 커밋 목록
- 기준 커밋 SHA
- 브랜치명
- 실행한 검증 명령
- 실행한 빌드 명령
- 통합 ZIP 파일명
- SHA256 checksum
- GUI 실행 방법
- 웹앱 실행 방법
- `Source code (zip)` / `Source code (tar.gz)`는 일반 사용자 실행 파일이 아니라는 안내

## 배포 파일 형식

GitHub Release에 직접 업로드하는 사용자 다운로드 파일은 아래 ZIP 1개입니다.

- `aruba-mm-cleanup_vYYYY.MM.DD-HHMMSS_windows.zip`

GitHub가 자동으로 표시하는 `Source code (zip)` / `Source code (tar.gz)`는 제거할 수 없는 소스 아카이브이며 실행용 파일이 아닙니다.

## 통합 ZIP 포함 파일

```text
README_START_HERE_KO.txt
gui/
  ArubaMMCleanupGUI.exe
  USER_GUIDE_KO.md
  config/mock_scenarios/profiling_users.txt
web/
  ArubaMMCleanupWeb.exe
  start_webapp.cmd
  config/mock_scenarios/profiling_users.txt
```

- 최종 사용자용 ZIP에는 GUI와 웹앱만 포함합니다.
- CLI 실행 파일은 최종 사용자용 ZIP에 포함하지 않습니다.
- SHA256 sidecar는 생성하거나 GitHub Release asset으로 업로드하지 않고 Release notes 본문에 기록합니다.

## 검증 명령

```powershell
python -m pip install -e ".[dev]" -c .\constraints.txt
python -m pip check
python -m pytest
python -m compileall src
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\validate.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows_gui_exe.ps1
python .\tools\verify_release_package.py --dist .\dist --smoke-gui --smoke-web --require-gui-smoke --require-web-smoke
```
