"""Configuration management for QuickFix.

Handles loading, validating, and persisting user configuration from config.json.
Provides sensible defaults and normalizes global hotkey definitions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pynput.keyboard import HotKey, Key

logger = logging.getLogger("quickfix.config")

DEFAULT_CONFIG_PATH = Path("config.json")

DEFAULT_CODE_APPS = [
    "code.exe",
    "pycharm64.exe",
    "devenv.exe",
    "sublime_text.exe",
    "idea64.exe",
    "notepad++.exe",
    "clion64.exe",
    "webstorm64.exe",
    "cursor.exe",
    "windsurf.exe",
    "vims.exe",
    "nvim.exe",
    "emacs.exe",
]

DEFAULT_TERMINAL_APPS = [
    "windowsterminal.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "conhost.exe",
    "mintty.exe",
    "alacritty.exe",
    "wezterm-gui.exe",
    "hyper.exe",
]


def normalize_hotkey(hotkey_str: str) -> str:
    """Normalize a human-readable hotkey string to pynput format.

    E.g. 'ctrl+alt+w' -> '<ctrl>+<alt>+w'
         'ctrl+shift+f9' -> '<ctrl>+<shift>+<f9>'

    Args:
        hotkey_str: Shortcut string such as 'ctrl+alt+w'.

    Returns:
        Canonical pynput HotKey string.
    """
    if not hotkey_str or not hotkey_str.strip():
        return ""

    tokens = [t.strip().lower() for t in hotkey_str.split("+") if t.strip()]
    normalized_parts: list[str] = []

    modifier_map = {
        "ctrl": "<ctrl>",
        "control": "<ctrl>",
        "alt": "<alt>",
        "menu": "<alt>",
        "shift": "<shift>",
        "cmd": "<cmd>",
        "win": "<cmd>",
        "super": "<cmd>",
    }

    for token in tokens:
        if token in modifier_map:
            normalized_parts.append(modifier_map[token])
        elif token.startswith("<") and token.endswith(">"):
            normalized_parts.append(token)
        elif hasattr(Key, token):
            normalized_parts.append(f"<{token}>")
        else:
            # Handle function keys f1 - f24 or single characters
            if token.startswith("f") and token[1:].isdigit():
                normalized_parts.append(f"<{token}>")
            else:
                normalized_parts.append(token)

    return "+".join(normalized_parts)


def validate_hotkey(hotkey_str: str) -> tuple[bool, str, str]:
    """Validate whether a hotkey string can be parsed by pynput.

    Args:
        hotkey_str: Raw hotkey string.

    Returns:
        Tuple of (is_valid, normalized_hotkey, error_message).
    """
    if not hotkey_str or not hotkey_str.strip():
        return False, "", "Empty hotkey string"

    normalized = normalize_hotkey(hotkey_str)
    try:
        keys = HotKey.parse(normalized)
        if not keys:
            return False, "", "No keys parsed from hotkey string"
        return True, normalized, ""
    except Exception as exc:
        return False, "", str(exc)


@dataclass
class Config:
    """QuickFix runtime configuration."""

    hotkey: str = "ctrl+alt+w"
    hotkey_code: str = ""
    hotkey_prose: str = ""
    output_mode: str = "replace"
    ollama_url: str = "http://localhost:11434"
    model_prose: str = "qwen2.5:7b"
    model_code: str = "qwen2.5:7b"
    request_timeout_seconds: float = 90.0
    keep_alive: str = "30m"
    temperature: float = 0.2
    code_apps: list[str] = field(default_factory=lambda: list(DEFAULT_CODE_APPS))
    terminal_apps: list[str] = field(default_factory=lambda: list(DEFAULT_TERMINAL_APPS))
    restore_clipboard: bool = True
    max_input_chars: int = 8000
    enabled: bool = True
    show_indicator: bool = True
    show_popup: bool = True

    def validate_and_normalize(self) -> list[str]:
        """Validate config values and fall back to safe defaults when invalid.

        Returns:
            List of warning messages for any corrected invalid fields.
        """
        warnings: list[str] = []

        # Validate primary hotkey
        valid, _, err = validate_hotkey(self.hotkey)
        if not valid:
            warnings.append(
                f"Invalid primary hotkey '{self.hotkey}' ({err}). "
                f"Falling back to default 'ctrl+alt+w'."
            )
            self.hotkey = "ctrl+alt+w"

        # Validate optional force-mode hotkeys
        if self.hotkey_code:
            valid_code, _, err_code = validate_hotkey(self.hotkey_code)
            if not valid_code:
                warnings.append(
                    f"Invalid hotkey_code '{self.hotkey_code}' ({err_code}). Disabling."
                )
                self.hotkey_code = ""

        if self.hotkey_prose:
            valid_prose, _, err_prose = validate_hotkey(self.hotkey_prose)
            if not valid_prose:
                warnings.append(
                    f"Invalid hotkey_prose '{self.hotkey_prose}' ({err_prose}). Disabling."
                )
                self.hotkey_prose = ""

        # Validate output_mode
        if self.output_mode not in ("replace", "clipboard"):
            warnings.append(
                f"Invalid output_mode '{self.output_mode}'. Defaulting to 'replace'."
            )
            self.output_mode = "replace"

        # Validate numeric constraints
        if self.request_timeout_seconds <= 0:
            warnings.append("request_timeout_seconds must be positive. Defaulting to 30.")
            self.request_timeout_seconds = 30.0

        if self.max_input_chars <= 0:
            warnings.append("max_input_chars must be positive. Defaulting to 8000.")
            self.max_input_chars = 8000

        if not (0.0 <= self.temperature <= 2.0):
            warnings.append("temperature should be between 0.0 and 2.0. Defaulting to 0.2.")
            self.temperature = 0.2

        # Clean URLs
        self.ollama_url = self.ollama_url.rstrip("/")

        # Lowercase process names for case-insensitive matching
        self.code_apps = [app.lower().strip() for app in self.code_apps if app.strip()]
        self.terminal_apps = [app.lower().strip() for app in self.terminal_apps if app.strip()]

        return warnings

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to serializable dictionary."""
        return asdict(self)

    def save(self, config_path: Path | str = DEFAULT_CONFIG_PATH) -> None:
        """Save configuration to JSON file.

        Args:
            config_path: Path where config.json will be saved.
        """
        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Saved configuration to {path.resolve()}")


