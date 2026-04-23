"""Unit tests for ``bestseller.services.skills_loader``."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from bestseller.services.skills_loader import (
    ResearchSkill,
    _parse_skill_file,
    load_skill_file,
    load_skill_registry,
    load_skills_for_genre,
    render_skills_prompt_block,
    reset_skill_cache,
)

pytestmark = pytest.mark.unit


def _write_skill(path: Path, body: str) -> None:
    path.write_text(dedent(body).lstrip(), encoding="utf-8")


# ── Frontmatter parsing ────────────────────────────────────────────────


class TestFrontmatterParsing:
    def test_markdown_with_frontmatter(self, tmp_path: Path) -> None:
        p = tmp_path / "x.skill.md"
        _write_skill(
            p,
            """
            ---
            key: xianxia-upgrade
            name: 仙侠升级流
            matches_genres:
              - 仙侠
              - 修真
            ---

            # 方法论

            具体调研步骤 ...
            """,
        )
        parsed = _parse_skill_file(p)
        assert parsed.metadata["key"] == "xianxia-upgrade"
        assert parsed.metadata["matches_genres"] == ["仙侠", "修真"]
        assert "具体调研步骤" in parsed.body

    def test_plain_yaml_form(self, tmp_path: Path) -> None:
        p = tmp_path / "plain.skill.yaml"
        _write_skill(
            p,
            """
            key: my-skill
            name: 纯 YAML 技能
            matches_genres: [都市]
            methodology_notes: "直接写在 yaml 里"
            """,
        )
        skill = load_skill_file(p)
        assert skill.key == "my-skill"
        assert skill.methodology_notes == "直接写在 yaml 里"

    def test_frontmatter_body_feeds_methodology_notes(self, tmp_path: Path) -> None:
        p = tmp_path / "x.skill.md"
        _write_skill(
            p,
            """
            ---
            key: skill-z
            name: 体裁 Z
            ---

            # 调研指导

            正文作为 methodology_notes。
            """,
        )
        skill = load_skill_file(p)
        assert "调研指导" in skill.methodology_notes

    def test_invalid_frontmatter_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.skill.md"
        _write_skill(
            p,
            """
            ---
            - just-a-list
            ---

            body
            """,
        )
        with pytest.raises(ValueError):
            load_skill_file(p)

    def test_invalid_field_type_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.skill.yaml"
        _write_skill(
            p,
            """
            key: 非法
            name: 含有空格和中文字符也没关系 key 不行
            """,
        )
        # ``key`` pattern `[a-zA-Z0-9_-]+` rejects Chinese characters.
        with pytest.raises(ValueError):
            load_skill_file(p)


# ── Registry loading ───────────────────────────────────────────────────


class TestRegistryLoading:
    def test_load_registry_reads_every_skill_file(self, tmp_path: Path) -> None:
        reset_skill_cache()
        (tmp_path / "xianxia").mkdir()
        (tmp_path / "urban").mkdir()
        _write_skill(
            tmp_path / "xianxia" / "x.skill.md",
            """
            ---
            key: x
            name: x
            matches_genres: [仙侠]
            ---
            body
            """,
        )
        _write_skill(
            tmp_path / "urban" / "u.skill.md",
            """
            ---
            key: u
            name: u
            matches_genres: [都市]
            ---
            body
            """,
        )
        registry = load_skill_registry(tmp_path)
        assert set(registry) == {"x", "u"}

    def test_invalid_skill_is_skipped_not_fatal(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        reset_skill_cache()
        _write_skill(
            tmp_path / "bad.skill.md",
            """
            ---
            # missing required fields: key + name
            description: broken
            ---
            """,
        )
        _write_skill(
            tmp_path / "ok.skill.md",
            """
            ---
            key: ok
            name: ok
            ---
            """,
        )
        with caplog.at_level("WARNING"):
            registry = load_skill_registry(tmp_path)
        assert "ok" in registry
        assert "bad" not in registry
        assert any("Skipping invalid skill" in rec.message for rec in caplog.records)

    def test_duplicate_key_first_wins(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        reset_skill_cache()
        _write_skill(
            tmp_path / "a.skill.md",
            """
            ---
            key: same
            name: first
            ---
            """,
        )
        _write_skill(
            tmp_path / "b.skill.md",
            """
            ---
            key: same
            name: second
            ---
            """,
        )
        with caplog.at_level("WARNING"):
            registry = load_skill_registry(tmp_path)
        assert registry["same"].name == "first"
        assert any("Duplicate skill key" in rec.message for rec in caplog.records)

    def test_missing_directory_returns_empty(self, tmp_path: Path) -> None:
        reset_skill_cache()
        assert load_skill_registry(tmp_path / "nope") == {}


# ── Genre matching ─────────────────────────────────────────────────────


class TestGenreMatching:
    @staticmethod
    def _seed(tmp_path: Path) -> Path:
        _write_skill(
            tmp_path / "base.skill.md",
            """
            ---
            key: common
            name: 通用
            is_common: true
            ---
            """,
        )
        _write_skill(
            tmp_path / "xianxia.skill.md",
            """
            ---
            key: xianxia-upgrade
            name: 仙侠升级
            matches_genres: [仙侠, 修真, xianxia]
            matches_sub_genres: [upgrade, 宗门]
            ---
            """,
        )
        _write_skill(
            tmp_path / "urban.skill.md",
            """
            ---
            key: urban-cultivation
            name: 都市修仙
            matches_genres: [都市修仙, 灵气复苏]
            ---
            """,
        )
        return tmp_path

    def test_common_skill_is_always_returned(self, tmp_path: Path) -> None:
        reset_skill_cache()
        self._seed(tmp_path)
        matches = load_skills_for_genre(
            "nonexistent", "nope", skills_dir=tmp_path
        )
        assert [s.key for s in matches] == ["common"]

    def test_genre_token_match(self, tmp_path: Path) -> None:
        reset_skill_cache()
        self._seed(tmp_path)
        matches = load_skills_for_genre("仙侠", "upgrade", skills_dir=tmp_path)
        keys = [s.key for s in matches]
        assert "common" in keys
        assert "xianxia-upgrade" in keys
        # Urban skill should not match a pure 仙侠 query.
        assert "urban-cultivation" not in keys

    def test_sub_genre_token_match(self, tmp_path: Path) -> None:
        reset_skill_cache()
        self._seed(tmp_path)
        matches = load_skills_for_genre(
            "奇幻", "宗门", skills_dir=tmp_path
        )
        # Matched on sub_genre token alone.
        assert any(s.key == "xianxia-upgrade" for s in matches)

    def test_english_alias_matches(self, tmp_path: Path) -> None:
        reset_skill_cache()
        self._seed(tmp_path)
        matches = load_skills_for_genre(
            "XIANXIA", None, skills_dir=tmp_path
        )
        assert any(s.key == "xianxia-upgrade" for s in matches)


# ── Prompt rendering ───────────────────────────────────────────────────


class TestRenderBlock:
    def test_empty_list_returns_empty_string(self) -> None:
        assert render_skills_prompt_block([]) == ""

    def test_render_contains_key_sections(self) -> None:
        skill = ResearchSkill(
            key="demo",
            name="演示",
            description="演示用",
            matches_genres=["仙侠"],
            search_dimensions=["world_settings", "power_systems"],
            seed_queries={
                "world_settings": ["q1", "q2", "q3", "q4", "q5"],
            },
            authoritative_sources=["https://example.test/a", "https://example.test/b"],
            taboo_patterns=["方域", "废灵根"],
            methodology_notes="注意事项",
        )
        block = render_skills_prompt_block([skill], max_seed_queries_per_dim=2)
        assert "## 研究方法论" in block
        assert "`demo`" in block
        assert "world_settings, power_systems" in block
        # Seed query cap is enforced.
        assert "q1" in block and "q2" in block
        assert "q3" not in block
        # Sources and taboos flow through.
        assert "https://example.test/a" in block
        assert "方域" in block
        assert "注意事项" in block


# ── Repo-level sanity: shipped seed skills load ────────────────────────


class TestShippedSeedSkills:
    """Validates that the four seed skill files added with B1.3 load cleanly."""

    def test_shipped_skills_load(self) -> None:
        reset_skill_cache()
        registry = load_skill_registry()
        # These four are committed alongside the loader.
        expected_keys = {
            "base-research-discipline",
            "xianxia-upgrade",
            "urban-cultivation",
            "scifi-starwar",
        }
        missing = expected_keys - registry.keys()
        assert not missing, f"Shipped skills missing: {missing}"

    def test_shipped_xianxia_skill_matches_expected_genre(self) -> None:
        reset_skill_cache()
        skills = load_skills_for_genre("仙侠", "upgrade")
        keys = {s.key for s in skills}
        assert "xianxia-upgrade" in keys
        assert "base-research-discipline" in keys

    def test_shipped_urban_skill_matches_subgenre(self) -> None:
        reset_skill_cache()
        skills = load_skills_for_genre("都市修仙", "灵气复苏")
        keys = {s.key for s in skills}
        assert "urban-cultivation" in keys
