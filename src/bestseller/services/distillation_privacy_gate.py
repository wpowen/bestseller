"""Redaction / privacy scanning for distillation material rows and JSON blobs."""

from __future__ import annotations

import json
from typing import Any

from bestseller.services.distillation_assets import _scan_sensitive_values


def scan_material_entry_row(row: dict[str, Any], *, path_prefix: str = "material") -> list[str]:
    """Scan all repo-relevant string fields on a material-library style row."""

    findings: list[str] = []
    for key in ("name", "slug", "narrative_summary", "genre", "sub_genre"):
        val = row.get(key)
        if isinstance(val, str):
            findings.extend(
                _scan_sensitive_values({key: val}, path=f"{path_prefix}.{key}")
            )
    tags = row.get("tags")
    if isinstance(tags, list):
        for i, t in enumerate(tags):
            if isinstance(t, str):
                findings.extend(
                    _scan_sensitive_values({f"tag_{i}": t}, path=f"{path_prefix}.tags[{i}]")
                )
    cj = row.get("content_json")
    if cj is not None:
        findings.extend(_scan_sensitive_values(cj, path=f"{path_prefix}.content_json"))
    return findings


def scan_blocked_phrases_in_row(
    row: dict[str, Any],
    *,
    blocked: list[str],
    path_prefix: str = "material",
) -> list[str]:
    """Return violation messages if any blocked phrase appears in string fields."""

    hits: list[str] = []
    if not blocked:
        return hits
    blobs: list[str] = []
    for key in ("name", "slug", "narrative_summary", "genre", "sub_genre"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            blobs.append(v)
    tags = row.get("tags")
    if isinstance(tags, list):
        blobs.extend(str(t) for t in tags if isinstance(t, str))
    cj = row.get("content_json")
    if cj is not None:
        try:
            blobs.append(json.dumps(cj, ensure_ascii=False))
        except (TypeError, ValueError):
            blobs.append(str(cj))
    hay = "\n".join(blobs)
    for phrase in blocked:
        p = str(phrase).strip()
        if p and p in hay:
            hits.append(f"{path_prefix}: blocked_combination substring {p!r}")
    return hits


def privacy_violation_count_for_material_row(
    row: dict[str, Any],
    *,
    anti_copy_ledger: dict[str, Any],
) -> tuple[int, list[str]]:
    """Return (count, messages) for sensitive patterns + anti-copy substring hits."""

    msgs = scan_material_entry_row(row)
    blocked = anti_copy_ledger.get("blocked_combinations") or []
    if isinstance(blocked, list):
        msgs.extend(scan_blocked_phrases_in_row(row, blocked=blocked))
    return len(msgs), msgs
