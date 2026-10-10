"""QuickFix - Global AI Text Correction Desktop Application.

Entry point, system tray controller, global hotkey manager, and execution orchestrator.
Runs completely offline in the background with clear on-screen indicators and
interactive popup preview.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
# pyrefly: ignore [missing-import]
import pystray  
from pynput.keyboard import GlobalHotKeys
import pyperclip

from .capture import SelectionCapture
from .classify import classify_text
from .config import Config, load_config, normalize_hotkey, validate_hotkey
from .llm import OllamaClient, logger
from .platform_win import (
    acquire_single_instance_mutex,
    get_active_process_name,
    get_cursor_pos,
    get_foreground_hwnd,
    play_beep,
)
from .prompts import format_user_message, get_system_prompt
from .sanitize import sanitize_output
from .ui import FloatingUIManager

COLOR_IDLE = (128, 128, 128)      # Grey
COLOR_WORKING = (245, 158, 11)    # Amber
COLOR_ERROR = (239, 68, 68)       # Red


def generate_icon_image(color: tuple[int, int, int]) -> Image.Image:
    """Generate a crisp high-DPI tray icon with smooth geometry.

    Args:
        color: RGB tuple for the icon color.

    Returns:
        PIL Image ready for pystray.
    """
    size = (64, 64)
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Subtle halo
    draw.ellipse((4, 4, 60, 60), fill=(*color, 50))
    # Crisp core circle
    draw.ellipse((10, 10, 54, 54), fill=(*color, 255))
    # Center accent
    draw.ellipse((24, 24, 40, 40), fill=(255, 255, 255, 180))
    return image


class QuickFixApp:
    """Main application orchestrator."""

    def __init__(self, config_path: Path | str = "config.json") -> None:
        """Initialize QuickFix application.

        Args:
            config_path: Path to config.json file.
        """
        self.config_path = Path(config_path)
        self.config, warnings = load_config(self.config_path)
        for w in warnings:
            logger.warning(w)

        self.capture_engine = SelectionCapture()
        self.llm_client = OllamaClient(
            base_url=self.config.ollama_url,
            timeout=self.config.request_timeout_seconds,
            keep_alive=self.config.keep_alive,
            temperature=self.config.temperature,
        )
        self.ui = FloatingUIManager()

        self.processing_lock = threading.Lock()
        self.error_timer: threading.Timer | None = None
        self.hotkey_listener: GlobalHotKeys | None = None
        self.tray_icon: pystray.Icon | None = None
        self.single_instance_handle: Any = None

        # Pre-rendered tray icons
        self.icon_idle = generate_icon_image(COLOR_IDLE)
        self.icon_working = generate_icon_image(COLOR_WORKING)
        self.icon_error = generate_icon_image(COLOR_ERROR)

    def set_tray_state(self, state: str) -> None:
        """Update system tray icon according to current operational state.

        Args:
            state: 'idle', 'working', or 'error'.
        """
        if not self.tray_icon:
            return

        if self.error_timer:
            self.error_timer.cancel()
            self.error_timer = None

        if state == "working":
            self.tray_icon.icon = self.icon_working
            self.tray_icon.title = "QuickFix: Processing..."
        elif state == "error":
            self.tray_icon.icon = self.icon_error
            self.tray_icon.title = "QuickFix: Error"
            play_beep(frequency=450, duration=200)

            # Revert to idle after 2.0 seconds
            def _revert() -> None:
                if self.tray_icon:
                    self.tray_icon.icon = self.icon_idle
                    self.tray_icon.title = "QuickFix: Ready"

            self.error_timer = threading.Timer(2.0, _revert)
            self.error_timer.daemon = True
            self.error_timer.start()
        else:  # idle
            self.tray_icon.icon = self.icon_idle
            self.tray_icon.title = "QuickFix: Ready"

    def handle_hotkey_trigger(self, force_mode: str | None = None) -> None:
        """Handle incoming global hotkey event on a background thread.

        Args:
            force_mode: Optional 'CODE' or 'PROSE' override.
        """
        acquired = self.processing_lock.acquire(blocking=False)
        if not acquired:
            logger.debug("Hotkey ignored: processing already in progress.")
            return

        def _worker() -> None:
            try:
                self._execute_pipeline(force_mode)
            finally:
                self.processing_lock.release()

        worker_thread = threading.Thread(target=_worker, name="QuickFixWorker", daemon=True)
        worker_thread.start()

    def _execute_pipeline(self, force_mode: str | None = None) -> None:
        """Execute the capture -> classify -> LLM -> sanitize -> display/paste pipeline."""
        if not self.config.enabled:
            logger.debug("QuickFix is currently disabled. Ignoring shortcut.")
            return

        # 1. Update tray state to WORKING
        self.set_tray_state("working")

        # 2. Record mouse cursor position & foreground window handle
        cursor_x, cursor_y = get_cursor_pos()
        target_hwnd = get_foreground_hwnd()
        active_proc = get_active_process_name()

        # 3. Capture selection and save original clipboard
        selected_text, orig_clipboard = self.capture_engine.capture()
        if not selected_text:
            logger.debug("No text selected; pipeline canceled.")
            self.set_tray_state("idle")
            return

        # 4. Check input length constraint
        if len(selected_text) > self.config.max_input_chars:
            logger.warning(
                f"Selected text length ({len(selected_text)}) exceeds max_input_chars "
                f"({self.config.max_input_chars}). Operation canceled."
            )
            self.set_tray_state("error")
            return

        # 5. Determine mode: forced mode or auto-classification
        if force_mode in ("CODE", "PROSE"):
            mode = force_mode
        else:
            mode = classify_text(selected_text, active_proc, self.config.code_apps)
        logger.info(f"Target process: '{active_proc}' | Mode: {mode} | Length: {len(selected_text)}")

        # 6. Select model and prepare prompts
        model = self.config.model_code if mode == "CODE" else self.config.model_prose
        sys_prompt = get_system_prompt(mode)
        user_msg = format_user_message(selected_text)

        # 7. Show clear floating working indicator if enabled
        if self.config.show_indicator:
            self.ui.show_indicator(model=model, x=cursor_x, y=cursor_y)

        # 8. Query local Ollama model
        try:
            raw_output = self.llm_client.generate(
                model=model,
                system_prompt=sys_prompt,
                user_message=user_msg,
            )
        finally:
            self.ui.hide_indicator()

        if raw_output is None:
            logger.error("LLM generation returned no output. Indicating error state.")
            self.set_tray_state("error")
            return

        # 9. Sanitize and validate output
        sanitized = sanitize_output(raw_output, selected_text, mode=mode)
        if sanitized is None:
            logger.warning("Sanitizer rejected output. Keeping original selection.")
            self.set_tray_state("error")
            return

        # 10. Deliver output via Interactive Popup OR Direct Paste
        if self.config.show_popup:
            # Display floating popup with Replace & Copy buttons
            def _on_replace(final_text: str) -> None:
                self.capture_engine.paste_back(
                    text=final_text,
                    active_process=active_proc,
                    terminal_apps=self.config.terminal_apps,
                    restore_clipboard=self.config.restore_clipboard,
                    original_clipboard=orig_clipboard,
                    target_hwnd=target_hwnd,
                )
                self.set_tray_state("idle")

            def _on_copy(final_text: str) -> None:
                pyperclip.copy(final_text)
                self.set_tray_state("idle")

            self.ui.show_popup(
                original_text=selected_text,
                corrected_text=sanitized,
                mode=mode,
                on_replace=_on_replace,
                on_copy=_on_copy,
                x=cursor_x,
                y=cursor_y,
            )
        else:
            # Direct paste or clipboard delivery without popup
            if self.config.output_mode == "clipboard":
                pyperclip.copy(sanitized)
                logger.info("Output mode is 'clipboard': copied result to clipboard.")
            else:
                self.capture_engine.paste_back(
                    text=sanitized,
                    active_process=active_proc,
                    terminal_apps=self.config.terminal_apps,
                    restore_clipboard=self.config.restore_clipboard,
                    original_clipboard=orig_clipboard,
                    target_hwnd=target_hwnd,
                )
                logger.info("Successfully pasted transformed text over selection.")

            self.set_tray_state("idle")

    def register_hotkeys(self) -> None:
        """Register configured global hotkeys with pynput."""
        self.unregister_hotkeys()

        hotkey_bindings: dict[str, Any] = {}

        # Primary hotkey
        norm_primary = normalize_hotkey(self.config.hotkey)
        valid, _, _ = validate_hotkey(norm_primary)
        if valid:
            hotkey_bindings[norm_primary] = lambda: self.handle_hotkey_trigger(None)
            logger.info(f"Registered primary hotkey: {self.config.hotkey} ({norm_primary})")
        else:
            logger.error(f"Failed to register invalid hotkey: {self.config.hotkey}")

        # Optional force CODE hotkey
        if self.config.hotkey_code:
            norm_code = normalize_hotkey(self.config.hotkey_code)
            valid_c, _, _ = validate_hotkey(norm_code)
            if valid_c:
                hotkey_bindings[norm_code] = lambda: self.handle_hotkey_trigger("CODE")
                logger.info(f"Registered CODE hotkey: {self.config.hotkey_code} ({norm_code})")

        # Optional force PROSE hotkey
        if self.config.hotkey_prose:
            norm_prose = normalize_hotkey(self.config.hotkey_prose)
            valid_p, _, _ = validate_hotkey(norm_prose)
            if valid_p:
                hotkey_bindings[norm_prose] = lambda: self.handle_hotkey_trigger("PROSE")
                logger.info(f"Registered PROSE hotkey: {self.config.hotkey_prose} ({norm_prose})")

        if hotkey_bindings:
            try:
                self.hotkey_listener = GlobalHotKeys(hotkey_bindings)
                self.hotkey_listener.start()
            except Exception as exc:
                logger.error(f"Error starting GlobalHotKeys listener: {exc}")
                self.set_tray_state("error")

    def unregister_hotkeys(self) -> None:
        """Stop and unregister current hotkey listener."""
        if self.hotkey_listener:
            try:
                self.hotkey_listener.stop()
            except Exception as exc:
                logger.debug(f"Error stopping hotkey listener: {exc}")
            self.hotkey_listener = None

    def reload_config(self) -> None:
        """Reload configuration from disk and re-register hotkeys."""
        logger.info("Reloading configuration from disk...")
        new_config, warnings = load_config(self.config_path)
        for w in warnings:
            logger.warning(w)

        self.config = new_config
        self.llm_client.base_url = self.config.ollama_url
        self.llm_client.timeout = self.config.request_timeout_seconds
        self.llm_client.keep_alive = self.config.keep_alive
        self.llm_client.temperature = self.config.temperature

        self.register_hotkeys()
        self._check_ollama_status_async()
        logger.info("Configuration reloaded successfully.")

    def open_config_file(self) -> None:
        """Open the config.json file in the user's default text editor."""
        cfg_path = self.config_path.resolve()
        if not cfg_path.exists():
            self.config.save(cfg_path)

        try:
            if sys.platform == "win32":
                os.startfile(str(cfg_path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(cfg_path)])
            else:
                subprocess.Popen(["xdg-open", str(cfg_path)])
        except Exception as exc:
            logger.error(f"Could not open config file: {exc}")

    def toggle_enabled(self) -> None:
        """Toggle active enabled state of QuickFix."""
        self.config.enabled = not self.config.enabled
        self.config.save(self.config_path)
        logger.info(f"QuickFix enabled state set to: {self.config.enabled}")

    def toggle_indicator(self) -> None:
        """Toggle floating working indicator on/off."""
        self.config.show_indicator = not self.config.show_indicator
        self.config.save(self.config_path)
        logger.info(f"QuickFix show_indicator set to: {self.config.show_indicator}")

    def toggle_popup(self) -> None:
        """Toggle interactive result popup on/off."""
        self.config.show_popup = not self.config.show_popup
        self.config.save(self.config_path)
        logger.info(f"QuickFix show_popup set to: {self.config.show_popup}")

    def quit_application(self) -> None:
        """Cleanly shut down listeners and exit."""
        logger.info("Shutting down QuickFix...")
        self.unregister_hotkeys()
        self.ui.hide_indicator()
        self.ui.hide_popup()
        if self.tray_icon:
            self.tray_icon.stop()

    def _check_ollama_status_async(self) -> None:
        """Check Ollama connectivity and models asynchronously without stalling startup."""
        def _check() -> None:
            models_to_check = [self.config.model_code, self.config.model_prose]
            ok, msg = self.llm_client.check_connection_and_models(models_to_check)
            if not ok:
                self.set_tray_state("error")
                logger.error(f"Ollama startup check notice: {msg}")
            else:
                for m in set(models_to_check):
                    self.llm_client.warm_model(m)

        thread = threading.Thread(target=_check, name="OllamaHealthCheck", daemon=True)
        thread.start()

    def run(self) -> None:
        """Start the system tray application and event loop."""
        self.single_instance_handle = acquire_single_instance_mutex()
        if self.single_instance_handle is None:
            logger.error("Another instance of QuickFix is already running. Exiting.")
            sys.exit(1)

        logger.info("Starting QuickFix desktop service...")
        self.register_hotkeys()
        self._check_ollama_status_async()

        menu = pystray.Menu(
            pystray.MenuItem(
                "Enabled",
                lambda icon, item: self.toggle_enabled(),
                checked=lambda item: self.config.enabled,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Show Working Indicator",
                lambda icon, item: self.toggle_indicator(),
                checked=lambda item: self.config.show_indicator,
            ),
            pystray.MenuItem(
                "Show Result Popup Preview",
                lambda icon, item: self.toggle_popup(),
                checked=lambda item: self.config.show_popup,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open config file", lambda icon, item: self.open_config_file()),
            pystray.MenuItem("Reload config", lambda icon, item: self.reload_config()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda icon, item: self.quit_application()),
        )

        self.tray_icon = pystray.Icon(
            "QuickFix",
            icon=self.icon_idle,
            title="QuickFix: Ready",
            menu=menu,
        )

        logger.info("QuickFix is active in the system tray. Press hotkey to fix selection.")
        try:
            self.tray_icon.run()
        finally:
            self.unregister_hotkeys()


def main() -> None:
    """Entry point for QuickFix."""
    app = QuickFixApp()
    app.run()


if __name__ == "__main__":
    main()
