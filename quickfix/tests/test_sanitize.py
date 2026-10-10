"""Tests for sanitize.py output cleaning and validation."""

from quickfix.sanitize import sanitize_output, strip_code_fences_and_preamble


def test_fenced_code_block_stripping():
    """Markdown code fences with language tags must be cleanly stripped."""
    raw = "```python\ndef add(a, b):\n    return a + b\n```"
    original = "def add(a,b):\nreturn a+b"
    result = sanitize_output(raw, original, mode="CODE")
    assert result == "def add(a, b):\n    return a + b"


def test_fenced_code_block_without_lang():
    """Markdown code fences without language tags must be stripped."""
    raw = "```\nconst x = 10;\n```"
    original = "const x=10"
    result = sanitize_output(raw, original, mode="CODE")
    assert result == "const x = 10;"


def test_preamble_stripping_code():
    """Common conversational preambles must be removed."""
    raw = "Here is the fixed code:\n\ndef add(a, b):\n    return a + b"
    original = "def add(a,b):\nreturn a+b"
    result = sanitize_output(raw, original, mode="CODE")
    assert result == "def add(a, b):\n    return a + b"


def test_preamble_stripping_prose():
    """Conversational preambles in prose mode must be removed."""
    raw = "Sure! I went to the store yesterday and bought milk."
    original = "i has went to the store yesterday and buyed milk"
    result = sanitize_output(raw, original, mode="PROSE")
    assert result == "I went to the store yesterday and bought milk."


def test_postamble_stripping():
    """Conversational postambles must be removed."""
    raw = "I went to the store yesterday and bought milk.\n\nHope this helps!"
    original = "i has went to the store yesterday and buyed milk"
    result = sanitize_output(raw, original, mode="PROSE")
    assert result == "I went to the store yesterday and bought milk."


def test_quotes_stripping_in_prose():
    """Surrounding quotation marks added by LLM in prose mode must be stripped."""
    raw = '"I went to the store yesterday and bought milk."'
    original = "i has went to the store yesterday and buyed milk"
    result = sanitize_output(raw, original, mode="PROSE")
    assert result == "I went to the store yesterday and bought milk."


def test_empty_or_whitespace_output_rejected():
    """Empty or blank model output must return None."""
    assert sanitize_output("", "hello world", mode="PROSE") is None
    assert sanitize_output("   \n\t  ", "hello world", mode="PROSE") is None
    assert sanitize_output(None, "hello world", mode="PROSE") is None


def test_runaway_output_rejected():
    """Output exceeding 3x original length plus 200 chars must be rejected."""
    original = "Short input sentence."
    # len(original) is 21. Max allowed: 3 * 21 + 200 = 263 chars
    runaway = "A" * 300
    result = sanitize_output(runaway, original, mode="PROSE")
    assert result is None


def test_python_ast_validation_rejects_broken_output():
    """If original was valid Python, but output introduces syntax error, reject it."""
    original = "def foo():\n    return 42"
    broken_output = "def foo():\nreturn 42 syntax error!!!"
    result = sanitize_output(broken_output, original, mode="CODE")
    assert result is None


def test_python_ast_validation_accepts_fixed_output():
    """If original had syntax errors and output fixed it, accept the output."""
    original = "def foo():\nreturn 42"  # Invalid indentation
    fixed_output = "def foo():\n    return 42"  # Valid Python
    result = sanitize_output(fixed_output, original, mode="CODE")
    assert result == "def foo():\n    return 42"


def test_trailing_newline_preserved_when_present():
    """If original input has trailing newline, output must retain a trailing newline."""
    original = "Line 1\nLine 2\n"
    raw = "Line 1\nLine 2"  # Missing trailing newline
    result = sanitize_output(raw, original, mode="PROSE")
    assert result.endswith("\n")
    assert result == "Line 1\nLine 2\n"


def test_trailing_newline_stripped_when_absent():
    """If original input lacks trailing newline, output must not have a trailing newline."""
    original = "Single line without newline"
    raw = "Single line without newline\n"  # LLM added newline
    result = sanitize_output(raw, original, mode="PROSE")
    assert not result.endswith("\n")
    assert result == "Single line without newline"
