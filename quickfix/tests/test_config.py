"""Tests for config.py hotkey validation and normalization."""

import json
from pathlib import Path
from quickfix.config import (
    Config,
    load_config,
    normalize_hotkey,
    validate_hotkey,
)


def test_normalize_hotkey():
    """Human-readable shortcuts normalize to valid pynput tokens."""
    assert normalize_hotkey("ctrl+alt+w") == "<ctrl>+<alt>+w"
    assert normalize_hotkey("CTRL+SHIFT+F9") == "<ctrl>+<shift>+<f9>"
    assert normalize_hotkey("alt+shift+k") == "<alt>+<shift>+k"
    assert normalize_hotkey("<ctrl>+<alt>+w") == "<ctrl>+<alt>+w"


def test_validate_hotkey():
    """Valid hotkeys pass and invalid hotkeys report errors."""
    valid, norm, err = validate_hotkey("ctrl+alt+w")
    assert valid is True
    assert norm == "<ctrl>+<alt>+w"

    valid, norm, err = validate_hotkey("ctrl+shift+f9")
    assert valid is True

    valid, norm, err = validate_hotkey("")
    assert valid is False

    valid, norm, err = validate_hotkey("ctrl+invalidkeythatdoesnotexist")
    assert valid is False


def test_config_fallback_on_invalid_hotkey(tmp_path: Path):
    """If config file specifies an invalid hotkey, fall back to default 'ctrl+alt+w'."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"hotkey": "invalid_combo_???", "output_mode": "replace"}),
        encoding="utf-8",
    )

    cfg, warnings = load_config(cfg_file)
    assert cfg.hotkey == "ctrl+alt+w"
    assert len(warnings) > 0


def test_config_save_and_load(tmp_path: Path):
    """Config can be persisted and reloaded correctly."""
    cfg_file = tmp_path / "config.json"
    original = Config(
        hotkey="ctrl+shift+f9",
        model_code="qwen2.5-coder:7b",
        temperature=0.3,
        request_timeout_seconds=45.0,
    )
    original.save(cfg_file)

    loaded, warnings = load_config(cfg_file)
    assert loaded.hotkey == "ctrl+shift+f9"
    assert loaded.model_code == "qwen2.5-coder:7b"
    assert loaded.temperature == 0.3
    assert loaded.request_timeout_seconds == 45.0
