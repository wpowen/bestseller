from __future__ import annotations

# ruff: noqa: RUF001
import json
from pathlib import Path

import pytest

from bestseller.services.project_systemic_readiness import (
    evaluate_output_systemic_readiness,
)

pytestmark = pytest.mark.unit


def test_output_systemic_readiness_summarizes_blocking_gates(tmp_path: Path) -> None:
    package = tmp_path / "book"
    story_bible = package / "story-bible"
    story_bible.mkdir(parents=True)
    (package / "chapter-001.md").write_text(
        "# 第1章\n\n林渊按住青囊，铜钱发黑。",
        encoding="utf-8",
    )
    (story_bible / "canon-guardrails.json").write_text(
        json.dumps({"forbidden_terms": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    findings = evaluate_output_systemic_readiness(
        package,
        target_chapters=500,
        identity_registry=[{"name": "林渊", "role": "protagonist"}],
    )

    codes = {finding.code for finding in findings}
    assert "kernel_file_integration" in codes
    assert "prewrite_contract_coverage" in codes
    assert "identity_freezer_gate" in codes
    assert "voice_profile_coverage" in codes
    assert any(finding.severity == "critical" for finding in findings)


def test_output_systemic_readiness_respects_locked_identity_manifest(
    tmp_path: Path,
) -> None:
    package = tmp_path / "book"
    story_bible = package / "story-bible"
    story_bible.mkdir(parents=True)
    (package / "chapter-001.md").write_text("# 第1章\n\n林渊按住青囊。", encoding="utf-8")
    (story_bible / "canon-guardrails.json").write_text(
        json.dumps({"forbidden_terms": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    findings = evaluate_output_systemic_readiness(
        package,
        identity_registry=[{"name": "林渊", "role": "protagonist"}],
        identity_registry_locked=True,
    )

    codes = {finding.code for finding in findings}
    assert "identity_freezer_gate" not in codes
    assert "voice_profile_coverage" in codes


def test_output_systemic_readiness_limits_voice_profiles_to_core_roles(
    tmp_path: Path,
) -> None:
    package = tmp_path / "book"
    story_bible = package / "story-bible"
    story_bible.mkdir(parents=True)
    (package / "chapter-001.md").write_text("# 第1章\n\n林渊按住青囊。", encoding="utf-8")
    (story_bible / "canon-guardrails.json").write_text(
        json.dumps({"forbidden_terms": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    findings = evaluate_output_systemic_readiness(
        package,
        identity_registry=[
            {"name": "林渊", "role": "protagonist"},
            {"name": "路人甲", "role": "supporting"},
        ],
        identity_registry_locked=True,
    )

    voice_finding = next(
        finding for finding in findings if finding.code == "voice_profile_coverage"
    )
    assert voice_finding.evidence["metrics"]["named_character_count"] == 1
