# Release Notes 운영 규칙

이 파일은 저장소에 커밋하는 릴리즈 준비 점검 문서입니다. 실제 GitHub Release 본문은 `.github/workflows/release.yml`에서 `main` push 시 한국어로 자동 생성합니다.

## GitHub Release 본문 형식

자동 Release notes는 WLC Role ACL Collector 릴리즈와 같은 간결한 형식으로 생성합니다.

- 제목: `Aruba MM Cleanup vYYYY.MM.DD-HHMMSS`
- `변경 내용`: 한국어 커밋 제목 또는 `Release-Note-KO:` 값의 실제 변경 사항만 기재

검증 명령, 첨부 파일 설명, 세부 커밋 목록, 변경 파일 목록은 GitHub Release 본문에 기재하지 않습니다.

## 배포 파일 형식

- `aruba-mm-cleanup_vYYYY.MM.DD-HHMMSS_windows.zip`

ZIP 포함 파일:

- `ArubaMMCleanupGUI.exe`
- `ArubaMMCleanupCLI.exe`
- `README.md`
- `USER_GUIDE_KO.md`
- `config/mock_scenarios/profiling_users.txt`

SHA256 sidecar는 생성하거나 GitHub Release asset으로 업로드하지 않습니다.

## 검증 명령

```powershell
python -m pip install -e ".[dev]" -c .\constraints.txt
python -m pip check
python -m pytest
python -m compileall src
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\validate.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows_gui_exe.ps1
python .\tools\verify_release_package.py --dist .\dist --smoke-cli --smoke-gui --require-cli-smoke --require-gui-smoke
```
