"""Lightweight browser UI for Aruba MM cleanup runs."""

from __future__ import annotations

import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from typing import Optional
from urllib.parse import parse_qs
import webbrowser

from .cleanup import MmCleanupRunner
from .web_support import (
    cleanup_settings_from_request,
    connection_config_from_request,
    parse_run_request,
    smoke_status,
    summary_view,
)


DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8765


class WebAppState:
    def __init__(self, *, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.runner = MmCleanupRunner(persistent_session=True)
        self.notice = ""
        self.error = ""
        self.last_summary: dict[str, object] = {}
        self.cumulative_queried_count = 0
        self.cumulative_deleted_count = 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="aruba-mm-cleanup-web", description="Aruba MM Cleanup web app.")
    parser.add_argument("--host", default=DEFAULT_WEB_HOST, help="web server bind address")
    parser.add_argument("--port", type=int, default=DEFAULT_WEB_PORT, help="web server port")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"), help="default audit output directory")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    parser.add_argument("--smoke", action="store_true", help="verify that the web app executable starts")
    args = parser.parse_args(argv)

    if args.smoke:
        print(smoke_status())
        return 0
    if args.port < 1 or args.port > 65535:
        parser.error("--port must be between 1 and 65535")

    state = WebAppState(output_dir=args.output_dir.expanduser())
    handler_class = _make_handler(state)
    server = ThreadingHTTPServer((args.host, args.port), handler_class)
    url = f"http://{args.host}:{args.port}/"
    print(f"Aruba MM Cleanup web app: {url}")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            state.runner.close_session(reason="web_app_shutdown")
        except Exception:
            pass
        server.server_close()
    return 0


def _make_handler(state: WebAppState):
    class ArubaMmCleanupWebHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._send_text("ok")
                return
            self._send_html(_render_page(state))

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/disconnect":
                state.runner.close_session(reason="web_manual")
                state.notice = "장비 세션 연결을 해제했습니다."
                state.error = ""
                self._send_html(_render_page(state))
                return
            if self.path != "/run":
                self.send_error(404)
                return
            try:
                form = self._read_form()
                request = parse_run_request(form, default_output_dir=state.output_dir)
                summary = state.runner.run_once(
                    connection_config_from_request(request),
                    cleanup_settings_from_request(request),
                    output_dir=request.output_dir.expanduser(),
                )
                state.last_summary = summary_view(summary)
                state.cumulative_queried_count += _safe_int(state.last_summary.get("queried_count", 0))
                state.cumulative_deleted_count += _safe_int(state.last_summary.get("delete_success_count", 0))
                state.notice = "작업이 완료되었습니다."
                state.error = _safe_summary_error(state.last_summary)
            except Exception as exc:
                state.notice = ""
                state.error = str(exc) or exc.__class__.__name__
            self._send_html(_render_page(state))

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_form(self) -> dict[str, list[str]]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(max(length, 0)).decode("utf-8", errors="replace")
            return parse_qs(raw, keep_blank_values=True)

        def _send_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_text(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return ArubaMmCleanupWebHandler


def _render_page(state: WebAppState) -> str:
    summary = state.last_summary
    notice = f'<div class="notice">{escape(state.notice)}</div>' if state.notice else ""
    error = f'<div class="error">{escape(state.error)}</div>' if state.error else ""
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Aruba MM Cleanup</title>
  <style>
    body {{ margin: 0; font-family: Segoe UI, Apple SD Gothic Neo, sans-serif; background: #f4f6f8; color: #1f2933; }}
    header {{ background: #12343b; color: #fff; padding: 18px 28px; }}
    main {{ display: grid; grid-template-columns: minmax(280px, 380px) 1fr; gap: 20px; padding: 20px; }}
    section {{ background: #fff; border: 1px solid #d8dee4; border-radius: 8px; padding: 18px; }}
    label {{ display: block; font-size: 13px; font-weight: 600; margin-top: 12px; }}
    input {{ width: 100%; box-sizing: border-box; padding: 9px 10px; border: 1px solid #b8c2cc; border-radius: 6px; }}
    button {{ margin-top: 14px; padding: 10px 12px; border: 0; border-radius: 6px; background: #126e82; color: #fff; font-weight: 700; cursor: pointer; }}
    button.secondary {{ background: #4b5563; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }}
    .card {{ border: 1px solid #d8dee4; border-radius: 8px; padding: 14px; background: #fbfcfd; }}
    .value {{ font-size: 28px; font-weight: 800; margin-top: 6px; }}
    .notice {{ background: #e8f7ee; border: 1px solid #9bd3ad; padding: 10px; border-radius: 6px; margin-bottom: 12px; }}
    .error {{ background: #fdecec; border: 1px solid #f0a3a3; padding: 10px; border-radius: 6px; margin-bottom: 12px; }}
    .path {{ word-break: break-all; font-family: Consolas, monospace; font-size: 12px; }}
    @media (max-width: 760px) {{ main {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Aruba MM Cleanup</h1>
  </header>
  <main>
    <section>
      <h2>장비 접속</h2>
      {notice}
      {error}
      <form method="post" action="/run">
        <label>MM 주소</label>
        <input name="host" autocomplete="off" required>
        <label>SSH 포트</label>
        <input name="port" value="22" inputmode="numeric">
        <label>계정</label>
        <input name="username" autocomplete="username" required>
        <label>암호</label>
        <input name="password" type="password" autocomplete="current-password" required>
        <label>Enable 암호</label>
        <input name="enable_password" type="password">
        <label>Role</label>
        <input name="role" value="profiling">
        <label>장비 응답 대기(초)</label>
        <input name="timeout" value="60" inputmode="numeric">
        <label>결과 폴더</label>
        <input name="output_dir" value="{escape(str(state.output_dir))}">
        <button type="submit">1회 실행</button>
      </form>
      <form method="post" action="/disconnect">
        <button class="secondary" type="submit">세션 연결 해제</button>
      </form>
    </section>
    <section>
      <h2>작업 결과</h2>
      <div class="cards">
        {_metric_card("누적 조회 MAC", state.cumulative_queried_count)}
        {_metric_card("누적 삭제 MAC", state.cumulative_deleted_count)}
      </div>
      <h3>최근 실행</h3>
      <p>조회 {escape(str(summary.get("queried_count", 0)))} / 삭제 {escape(str(summary.get("delete_success_count", 0)))} / 실패 {escape(str(summary.get("delete_failure_count", 0)))} / 남은 MAC {escape(str(summary.get("remaining_count", 0)))} / 재조회 {escape(str(summary.get("reappeared_count", 0)))}</p>
      <h3>저장 경로</h3>
      <p class="path">Audit: {escape(str(summary.get("audit_path", "")))}</p>
      <p class="path">History: {escape(str(summary.get("history_path", "")))}</p>
    </section>
  </main>
</body>
</html>"""


def _metric_card(label: str, value: object) -> str:
    return f'<div class="card"><div>{escape(label)}</div><div class="value">{escape(str(value))}</div></div>'


def _safe_summary_error(summary: dict[str, object]) -> str:
    for key in ("error", "audit_error", "history_error"):
        value = summary.get(key, "")
        if value:
            return str(value)
    return ""


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
