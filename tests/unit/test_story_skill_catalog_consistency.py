"""Frontend skill keys must stay a subset of the backend effect-skill catalog.

``StoryEnhancerSelection`` silently drops any ``effect_skills`` key that is not
in ``ALL_STORY_EFFECT_SKILL_KEYS`` (story_enhancers.py). The creation form
renders its skill grid from ``SE_SKILLS`` in novel_quickstart.html. If a new
frontend key is added without the backend catalog entry (or vice versa), the
user's selection is silently emptied at the boundary — P12. This test pins the
two lists together so the drift is caught in CI instead of at book creation.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from bestseller.services.story_effect_skills import ALL_STORY_EFFECT_SKILL_KEYS
from bestseller.services.story_enhancers import StoryEnhancerSelection

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_QUICKSTART_HTML = _REPO_ROOT / "src" / "bestseller" / "web" / "novel_quickstart.html"


def _frontend_se_skill_keys() -> list[str]:
    if not _QUICKSTART_HTML.exists():
        pytest.skip("novel_quickstart.html not present")
    text = _QUICKSTART_HTML.read_text(encoding="utf-8")
    # SE_SKILLS = [ ['brainhole_engine','脑洞'], ['comedy_engine','喜剧'], ... ];
    match = re.search(r"const SE_SKILLS\s*=\s*\[(.*?)\];", text, re.S)
    assert match is not None, "SE_SKILLS block not found in novel_quickstart.html"
    return re.findall(r"\[\s*'([a-z0-9_]+)'\s*,", match.group(1))


def test_every_frontend_skill_key_exists_in_backend_catalog() -> None:
    backend = set(ALL_STORY_EFFECT_SKILL_KEYS)
    frontend = _frontend_se_skill_keys()

    assert frontend, "no skill keys parsed from SE_SKILLS"
    missing = [k for k in frontend if k not in backend]
    assert not missing, (
        "frontend SE_SKILLS has keys the backend catalog does not know — "
        f"they are silently dropped by resolve_story_enhancers: {missing}"
    )


def test_frontend_and_backend_catalogs_are_the_same_set() -> None:
    frontend = set(_frontend_se_skill_keys())
    backend = set(ALL_STORY_EFFECT_SKILL_KEYS)

    assert frontend == backend, (
        f"frontend/backend skill catalogs drifted. "
        f"frontend-only: {sorted(frontend - backend)}; "
        f"backend-only: {sorted(backend - frontend)}"
    )


def test_every_backend_key_survives_the_selection_round_trip() -> None:
    """No catalog key may be dropped by StoryEnhancerSelection validation."""
    selection = StoryEnhancerSelection.model_validate(
        {"effect_skills": list(ALL_STORY_EFFECT_SKILL_KEYS)}
    )
    assert set(selection.effect_skills) == set(ALL_STORY_EFFECT_SKILL_KEYS)
