"""Independent post-edit guards for publication hygiene."""

from collections import Counter
from pathlib import Path
import re

import yaml

from .text import replace


_RULES = yaml.safe_load((Path(__file__).resolve().parent / "data/deai_rules.yaml").read_text())
_ENGLISH = re.compile(next(row["regex"] for row in _RULES["patterns"] if row.get("id") == "english_fragment"))
_STRUCTURE = re.compile(
    r"第\s*[0-9０-９零〇一二两三四五六七八九十百千]+\s*场戏|"
    r"场景\s*[0-9０-９零〇一二两三四五六七八九十百千]+|本章|章纲|原章"
)


def _matches(pattern, text):
    return Counter(match[0].lower() for match in pattern.finditer(text if isinstance(text, str) else ""))


def introduced_types(before, after):
    """Name hygiene categories newly introduced by one proposed edit."""
    result = []
    if _matches(_ENGLISH, after) - _matches(_ENGLISH, before):
        result.append("english")
    if _matches(_STRUCTURE, after) - _matches(_STRUCTURE, before):
        result.append("structure")
    return result


def reject_introduced(source, edits):
    """Drop offending sentence edits and rebuild the candidate from source."""
    kept = []
    warnings = []
    for edit in edits:
        kinds = introduced_types(edit.get("before", ""), edit.get("replacement", ""))
        if kinds:
            warnings.append(dict(rollback="text_hygiene", pid=edit.get("pid"), types=kinds))
        else:
            kept.append(edit)
    return replace(source, kept), kept, warnings
