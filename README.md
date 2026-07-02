# Aruba MM Cleanup

Aruba Mobility Master/MM에서 `profiling` Role 사용자 MAC을 조회하고, 60초 확인 대기 후 자동으로 `aaa user delete mac <mac>`을 실행하는 Windows 11 운영 대시보드입니다.

## Windows 11 사용 방법

1. GitHub Release에서 아래 두 파일을 다운로드합니다.
   - `aruba-mm-cleanup_vYYYY.MM.DD-HHMMSS_windows.zip`
   - `aruba-mm-cleanup_vYYYY.MM.DD-HHMMSS_windows.zip.sha256`
2. ZIP 파일 압축을 풉니다.
3. `ArubaMMCleanupGUI.exe`를 실행합니다.
4. CLI가 필요하면 같은 폴더의 `ArubaMMCleanupCLI.exe`를 사용합니다.
5. MM IP, 계정, 암호, Role을 입력합니다. Role 기본값은 `profiling`입니다.
6. `1회 실행`을 누르면 조회 후 60초 카운트다운이 시작됩니다.
7. 60초 안에 중단하려면 `이번 삭제 취소`를 누릅니다.
8. 주기적으로 반복하려면 `주기(초)`를 설정하고 `주기 실행 시작`을 누릅니다.

## 동작 흐름

- 조회 명령: `show global-user-table list role <role>`
- 삭제 명령: `aaa user delete mac <mac>`
- 삭제 대상은 조회 snapshot에서 파싱된 사용자 MAC만 사용합니다.
- BSSID/AP 등 다른 컬럼의 MAC-like 값은 삭제 대상으로 사용하지 않습니다.
- 삭제 후 같은 조회 명령을 다시 실행해 남은 MAC 수를 표시합니다.
- raw 장비 출력은 저장하지 않고 실행 요약 JSON만 로컬 결과 폴더에 저장합니다.

## 로컬 개발

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m compileall src
```

Windows 패키지 빌드:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows_gui_exe.ps1
python .\tools\verify_release_package.py --dist .\dist --smoke-cli
```

macOS에서는 Windows EXE smoke test가 건너뛰어집니다. 최종 EXE 검증은 Windows 11 PC 또는 GitHub Actions Windows runner에서 수행해야 합니다.

## 보안 주의

이 프로그램은 실제 사용자 세션을 삭제하는 운영 도구입니다. 실제 MM 접속 테스트는 운영자가 명시적으로 실행해야 하며, Codex/자동 테스트는 fixture와 fake connection만 사용합니다.