def load_config(config_path: Path | str = DEFAULT_CONFIG_PATH) -> tuple[Config, list[str]]:
    """Load configuration from file or create defaults if missing.

    Args:
        config_path: Target path to config.json.

    Returns:
        Tuple of (Config instance, list of validation warning messages).
    """
    path = Path(config_path)
    if not path.exists():
        cfg = Config()
        cfg.save(path)
        logger.info(f"Created default config at {path.resolve()}")
        return cfg, []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        cfg = Config(
            hotkey=data.get("hotkey", "ctrl+alt+w"),
            hotkey_code=data.get("hotkey_code", ""),
            hotkey_prose=data.get("hotkey_prose", ""),
            output_mode=data.get("output_mode", "replace"),
            ollama_url=data.get("ollama_url", "http://localhost:11434"),
            model_prose=data.get("model_prose", "qwen2.5:7b"),
            model_code=data.get("model_code", "qwen2.5:7b"),
            request_timeout_seconds=float(data.get("request_timeout_seconds", 30)),
            keep_alive=str(data.get("keep_alive", "30m")),
            temperature=float(data.get("temperature", 0.2)),
            code_apps=list(data.get("code_apps", DEFAULT_CODE_APPS)),
            terminal_apps=list(data.get("terminal_apps", DEFAULT_TERMINAL_APPS)),
            restore_clipboard=bool(data.get("restore_clipboard", True)),
            max_input_chars=int(data.get("max_input_chars", 8000)),
            enabled=bool(data.get("enabled", True)),
            show_indicator=bool(data.get("show_indicator", True)),
            show_popup=bool(data.get("show_popup", True)),
        )
    except Exception as exc:
        logger.error(f"Error reading {path}: {exc}. Using defaults.")
        cfg = Config()
        return cfg, [f"Failed to read config file ({exc}); defaults loaded."]

    warnings = cfg.validate_and_normalize()
    return cfg, warnings
