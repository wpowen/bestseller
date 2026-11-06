"""Locate continuous literal text while allowing formatting whitespace differences."""


def literal_quote(text, supplied):
    """Return the exact source slice; never skip words, change punctuation or rewrite input."""
    if not isinstance(supplied, str) or not supplied.strip():
        return None
    if supplied in text:
        return supplied
    positions = [i for i, char in enumerate(text) if not char.isspace()]
    compact = "".join(text[i] for i in positions)
    needle = "".join(char for char in supplied if not char.isspace())
    start = compact.find(needle)
    if start < 0 or not needle:
        return None
    return text[positions[start] : positions[start + len(needle) - 1] + 1]
