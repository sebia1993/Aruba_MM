"""Tkinter GUI for Windows operators."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from .cleanup import MmCleanupRunner, build_query_command
from .models import CleanupSettings, MmConnectionConfig
from .parser import normalize_mac


APP_TITLE = "Aruba MM Cleanup Dashboard"
DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / "ArubaMMCleanup" / "outputs"
DEFAULT_ROLE = "profiling"
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 1
MAX_HISTORY_ROWS = 500
MAX_LOG_LINES = 1000
HISTORY_FILE_NAME = "deletion_history.jsonl"
SHUTDOWN_GRACE_MS = 250
TYPE_NA_MESSAGE = "Type=N/A: 관리자 직접 장비 지정 필요"

BG = "#f4f4f4"
PANEL = "#ffffff"
TEXT = "#171a20"
BODY = "#393c41"
MUTED = "#5c5e62"
ACCENT = "#3e6ae1"
DANGER = "#b42318"
DANGER_ACTIVE = "#8f1d14"
DANGER_SOFT = "#fff4f2"
LINE = "#eeeeee"
FIELD_BG = "#fafafa"
CARD_BG = "#ffffff"
DISABLED = "#8e8e8e"
SIDEBAR_BG = "#ffffff"
SECONDARY_BG = "#f4f4f4"
SECONDARY_ACTIVE = "#eeeeee"
LOG_BG = "#171a20"
LOG_TEXT = "#f4f4f4"


class ArubaMmCleanupGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1160x760")
        self.minsize(980, 660)
        self.configure(bg=BG)

        self.event_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.scheduler_worker: Optional[threading.Thread] = None
        self.cancel_event = threading.Event()
        self.scheduler_stop_event = threading.Event()
        self.is_running = False
        self.scheduler_running = False
        self.closing = False
        self.runner = MmCleanupRunner(persistent_session=True)
        self.runner_lock = threading.Lock()
        self.session_close_worker: Optional[threading.Thread] = None
        self.history_row_counter = 0
        self.settings_frame: Optional[tk.Frame] = None
        self.loaded_history_dir: Optional[Path] = None
        self._drain_after_id: Optional[str] = None
        self.copy_notice_after_id: Optional[str] = None
        self.copy_notice_frame: Optional[tk.Frame] = None
        self.cumulative_queried_count = 0
        self.cumulative_deleted_count = 0
        self.current_run_queried_count = 0
        self.current_run_query_counted = False
        self.current_run_delete_counted = False

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
        self.timer_value_var = tk.StringVar(value="-")
        self.timer_state_var = tk.StringVar(value="대기")
        self.copy_notice_title_var = tk.StringVar(value="")
        self.copy_notice_mac_var = tk.StringVar(value="")
        self.counter_vars = {
            "queried": tk.StringVar(value="0"),
            "deleted": tk.StringVar(value="0"),
        }

        self._build_styles()
        self._build_layout()
        self._load_history_from_output_dir(DEFAULT_OUTPUT_DIR)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._drain_after_id = self.after(150, self._drain_events)

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Treeview",
            rowheight=30,
            font=("Segoe UI", 10),
            fieldbackground=PANEL,
            background=PANEL,
            foreground=BODY,
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "Treeview.Heading",
            font=("Segoe UI Semibold", 10),
            foreground=TEXT,
            background=SECONDARY_BG,
            borderwidth=0,
            relief="flat",
        )
        style.map("Treeview", background=[("selected", SECONDARY_ACTIVE)], foreground=[("selected", TEXT)])

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = tk.Frame(self, bg=SIDEBAR_BG, width=220, highlightbackground=LINE, highlightthickness=1)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        tk.Label(
            sidebar,
            text="Aruba MM",
            bg=SIDEBAR_BG,
            fg=TEXT,
            justify="left",
            font=("Segoe UI Semibold", 20),
        ).pack(anchor="w", padx=20, pady=(24, 8))
        tk.Label(
            sidebar,
            text="Cleanup Dashboard",
            bg=SIDEBAR_BG,
            fg=MUTED,
            justify="left",
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=20)
        self.manual_button = self._sidebar_button(sidebar, "1회 실행", self.start_manual_run)
        self.manual_button.pack(fill="x", padx=14, pady=(28, 8))
        self.schedule_button = self._sidebar_button(sidebar, "주기 실행 시작", self.start_scheduler, variant="secondary")
        self.schedule_button.pack(fill="x", padx=14, pady=8)
        self.stop_schedule_button = self._sidebar_button(
            sidebar,
            "주기 실행 정지",
            self.stop_scheduler,
            state="disabled",
            variant="secondary",
        )
        self.stop_schedule_button.pack(fill="x", padx=14, pady=8)
        self.disconnect_button = self._sidebar_button(
            sidebar,
            "세션 연결 해제",
            self.disconnect_session,
            variant="secondary",
        )
        self.disconnect_button.pack(fill="x", padx=14, pady=8)

        main = tk.Frame(self, bg=BG)
        main.grid(row=0, column=1, sticky="nsew", padx=24, pady=24)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=0)
        main.grid_rowconfigure(3, weight=1)

        self._build_header(main)
        self._build_settings(main)
        self._build_cards(main)
        self._build_results(main)
        self._build_log(main)
        self._build_copy_notice_overlay()

    def _sidebar_button(
        self,
        parent: tk.Widget,
        text: str,
        command,
        state: str = "normal",
        *,
        variant: str = "primary",
    ) -> tk.Button:
        if variant == "primary":
            background = ACCENT
            foreground = "#ffffff"
            active_background = "#3457b1"
            active_foreground = "#ffffff"
        else:
            background = SECONDARY_BG
            foreground = TEXT
            active_background = SECONDARY_ACTIVE
            active_foreground = TEXT
        return tk.Button(
            parent,
            text=text,
            command=command,
            state=state,
            bg=background,
            fg=foreground,
            disabledforeground=DISABLED,
            activebackground=active_background,
            activeforeground=active_foreground,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Segoe UI Semibold", 10),
            padx=12,
            pady=10,
            cursor="hand2",
        )

    def _build_header(self, parent: tk.Widget) -> None:
        frame = self._panel(parent)
        frame.grid(row=0, column=0, sticky="ew")
        frame.grid_columnconfigure(0, weight=1)
        tk.Label(frame, text=APP_TITLE, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 17)).grid(
            row=0, column=0, sticky="w", padx=18, pady=(16, 2)
        )
        tk.Label(
            frame,
            textvariable=self.status_var,
            bg=PANEL,
            fg=ACCENT,
            font=("Segoe UI Semibold", 11),
        ).grid(row=0, column=1, sticky="e", padx=18, pady=(16, 2))
        tk.Label(
            frame,
            text="조회 snapshot에서 수집한 MAC만 사용하며, 조회가 끝나면 즉시 삭제 명령을 실행합니다.",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 16))

    def _build_settings(self, parent: tk.Widget) -> None:
        frame = self._panel(parent)
        self.settings_frame = frame
        frame.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        for column in range(6):
            frame.grid_columnconfigure(column, weight=1)

        self._entry(frame, "MM IP/Host", self.host_var, 0, 0)
        self._entry(frame, "Port", self.port_var, 0, 1, width=7)
        self._entry(frame, "계정", self.username_var, 0, 2)
        self._entry(frame, "암호", self.password_var, 0, 3, show="*")
        self._entry(frame, "Enable 암호", self.enable_password_var, 0, 4, show="*")
        self._entry(frame, "Role", self.role_var, 0, 5)
        self._entry(frame, "장비 응답 대기(초)", self.timeout_var, 2, 0, width=12)
        self._entry(frame, "주기(초)", self.interval_var, 2, 1, width=8)
        tk.Label(frame, text="결과 폴더", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=2, column=2, sticky="w", padx=12, pady=(10, 2)
        )
        tk.Entry(
            frame,
            textvariable=self.output_dir_var,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=ACCENT,
            bg=FIELD_BG,
            fg=TEXT,
            insertbackground=TEXT,
            font=("Segoe UI", 10),
        ).grid(
            row=3, column=2, columnspan=3, sticky="ew", padx=12, pady=(0, 14)
        )
        tk.Button(
            frame,
            text="폴더 선택",
            command=self.browse_output_dir,
            bg=SECONDARY_BG,
            fg=TEXT,
            activebackground=SECONDARY_ACTIVE,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Segoe UI Semibold", 10),
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
        tk.Label(parent, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=row, column=column, sticky="w", padx=12, pady=(12, 2)
        )
        tk.Entry(
            parent,
            textvariable=variable,
            show=show,
            width=width,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=ACCENT,
            bg=FIELD_BG,
            fg=TEXT,
            insertbackground=TEXT,
            font=("Segoe UI", 10),
        ).grid(
            row=row + 1, column=column, sticky="ew", padx=12, pady=(0, 12)
        )

    def _build_cards(self, parent: tk.Widget) -> None:
        frame = tk.Frame(parent, bg=BG)
        frame.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        for column in range(3):
            frame.grid_columnconfigure(column, weight=1, uniform="cards")
        self._card(frame, "누적 조회 MAC", self.counter_vars["queried"], 0, TEXT)
        self._card(frame, "누적 삭제 완료", self.counter_vars["deleted"], 1, TEXT)
        self._timer_card(frame, 2)

    def _card(self, parent: tk.Widget, title: str, variable: tk.StringVar, column: int, color: str) -> None:
        card = tk.Frame(parent, bg=CARD_BG, highlightbackground=LINE, highlightthickness=1)
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        tk.Label(card, text=title, bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(card, textvariable=variable, bg=CARD_BG, fg=color, font=("Segoe UI Semibold", 24)).pack(
            anchor="w", padx=16, pady=(0, 12)
        )

    def _timer_card(self, parent: tk.Widget, column: int) -> None:
        card = tk.Frame(parent, bg=CARD_BG, highlightbackground=LINE, highlightthickness=1)
        card.grid(row=0, column=column, sticky="ew", padx=(8, 0))
        tk.Label(card, text="작업 상태", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9)).pack(
            anchor="w", padx=16, pady=(12, 0)
        )
        tk.Label(card, textvariable=self.timer_value_var, bg=CARD_BG, fg=ACCENT, font=("Segoe UI Semibold", 20)).pack(
            anchor="w", padx=16, pady=(0, 0)
        )
        tk.Label(card, textvariable=self.timer_state_var, bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9)).pack(
            anchor="w", padx=16, pady=(0, 10)
        )

    def _build_results(self, parent: tk.Widget) -> None:
        frame = self._panel(parent)
        frame.grid(row=3, column=0, sticky="nsew", pady=(16, 0))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=3)
        frame.grid_rowconfigure(3, weight=1)
        top = tk.Frame(frame, bg=PANEL)
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        tk.Label(top, text="삭제 대상 및 결과", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(side="left")
        columns = ("mac", "status", "queried_at", "deleted_at", "error")
        self.table = ttk.Treeview(frame, columns=columns, show="headings", height=9)
        headings = {
            "mac": "MAC",
            "status": "상태",
            "queried_at": "조회시각",
            "deleted_at": "삭제시각",
            "error": "메시지",
        }
        widths = {"mac": 150, "status": 120, "queried_at": 150, "deleted_at": 150, "error": 360}
        for key in columns:
            self.table.heading(key, text=headings[key])
            self.table.column(key, width=widths[key], anchor="w")
        self.table.tag_configure("reappeared", foreground=DANGER)
        self.table.bind("<ButtonRelease-1>", lambda event: self._copy_mac_from_table_event(event, self.table, "#1"))
        self.table.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 12))

        history_top = tk.Frame(frame, bg=PANEL)
        history_top.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 10))
        tk.Label(history_top, text="최근 삭제 이력", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(
            side="left"
        )
        self._action_button(
            history_top,
            "이력 전체 지우기",
            self.clear_history,
            variant="danger_outline",
        ).pack(side="right")
        history_columns = ("run_at", "mac", "result", "error")
        self.history_table = ttk.Treeview(frame, columns=history_columns, show="headings", height=4)
        history_headings = {
            "run_at": "실행시각",
            "mac": "MAC",
            "result": "결과",
            "error": "오류",
        }
        history_widths = {"run_at": 150, "mac": 150, "result": 120, "error": 500}
        for key in history_columns:
            self.history_table.heading(key, text=history_headings[key])
            self.history_table.column(key, width=history_widths[key], anchor="w")
        self.history_table.tag_configure("reappeared", foreground=DANGER)
        self.history_table.bind(
            "<ButtonRelease-1>",
            lambda event: self._copy_mac_from_table_event(event, self.history_table, "#2"),
        )
        self.history_table.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 14))

    def _build_log(self, parent: tk.Widget) -> None:
        frame = self._panel(parent)
        frame.grid(row=4, column=0, sticky="ew", pady=(16, 0))
        frame.grid_columnconfigure(0, weight=1)
        button_row = tk.Frame(frame, bg=PANEL)
        button_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        self.cancel_button = self._action_button(
            button_row,
            "이번 삭제 취소",
            self.cancel_current_delete,
            state="disabled",
            variant="danger",
        )
        self.cancel_button.pack(side="left")
        self._action_button(
            button_row,
            "로그 지우기",
            self.clear_log,
            variant="secondary",
        ).pack(side="right")
        self.log_text = tk.Text(
            frame,
            height=7,
            bg=LOG_BG,
            fg=LOG_TEXT,
            insertbackground=LOG_TEXT,
            relief="flat",
            font=("Consolas", 10),
            wrap="word",
        )
        self.log_text.grid(row=1, column=0, sticky="ew", padx=16, pady=12)

    def _panel(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1)

    def _build_copy_notice_overlay(self) -> None:
        frame = tk.Frame(self, bg=TEXT, highlightbackground=ACCENT, highlightthickness=1)
        tk.Label(
            frame,
            textvariable=self.copy_notice_title_var,
            bg=TEXT,
            fg="#ffffff",
            font=("Segoe UI Semibold", 13),
        ).pack(anchor="center", padx=34, pady=(18, 4))
        tk.Label(
            frame,
            textvariable=self.copy_notice_mac_var,
            bg=TEXT,
            fg="#ffffff",
            font=("Consolas", 12),
        ).pack(anchor="center", padx=34, pady=(0, 18))
        self.copy_notice_frame = frame

    def _action_button(
        self,
        parent: tk.Widget,
        text: str,
        command,
        *,
        state: str = "normal",
        variant: str = "secondary",
    ) -> tk.Button:
        if variant == "danger":
            background = DANGER
            foreground = "#ffffff"
            active_background = DANGER_ACTIVE
            active_foreground = "#ffffff"
            disabled_foreground = "#f7d6d2"
            highlight_thickness = 0
            highlight_background = background
        elif variant == "danger_outline":
            background = PANEL
            foreground = DANGER
            active_background = DANGER_SOFT
            active_foreground = DANGER
            disabled_foreground = DISABLED
            highlight_thickness = 1
            highlight_background = DANGER
        else:
            background = SECONDARY_BG
            foreground = TEXT
            active_background = SECONDARY_ACTIVE
            active_foreground = TEXT
            disabled_foreground = DISABLED
            highlight_thickness = 0
            highlight_background = background
        return tk.Button(
            parent,
            text=text,
            command=command,
            state=state,
            bg=background,
            fg=foreground,
            disabledforeground=disabled_foreground,
            activebackground=active_background,
            activeforeground=active_foreground,
            relief="flat",
            bd=0,
            highlightthickness=highlight_thickness,
            highlightbackground=highlight_background,
            highlightcolor=highlight_background,
            font=("Segoe UI Semibold", 10),
            padx=16,
            pady=9,
            cursor="hand2",
        )

    def browse_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_dir_var.get() or str(DEFAULT_OUTPUT_DIR))
        if selected:
            self.output_dir_var.set(selected)
            self._load_history_from_output_dir(Path(selected), force=True)

    def start_manual_run(self) -> None:
        if self.closing or self.is_running:
            return
        if self.scheduler_running:
            self._log("주기 실행 중에는 1회 실행을 시작할 수 없습니다.")
            return
        try:
            config, settings, output_dir = self._read_inputs()
        except ValueError as exc:
            messagebox.showerror("입력 오류", str(exc))
            return
        self._load_history_from_output_dir(output_dir)
        self.cancel_event.clear()
        self._set_running(True)
        self.worker = threading.Thread(
            target=self._run_once_worker,
            args=(config, settings, output_dir),
            daemon=True,
        )
        self.worker.start()

    def start_scheduler(self) -> None:
        if self.closing or self.scheduler_running:
            return
        if self.is_running:
            self._log("실행 중에는 주기 실행을 시작할 수 없습니다.")
            return
        try:
            config, settings, output_dir = self._read_inputs()
            interval = self._read_interval()
        except ValueError as exc:
            messagebox.showerror("입력 오류", str(exc))
            return
        self._load_history_from_output_dir(output_dir)
        self.scheduler_stop_event.clear()
        self.scheduler_running = True
        self.manual_button.configure(state="disabled")
        self.schedule_button.configure(state="disabled")
        self.stop_schedule_button.configure(state="normal")
        self._sync_settings_visibility()
        self._log(f"주기 실행 시작: {interval}초 간격")
        self.scheduler_worker = threading.Thread(
            target=self._scheduler_loop,
            args=(config, settings, output_dir, interval),
            daemon=True,
        )
        self.scheduler_worker.start()

    def stop_scheduler(self) -> None:
        self.scheduler_stop_event.set()
        self.cancel_event.set()
        self.scheduler_running = False
        self.manual_button.configure(state="disabled" if self.is_running else "normal")
        self.schedule_button.configure(state="disabled" if self.is_running else "normal")
        self.stop_schedule_button.configure(state="disabled")
        self._set_timer("-", "대기")
        self._sync_settings_visibility()
        self._log("주기 실행 정지 요청")

    def cancel_current_delete(self) -> None:
        self.cancel_event.set()
        self._log("이번 삭제 취소 요청")

    def disconnect_session(self) -> None:
        if self.closing:
            return
        if self.is_running:
            self._log("실행 중에는 세션 연결 해제를 건너뜁니다.")
            return
        self._start_session_close(reason="manual", enqueue_progress=True)
        self.status_var.set("세션 연결 해제")
        self._log("SESSION DISCONNECT REQUEST")

    def on_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.scheduler_stop_event.set()
        self.cancel_event.set()
        if self._drain_after_id is not None:
            try:
                self.after_cancel(self._drain_after_id)
            except tk.TclError:
                pass
            self._drain_after_id = None
        if self.copy_notice_after_id is not None:
            try:
                self.after_cancel(self.copy_notice_after_id)
            except tk.TclError:
                pass
            self.copy_notice_after_id = None
        self._start_session_close(reason="app_close", enqueue_progress=False)
        try:
            self.after(SHUTDOWN_GRACE_MS, self._destroy_window)
        except tk.TclError:
            self._destroy_window()

    def _scheduler_loop(
        self,
        config: MmConnectionConfig,
        settings: CleanupSettings,
        output_dir: Path,
        interval: int,
    ) -> None:
        while not self.scheduler_stop_event.is_set():
            self.cancel_event.clear()
            self._enqueue_event("running", True)
            try:
                self._run_summary(config, settings, output_dir)
            finally:
                self._enqueue_event("running", False)
            for remaining in range(interval, 0, -1):
                if self.scheduler_stop_event.is_set():
                    break
                self._enqueue_event("next_run", remaining)
                if self.scheduler_stop_event.wait(1):
                    break
        self._enqueue_event("scheduler_stopped", None)

    def _run_once_worker(self, config: MmConnectionConfig, settings: CleanupSettings, output_dir: Path) -> None:
        try:
            self._run_summary(config, settings, output_dir)
        finally:
            self._enqueue_event("running", False)

    def _run_summary(self, config: MmConnectionConfig, settings: CleanupSettings, output_dir: Path) -> None:
        def progress(event: str, payload: dict[str, object]) -> None:
            self._enqueue_event("progress", (event, payload))

        with self.runner_lock:
            summary = self.runner.run_once(
                config,
                settings,
                output_dir=output_dir,
                progress_callback=progress,
                should_cancel=self._should_cancel_run,
            )
        self._enqueue_event("summary", summary)

    def _close_runner_session(self, *, reason: str, enqueue_progress: bool) -> None:
        progress = None
        if enqueue_progress:
            progress = lambda event, payload: self._enqueue_event("progress", (event, payload))
        with self.runner_lock:
            self.runner.close_session(progress_callback=progress, reason=reason)

    def _start_session_close(self, *, reason: str, enqueue_progress: bool) -> None:
        if self.session_close_worker is not None and self.session_close_worker.is_alive():
            return
        self.session_close_worker = threading.Thread(
            target=self._close_runner_session,
            kwargs={"reason": reason, "enqueue_progress": enqueue_progress},
            daemon=True,
        )
        self.session_close_worker.start()

    def _should_cancel_run(self) -> bool:
        return self.cancel_event.is_set() or self.scheduler_stop_event.is_set() or self.closing

    def _enqueue_event(self, event: str, payload: object) -> bool:
        if self.closing:
            return False
        self.event_queue.put((event, payload))
        return True

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
        if port < 1 or port > 65535:
            raise ValueError("Port는 1부터 65535 사이 숫자로 입력하세요.")
        try:
            timeout = max(5, int(self.timeout_var.get().strip() or "60"))
        except ValueError as exc:
            raise ValueError("장비 응답 대기(초)는 숫자로 입력하세요.") from exc
        role = self.role_var.get().strip() or DEFAULT_ROLE
        try:
            build_query_command(role)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        config = MmConnectionConfig(
            host=host,
            username=username,
            password=password,
            port=port,
            enable_password=self.enable_password_var.get(),
        )
        settings = CleanupSettings(
            role=role,
            timeout=timeout,
            delete_delay_seconds=0,
        )
        output_dir = Path(self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR).expanduser()
        return config, settings, output_dir

    def _read_interval(self) -> int:
        try:
            interval = int(self.interval_var.get().strip() or str(DEFAULT_INTERVAL_SECONDS))
        except ValueError as exc:
            raise ValueError("주기(초)는 1 이상 숫자로 입력하세요.") from exc
        if interval < MIN_INTERVAL_SECONDS:
            raise ValueError("주기(초)는 1 이상 숫자로 입력하세요.")
        return interval

    def _drain_events(self) -> None:
        if self.closing:
            return
        try:
            while True:
                event, payload = self.event_queue.get_nowait()
                try:
                    if event == "running":
                        self._set_running(bool(payload))
                    elif event == "progress":
                        progress_event, progress_payload = payload
                        if not isinstance(progress_payload, dict):
                            progress_payload = {}
                        self._handle_progress(str(progress_event), progress_payload)
                    elif event == "summary":
                        self._handle_summary(payload)
                    elif event == "next_run":
                        self._set_timer(f"{payload}s", "다음 실행")
                    elif event == "scheduler_stopped":
                        self.scheduler_running = False
                        self.manual_button.configure(state="disabled" if self.is_running else "normal")
                        self.schedule_button.configure(state="disabled" if self.is_running else "normal")
                        self.stop_schedule_button.configure(state="disabled")
                        self._set_timer("-", "대기")
                        self._sync_settings_visibility()
                except Exception as exc:
                    try:
                        self._log(f"WARNING: 이벤트 처리 실패({event}) - {exc}")
                    except Exception:
                        pass
        except queue.Empty:
            pass
        if not self.closing:
            try:
                self._drain_after_id = self.after(150, self._drain_events)
            except tk.TclError:
                self._drain_after_id = None

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
            self._set_timer("실행 중", "조회 처리")
            self._log(f"QUERY: {payload.get('command')}")
        elif event == "query_done":
            raw_macs = payload.get("macs")
            raw_type_na_macs = payload.get("type_na_macs")
            macs = list(raw_macs) if isinstance(raw_macs, (list, tuple, set)) else []
            type_na_macs = (
                [str(mac) for mac in raw_type_na_macs]
                if isinstance(raw_type_na_macs, (list, tuple, set))
                else []
            )
            self._count_current_query(len(_unique_display_macs(macs)))
            self._replace_table(macs, "삭제 대상", type_na_macs=type_na_macs)
            self._log(f"QUERY DONE: {payload.get('count', 0)} MAC(s)")
            for mac in _unique_display_macs(type_na_macs):
                self._log(f"TYPE N/A: {mac} - 관리자 직접 장비 지정 필요")
        elif event == "countdown":
            remaining = int(payload.get("remaining", 0))
            self._set_timer(f"{remaining}s", "삭제 시작 대기" if remaining > 0 else "삭제 시작")
            self.status_var.set(f"{remaining}초 후 삭제 시작" if remaining > 0 else "삭제 시작")
            self.cancel_button.configure(state="normal" if remaining > 0 else "disabled")
        elif event == "delete_start":
            self.status_var.set("MAC 삭제 중")
            self._set_timer("실행 중", "삭제 처리")
            self._set_row_status(str(payload.get("mac")), "삭제 중", "")
            self._log(f"DELETE START: {payload.get('mac')}")
        elif event == "delete_done":
            self._set_row_status(str(payload.get("mac")), "삭제 완료", "")
            self._log(f"DELETE OK: {payload.get('mac')}")
        elif event == "delete_error":
            self._set_row_status(str(payload.get("mac")), "삭제 실패", str(payload.get("error") or ""))
            self._log(f"DELETE ERROR: {payload.get('mac')} | {payload.get('error')}")
        elif event == "delete_unknown":
            self._set_row_status(str(payload.get("mac")), "확인 필요", str(payload.get("error") or ""))
            self._log(f"DELETE UNKNOWN: {payload.get('mac')} | {payload.get('error')}")
        elif event == "reappeared_macs":
            raw_macs = payload.get("macs")
            macs = [str(mac) for mac in raw_macs] if isinstance(raw_macs, (list, tuple, set)) else []
            self.status_var.set("삭제 MAC 재조회됨")
            self._mark_reappeared_rows(macs)
            for mac in macs:
                self._log(f"REAPPEARED: {mac}")
        elif event == "delete_canceled":
            self.status_var.set("이번 삭제 취소됨")
            self._set_timer("-", "대기")
            self.cancel_button.configure(state="disabled")
            self._set_all_pending_status("취소됨")
            self._log(f"CANCELED: {payload.get('count')} pending MAC(s)")
        elif event == "run_error":
            self.status_var.set("실패")
            self._set_timer("-", "대기")
            self.cancel_button.configure(state="disabled")
            self._log(f"ERROR: {payload.get('error')}")

    def _handle_summary(self, summary) -> None:
        self._ensure_cumulative_counters()
        error = getattr(summary, "error", "")
        canceled = bool(getattr(summary, "canceled", False))
        verification_skipped = bool(getattr(summary, "verification_skipped", False))
        delete_success_count = getattr(summary, "delete_success_count", 0)
        reappeared_count = getattr(summary, "reappeared_count", 0)
        raw_reappeared_macs = getattr(summary, "reappeared_macs", []) or []
        reappeared_macs = (
            [str(mac) for mac in raw_reappeared_macs]
            if isinstance(raw_reappeared_macs, (list, tuple, set))
            else []
        )
        audit_path = getattr(summary, "audit_path", None)
        audit_error = getattr(summary, "audit_error", "")
        history_error = getattr(summary, "history_error", "")
        target_macs = getattr(summary, "target_macs", []) or []
        target_count = (
            len(target_macs)
            if isinstance(target_macs, (list, tuple, set)) and target_macs
            else _safe_int(getattr(summary, "queried_count", 0))
        )
        if not self.current_run_query_counted:
            self._count_current_query(target_count)
        if not self.current_run_delete_counted:
            if not (error or canceled or verification_skipped):
                self.cumulative_deleted_count += _safe_int(delete_success_count)
            self.current_run_delete_counted = True
            self._sync_counter_vars()
        self._set_timer("-", "대기")
        self.cancel_button.configure(state="disabled")
        if error:
            self.status_var.set("실패")
        elif canceled:
            self.status_var.set("취소됨")
        elif reappeared_count:
            self.status_var.set("삭제 MAC 재조회됨")
        else:
            self.status_var.set("완료")
        if reappeared_macs:
            self._mark_reappeared_rows(reappeared_macs)
        if audit_path:
            self._log(f"AUDIT: {audit_path}")
        if audit_error:
            self._log(f"AUDIT WARNING: {audit_error}")
        if history_error:
            self._log(f"HISTORY WARNING: {history_error}")
        self._append_history_rows(summary)

    def _replace_table(self, macs: list[str], status: str, *, type_na_macs: Optional[list[str]] = None) -> None:
        self.table.delete(*self.table.get_children())
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        type_na_set = set(_unique_display_macs(type_na_macs or []))
        for mac in _unique_display_macs(macs):
            message = TYPE_NA_MESSAGE if mac in type_na_set else ""
            self.table.insert("", "end", iid=mac, values=(mac, status, now, "", message))

    def _set_row_status(self, mac: str, status: str, error: str) -> None:
        if not mac or not self.table.exists(mac):
            return
        values = list(self.table.item(mac, "values"))
        if len(values) < 5:
            return
        values[1] = status
        if status in {"삭제 완료", "삭제 실패", "확인 필요", "재조회됨"}:
            values[3] = time.strftime("%Y-%m-%d %H:%M:%S")
        values[4] = _merge_status_message(str(values[4] or ""), error)
        self.table.item(mac, values=values)
        self.table.item(mac, tags=("reappeared",) if status == "재조회됨" else ())

    def _mark_reappeared_rows(self, macs: list[str]) -> None:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        error = "삭제 성공 후 검증 조회에서 다시 발견"
        for mac in macs:
            if self.table.exists(mac):
                self._set_row_status(mac, "재조회됨", error)
            else:
                self.table.insert("", "end", iid=mac, values=(mac, "재조회됨", now, now, error), tags=("reappeared",))

    def _set_all_pending_status(self, status: str) -> None:
        for item_id in self.table.get_children():
            values = list(self.table.item(item_id, "values"))
            if len(values) < 2:
                continue
            if values[1] in {"삭제 대상", "삭제 중"}:
                values[1] = status
                self.table.item(item_id, values=values)

    def _set_running(self, running: bool) -> None:
        self.is_running = running
        self.manual_button.configure(state="disabled" if running or self.scheduler_running else "normal")
        self.schedule_button.configure(state="disabled" if running or self.scheduler_running else "normal")
        if running:
            self._reset_run_counters()
            self._set_timer("실행 중", "조회/삭제 처리")
            self.cancel_button.configure(state="disabled")
        elif not self.scheduler_running:
            self._set_timer("-", "대기")
        self._sync_settings_visibility()

    def _set_timer(self, value: str, state: str) -> None:
        self.timer_value_var.set(value)
        self.timer_state_var.set(state)

    def _reset_run_counters(self) -> None:
        self._ensure_cumulative_counters()
        self.current_run_queried_count = 0
        self.current_run_query_counted = False
        self.current_run_delete_counted = False
        self._sync_counter_vars()

    def _count_current_query(self, count: int) -> None:
        self._ensure_cumulative_counters()
        self.current_run_queried_count = max(int(count), 0)
        if not self.current_run_query_counted:
            self.cumulative_queried_count += self.current_run_queried_count
            self.current_run_query_counted = True
        self._sync_counter_vars()

    def _sync_counter_vars(self) -> None:
        self._ensure_cumulative_counters()
        self.counter_vars["queried"].set(str(self.cumulative_queried_count))
        self.counter_vars["deleted"].set(str(self.cumulative_deleted_count))

    def _ensure_cumulative_counters(self) -> None:
        if not hasattr(self, "cumulative_queried_count"):
            self.cumulative_queried_count = _safe_int(self.counter_vars["queried"].get())
        if not hasattr(self, "cumulative_deleted_count"):
            self.cumulative_deleted_count = _safe_int(self.counter_vars["deleted"].get())
        if not hasattr(self, "current_run_queried_count"):
            self.current_run_queried_count = 0
        if not hasattr(self, "current_run_query_counted"):
            self.current_run_query_counted = False
        if not hasattr(self, "current_run_delete_counted"):
            self.current_run_delete_counted = False

    def _sync_settings_visibility(self) -> None:
        if self.settings_frame is None:
            return
        if self.is_running or self.scheduler_running:
            self.settings_frame.grid_remove()
        else:
            self.settings_frame.grid()

    def _append_history_rows(self, summary) -> None:
        delete_results = getattr(summary, "delete_results", None)
        if not isinstance(delete_results, (list, tuple)) or not delete_results:
            return
        started_at = getattr(summary, "started_at", None)
        run_at = (
            started_at.strftime("%Y-%m-%d %H:%M:%S")
            if callable(getattr(started_at, "strftime", None))
            else ""
        )
        raw_reappeared_macs = getattr(summary, "reappeared_macs", []) or []
        reappeared_macs = (
            set(_unique_display_macs([mac for mac in raw_reappeared_macs if isinstance(mac, str)]))
            if isinstance(raw_reappeared_macs, (list, tuple, set))
            else set()
        )
        for item in delete_results:
            mac = getattr(item, "mac", "")
            if not isinstance(mac, str) or not mac:
                continue
            result, error, tags = self._history_row_display(
                {
                    "mac": mac,
                    "result": "",
                    "status": getattr(item, "status", ""),
                    "success": bool(getattr(item, "success", False)),
                    "error": getattr(item, "error", ""),
                    "reappeared": mac in reappeared_macs or getattr(item, "status", "") == "reappeared",
                }
            )
            self._insert_history_row(run_at, mac, result, error, tags=tags)
        self._cap_history_rows()

    def _cap_history_rows(self) -> None:
        children = self.history_table.get_children()
        overflow = len(children) - MAX_HISTORY_ROWS
        if overflow > 0:
            self.history_table.delete(*children[:overflow])

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{time.strftime('%H:%M:%S')} {message}\n")
        self._cap_log_lines()
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _cap_log_lines(self) -> None:
        try:
            line_count = int(self.log_text.index("end-1c").split(".")[0])
        except (tk.TclError, TypeError, ValueError):
            return
        overflow = line_count - MAX_LOG_LINES
        if overflow > 0:
            try:
                self.log_text.delete("1.0", f"{overflow + 1}.0")
            except tk.TclError:
                return

    def clear_log(self) -> None:
        try:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            try:
                self.log_text.configure(state="disabled")
            except tk.TclError:
                pass

    def clear_history(self) -> None:
        try:
            self.history_table.delete(*self.history_table.get_children())
        except tk.TclError:
            return
        self.history_row_counter = 0

    def _load_history_from_output_dir(self, output_dir: Path, *, force: bool = False) -> None:
        output_dir = output_dir.expanduser()
        if not force and self.loaded_history_dir == output_dir:
            return
        self.loaded_history_dir = output_dir
        if not hasattr(self, "history_table"):
            return
        records = self._read_history_records(output_dir)
        try:
            self.history_table.delete(*self.history_table.get_children())
        except tk.TclError:
            return
        self.history_row_counter = 0
        for record in records[-MAX_HISTORY_ROWS:]:
            run_at = str(record.get("run_at", ""))[:19].replace("T", " ")
            mac = str(record.get("mac", ""))
            result, error, tags = self._history_row_display(record)
            self._insert_history_row(run_at, mac, result, error, tags=tags)

    def _read_history_records(self, output_dir: Path) -> list[dict[str, object]]:
        jsonl_path = output_dir / HISTORY_FILE_NAME
        if jsonl_path.exists():
            records: list[dict[str, object]] = []
            try:
                with jsonl_path.open(encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(record, dict) and isinstance(record.get("mac"), str) and record.get("mac"):
                            records.append(record)
            except (OSError, UnicodeError):
                return records
            return records
        records = []
        try:
            audit_paths = sorted(output_dir.glob("*/cleanup_summary.json"))
        except OSError:
            return records
        for audit_path in audit_paths:
            try:
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(audit, dict):
                continue
            run_at = str(audit.get("started_at", ""))
            reappeared_macs = audit.get("reappeared_macs") or []
            if not isinstance(reappeared_macs, (list, tuple, set)):
                reappeared_macs = []
            reappeared = {mac for mac in reappeared_macs if isinstance(mac, str)}
            delete_results = audit.get("delete_results") or []
            if not isinstance(delete_results, (list, tuple)):
                delete_results = []
            for item in delete_results:
                if not isinstance(item, dict):
                    continue
                mac = item.get("mac")
                if not isinstance(mac, str) or not mac:
                    continue
                record = dict(item)
                record["run_at"] = run_at
                record["reappeared"] = mac in reappeared or item.get("status") == "reappeared"
                records.append(record)
        return records

    def _history_row_display(self, record: dict[str, object]) -> tuple[str, str, tuple[str, ...]]:
        status = str(record.get("status") or "")
        result = str(record.get("result") or "")
        error = str(record.get("error") or "")
        reappeared = bool(record.get("reappeared")) or status == "reappeared"
        if reappeared:
            return "재조회됨", error or "삭제 성공 후 검증 조회에서 다시 발견", ("reappeared",)
        if result:
            return result, error, ()
        if status == "unknown":
            return "확인 필요", error, ()
        if bool(record.get("success")):
            return "삭제 완료", error, ()
        return "삭제 실패", error, ()

    def _insert_history_row(
        self,
        run_at: str,
        mac: str,
        result: str,
        error: str,
        *,
        tags: tuple[str, ...] = (),
    ) -> None:
        row_id = f"history-{self.history_row_counter}"
        try:
            self.history_table.insert("", "end", iid=row_id, values=(run_at, mac, result, error), tags=tags)
        except tk.TclError:
            return
        self.history_row_counter += 1

    def _copy_mac_from_table_event(self, event: tk.Event, table: ttk.Treeview, mac_column: str) -> None:
        if table.identify_column(event.x) != mac_column:
            return
        row_id = table.identify_row(event.y)
        if not row_id:
            return
        try:
            values = list(table.item(row_id, "values"))
        except (tk.TclError, TypeError):
            return
        try:
            column_index = int(mac_column.removeprefix("#")) - 1
        except (AttributeError, ValueError):
            return
        if column_index < 0 or column_index >= len(values):
            return
        mac = str(values[column_index]).strip()
        if not mac:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(mac)
        except tk.TclError:
            return
        self._show_copy_notice(mac)

    def _show_copy_notice(self, mac: str) -> None:
        if self.copy_notice_after_id is not None:
            try:
                self.after_cancel(self.copy_notice_after_id)
            except tk.TclError:
                pass
        self.copy_notice_title_var.set("복사 완료")
        self.copy_notice_mac_var.set(mac)
        if self.copy_notice_frame is not None:
            self.copy_notice_frame.place(relx=0.5, rely=0.5, anchor="center")
            self.copy_notice_frame.lift()
        self.copy_notice_after_id = self.after(1000, self._hide_copy_notice)

    def _hide_copy_notice(self) -> None:
        self.copy_notice_title_var.set("")
        self.copy_notice_mac_var.set("")
        if self.copy_notice_frame is not None:
            self.copy_notice_frame.place_forget()
        self.copy_notice_after_id = None

    def _destroy_window(self) -> None:
        try:
            self.destroy()
        except tk.TclError:
            pass


def _unique_display_macs(macs: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for mac in macs:
        if not isinstance(mac, str):
            continue
        normalized = normalize_mac(mac) or mac.strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _merge_status_message(existing: str, update: str) -> str:
    has_type_na_message = TYPE_NA_MESSAGE in existing
    update = update.strip()
    if has_type_na_message and update:
        return f"{TYPE_NA_MESSAGE} | {update}"
    if has_type_na_message:
        return TYPE_NA_MESSAGE
    return update


def _safe_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def main() -> int:
    if os.environ.get("ARUBA_MM_CLEANUP_GUI_SMOKE") == "1":
        app = ArubaMmCleanupGui()
        app.update_idletasks()
        app.closing = True
        if app._drain_after_id is not None:
            try:
                app.after_cancel(app._drain_after_id)
            except tk.TclError:
                pass
        app.destroy()
        return 0
    app = ArubaMmCleanupGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
