"""Heuristic-based CODE vs PROSE classifier.

Evaluates selection content and process context using a composite scoring model
to accurately determine whether to route input to code or prose transformation.
"""

from __future__ import annotations

import ast
import logging
import re
from typing import Sequence

logger = logging.getLogger("quickfix.classify")

# Code punctuation set for density calculation
CODE_PUNCTUATION = set("{}[]();=<>")

# Regular expressions for common programming language keywords & syntax
CODE_KEYWORD_PATTERNS = [
    re.compile(r"\b(def|class|import|return|function|const|let|var)\b"),
    re.compile(r"=>"),
    re.compile(r"#include\b"),
    re.compile(r"\bpublic\s+static\b"),
    re.compile(r"\bSELECT\b[\s\S]+?\bFROM\b", re.IGNORECASE),
    re.compile(r"\b(null|true|false)\b"),
]

# Set of high-frequency English words used to detect natural-language text
COMMON_ENGLISH_WORDS = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i", "it",
    "for", "not", "on", "with", "he", "as", "you", "do", "at", "this", "but",
    "his", "by", "from", "they", "we", "say", "her", "she", "or", "an", "will",
    "my", "one", "all", "would", "there", "their", "what", "so", "up", "out",
    "if", "about", "who", "get", "which", "go", "me", "when", "make", "can",
    "like", "time", "no", "just", "him", "know", "take", "people", "into",
    "year", "your", "good", "some", "could", "them", "see", "other", "than",
    "then", "now", "look", "only", "come", "its", "over", "think", "also",
    "back", "after", "use", "two", "how", "our", "work", "first", "well",
    "way", "even", "new", "want", "because", "any", "these", "give", "day",
    "most", "us", "yesterday", "tomorrow", "please", "thank", "thanks",
    "went", "bought", "store", "milk", "write", "poem", "instructions",
}


def classify_text(
    text: str,
    active_process: str = "",
    code_apps: Sequence[str] | None = None,
) -> str:
    """Classify the input text as 'CODE' or 'PROSE'.

    Scoring rules:
      +3 if the active process is in code_apps
      +2 if text contains common code keywords/tokens
      +2 if density of characters from {}[]();=<> exceeds 5% of text
      +1 if most lines are consistently indented or end with : { or ;
      -3 if it looks like natural-language sentences (ends with . ? !, many common English words)
      +2 bonus if Python's ast.parse succeeds (never required)

    CODE if score >= 3, else PROSE.

    Args:
        text: Selection text to classify.
        active_process: Name of foreground application executable.
        code_apps: List of process names treated as code editors.

    Returns:
        'CODE' or 'PROSE'
    """
    if not text or not text.strip():
        return "PROSE"

    score = 0
    stripped_text = text.strip()

    # 1. Process context (+3 if in code_apps)
    if active_process and code_apps:
        proc_clean = active_process.lower().strip()
        code_app_set = {app.lower().strip() for app in code_apps}
        if proc_clean in code_app_set:
            score += 3
            logger.debug(f"[Classifier] Process '{proc_clean}' matched code_apps (+3)")

    # 2. Code keywords / tokens (+2)
    has_code_keywords = any(pattern.search(text) for pattern in CODE_KEYWORD_PATTERNS)
    if has_code_keywords:
        score += 2
        logger.debug("[Classifier] Matched code keywords/tokens (+2)")

    # 3. Density of {}[]();=<> (> 5% of text) (+2)
    if text:
        code_char_count = sum(1 for ch in text if ch in CODE_PUNCTUATION)
        density = code_char_count / len(text)
        if density > 0.05:
            score += 2
            logger.debug(f"[Classifier] Code punctuation density {density:.2%} > 5% (+2)")

    # 4. Indentation or structured line endings (: { ;) (+1)
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if lines:
        qualifying_lines = 0
        for line in lines:
            stripped = line.strip()
            # Ends with colon, brace, or semicolon
            ends_with_syntax = stripped.endswith((":", "{", ";"))
            # Has leading indentation (>= 2 spaces or tab)
            is_indented = line.startswith(("  ", "\t"))
            if ends_with_syntax or is_indented:
                qualifying_lines += 1

        if qualifying_lines / len(lines) > 0.5:
            score += 1
            logger.debug("[Classifier] Structured line endings / indentation detected (+1)")

    # 5. Natural-language sentence detection (-3)
    ends_with_sentence_punct = stripped_text.endswith((".", "?", "!"))
    words = re.findall(r"\b[a-zA-Z]{2,}\b", text.lower())
    common_words_count = sum(1 for w in words if w in COMMON_ENGLISH_WORDS)

    # If it ends with sentence punctuation and has multiple common English words
    if ends_with_sentence_punct and common_words_count >= 2:
        score -= 3
        logger.debug(
            f"[Classifier] Natural language sentence structure detected (ends with punctuation, "
            f"{common_words_count} common words) (-3)"
        )

    # 6. Python ast.parse bonus (+2) - never required
    try:
        ast.parse(text)
        score += 2
        logger.debug("[Classifier] Python ast.parse bonus (+2)")
    except Exception:
        pass

    result = "CODE" if score >= 3 else "PROSE"
    logger.debug(f"[Classifier] Final score: {score} -> {result}")
    return result
