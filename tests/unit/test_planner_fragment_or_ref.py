"""Unit tests for :func:`bestseller.services.planner._planner_fragment_or_ref`.

The helper is the switchboard that severs L3 homogenisation: when a project
has already been processed by Material Forge and has a stashed reference
block (``project.metadata_json["material_reference_block"]``), the helper
suppresses B-class ``planner_*`` pack fragments so the reference block
fully replaces the pre-baked script that caused same-genre books to
converge on identical beats.

When no reference block exists (flag off, or Forge skipped because the
library was empty at cold-start), the helper falls back to the legacy
fragment so baseline quality is preserved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from bestseller.services import planner as planner_services


pytestmark = pytest.mark.unit


@dataclass
class FakeProject:
    """Minimal project stand-in for the helper; only ``metadata_json`` is read."""

    metadata_json: dict[str, Any] | None = field(default_factory=dict)


class FakePack:
    """Truthy sentinel — the helper only checks ``bool(prompt_pack)``."""

    def __bool__(self) -> bool:  # pragma: no cover — trivial
        return True


@pytest.fixture
def fake_pack() -> FakePack:
    return FakePack()


@pytest.fixture(autouse=True)
def _patch_fragment_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``render_prompt_pack_fragment`` return a predictable sentinel
    so tests can assert whether the helper fell through to legacy rendering.
    """

    def fake_render(pack: Any, name: str) -> str:
        return f"<<LEGACY:{name}>>"

    monkeypatch.setattr(
        planner_services, "render_prompt_pack_fragment", fake_render
    )


# --------------------------------------------------------------------------
# None / empty guards
# --------------------------------------------------------------------------


def test_returns_empty_when_prompt_pack_missing() -> None:
    """No pack → always empty string, regardless of flag / metadata."""
    project = FakeProject(metadata_json={"material_reference_block": "anything"})
    assert planner_services._planner_fragment_or_ref(
        None, project, "planner_book_spec"
    ) == ""


def test_returns_empty_when_prompt_pack_falsy(fake_pack: FakePack) -> None:
    """Falsy pack short-circuits before settings are touched."""
    class FalsyPack:
        def __bool__(self) -> bool:
            return False

    project = FakeProject(metadata_json={})
    assert planner_services._planner_fragment_or_ref(
        FalsyPack(), project, "planner_outline"
    ) == ""


# --------------------------------------------------------------------------
# Flag off → legacy fragment is always returned
# --------------------------------------------------------------------------


def test_legacy_path_when_flag_off(
    fake_pack: FakePack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the feature flag is off, the helper must emit the original
    pack fragment even if a stale ``material_reference_block`` is stashed.
    """
    # Stub the settings object shape that the helper queries
    class StubPipeline:
        enable_reference_style_generation = False

    class StubSettings:
        pipeline = StubPipeline()

    monkeypatch.setattr(planner_services, "get_settings", lambda: StubSettings())

    project = FakeProject(
        metadata_json={"material_reference_block": "stale leftovers"}
    )
    result = planner_services._planner_fragment_or_ref(
        fake_pack, project, "planner_book_spec"
    )
    assert result == "<<LEGACY:planner_book_spec>>\n"


def test_settings_exception_falls_through_to_metadata_check(
    fake_pack: FakePack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If settings load raises, the helper should not crash — it swallows
    the exception and proceeds to inspect ``metadata_json``.
    """
    def boom() -> None:
        raise RuntimeError("settings blew up")

    monkeypatch.setattr(planner_services, "get_settings", boom)

    # Case A: no reference block → legacy fragment
    project_a = FakeProject(metadata_json={})
    assert planner_services._planner_fragment_or_ref(
        fake_pack, project_a, "planner_cast_spec"
    ) == "<<LEGACY:planner_cast_spec>>\n"

    # Case B: reference block present → suppress
    project_b = FakeProject(
        metadata_json={"material_reference_block": "## refs\n§world/x/y: Z"}
    )
    assert planner_services._planner_fragment_or_ref(
        fake_pack, project_b, "planner_cast_spec"
    ) == ""


# --------------------------------------------------------------------------
# Flag on + metadata variations
# --------------------------------------------------------------------------


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubPipeline:
        enable_reference_style_generation = True

    class StubSettings:
        pipeline = StubPipeline()

    monkeypatch.setattr(planner_services, "get_settings", lambda: StubSettings())


def test_empty_metadata_returns_legacy_fragment(
    fake_pack: FakePack, flag_on: None
) -> None:
    """Flag on but no material_reference_block → Forge likely skipped
    (empty library or cold-start fallback). Keep legacy fragment so
    baseline quality holds.
    """
    project = FakeProject(metadata_json={})
    result = planner_services._planner_fragment_or_ref(
        fake_pack, project, "planner_volume_plan"
    )
    assert result == "<<LEGACY:planner_volume_plan>>\n"


def test_none_metadata_returns_legacy_fragment(
    fake_pack: FakePack, flag_on: None
) -> None:
    """``metadata_json`` can be ``None`` on freshly-created projects —
    the helper must tolerate that without crashing.
    """
    project = FakeProject(metadata_json=None)
    result = planner_services._planner_fragment_or_ref(
        fake_pack, project, "planner_world_spec"
    )
    assert result == "<<LEGACY:planner_world_spec>>\n"


def test_empty_string_block_treated_as_no_block(
    fake_pack: FakePack, flag_on: None
) -> None:
    """``material_reference_block`` explicitly set to empty string must
    behave like "no block" so rendering stays graceful when the renderer
    returns ``""`` (no project_materials yet).
    """
    project = FakeProject(metadata_json={"material_reference_block": ""})
    result = planner_services._planner_fragment_or_ref(
        fake_pack, project, "planner_outline"
    )
    assert result == "<<LEGACY:planner_outline>>\n"


def test_reference_block_suppresses_legacy_fragment(
    fake_pack: FakePack, flag_on: None
) -> None:
    """The whole point: Forge ran, §slugs exist, legacy fragment is killed."""
    project = FakeProject(
        metadata_json={
            "material_reference_block": (
                "## 可引用物料\n"
                "§world_settings/proj-1/yunhe-town：云和镇 — ...\n"
            )
        }
    )
    result = planner_services._planner_fragment_or_ref(
        fake_pack, project, "planner_book_spec"
    )
    assert result == ""


@pytest.mark.parametrize(
    "fragment_name",
    [
        "planner_book_spec",
        "planner_world_spec",
        "planner_cast_spec",
        "planner_volume_plan",
        "planner_outline",
    ],
)
def test_all_five_fragment_keys_are_suppressed_symmetrically(
    fake_pack: FakePack, flag_on: None, fragment_name: str
) -> None:
    """All 5 planner fragment keys used in the planner must be suppressed
    when reference-style is active — no key gets special exemption.
    """
    project = FakeProject(
        metadata_json={"material_reference_block": "non-empty"}
    )
    assert planner_services._planner_fragment_or_ref(
        fake_pack, project, fragment_name
    ) == ""


def test_malformed_metadata_non_dict_falls_through(
    fake_pack: FakePack, flag_on: None
) -> None:
    """If ``metadata_json`` is somehow not a dict (e.g. legacy list data),
    the helper must not raise — it should treat it as "no block" and
    return the legacy fragment.
    """
    project = FakeProject(metadata_json=["not", "a", "dict"])  # type: ignore[arg-type]
    result = planner_services._planner_fragment_or_ref(
        fake_pack, project, "planner_cast_spec"
    )
    assert result == "<<LEGACY:planner_cast_spec>>\n"
