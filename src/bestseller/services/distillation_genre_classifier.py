"""LLM-assisted stable genre bucket for distillation (repo-safe slug only)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.distillation_assets import read_json, write_json
from bestseller.services.distillation_chapter_llm import resolve_private_payload_path
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.planner import _extract_json_payload
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

# Stable aggregate keys — must align with config/story_design_grammars naming where applicable.
ALLOWED_DISTILLATION_GENRE_BUCKETS: frozenset[str] = frozenset(
    {
        "otherworld-cross-system",
        "urban-contemporary",
        "eastern-progression-fantasy",
        "science-fiction-progression",
        "suspense-mystery",
        "historical-fiction",
        "romance-relationship",
        "game-esports",
        "strategy-worldbuilding",
        "female-growth-ncp",
        "action-progression",
        "distillation-genre-unclassified",
    }
)


def _first_chapter_private_ref(chapters_index: dict[str, Any]) -> str | None:
    chs = chapters_index.get("chapters") or []
    if not isinstance(chs, list) or not chs:
        return None
    first = chs[0]
    if not isinstance(first, dict):
        return None
    ref = first.get("private_chunk_ref")
    return str(ref) if isinstance(ref, str) and ref.strip() else None


def load_private_chapter_preview(
    *,
    repo_root: Path,
    private_root: Path,
    private_chunk_ref: str,
    max_chars: int = 6000,
) -> str:
    path = resolve_private_payload_path(repo_root, private_root, private_chunk_ref)
    if not path.is_file():
        return ""
    raw = path.read_text(encoding="utf-8", errors="replace")
    return raw[:max_chars]


async def classify_genre_bucket_for_package(
    session: AsyncSession,
    settings: AppSettings,
    *,
    package_dir: Path,
    repo_root: Path,
    private_root: Path,
    force: bool = False,
) -> str:
    """Persist ``distillation_genre_bucket`` on ``source_manifest.json`` (allowed slug only)."""

    manifest_path = package_dir / "source_manifest.json"
    manifest = read_json(manifest_path)
    existing = manifest.get("distillation_genre_bucket")
    if (
        not force
        and isinstance(existing, str)
        and existing in ALLOWED_DISTILLATION_GENRE_BUCKETS
        and existing != "distillation-genre-unclassified"
    ):
        return existing

    ch_path = package_dir / "chapters.index.json"
    preview = ""
    if ch_path.is_file():
        ch_index = read_json(ch_path)
        ref = _first_chapter_private_ref(ch_index)
        if ref:
            preview = load_private_chapter_preview(
                repo_root=repo_root, private_root=private_root, private_chunk_ref=ref, max_chars=6000
            )

    genre_hint = str(manifest.get("genre_hint") or "")
    system = (
        "You classify Chinese web-fiction corpora into EXACTLY ONE stable genre bucket slug. "
        "Output JSON only with keys: genre_bucket (string), confidence (number 0-1). "
        "genre_bucket MUST be one of the allowed slugs verbatim — no prose, no markdown."
    )
    user = json.dumps(
        {
            "allowed_genre_buckets": sorted(ALLOWED_DISTILLATION_GENRE_BUCKETS),
            "genre_hint": genre_hint,
            "parse_profile": manifest.get("parse_profile"),
            "first_chapter_text_preview_redacted_use": (
                "Use only for tone/mechanism signals; do not copy named entities into output."
            ),
            "first_chapter_text_preview": preview,
        },
        ensure_ascii=False,
    )

    result = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="summarizer",
            system_prompt=system,
            user_prompt=user,
            fallback_response=json.dumps(
                {"genre_bucket": "distillation-genre-unclassified", "confidence": 0.0},
                ensure_ascii=False,
            ),
            prompt_template="distillation_genre_bucket",
            prompt_version="v1",
            project_id=None,
            workflow_run_id=None,
            metadata={"distillation_package": package_dir.name},
            max_tokens_override=512,
        ),
    )
    obj = _extract_json_payload(result.content)
    if not isinstance(obj, dict):
        bucket = "distillation-genre-unclassified"
        conf = 0.0
    else:
        bucket = str(obj.get("genre_bucket") or "distillation-genre-unclassified").strip()
        if bucket not in ALLOWED_DISTILLATION_GENRE_BUCKETS:
            bucket = "distillation-genre-unclassified"
        try:
            conf = float(obj.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0

    manifest["distillation_genre_bucket"] = bucket
    manifest["distillation_genre_bucket_confidence"] = conf
    write_json(manifest_path, manifest)
    logger.info("genre bucket %s for %s (conf=%.2f)", bucket, package_dir.name, conf)
    return bucket


async def classify_genre_buckets_for_packages(
    session: AsyncSession,
    settings: AppSettings,
    *,
    packages: list[Path],
    repo_root: Path,
    private_root: Path,
    force: bool = False,
) -> dict[str, str]:
    """Classify many packages sequentially (deterministic ordering)."""

    out: dict[str, str] = {}
    for pkg in packages:
        key = await classify_genre_bucket_for_package(
            session,
            settings,
            package_dir=pkg,
            repo_root=repo_root,
            private_root=private_root,
            force=force,
        )
        out[pkg.name] = key
    return out
