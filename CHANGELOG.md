# Changelog

## 0.1.0

- Windows 11 GUI 대시보드에서 Aruba MM profiling Role MAC 조회와 삭제를 실행할 수 있습니다.
- `show global-user-table list role <role>` 조회 후 `aaa user delete mac <mac>` 삭제 명령을 실행합니다.
- 같은 MAC이 여러 줄에서 발견되어도 정규화된 MAC 기준으로 삭제 명령은 한 번만 실행합니다.
- GUI는 프로그램 실행 중 같은 장비 세션을 유지하고, 종료 또는 수동 연결 해제 시 세션을 닫습니다.
- 수동 1회 실행과 주기 실행에서 조회 수, 삭제 성공/실패 수, 삭제 후 남은 MAC 수를 표시합니다.
- 실행 중에는 장비/타임아웃/주기 설정 영역을 접고, 결과와 최근 삭제 이력 영역을 넓게 표시합니다.
- 최근 삭제 이력을 화면에 유지해 이전 실행에서 어떤 MAC을 삭제했는지 확인할 수 있습니다.
- GitHub Actions에서 Windows ZIP 패키지를 빌드, 검증, 공개 Release로 자동 배포합니다.
