"""Selection capture and paste-back engine.

Safely captures the user's current text selection across arbitrary desktop
applications via synthetic keystrokes, clipboard management with sentinel
verification, modifier state resolution, and automatic clipboard restoration.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Tuple

import pyperclip
from pynput.keyboard import Controller, Key

from .platform_win import (
    is_terminal_window,
    release_modifiers,
    wait_for_modifiers_release,
)

logger = logging.getLogger("quickfix.capture")


class SelectionCapture:
    """Manages clipboard capture and paste simulation."""

    def __init__(self, keyboard: Controller | None = None) -> None:
        """Initialize selection capture with a keyboard controller.

        Args:
            keyboard: Optional existing pynput Controller.
        """
        self.keyboard = keyboard or Controller()

    def capture(
        self,
        modifier_timeout: float = 1.0,
        poll_timeout: float = 0.30,
    ) -> Tuple[str | None, str]:
        """Capture the currently selected text in the active window.

        Waits for physical modifier keys to be released, saves the existing
        clipboard, copies the selection using Ctrl+C with sentinel polling,
        and guarantees the sentinel is never left on the clipboard.

        Args:
            modifier_timeout: Maximum seconds to wait for modifiers release.
            poll_timeout: Maximum seconds to poll clipboard for Ctrl+C result.

        Returns:
            Tuple of (selected_text_or_None, original_clipboard_text).
        """
        # 1. Wait for user to release physical hotkey modifier keys
        wait_for_modifiers_release(timeout=modifier_timeout)
        release_modifiers(self.keyboard)
        time.sleep(0.02)  # Short stabilization pause

        # 2. Save current clipboard content safely
        try:
            original_clipboard = pyperclip.paste()
        except Exception as exc:
            logger.debug(f"Unable to read initial clipboard: {exc}")
            original_clipboard = ""

        # 3. Create and set unique sentinel to detect copy success
        sentinel = f"__QUICKFIX_SENTINEL_{uuid.uuid4().hex}__"
        captured_text: str | None = None

        try:
            pyperclip.copy(sentinel)
            time.sleep(0.015)

            # 4. Synthesize Ctrl+C
            self.keyboard.press(Key.ctrl)
            self.keyboard.press("c")
            time.sleep(0.02)
            self.keyboard.release("c")
            self.keyboard.release(Key.ctrl)

            # 5. Poll clipboard every 10 ms up to poll_timeout
            deadline = time.perf_counter() + poll_timeout
            while time.perf_counter() < deadline:
                time.sleep(0.01)
                try:
                    current_clip = pyperclip.paste()
                except Exception:
                    continue

                if current_clip != sentinel:
                    captured_text = current_clip
                    break

        except Exception as exc:
            logger.error(f"Error during selection capture: {exc}")
        finally:
            # 6. Never leave sentinel on clipboard
            try:
                current_val = pyperclip.paste()
                if current_val == sentinel:
                    pyperclip.copy(original_clipboard)
            except Exception as exc:
                logger.debug(f"Clipboard cleanup error: {exc}")

        # If no new selection was captured or text is empty, restore clipboard
        if captured_text is None or captured_text == "":
            try:
                pyperclip.copy(original_clipboard)
            except Exception:
                pass
            return None, original_clipboard

        return captured_text, original_clipboard

    def paste_back(
        self,
        text: str,
        active_process: str,
        terminal_apps: list[str],
        restore_clipboard: bool,
        original_clipboard: str,
        target_hwnd: int = 0,
    ) -> None:
        """Paste modified text back over the active selection.

        Args:
            text: Sanitized text to paste.
            active_process: Name of foreground application.
            terminal_apps: List of terminal process names requiring Ctrl+Shift+V.
            restore_clipboard: If True, restore user's original clipboard afterwards.
            original_clipboard: Original clipboard contents prior to capture.
            target_hwnd: Optional window handle to ensure focus before paste.
        """
        try:
            # Re-activate target window if known
            if target_hwnd:
                try:
                    import win32gui
                    win32gui.SetForegroundWindow(target_hwnd)
                    time.sleep(0.04)
                except Exception:
                    pass

            # Ensure all modifiers are released before synthesizing Ctrl+V
            release_modifiers(self.keyboard)
            time.sleep(0.02)

            # Write target text to clipboard
            pyperclip.copy(text)
            time.sleep(0.04)

            # Check if target is a terminal window requiring Ctrl+Shift+V
            is_term = is_terminal_window(active_process, terminal_apps)

            if is_term:
                logger.debug(f"Pasting into terminal application '{active_process}' with Ctrl+Shift+V")
                self.keyboard.press(Key.ctrl)
                self.keyboard.press(Key.shift)
                time.sleep(0.02)
                self.keyboard.press("v")
                time.sleep(0.03)
                self.keyboard.release("v")
                self.keyboard.release(Key.shift)
                self.keyboard.release(Key.ctrl)
            else:
                logger.debug(f"Pasting into standard application '{active_process}' with Ctrl+V")
                self.keyboard.press(Key.ctrl)
                time.sleep(0.02)
                self.keyboard.press("v")
                time.sleep(0.03)
                self.keyboard.release("v")
                self.keyboard.release(Key.ctrl)

            # Wait ~180 ms for target app to absorb clipboard data
            time.sleep(0.18)

        except Exception as exc:
            logger.error(f"Error during paste-back simulation: {exc}")
        finally:
            if restore_clipboard:
                try:
                    pyperclip.copy(original_clipboard)
                except Exception as exc:
                    logger.warning(f"Failed to restore original clipboard: {exc}")
