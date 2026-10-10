"""Output sanitization and validation for QuickFix.

Strips code fences, conversational preambles/postambles, enforces length limits,
verifies AST integrity for Python code, and preserves exact trailing newline behavior.
"""

from __future__ import annotations

import ast
import logging
import re

logger = logging.getLogger("quickfix.sanitize")

# Regex to match conversational preambles
PREAMBLE_PATTERN = re.compile(
    r"^(?:here\s+is\s+(?:the\s+)?(?:fixed|corrected)\s+(?:code|text|version)[:!\.]?|"
    r"here\'s\s+(?:the\s+)?(?:fixed|corrected)\s+(?:code|text|version)[:!\.]?|"
    r"sure!?[\s,]+(?:here\s+is|I\s+have|here\s+you\s+go).*?[:!\.]?|"
    r"sure[!:\.,]?|"
    r"certainly!?[\s,]+(?:here\s+is|I\s+have).*?[:!\.]?|"
    r"certainly[!:\.,]?|"
    r"corrected\s+text[:!\.]?|"
    r"fixed\s+code[:!\.]?|"
    r"rewritten\s+text[:!\.]?)\s*",
    re.IGNORECASE,
)

# Regex to match conversational postambles
POSTAMBLE_PATTERN = re.compile(
    r"\r?\n+(?:hope\s+this\s+helps!?|"
    r"let\s+me\s+know\s+if\s+you\s+need.*|"
    r"this\s+(?:fixes|corrects)\s+the\s+.*)\s*$",
    re.IGNORECASE,
)

# Regex for Markdown fenced code blocks: ```lang ... ```
FENCE_PATTERN = re.compile(
    r"```(?:[a-zA-Z0-9_\-\.\+#]*\r?\n)?([\s\S]*?)```"
)


def strip_code_fences_and_preamble(text: str) -> str:
    """Extract core content by removing markdown fences and boilerplate lines.

    Args:
        text: Raw LLM output.

    Returns:
        Cleaned text.
    """
    cleaned = text.strip()

    # If output contains markdown fenced block, extract block content
    fence_match = FENCE_PATTERN.search(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    else:
        # Strip common preamble lines
        cleaned = PREAMBLE_PATTERN.sub("", cleaned).strip()
        # Strip common postambles
        cleaned = POSTAMBLE_PATTERN.sub("", cleaned).strip()

    return cleaned


def strip_unwanted_quotes(text: str, original: str) -> str:
    """Strip surrounding quotes if model added them unnecessarily in prose mode.

    Args:
        text: Cleaned text.
        original: Original user input.

    Returns:
        De-quoted text if appropriate.
    """
    orig_stripped = original.strip()
    # If the original did not start and end with matching quotes, strip quotes from output
    if len(text) >= 2:
        if (text.startswith('"') and text.endswith('"')) and not (
            orig_stripped.startswith('"') and orig_stripped.endswith('"')
        ):
            return text[1:-1].strip()
        if (text.startswith("'") and text.endswith("'")) and not (
            orig_stripped.startswith("'") and orig_stripped.endswith("'")
        ):
            return text[1:-1].strip()
    return text


def sanitize_output(
    raw_output: str | None,
    original_input: str,
    mode: str = "PROSE",
) -> str | None:
    """Validate and sanitize LLM output.

    Enforces:
      - Strips fences and preambles
      - Rejection if output is empty
      - Rejection if runaway length (> 3x original + 200 chars)
      - Rejection if original was valid Python but output fails ast.parse (CODE mode)
      - Preserves original trailing-newline behavior

    Args:
        raw_output: Raw string from LLM response.
        original_input: Original text selected by the user.
        mode: Classification mode ('CODE' or 'PROSE').

    Returns:
        Sanitized string, or None if rejected.
    """
    if raw_output is None or not raw_output.strip():
        logger.warning("Sanitizer rejected: empty or null output from LLM.")
        return None

    # 1. Clean fences and preambles
    cleaned = strip_code_fences_and_preamble(raw_output)

    # 2. In PROSE mode, strip surrounding quotes if LLM added them
    if mode.upper() == "PROSE":
        cleaned = strip_unwanted_quotes(cleaned, original_input)

    # 3. Check for empty output after stripping
    if not cleaned:
        logger.warning("Sanitizer rejected: output is empty after stripping.")
        return None

    # 4. Length check: reject if output is more than 3x original length plus 200 chars
    max_allowed_len = 3 * len(original_input) + 200
    if len(cleaned) > max_allowed_len:
        logger.warning(
            f"Sanitizer rejected runaway output: len={len(cleaned)} > allowed={max_allowed_len}"
        )
        return None

    # 5. Python AST verification in CODE mode
    if mode.upper() == "CODE":
        original_parsed_as_python = False
        try:
            ast.parse(original_input)
            original_parsed_as_python = True
        except Exception:
            original_parsed_as_python = False

        if original_parsed_as_python:
            try:
                ast.parse(cleaned)
            except Exception as exc:
                logger.warning(
                    f"Sanitizer rejected output: original parsed as Python, but output failed ({exc})"
                )
                return None

    # 6. Preserve trailing newline behavior
    original_has_newline = original_input.endswith("\n") or original_input.endswith("\r\n")
    if original_has_newline:
        if not (cleaned.endswith("\n") or cleaned.endswith("\r\n")):
            newline_char = "\r\n" if "\r\n" in original_input else "\n"
            cleaned = cleaned + newline_char
    else:
        cleaned = cleaned.rstrip("\r\n")

    return cleaned
