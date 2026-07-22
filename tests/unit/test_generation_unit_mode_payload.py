"""Guard: the per-book generation unit needs a write path that survives a run.

Root cause (2026-07-21 validation): the routing switch shipped without any way
to set it. Setting it out-of-band with SQL mid-run looked like it worked and
then vanished — the pipeline holds the project in its ORM identity map and
rewrites the whole ``metadata`` JSONB from that stale in-memory copy, so an
externally added key survives only until the next pipeline write. The key has
to be stamped at project-row creation.
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.unit


def _autowrite_source() -> str:
    from bestseller.web import server

    return inspect.getsource(server)


class TestPayloadIsHonouredAtProjectCreation:
    def test_payload_key_is_read(self) -> None:
        assert '"generation_unit_mode"' in _autowrite_source()

    def test_key_is_written_into_project_metadata(self) -> None:
        source = _autowrite_source()
        assert "apply_new_project_generation_policy(" in source

    def test_written_before_project_create(self) -> None:
        """Stamped onto the metadata dict that ProjectCreate receives — writing
        it after the row exists is the failure mode this guards."""

        source = _autowrite_source()
        write_pos = source.index("project_metadata = apply_new_project_generation_policy(")
        create_pos = source.index("project_create = ProjectCreate(")
        assert write_pos < create_pos


class TestProsePromptProfilePayload:
    """The lean prose profile needs the same creation-time write path — setting
    it later is silently clobbered by the pipeline's metadata rewrites."""

    def test_payload_key_is_read(self) -> None:
        assert '"prose_prompt_profile"' in _autowrite_source()

    def test_key_is_written_into_project_metadata(self) -> None:
        assert "apply_new_project_generation_policy(" in _autowrite_source()

    def test_written_before_project_create(self) -> None:
        source = _autowrite_source()
        assert source.index("project_metadata = apply_new_project_generation_policy(") < source.index(
            "project_create = ProjectCreate("
        )

    def test_resolver_accepts_the_stamped_value(self) -> None:
        from bestseller.services.prose_prompt_profile import resolve_prose_prompt_profile

        assert (
            resolve_prose_prompt_profile(
                project_metadata={"prose_prompt_profile": "lean"}
            )
            == "lean"
        )


class TestNormalisation:
    """The router accepts several spellings; the stored value should be the
    canonical one so a book's mode is greppable."""

    @pytest.mark.parametrize("alias", ["chapter", "chapter_first", "chapter_hybrid"])
    def test_chapter_aliases_normalise(self, alias: str) -> None:
        from bestseller.services.pipelines import _project_chapter_first_preference

        assert (
            _project_chapter_first_preference(
                type("P", (), {"metadata_json": {"generation_unit_mode": alias}})()
            )
            is True
        )

    @pytest.mark.parametrize("alias", ["scene", "scene_by_scene"])
    def test_scene_aliases_normalise(self, alias: str) -> None:
        from bestseller.services.pipelines import _project_chapter_first_preference

        assert (
            _project_chapter_first_preference(
                type("P", (), {"metadata_json": {"generation_unit_mode": alias}})()
            )
            is False
        )

    def test_unknown_value_is_ignored_not_guessed(self) -> None:
        """An unrecognised mode must fall through to the global default rather
        than silently picking one."""

        from bestseller.services.pipelines import _project_chapter_first_preference

        assert (
            _project_chapter_first_preference(
                type("P", (), {"metadata_json": {"generation_unit_mode": "banana"}})()
            )
            is None
        )

    def test_omitted_payload_key_defaults_to_chapter(self) -> None:
        """Changed 2026-07-22: an omitted key now defaults to chapter-first
        (the A/B-preferred unit), stamped via the else branch. An explicit pick
        still wins because the if/elif precede the else."""

        from bestseller.services.generation_policy import apply_new_project_generation_policy

        assert apply_new_project_generation_policy({})["generation_unit_mode"] == "chapter"

    def test_explicit_scene_wins_over_default(self) -> None:
        from bestseller.services.generation_policy import apply_new_project_generation_policy

        policy = apply_new_project_generation_policy(
            {}, generation_unit_mode="scene_by_scene"
        )
        assert policy["generation_unit_mode"] == "scene"

    def test_new_project_default_is_shared_by_central_create_service(self) -> None:
        from bestseller.services import projects

        source = inspect.getsource(projects.create_project)
        assert "apply_new_project_generation_policy(" in source

    def test_repair_uses_the_same_generation_policy_resolver(self) -> None:
        from bestseller.services import repair

        source = inspect.getsource(repair.run_project_repair)
        assert "generation_unit_preference_from_metadata(project_metadata)" in source
        assert "chapter_first=use_chapter_first" in source
