"""Floating visual indicator and interactive popup for QuickFix.

Provides a clear, sleek on-screen indicator while the model is processing,
and an interactive floating popup near the cursor showing the corrected text
with quick-action buttons for 'Replace' and 'Copy'.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

logger = logging.getLogger("quickfix.ui")


class FloatingUIManager:
    """Manages floating status indicators and result popups using a dedicated Tkinter loop."""

    def __init__(self) -> None:
        self._command_queue: queue.Queue = queue.Queue()
        self._root: tk.Tk | None = None
        self._indicator_win: tk.Toplevel | None = None
        self._indicator_label: tk.Label | None = None
        self._indicator_timer_label: tk.Label | None = None
        self._popup_win: tk.Toplevel | None = None

        self._start_time: float = 0.0
        self._is_working: bool = False
        self._active_model: str = ""

        self._ui_thread = threading.Thread(target=self._run_ui_loop, name="QuickFixUI", daemon=True)
        self._ui_thread.start()

    def _run_ui_loop(self) -> None:
        """Run the Tkinter main loop in a background thread."""
        self._root = tk.Tk()
        self._root.withdraw()  # Hide root window

        # Periodic check for commands from worker threads
        self._root.after(50, self._process_queue)
        self._root.mainloop()

    def _process_queue(self) -> None:
        """Process incoming UI requests from the thread-safe queue."""
        try:
            while not self._command_queue.empty():
                cmd, args = self._command_queue.get_nowait()
                if cmd == "show_indicator":
                    self._do_show_indicator(*args)
                elif cmd == "hide_indicator":
                    self._do_hide_indicator()
                elif cmd == "show_popup":
                    self._do_show_popup(*args)
                elif cmd == "hide_popup":
                    self._do_hide_popup()
        except Exception as exc:
            logger.debug(f"UI queue processing error: {exc}")
        finally:
            if self._root:
                self._root.after(50, self._process_queue)

    def show_indicator(self, model: str, x: int | None = None, y: int | None = None) -> None:
        """Thread-safe request to show the working indicator pill."""
        self._command_queue.put(("show_indicator", (model, x, y)))

    def hide_indicator(self) -> None:
        """Thread-safe request to hide the working indicator pill."""
        self._command_queue.put(("hide_indicator", ()))

    def show_popup(
        self,
        original_text: str,
        corrected_text: str,
        mode: str,
        on_replace: Callable[[], None] | None,
        on_copy: Callable[[], None] | None,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        """Thread-safe request to show the interactive result popup."""
        self._command_queue.put(
            ("show_popup", (original_text, corrected_text, mode, on_replace, on_copy, x, y))
        )

    def hide_popup(self) -> None:
        """Thread-safe request to hide the result popup."""
        self._command_queue.put(("hide_popup", ()))

    # --- Internal Tkinter Implementations ---

    def _get_target_coords(self, x: int | None, y: int | None, width: int = 340, height: int = 60) -> tuple[int, int]:
        """Determine position near mouse cursor or screen bottom-right."""
        screen_w = self._root.winfo_screenwidth() if self._root else 1920
        screen_h = self._root.winfo_screenheight() if self._root else 1080

        if x is not None and y is not None and x > 0 and y > 0:
            target_x = x + 20
            target_y = y + 20
            # Ensure it fits on screen
            if target_x + width > screen_w:
                target_x = screen_w - width - 20
            if target_y + height > screen_h:
                target_y = screen_h - height - 40
            return target_x, target_y

        # Default: Bottom-right above taskbar
        return screen_w - width - 40, screen_h - height - 80

    def _do_show_indicator(self, model: str, x: int | None, y: int | None) -> None:
        if not self._root:
            return

        self._active_model = model
        self._start_time = time.time()
        self._is_working = True

        if self._indicator_win is None or not self._indicator_win.winfo_exists():
            self._indicator_win = tk.Toplevel(self._root)
            self._indicator_win.overrideredirect(True)  # Frameless
            self._indicator_win.attributes("-topmost", True)
            self._indicator_win.configure(bg="#0f172a")  # Dark slate background

            # Main container frame with subtle border
            container = tk.Frame(
                self._indicator_win,
                bg="#0f172a",
                highlightbackground="#38bdf8",
                highlightthickness=1,
                padx=14,
                pady=10,
            )
            container.pack(fill="both", expand=True)

            # Header row
            header_frame = tk.Frame(container, bg="#0f172a")
            header_frame.pack(fill="x")

            icon_badge = tk.Label(
                header_frame,
                text="⚡ QuickFix",
                font=("Segoe UI", 9, "bold"),
                fg="#38bdf8",
                bg="#0f172a",
            )
            icon_badge.pack(side="left")

            self._indicator_timer_label = tk.Label(
                header_frame,
                text="(0s)",
                font=("Segoe UI", 8),
                fg="#94a3b8",
                bg="#0f172a",
            )
            self._indicator_timer_label.pack(side="right")

            # Status message label
            self._indicator_label = tk.Label(
                container,
                text=f"Polishing text with {model}...",
                font=("Segoe UI", 9),
                fg="#f8fafc",
                bg="#0f172a",
                anchor="w",
            )
            self._indicator_label.pack(fill="x", pady=(4, 0))

        pos_x, pos_y = self._get_target_coords(x, y, width=320, height=65)
        self._indicator_win.geometry(f"320x65+{pos_x}+{pos_y}")
        self._indicator_win.deiconify()

        self._tick_indicator()

    def _tick_indicator(self) -> None:
        """Update elapsed duration and pulse dots while working."""
        if not self._is_working or not self._indicator_win or not self._indicator_win.winfo_exists():
            return

        elapsed = int(time.time() - self._start_time)
        dots = "." * ((elapsed % 3) + 1)
        if self._indicator_label:
            self._indicator_label.config(text=f"Polishing text with {self._active_model}{dots}")
        if self._indicator_timer_label:
            self._indicator_timer_label.config(text=f"({elapsed}s)")

        self._root.after(400, self._tick_indicator)

    def _do_hide_indicator(self) -> None:
        self._is_working = False
        if self._indicator_win and self._indicator_win.winfo_exists():
            self._indicator_win.withdraw()

    def _do_show_popup(
        self,
        original_text: str,
        corrected_text: str,
        mode: str,
        on_replace: Callable[[], None] | None,
        on_copy: Callable[[], None] | None,
        x: int | None,
        y: int | None,
    ) -> None:
        if not self._root:
            return

        self._do_hide_indicator()

        if self._popup_win and self._popup_win.winfo_exists():
            self._popup_win.destroy()

        self._popup_win = tk.Toplevel(self._root)
        self._popup_win.overrideredirect(True)
        self._popup_win.attributes("-topmost", True)
        self._popup_win.configure(bg="#0f172a")

        container = tk.Frame(
            self._popup_win,
            bg="#0f172a",
            highlightbackground="#38bdf8",
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        container.pack(fill="both", expand=True)

        # Header
        header = tk.Frame(container, bg="#0f172a")
        header.pack(fill="x", pady=(0, 8))

        title = tk.Label(
            header,
            text="⚡ QuickFix",
            font=("Segoe UI", 10, "bold"),
            fg="#38bdf8",
            bg="#0f172a",
        )
        title.pack(side="left")

        mode_badge = tk.Label(
            header,
            text=f"[{mode.upper()}]",
            font=("Segoe UI", 8, "bold"),
            fg="#a855f7" if mode == "CODE" else "#10b981",
            bg="#0f172a",
        )
        mode_badge.pack(side="left", padx=6)

        close_btn = tk.Label(
            header,
            text="✕",
            font=("Segoe UI", 9, "bold"),
            fg="#94a3b8",
            bg="#0f172a",
            cursor="hand2",
        )
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: self._do_hide_popup())

        # Action Buttons (pinned to BOTTOM so they are ALWAYS visible regardless of text length)
        btn_frame = tk.Frame(container, bg="#0f172a")
        btn_frame.pack(side="bottom", fill="x", pady=(8, 0))

        def handle_replace() -> None:
            # If user edited text in widget, use widget text
            final_val = text_widget.get("1.0", "end-1c")
            self._do_hide_popup()
            if on_replace:
                on_replace(final_val)

        def handle_copy() -> None:
            final_val = text_widget.get("1.0", "end-1c")
            import pyperclip
            pyperclip.copy(final_val)
            self._do_hide_popup()
            if on_copy:
                on_copy(final_val)

        replace_btn = tk.Button(
            btn_frame,
            text="↵ Replace Selection",
            font=("Segoe UI", 9, "bold"),
            bg="#0284c7",
            fg="#ffffff",
            activebackground="#0369a1",
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            pady=5,
            cursor="hand2",
            command=handle_replace,
        )
        replace_btn.pack(side="left", padx=(0, 8))

        copy_btn = tk.Button(
            btn_frame,
            text="📋 Copy",
            font=("Segoe UI", 9),
            bg="#334155",
            fg="#f8fafc",
            activebackground="#475569",
            activeforeground="#ffffff",
            relief="flat",
            padx=12,
            pady=5,
            cursor="hand2",
            command=handle_copy,
        )
        copy_btn.pack(side="left")

        # Corrected Text Content Area (fills remaining middle space)
        text_frame = tk.Frame(container, bg="#1e293b", padx=6, pady=6)
        text_frame.pack(side="top", fill="both", expand=True)

        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side="right", fill="y")

        is_code = (mode.upper() == "CODE")
        text_widget = tk.Text(
            text_frame,
            font=("Consolas", 10) if is_code else ("Segoe UI", 9),
            bg="#1e293b",
            fg="#f8fafc",
            insertbackground="#38bdf8",
            selectbackground="#0284c7",
            wrap="none" if is_code else "word",
            relief="flat",
            borderwidth=0,
            yscrollcommand=scrollbar.set,
        )
        text_widget.insert("1.0", corrected_text)
        text_widget.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=text_widget.yview)

        # Key bindings
        self._popup_win.bind("<Return>", lambda e: handle_replace())
        self._popup_win.bind("<Escape>", lambda e: self._do_hide_popup())

        # Dynamic size based on line count and mode
        lines_count = len(corrected_text.splitlines())
        width = 520 if is_code else 440
        height = max(220, min(380, 100 + lines_count * 20))
        pos_x, pos_y = self._get_target_coords(x, y, width=width, height=height)
        self._popup_win.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
        self._popup_win.deiconify()
        replace_btn.focus_set()

    def _do_hide_popup(self) -> None:
        if self._popup_win and self._popup_win.winfo_exists():
            self._popup_win.destroy()
            self._popup_win = None
