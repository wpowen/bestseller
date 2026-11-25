"""List chapter artifacts in story order across the three-/four-digit boundary."""

from functools import lru_cache
from pathlib import Path
import re


@lru_cache
def _number_pattern(pattern):
    prefix, wildcard, suffix = pattern.partition("*")
    if not wildcard:
        raise ValueError("Chapter filename pattern must contain a number wildcard")
    suffix = re.escape(suffix).replace(r"\*", ".+")
    return re.compile(re.escape(prefix) + r"([0-9]+)" + suffix)


def chapter_number(path, pattern):
    match = _number_pattern(pattern).fullmatch(Path(path).name)
    number = int(match[1]) if match else 0
    return number or None


def chapter_files(folder, pattern):
    """The first wildcard is the positive chapter number, any later wildcard is label text."""
    paths = [
        (number, path)
        for path in Path(folder).glob(pattern)
        if (number := chapter_number(path, pattern)) is not None and path.is_file()
    ]
    return [path for _, path in sorted(paths)]
