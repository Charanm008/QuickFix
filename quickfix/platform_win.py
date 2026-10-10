"""Windows-specific platform integrations for QuickFix.

Keeps all OS-specific dependencies (win32gui, win32process, win32api, win32event,
psutil, winsound) isolated in this module so other operating systems can be
supported with alternative implementations.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import psutil
from pynput.keyboard import Controller, Key

logger = logging.getLogger("quickfix.platform_win")

# Windows Virtual-Key Codes for modifier keys
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt key
VK_LWIN = 0x5B
VK_RWIN = 0x5C

# Windows Error Code for already existing mutex
ERROR_ALREADY_EXISTS = 183


def get_foreground_hwnd() -> int:
    """Return the HWND handle of the current foreground window."""
    try:
        import win32gui
        return win32gui.GetForegroundWindow() or 0
    except Exception:
        return 0


def get_cursor_pos() -> tuple[int, int]:
    """Return the current mouse cursor screen coordinates."""
    try:
        import win32gui
        return win32gui.GetCursorPos()
    except Exception:
        try:
            from pynput.mouse import Controller
            pos = Controller().position
            if pos:
                return int(pos[0]), int(pos[1])
        except Exception:
            pass
        return 0, 0


def get_active_process_name() -> str:
    """Return the executable name of the current foreground window in lowercase.

    Returns:
        Process executable name (e.g. 'code.exe', 'notepad.exe'), or empty string
        if unavailable.
    """
    try:
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ""

        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid <= 0 or not psutil.pid_exists(pid):
            return ""

        proc = psutil.Process(pid)
        return proc.name().lower()
    except Exception as exc:
        logger.debug(f"Failed to resolve active window process: {exc}")
        return ""


def are_modifiers_pressed() -> bool:
    """Check whether any keyboard modifier key (Ctrl, Alt, Shift, Win) is held down.

    Returns:
        True if at least one modifier key is physically pressed down.
    """
    try:
        import win32api

        for vk in (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN):
            # High-order bit is set if the key is down
            if win32api.GetAsyncKeyState(vk) & 0x8000:
                return True
        return False
    except Exception as exc:
        logger.debug(f"Error checking modifier key state: {exc}")
        return False


def wait_for_modifiers_release(timeout: float = 1.0) -> None:
    """Poll modifier key state until all are released or timeout expires.

    Args:
        timeout: Maximum seconds to wait for user to release hotkey modifiers.
    """
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if not are_modifiers_pressed():
            break
        time.sleep(0.015)


def release_modifiers(controller: Controller | None = None) -> None:
    """Explicitly release all modifier keys via synthetic keyboard events.

    Ensures that lingering OS modifier states do not corrupt the subsequent
    copy or paste keystrokes.

    Args:
        controller: Optional pynput keyboard Controller. Creates one if None.
    """
    kb = controller or Controller()
    for key in (
        Key.alt,
        Key.alt_l,
        Key.alt_r,
        Key.ctrl,
        Key.ctrl_l,
        Key.ctrl_r,
        Key.shift,
        Key.shift_l,
        Key.shift_r,
        Key.cmd,
        Key.cmd_l,
        Key.cmd_r,
    ):
        try:
            kb.release(key)
        except Exception:
            pass


def is_terminal_window(process_name: str, terminal_apps: list[str]) -> bool:
    """Determine whether the specified process is a terminal emulator.

    Args:
        process_name: Executable name of target process.
        terminal_apps: List of recognized terminal process names.

    Returns:
        True if process is in terminal_apps list.
    """
    if not process_name:
        return False
    proc = process_name.lower().strip()
    terminals = {app.lower().strip() for app in terminal_apps}
    return proc in terminals


def play_beep(frequency: int = 400, duration: int = 250) -> None:
    """Play a short system beep on error without interrupting execution.

    Args:
        frequency: Audio tone frequency in Hertz.
        duration: Tone duration in milliseconds.
    """
    try:
        import winsound

        winsound.Beep(frequency, duration)
    except Exception as exc:
        logger.debug(f"Unable to play system beep: {exc}")


def acquire_single_instance_mutex(
    mutex_name: str = "Global\\QuickFix_SingleInstance_Mutex_2026",
) -> Any:
    """Acquire a Windows named mutex to prevent concurrent application instances.

    Args:
        mutex_name: Unique global identifier for the application mutex.

    Returns:
        Mutex handle if acquired as first instance, or None if already running.
    """
    try:
        import win32api
        import win32event

        mutex = win32event.CreateMutex(None, False, mutex_name)
        last_error = win32api.GetLastError()
        if last_error == ERROR_ALREADY_EXISTS:
            logger.warning("Another instance of QuickFix is already running.")
            return None
        return mutex
    except Exception as exc:
        logger.warning(f"Could not create Windows mutex ({exc}); proceeding without guard.")
        return True
