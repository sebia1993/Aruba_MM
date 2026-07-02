# Aruba_MM

Aruba MM Cleanup은 Aruba Mobility Master/MM에서 `profiling` Role 사용자 MAC을 조회하고, 조회 직후 자동으로 `aaa user delete mac <mac>`을 실행하는 Windows 11 운영 대시보드입니다.

## Windows 11 사용 방법

1. GitHub Release에서 `aruba-mm-cleanup_vYYYY.MM.DD-HHMMSS_windows.zip` 파일을 다운로드합니다.
2. ZIP 파일 압축을 풉니다.
3. `ArubaMMCleanupGUI.exe`를 실행합니다.
4. CLI가 필요하면 같은 폴더의 `ArubaMMCleanupCLI.exe`를 사용합니다.
5. MM IP, 계정, 암호, Role을 입력합니다. Role 기본값은 `profiling`입니다.
6. `장비 응답 대기(초)`는 SSH 접속, 인증, 명령 응답을 기다리는 시간입니다.
7. `1회 실행`을 누르면 조회 후 즉시 삭제 단계로 진행합니다.
8. 주기적으로 반복하려면 `주기(초)`를 설정하고 `주기 실행 시작`을 누릅니다. `1`을 입력하면 다음 실행 대기도 1초로 적용됩니다.
9. 장비 세션을 즉시 끊고 싶으면 `세션 연결 해제`를 누릅니다.

## 동작 흐름

- 조회 명령: `show global-user-table list role <role>`
- 삭제 명령: `aaa user delete mac <mac>`
- 삭제 대상은 조회 snapshot에서 파싱된 사용자 MAC만 사용합니다.
- GUI는 조회 완료 후 즉시 삭제를 시작합니다. CLI만 `--delay`로 삭제 전 대기 시간을 조절할 수 있습니다.
- 장비 응답 대기 시간은 GUI의 `장비 응답 대기(초)` 또는 CLI의 `--timeout`으로 조절합니다.
- 같은 MAC이 여러 줄에서 발견되어도 정규화된 MAC 기준으로 삭제 명령은 한 번만 실행합니다.
- GUI 상단 카드는 `조회 MAC`, `삭제한 총 MAC`, `타이머`만 표시합니다.
- `조회 MAC`은 중복 제거된 삭제 대상 MAC 수이고, `삭제한 총 MAC`은 삭제 후 검증 조회에서 사라진 MAC 수입니다.
- 삭제 명령은 응답 실패 시 재시도하지 않고 `확인 필요`로 기록합니다. 장비에 명령이 들어갔지만 응답만 실패한 경우 같은 MAC 삭제 명령이 재전송되지 않게 하기 위한 정책입니다.
- 삭제 응답이 성공이고 삭제 후 검증 조회에서 사라진 MAC만 최종 삭제 성공으로 확정합니다.
- BSSID/AP 등 다른 컬럼의 MAC-like 값은 삭제 대상으로 사용하지 않습니다.
- 삭제 후 같은 조회 명령을 다시 실행해 상세 결과 테이블에서 남은 MAC과 실패/확인 필요 상태를 표시합니다.
- 삭제 성공으로 기록된 MAC이 검증 조회에서 다시 발견되면 `재조회됨`으로 강조하고 audit JSON에 `reappeared_macs`로 남깁니다. 이 경우 자동 재삭제는 하지 않습니다.
- GUI는 프로그램이 실행되는 동안 MM 세션을 유지하고 같은 접속 정보에서는 다음 실행에도 재사용합니다.
- 접속 정보가 바뀌거나 `세션 연결 해제`를 누르거나 프로그램을 종료하면 세션을 닫습니다.
- 실행 중 창을 닫으면 네트워크 timeout을 기다리지 않고 UI 종료를 진행하고, 남은 세션 정리는 백그라운드에서 처리합니다.
- 주기 실행 중에는 대기 시간에도 수동 1회 실행을 시작할 수 없습니다.
- 주기 실행 정지와 `이번 삭제 취소`는 다음 MAC 삭제 전과 검증 조회 전에 반영됩니다.
- 최근 삭제 이력은 UI 성능을 위해 최근 500개 행만 유지합니다.
- 최근 삭제 이력은 결과 폴더의 `deletion_history.jsonl`에 저장되고 프로그램 재시작 시 복원됩니다.
- 최근 삭제 이력은 `이력 전체 지우기` 버튼으로 화면에서 지울 수 있습니다.
- 로그창은 장시간 실행 중에도 최근 1000줄만 유지합니다.
- CLI는 한 번 실행할 때 연결을 열고 조회/삭제/검증을 마친 뒤 연결을 닫습니다.
- raw 장비 출력은 저장하지 않고 실행 요약 JSON에는 parser 선택/제외 reason만 저장합니다.
- 실행 요약 JSON 저장에 실패해도 조회/삭제 결과는 UI에 표시하고 warning 로그를 남깁니다.

## 로컬 개발

```powershell
python -m pip install -e ".[dev]" -c .\constraints.txt
python -m pip check
python -m pytest
python -m compileall src
```

Windows 패키지 빌드:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows_gui_exe.ps1
python .\tools\verify_release_package.py --dist .\dist --smoke-cli --smoke-gui
```

macOS에서는 Windows EXE smoke test가 건너뛰어집니다. 최종 EXE 검증은 Windows 11 PC 또는 GitHub Actions Windows runner에서 수행해야 합니다.

## 보안 주의

이 프로그램은 실제 사용자 세션을 삭제하는 운영 도구입니다. 실제 MM 접속 테스트는 운영자가 명시적으로 실행해야 하며, Codex/자동 테스트는 fixture와 fake connection만 사용합니다.
