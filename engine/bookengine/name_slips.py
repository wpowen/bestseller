"""Instruction 68 (E2): a name spliced from two registered characters, or one character off a registered name.

《散修丹途》 chapter 48 wrote “苏元铮” for a cast holding 苏以宁 and 石元铮. Such slips are found by
construction from the cast, so an ordinary unregistered walk-on is never mistaken for one.
"""

import re

from .precision import _sentences
from .unregistered_names import _known_labels, name_findings

_HAN_NAME = re.compile(r"^[㐀-鿿]{2,4}$")


def _cast(bible: dict) -> list[str]:
    rows = [*(bible or {}).get("characters", []), *(bible or {}).get("antagonists", [])]
    names = [row.get("name") for row in rows if isinstance(row, dict)]
    return list(dict.fromkeys(name for name in names if isinstance(name, str) and _HAN_NAME.match(name)))


def _blends(names: list[str], known: set[str]) -> dict[str, tuple[str, str]]:
    """surname of one + given name of another, for single-character surnames."""
    result = {}
    for first in names:
        for second in names:
            blend = first[0] + second[1:]
            if first != second and len(second) >= 3 and blend not in known and blend != first:
                result.setdefault(blend, (first, second))
    return result


def _near(name: str, names: list[str]) -> list[str]:
    return [
        other for other in names
        if len(other) == len(name) >= 3 and other[0] == name[0] and sum(a != b for a, b in zip(other, name)) == 1
    ]  # fmt: skip


def name_slip_findings(text: str, bible: dict, chapter: int | None = None) -> list[dict]:
    names, known = _cast(bible), _known_labels(bible)
    spans = _sentences(text)
    found = {}
    for blend, sources in _blends(names, known).items():
        for match in re.finditer(re.escape(blend), text):
            # 苏以宁 is not a slip just because 顾以宁 exists: skip when the match sits inside a known label.
            if any(label in text[max(0, match.start() - 3) : match.end() + 3] for label in known if blend in label):
                continue
            # The two-character given name is the distinctive part; a wrong surname is the likelier slip.
            found.setdefault(blend, dict(kind="blended_name", candidates=list(sources), offset=match.start(),
                                         replacement=sources[1], basis="given_name_owner"))  # fmt: skip
    for row in name_findings(text, bible, chapter):
        near = _near(row["name"], names)
        if row["name"] not in found and near:
            found[row["name"]] = dict(kind="near_name", candidates=near, offset=row["start"],
                                      replacement=near[0] if len(near) == 1 else None)  # fmt: skip
    result = []
    for name, row in found.items():
        offset = row.pop("offset")
        start, end = next(((a, b) for a, b in spans if a <= offset < b), (offset, offset + len(name)))
        result.append(dict(row, name=name, chapter=chapter, quote=text[start:end], status="Evidence"))
    return result


def fix_name_slips(text: str, bible: dict, chapter: int | None = None) -> tuple[str, dict]:
    """Replace a slip only when exactly one registered character can be meant; report the rest."""
    findings = name_slip_findings(text, bible, chapter)
    changed = text
    for row in findings:
        if row["replacement"]:
            changed = changed.replace(row["name"], row["replacement"])
    status = "repaired" if changed != text else "checked"
    return changed, dict(status=status, findings=findings, remaining=name_slip_findings(changed, bible, chapter))


def name_slip_checkup(bible: dict, chapters: list[dict]) -> dict:
    issues = [row for c in chapters for row in name_slip_findings(c.get("text", ""), bible, c.get("number"))]
    return dict(count=len(issues), issues=issues, status="Evidence")
