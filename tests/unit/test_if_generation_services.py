from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bestseller.domain.project import IFCharacterDraft, InteractiveFictionConfig
from bestseller.services.if_generation import (
    CharacterConsistencyError,
    _enforce_canonical_characters,
    _load_story_package_characters,
    build_concept_json,
    run_bible_phase,
)


pytestmark = pytest.mark.unit


def _build_project(slug: str = "apocalypse-001", **overrides: object) -> SimpleNamespace:
    """Build a minimal ProjectModel stand-in for concept-building tests."""
    project = SimpleNamespace(
        slug=slug,
        title=overrides.get("title", "灰烬执政官"),
        metadata_json=overrides.get("metadata_json", {}),
    )
    return project


def _minimal_cfg() -> InteractiveFictionConfig:
    return InteractiveFictionConfig(
        enabled=True,
        if_genre="末日爽文",
        target_chapters=60,
        free_chapters=20,
        premise="黑雨降临后主角继承灰楼权限，夺回安全区规则。",
        protagonist="陆沉",
        core_conflict="黑雨末日下的资源与秩序争夺",
        tone="狠、快、压迫感",
    )


def test_load_story_package_characters_reads_canonical_names(tmp_path: Path) -> None:
    """``_load_story_package_characters`` should read a project's configured
    canonical cast so the IF pipeline can honour the user's authored setting.
    """
    slug = "my-apocalypse-project"
    package_dir = (
        tmp_path / "story-factory" / "projects" / slug.replace("-", "_")
    )
    package_dir.mkdir(parents=True)
    (package_dir / "story_package.json").write_text(
        json.dumps(
            {
                "book": {
                    "title": "灰烬执政官",
                    "characters": [
                        {
                            "id": "char_lin_shuang",
                            "name": "林霜",
                            "title": "野战医生",
                            "description": "灾变前是急诊主治。",
                            "role": "红颜",
                        },
                        {
                            "id": "char_han_ce",
                            "name": "韩策",
                            "title": "外环武装队长",
                            "description": "前城防机动队骨干。",
                            "role": "宿敌",
                        },
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    project = _build_project(slug=slug, metadata_json={})

    prev_cwd = Path.cwd()
    import os

    os.chdir(tmp_path)
    try:
        characters = _load_story_package_characters(project)
    finally:
        os.chdir(prev_cwd)

    names = [c["name"] for c in characters]
    assert "林霜" in names
    assert "韩策" in names


def test_load_story_package_characters_returns_empty_when_absent(tmp_path: Path) -> None:
    project = _build_project(slug="no-package", metadata_json={})
    prev_cwd = Path.cwd()
    import os

    os.chdir(tmp_path)
    try:
        characters = _load_story_package_characters(project)
    finally:
        os.chdir(prev_cwd)
    assert characters == []


def test_build_concept_json_merges_story_package_canonical_names(tmp_path: Path) -> None:
    """``build_concept_json`` must surface canonical character names so
    downstream bible validation can enforce them.
    """
    slug = "canonical-merge-case"
    package_dir = (
        tmp_path / "story-factory" / "projects" / slug.replace("-", "_")
    )
    package_dir.mkdir(parents=True)
    (package_dir / "story_package.json").write_text(
        json.dumps(
            {
                "book": {
                    "characters": [
                        {"name": "林霜", "role": "红颜", "description": "野战医生"},
                        {"name": "唐海", "role": "盟友", "description": "灾备工程师"},
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    project = _build_project(slug=slug, metadata_json={})
    cfg = _minimal_cfg()

    prev_cwd = Path.cwd()
    import os

    os.chdir(tmp_path)
    try:
        concept = build_concept_json(cfg, project)
    finally:
        os.chdir(prev_cwd)

    assert "key_characters" in concept
    assert {c["name"] for c in concept["key_characters"]} == {"林霜", "唐海"}
    assert concept["canonical_character_names"] == ["林霜", "唐海"]


def test_build_concept_json_falls_back_to_cfg_key_characters() -> None:
    """Without a story_package.json, cfg.key_characters should populate the concept."""
    project = _build_project(slug="no-package-present", metadata_json={})
    cfg = _minimal_cfg()
    cfg.key_characters = [
        IFCharacterDraft(name="程彻", role="盟友", description="重生者"),
        IFCharacterDraft(name="方择", role="反派", description="档案局高层"),
    ]

    concept = build_concept_json(cfg, project)

    assert concept["key_characters"] == [
        {"name": "程彻", "role": "盟友", "description": "重生者"},
        {"name": "方择", "role": "反派", "description": "档案局高层"},
    ]
    assert concept["canonical_character_names"] == ["程彻", "方择"]


def test_enforce_canonical_characters_accepts_matching_bible() -> None:
    bible = {
        "book": {
            "characters": [
                {"name": "林霜", "role": "红颜"},
                {"name": "唐海", "role": "盟友"},
                {"name": "沈崇", "role": "反派"},
            ]
        }
    }
    result = _enforce_canonical_characters(bible, ["林霜", "唐海"])
    assert result is bible  # identity preserved


def test_enforce_canonical_characters_rejects_dropped_name() -> None:
    bible = {
        "book": {
            "characters": [
                {"name": "陆沉", "role": "盟友"},  # renamed!
                {"name": "唐海", "role": "盟友"},
            ]
        }
    }
    with pytest.raises(CharacterConsistencyError) as excinfo:
        _enforce_canonical_characters(bible, ["林霜", "唐海"])
    assert "林霜" in str(excinfo.value)


def test_enforce_canonical_characters_no_op_when_canonical_empty() -> None:
    """If no canonical names are known, the validator is a no-op."""
    bible = {"book": {"characters": []}}
    result = _enforce_canonical_characters(bible, [])
    assert result is bible


def test_enforce_canonical_characters_requires_book_section() -> None:
    with pytest.raises(CharacterConsistencyError):
        _enforce_canonical_characters({}, ["林霜"])


class _StubLLMClient:
    """Minimal stand-in for the real ``_LLMCaller`` used by ``run_bible_phase``."""

    def __init__(self, raw_json: str) -> None:
        self._raw = raw_json

    def heavy(self, prompt: str, max_tokens: int) -> str:
        return self._raw


def test_run_bible_phase_rejects_bible_that_drops_canonical_characters() -> None:
    """run_bible_phase must raise when the LLM drops canonical characters.

    This is the regression guard for the ``程彻→陆沉`` renaming bug where the
    main character was replaced halfway through the book.
    """
    cfg = _minimal_cfg()
    concept = {
        "book_id": "demo",
        "title": "demo",
        "canonical_character_names": ["林霜", "韩策"],
    }
    raw = json.dumps(
        {
            "book": {
                "id": "demo",
                "title": "demo",
                "characters": [
                    {"name": "陆沉", "role": "盟友"},
                    {"name": "何承", "role": "反派"},
                ],
                "total_chapters": 60,
                "free_chapters": 20,
            },
            "story_bible": {"premise": "demo"},
        },
        ensure_ascii=False,
    )
    client = _StubLLMClient(raw)

    with pytest.raises(CharacterConsistencyError):
        run_bible_phase(client, concept, cfg)


def test_run_bible_phase_accepts_bible_with_all_canonical_names() -> None:
    cfg = _minimal_cfg()
    concept = {
        "book_id": "demo",
        "title": "demo",
        "canonical_character_names": ["林霜", "韩策"],
    }
    raw = json.dumps(
        {
            "book": {
                "id": "demo",
                "title": "demo",
                "characters": [
                    {"name": "林霜", "role": "红颜"},
                    {"name": "韩策", "role": "宿敌"},
                    {"name": "其他", "role": "中立"},  # extras are fine
                ],
                "total_chapters": 60,
                "free_chapters": 20,
            },
            "story_bible": {"premise": "demo"},
        },
        ensure_ascii=False,
    )
    client = _StubLLMClient(raw)

    bible = run_bible_phase(client, concept, cfg)
    assert bible["book"]["total_chapters"] == cfg.target_chapters
    assert bible["book"]["free_chapters"] == cfg.free_chapters
    names = [c["name"] for c in bible["book"]["characters"]]
    assert "林霜" in names and "韩策" in names
