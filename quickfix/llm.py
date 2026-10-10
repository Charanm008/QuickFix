"""Ollama LLM client integration for QuickFix.

Connects strictly to local Ollama instance via the /api/chat endpoint.
Logs all operational errors to rotating quickfix.log without throwing exceptions
into the hotkey handler.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Sequence

import requests

LOG_FILE = Path("quickfix.log")

# Setup dedicated logger for QuickFix
logger = logging.getLogger("quickfix")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=2 * 1024 * 1024,  # 2 MB
        backupCount=3,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


class OllamaClient:
    """Handles communication with local Ollama service."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: float = 30.0,
        keep_alive: str = "30m",
        temperature: float = 0.2,
    ) -> None:
        """Initialize Ollama client.

        Args:
            base_url: Local Ollama service URL.
            timeout: Maximum request duration in seconds.
            keep_alive: Duration to keep model in GPU/RAM memory.
            temperature: Sampling temperature for generation.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.temperature = temperature

    def check_connection_and_models(
        self,
        required_models: Sequence[str],
    ) -> tuple[bool, str]:
        """Verify that Ollama is reachable and configured models are downloaded.

        Args:
            required_models: List of model identifiers expected to exist locally.

        Returns:
            Tuple of (all_ok_bool, status_or_error_message).
        """
        tags_url = f"{self.base_url}/api/tags"
        try:
            resp = requests.get(tags_url, timeout=5.0)
            if resp.status_code != 200:
                err_msg = f"Ollama returned HTTP {resp.status_code} at {tags_url}"
                logger.error(err_msg)
                return False, err_msg

            data = resp.json()
            available_models = [m.get("name", "") for m in data.get("models", [])]

            missing_models: list[str] = []
            for req in set(required_models):
                if not req:
                    continue
                # Match either exact tag (e.g. 'qwen2.5:7b') or base model name
                req_norm = req.lower()
                matched = any(
                    req_norm == m.lower()
                    or req_norm == m.lower().split(":")[0]
                    or (":" not in req_norm and req_norm == m.lower().split(":")[0])
                    for m in available_models
                )
                if not matched:
                    missing_models.append(req)

            if missing_models:
                pull_cmds = " && ".join(f"ollama pull {m}" for m in missing_models)
                msg = (
                    f"Configured model(s) not found in local Ollama: {', '.join(missing_models)}. "
                    f"Please run: '{pull_cmds}'"
                )
                logger.error(msg)
                return False, msg

            logger.info("Ollama connection verified and required models are available.")
            return True, "Ready"

        except requests.exceptions.ConnectionError:
            msg = (
                f"Could not connect to Ollama at {self.base_url}. "
                "Ensure Ollama is installed and running locally."
            )
            logger.error(msg)
            return False, msg
        except Exception as exc:
            msg = f"Unexpected error checking Ollama status: {exc}"
            logger.error(msg)
            return False, msg

    def warm_model(self, model: str) -> None:
        """Issue a tiny prompt to pre-load the model into memory.

        Args:
            model: Model name to warm up.
        """
        if not model:
            return
        logger.info(f"Warming model '{model}' in background...")
        try:
            self.generate(
                model=model,
                system_prompt="Return 'OK'.",
                user_message="ping",
                timeout=45.0,
            )
            logger.info(f"Model '{model}' is warm and ready.")
        except Exception as exc:
            logger.debug(f"Model warming failed (non-critical): {exc}")

    def generate(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        timeout: float | None = None,
    ) -> str | None:
        """Call Ollama /api/chat endpoint to transform text.

        Never raises exceptions; logs errors and returns None on any failure.

        Args:
            model: Ollama model name.
            system_prompt: System role instructions.
            user_message: Formatted user content.
            timeout: Optional override for request timeout.

        Returns:
            Assistant content string, or None if request failed.
        """
        chat_url = f"{self.base_url}/api/chat"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_ctx": 4096,
            },
        }

        req_timeout = timeout if timeout is not None else self.timeout

        try:
            logger.debug(f"Sending request to {chat_url} using model '{model}'")
            resp = requests.post(chat_url, json=payload, timeout=req_timeout)

            if resp.status_code != 200:
                logger.error(
                    f"Ollama request failed with HTTP {resp.status_code}: {resp.text}"
                )
                return None

            data = resp.json()
            message = data.get("message", {})
            content = message.get("content", "")

            if not content:
                # If content is empty but model produced thinking, attempt fallback extraction
                thinking_text = message.get("thinking", "")
                if thinking_text:
                    logger.debug("Ollama returned empty content with thinking text; inspecting thinking output.")
                    lines = [line.strip() for line in thinking_text.splitlines() if line.strip()]
                    if lines:
                        content = lines[-1]

            if not content:
                logger.warning("Ollama returned an empty response.")
                return None

            return content

        except requests.exceptions.Timeout:
            logger.error(
                f"Ollama request timed out after {req_timeout}s. "
                "Consider increasing request_timeout_seconds in config.json."
            )
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f"Cannot reach Ollama at {chat_url}. Is Ollama running?")
            return None
        except Exception as exc:
            logger.error(f"Ollama API request failed: {exc}")
            return None
