# Release Notes

이 파일은 저장소에 커밋하는 릴리즈 준비 점검 문서입니다. 실제 GitHub Release 본문은 `.github/workflows/release.yml`에서 `main` push 시 한국어로 자동 생성합니다.

## 배포 파일 형식

- `aruba-mm-cleanup_vYYYY.MM.DD-HHMMSS_windows.zip`

ZIP 포함 파일:

- `ArubaMMCleanupGUI.exe`
- `ArubaMMCleanupCLI.exe`
- `README.md`
- `USER_GUIDE_KO.md`
- `config/mock_scenarios/profiling_users.txt`

## 검증 명령

```powershell
python -m pytest
python -m compileall src
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\validate.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows_gui_exe.ps1
python .\tools\verify_release_package.py --dist .\dist --smoke-cli
```
