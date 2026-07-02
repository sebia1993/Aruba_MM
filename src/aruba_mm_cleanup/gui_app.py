"""Tkinter GUI for Windows operators."""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from .cleanup import MmCleanupRunner
from .models import CleanupSettings, MmConnectionConfig


APP_TITLE = "Aruba MM Cleanup Dashboard"
DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / "ArubaMMCleanup" / "outputs"
DEFAULT_ROLE = "profiling"
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60
DELETE_DELAY_SECONDS = 60

BG = "#f4f7fb"
PANEL = "#ffffff"
TEXT = "#172033"
MUTED = "#667085"
ACCENT = "#0f766e"
DANGER = "#b42318"
WARNING = "#b54708"
SUCCESS = "#027a48"
LINE = "#d7dee8"
CARD_BG = "#eef6f5"


class ArubaMmCleanupGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1120x760")
        self.minsize(980, 660)
        self.configure(bg=BG)

        self.event_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.scheduler_worker: Optional[threading.Thread] = None
        self.cancel_event = threading.Event()
        self.scheduler_stop_event = threading.Event()
        self.is_running = False
        self.scheduler_running = False
        self.runner = MmCleanupRunner(persistent_session=True)

        self.host_var = tk.StringVar()
        self.port_var = tk.StringVar(value="22")
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.enable_password_var = tk.StringVar()
        self.role_var = tk.StringVar(value=DEFAULT_ROLE)
        self.timeout_var = tk.StringVar(value="60")
        self.interval_var = tk.StringVar(value=str(DEFAULT_INTERVAL_SECONDS))
        self.output_dir_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.status_var = tk.StringVar(value="대기 중")
        self.countdown_var = tk.StringVar(value="-")
        self.next_run_var = tk.StringVar(value="-")
        self.counter_vars = {
            "queried": tk.StringVar(value="0"),
            "deleted": tk.StringVar(value="0"),
            "failed": tk.StringVar(value="0"),
            "remaining": tk.StringVar(value="0"),
        }

        self._build_styles()
        self._build_layout()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(150, self._drain_events)

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=30, font=("Segoe UI", 10), fieldbackground=PANEL, background=PANEL)
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10), foreground=TEXT)
        style.map("Treeview", background=[("selected", "#d9f2ee")], foreground=[("selected", TEXT)])

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = tk.Frame(self, bg="#0f172a", width=238)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        tk.Label(
            sidebar,
            text="Aruba MM\nCleanup",
            bg="#0f172a",
            fg="#ffffff",
            justify="left",
            font=("Segoe UI Semibold", 20),
        ).pack(anchor="w", padx=20, pady=(24, 8))
        tk.Label(
            sidebar,
            text="profiling role user cleanup",
            bg="#0f172a",
            fg="#cbd5e1",
            justify="left",
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=20)
        self.manual_button = self._sidebar_button(sidebar, "1회 실행", self.start_manual_run)
        self.manual_button.pack(fill="x", padx=14, pady=(28, 8))
        self.schedule_button = self._sidebar_button(sidebar, "주기 실행 시작", self.start_scheduler)
        self.schedule_button.pack(fill="x", padx=14, pady=8)
        self.stop_schedule_button = self._sidebar_button(sidebar, "주기 실행 정지", self.stop_scheduler, state="disabled")
        self.stop_schedule_button.pack(fill="x", padx=14, pady=8)
        self.disconnect_button = self._sidebar_button(sidebar, "세션 연결 해제", self.disconnect_session)
        self.disconnect_button.pack(fill="x", padx=14, pady=8)

        main = tk.Frame(self, bg=BG)
        main.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(3, weight=1)

        self._build_header(main)
        self._build_settings(main)
        self._build_cards(main)
        self._build_results(main)
        self._build_log(main)

    def _sidebar_button(self, parent: tk.Widget, text: str, command, state: str = "normal") -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            state=state,
            bg=ACCENT,
            fg="#ffffff",
            disabledforeground="#94a3b8",
            activebackground="#115e59",
            activeforeground="#ffffff",
            relief="flat",
            font=("Segoe UI Semibold", 11),
            padx=12,
            pady=10,
            cursor="hand2",
        )

    def _build_header(self, parent: tk.Widget) -> None:
        frame = self._panel(parent)
        frame.grid(row=0, column=0, sticky="ew")
        frame.grid_columnconfigure(0, weight=1)
        tk.Label(frame, text=APP_TITLE, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 18)).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 2)
        )
        tk.Label(
            frame,
            textvariable=self.status_var,
            bg=PANEL,
            fg=ACCENT,
            font=("Segoe UI Semibold", 11),
        ).grid(row=0, column=1, sticky="e", padx=16, pady=(14, 2))
        tk.Label(
            frame,
            text="조회 후 60초 동안 취소할 수 있고, 시간이 지나면 조회 snapshot의 MAC만 삭제합니다.",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 14))

    def _build_settings(self, parent: tk.Widget) -> None:
        frame = self._panel(parent)
        frame.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        for column in range(6):
            frame.grid_columnconfigure(column, weight=1)

        self._entry(frame, "MM IP/Host", self.host_var, 0, 0)
        self._entry(frame, "Port", self.port_var, 0, 1, width=7)
        self._entry(frame, "계정", self.username_var, 0, 2)
        self._entry(frame, "암호", self.password_var, 0, 3, show="*")
        self._entry(frame, "Enable 암호", self.enable_password_var, 0, 4, show="*")
        self._entry(frame, "Role", self.role_var, 0, 5)
        self._entry(frame, "Timeout", self.timeout_var, 2, 0, width=7)
        self._entry(frame, "주기(초)", self.interval_var, 2, 1, width=8)
        tk.Label(frame, text="결과 폴더", bg=PANEL, fg=MUTED, font=("Segoe UI Semibold", 9)).grid(
            row=2, column=2, sticky="w", padx=12, pady=(10, 2)
        )
        tk.Entry(frame, textvariable=self.output_dir_var, relief="solid", bd=1, font=("Segoe UI", 10)).grid(
            row=3, column=2, columnspan=3, sticky="ew", padx=12, pady=(0, 14)
        )
        tk.Button(
            frame,
            text="폴더 선택",
            command=self.browse_output_dir,
            bg="#e2e8f0",
            fg=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
        ).grid(row=3, column=5, sticky="ew", padx=12, pady=(0, 14))

    def _entry(
        self,
        parent: tk.Widget,
        label: str,
        variable: tk.StringVar,
        row: int,
        column: int,
        *,
        show: str = "",
        width: int = 16,
    ) -> None:
        tk.Label(parent, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI Semibold", 9)).grid(
            row=row, column=column, sticky="w", padx=12, pady=(12, 2)
        )
        tk.Entry(parent, textvariable=variable, show=show, width=width, relief="solid", bd=1, font=("Segoe UI", 10)).grid(
            row=row + 1, column=column, sticky="ew", padx=12, pady=(0, 12)
        )

    def _build_cards(self, parent: tk.Widget) -> None:
        frame = tk.Frame(parent, bg=BG)
        frame.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        for column in range(5):
            frame.grid_columnconfigure(column, weight=1, uniform="cards")
        self._card(frame, "조회", self.counter_vars["queried"], 0, ACCENT)
        self._card(frame, "삭제 성공", self.counter_vars["deleted"], 1, SUCCESS)
        self._card(frame, "삭제 실패", self.counter_vars["failed"], 2, DANGER)
        self._card(frame, "남은 MAC", self.counter_vars["remaining"], 3, WARNING)
        self._card(frame, "카운트다운", self.countdown_var, 4, TEXT)

    def _card(self, parent: tk.Widget, title: str, variable: tk.StringVar, column: int, color: str) -> None:
        card = tk.Frame(parent, bg=CARD_BG, highlightbackground=LINE, highlightthickness=1)
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        tk.Label(card, text=title, bg=CARD_BG, fg=MUTED, font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=14, pady=(10, 0))
        tk.Label(card, textvariable=variable, bg=CARD_BG, fg=color, font=("Segoe UI Semibold", 24)).pack(
            anchor="w", padx=14, pady=(0, 10)
        )

    def _build_results(self, parent: tk.Widget) -> None:
        frame = self._panel(parent)
        frame.grid(row=3, column=0, sticky="nsew", pady=(14, 0))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        top = tk.Frame(frame, bg=PANEL)
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        tk.Label(top, text="삭제 대상 및 결과", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(side="left")
        tk.Label(top, textvariable=self.next_run_var, bg=PANEL, fg=MUTED, font=("Segoe UI", 10)).pack(side="right")
        columns = ("mac", "status", "queried_at", "deleted_at", "error")
        self.table = ttk.Treeview(frame, columns=columns, show="headings", height=9)
        headings = {
            "mac": "MAC",
            "status": "상태",
            "queried_at": "조회시각",
            "deleted_at": "삭제시각",
            "error": "오류",
        }
        widths = {"mac": 150, "status": 120, "queried_at": 150, "deleted_at": 150, "error": 360}
        for key in columns:
            self.table.heading(key, text=headings[key])
            self.table.column(key, width=widths[key], anchor="w")
        self.table.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 12))

    def _build_log(self, parent: tk.Widget) -> None:
        frame = self._panel(parent)
        frame.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        frame.grid_columnconfigure(0, weight=1)
        button_row = tk.Frame(frame, bg=PANEL)
        button_row.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 0))
        self.cancel_button = tk.Button(
            button_row,
            text="이번 삭제 취소",
            command=self.cancel_current_delete,
            state="disabled",
            bg=DANGER,
            fg="#ffffff",
            relief="flat",
            font=("Segoe UI Semibold", 10),
        )
        self.cancel_button.pack(side="left")
        tk.Button(
            button_row,
            text="로그 지우기",
            command=self.clear_log,
            bg="#e2e8f0",
            fg=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
        ).pack(side="right")
        self.log_text = tk.Text(
            frame,
            height=7,
            bg="#111827",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            relief="flat",
            font=("Consolas", 10),
            wrap="word",
        )
        self.log_text.grid(row=1, column=0, sticky="ew", padx=14, pady=10)

    def _panel(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1)

    def browse_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_dir_var.get() or str(DEFAULT_OUTPUT_DIR))
        if selected:
            self.output_dir_var.set(selected)

    def start_manual_run(self) -> None:
        if self.is_running:
            return
        try:
            config, settings, output_dir = self._read_inputs()
        except ValueError as exc:
            messagebox.showerror("입력 오류", str(exc))
            return
        self.cancel_event.clear()
        self._set_running(True)
        self.worker = threading.Thread(
            target=self._run_once_worker,
            args=(config, settings, output_dir),
            daemon=True,
        )
        self.worker.start()

    def start_scheduler(self) -> None:
        if self.scheduler_running:
            return
        try:
            config, settings, output_dir = self._read_inputs()
            interval = self._read_interval()
        except ValueError as exc:
            messagebox.showerror("입력 오류", str(exc))
            return
        self.scheduler_stop_event.clear()
        self.scheduler_running = True
        self.schedule_button.configure(state="disabled")
        self.stop_schedule_button.configure(state="normal")
        self._log(f"주기 실행 시작: {interval}초 간격")
        self.scheduler_worker = threading.Thread(
            target=self._scheduler_loop,
            args=(config, settings, output_dir, interval),
            daemon=True,
        )
        self.scheduler_worker.start()

    def stop_scheduler(self) -> None:
        self.scheduler_stop_event.set()
        self.scheduler_running = False
        self.schedule_button.configure(state="normal")
        self.stop_schedule_button.configure(state="disabled")
        self.next_run_var.set("-")
        self._log("주기 실행 정지 요청")

    def cancel_current_delete(self) -> None:
        self.cancel_event.set()
        self._log("이번 삭제 취소 요청")

    def disconnect_session(self) -> None:
        if self.is_running:
            self._log("실행 중에는 세션 연결 해제를 건너뜁니다.")
            return
        self.runner.close_session(
            progress_callback=lambda event, payload: self.event_queue.put(("progress", (event, payload))),
            reason="manual",
        )
        self.status_var.set("세션 연결 해제")
        self._log("SESSION DISCONNECT REQUEST")

    def on_close(self) -> None:
        self.scheduler_stop_event.set()
        self.cancel_event.set()
        self.runner.close_session(reason="app_close")
        self.destroy()

    def _scheduler_loop(
        self,
        config: MmConnectionConfig,
        settings: CleanupSettings,
        output_dir: Path,
        interval: int,
    ) -> None:
        while not self.scheduler_stop_event.is_set():
            self.cancel_event.clear()
            self.event_queue.put(("running", True))
            try:
                self._run_summary(config, settings, output_dir)
            finally:
                self.event_queue.put(("running", False))
            for remaining in range(interval, 0, -1):
                if self.scheduler_stop_event.is_set():
                    break
                self.event_queue.put(("next_run", f"다음 실행: {remaining}초 후"))
                time.sleep(1)
        self.event_queue.put(("scheduler_stopped", None))

    def _run_once_worker(self, config: MmConnectionConfig, settings: CleanupSettings, output_dir: Path) -> None:
        try:
            self._run_summary(config, settings, output_dir)
        finally:
            self.event_queue.put(("running", False))

    def _run_summary(self, config: MmConnectionConfig, settings: CleanupSettings, output_dir: Path) -> None:
        def progress(event: str, payload: dict[str, object]) -> None:
            self.event_queue.put(("progress", (event, payload)))

        summary = self.runner.run_once(
            config,
            settings,
            output_dir=output_dir,
            progress_callback=progress,
            should_cancel=self.cancel_event.is_set,
        )
        self.event_queue.put(("summary", summary))

    def _read_inputs(self) -> tuple[MmConnectionConfig, CleanupSettings, Path]:
        host = self.host_var.get().strip()
        if not host:
            raise ValueError("MM IP/Host를 입력하세요.")
        username = self.username_var.get().strip()
        if not username:
            raise ValueError("계정을 입력하세요.")
        password = self.password_var.get()
        if not password:
            raise ValueError("암호를 입력하세요.")
        try:
            port = int(self.port_var.get().strip() or "22")
        except ValueError as exc:
            raise ValueError("Port는 숫자로 입력하세요.") from exc
        try:
            timeout = max(5, int(self.timeout_var.get().strip() or "60"))
        except ValueError as exc:
            raise ValueError("Timeout은 숫자로 입력하세요.") from exc
        config = MmConnectionConfig(
            host=host,
            username=username,
            password=password,
            port=port,
            enable_password=self.enable_password_var.get(),
        )
        settings = CleanupSettings(
            role=self.role_var.get().strip() or DEFAULT_ROLE,
            timeout=timeout,
            delete_delay_seconds=DELETE_DELAY_SECONDS,
        )
        output_dir = Path(self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR)
        return config, settings, output_dir

    def _read_interval(self) -> int:
        try:
            return max(MIN_INTERVAL_SECONDS, int(self.interval_var.get().strip() or str(DEFAULT_INTERVAL_SECONDS)))
        except ValueError as exc:
            raise ValueError("주기(초)는 숫자로 입력하세요.") from exc

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.event_queue.get_nowait()
                if event == "running":
                    self._set_running(bool(payload))
                elif event == "progress":
                    progress_event, progress_payload = payload
                    self._handle_progress(str(progress_event), progress_payload)
                elif event == "summary":
                    self._handle_summary(payload)
                elif event == "next_run":
                    self.next_run_var.set(str(payload))
                elif event == "scheduler_stopped":
                    self.scheduler_running = False
                    self.schedule_button.configure(state="normal")
                    self.stop_schedule_button.configure(state="disabled")
                    self.next_run_var.set("-")
        except queue.Empty:
            pass
        self.after(150, self._drain_events)

    def _handle_progress(self, event: str, payload: dict[str, object]) -> None:
        if event == "connect_start":
            self.status_var.set("MM 접속 중")
            self._log(f"CONNECT: {payload.get('host')}")
        elif event == "connect_done":
            self.status_var.set("MM 세션 연결됨")
            self._log(f"CONNECT OK: {payload.get('host')}")
        elif event == "session_reconnect_start":
            self.status_var.set("MM 세션 재접속 중")
            self._log(f"RECONNECT: {payload.get('command')} | {payload.get('error')}")
        elif event == "session_disconnected":
            self.status_var.set("세션 연결 해제")
            self._log(f"DISCONNECT: {payload.get('reason')}")
        elif event == "warning":
            self._log(f"WARNING: {payload.get('message')}")
        elif event == "query_start":
            self.status_var.set("global-user-table 조회 중")
            self._log(f"QUERY: {payload.get('command')}")
        elif event == "query_done":
            macs = list(payload.get("macs") or [])
            self.counter_vars["queried"].set(str(payload.get("count", 0)))
            self._replace_table(macs, "삭제 대기")
            self._log(f"QUERY DONE: {payload.get('count', 0)} MAC(s)")
        elif event == "countdown":
            remaining = int(payload.get("remaining", 0))
            self.countdown_var.set(f"{remaining}s")
            self.status_var.set(f"{remaining}초 후 자동 삭제")
            self.cancel_button.configure(state="normal" if remaining > 0 else "disabled")
        elif event == "delete_start":
            self.status_var.set("MAC 삭제 중")
            self._set_row_status(str(payload.get("mac")), "삭제 중", "")
            self._log(f"DELETE START: {payload.get('mac')}")
        elif event == "delete_done":
            self._set_row_status(str(payload.get("mac")), "삭제 완료", "")
            self._log(f"DELETE OK: {payload.get('mac')}")
        elif event == "delete_error":
            self._set_row_status(str(payload.get("mac")), "삭제 실패", str(payload.get("error") or ""))
            self._log(f"DELETE ERROR: {payload.get('mac')} | {payload.get('error')}")
        elif event == "delete_canceled":
            self.status_var.set("이번 삭제 취소됨")
            self.countdown_var.set("-")
            self.cancel_button.configure(state="disabled")
            self._set_all_pending_status("취소됨")
            self._log(f"CANCELED: {payload.get('count')} pending MAC(s)")
        elif event == "run_error":
            self.status_var.set("실패")
            self.cancel_button.configure(state="disabled")
            self._log(f"ERROR: {payload.get('error')}")

    def _handle_summary(self, summary) -> None:
        self.counter_vars["queried"].set(str(summary.queried_count))
        self.counter_vars["deleted"].set(str(summary.delete_success_count))
        self.counter_vars["failed"].set(str(summary.delete_failure_count))
        self.counter_vars["remaining"].set(str(summary.remaining_count))
        self.countdown_var.set("-")
        self.cancel_button.configure(state="disabled")
        if summary.error:
            self.status_var.set("실패")
        elif summary.canceled:
            self.status_var.set("취소됨")
        else:
            self.status_var.set("완료")
        if summary.audit_path:
            self._log(f"AUDIT: {summary.audit_path}")

    def _replace_table(self, macs: list[str], status: str) -> None:
        self.table.delete(*self.table.get_children())
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for mac in macs:
            self.table.insert("", "end", iid=mac, values=(mac, status, now, "", ""))

    def _set_row_status(self, mac: str, status: str, error: str) -> None:
        if not mac or not self.table.exists(mac):
            return
        values = list(self.table.item(mac, "values"))
        values[1] = status
        if status in {"삭제 완료", "삭제 실패"}:
            values[3] = time.strftime("%Y-%m-%d %H:%M:%S")
        values[4] = error
        self.table.item(mac, values=values)

    def _set_all_pending_status(self, status: str) -> None:
        for item_id in self.table.get_children():
            values = list(self.table.item(item_id, "values"))
            if values[1] in {"삭제 대기", "삭제 중"}:
                values[1] = status
                self.table.item(item_id, values=values)

    def _set_running(self, running: bool) -> None:
        self.is_running = running
        self.manual_button.configure(state="disabled" if running else "normal")
        if running:
            self.cancel_button.configure(state="disabled")

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{time.strftime('%H:%M:%S')} {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")


def main() -> int:
    app = ArubaMmCleanupGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
