"""File-backed persistence for Voice DNA.

Layout (Mode A book packages):
    output/<slug>/story-bible/voice-dna.json

Layout (Mode B / ai-generated packages):
    output/ai-generated/<slug>/story-bible/voice-dna.json

The DNA is produced once per project (via the
``extract_voice_dna`` CLI or programmatically) and read at every chapter
generation. Persisted JSON is the canonical ``VoiceDNA.model_dump(mode="json")``
shape so domain validation is trivially re-applied on load.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from bestseller.domain.voice_dna import VoiceDNA

logger = logging.getLogger(__name__)

_DEFAULT_FILENAME = "voice-dna.json"
_DEFAULT_RELATIVE_SUBDIR = ("story-bible",)


def resolve_voice_dna_path(
    slug: str,
    *,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
    filename: str = _DEFAULT_FILENAME,
) -> Path:
    """Return the canonical voice-dna.json path for a project slug."""

    base = Path(output_base_dir)
    if mode_b:
        base = base / "ai-generated"
    return Path(base, slug, *_DEFAULT_RELATIVE_SUBDIR, filename)


def save_voice_dna(
    dna: VoiceDNA,
    slug: str,
    *,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
    filename: str = _DEFAULT_FILENAME,
) -> Path:
    """Persist a VoiceDNA to ``output/<slug>/story-bible/voice-dna.json``.

    Creates the parent directories if needed. The file is written atomically
    via a sibling temp file + rename so partial writes do not corrupt prior
    DNA when a long extraction crashes mid-write.
    """

    path = resolve_voice_dna_path(
        slug,
        output_base_dir=output_base_dir,
        mode_b=mode_b,
        filename=filename,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dna.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)
    logger.info("voice DNA saved: %s (%d chars sample)", path, dna.sample_chars)
    return path


def load_voice_dna(
    slug: str,
    *,
    output_base_dir: str | Path = "output",
    mode_b: bool = False,
    filename: str = _DEFAULT_FILENAME,
) -> VoiceDNA | None:
    """Load the VoiceDNA persisted for a slug, or ``None`` if absent/invalid.

    ``None`` is returned for missing files, unreadable files, malformed JSON,
    or payloads that fail Pydantic validation. Callers should treat ``None``
    as "no DNA configured" and skip injection rather than crashing.
    """

    path = resolve_voice_dna_path(
        slug,
        output_base_dir=output_base_dir,
        mode_b=mode_b,
        filename=filename,
    )
    return load_voice_dna_file(path)


def load_voice_dna_file(path: str | Path) -> VoiceDNA | None:
    """Load a VoiceDNA from an explicit JSON file path."""

    effective = Path(path)
    if not effective.exists():
        return None
    try:
        raw = json.loads(effective.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("voice DNA load failed (%s): %s", effective, exc)
        return None
    try:
        return VoiceDNA.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError is broad on construction
        logger.warning("voice DNA validation failed (%s): %s", effective, exc)
        return None


__all__ = [
    "resolve_voice_dna_path",
    "save_voice_dna",
    "load_voice_dna",
    "load_voice_dna_file",
]
