from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts/_deprecated/qingnang_repair/repair_qingnang_systemic_assets.py"
    spec = importlib.util.spec_from_file_location("repair_qingnang_systemic_assets", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_prewrite_contract_materializes_chapter_anchors(tmp_path: Path) -> None:
    package = tmp_path / "book"
    story_bible = package / "story-bible"
    story_bible.mkdir(parents=True)
    (package / "chapter-001.md").write_text("# 第1章 子时入镜\n\n正文", encoding="utf-8")
    (package / "chapter-002.md").write_text("# 第2章 第一名否认者\n\n正文", encoding="utf-8")
    (story_bible / "prewrite-contract.json").write_text("{}", encoding="utf-8")

    contract = _load_module().build_prewrite_contract(package, up_to_chapter=3)

    assert set(contract["chapters"]) == {"1", "2", "3"}
    assert "prewrite_anchor" in contract["chapters"]["1"]
    assert "第2章 第一名否认者" in contract["chapters"]["1"]["prewrite_anchor"]
    assert "推进一个可验证证据/账印/方位动作" not in contract["chapters"]["1"]["prewrite_anchor"]
    assert contract["chapters"]["1"]["required_payoff"]
    assert len(contract["chapters"]["1"]["scene_beats"]) >= 3


def test_qingnang_front_outline_uses_specific_pacing_controls(tmp_path: Path) -> None:
    package = tmp_path / "book"
    story_bible = package / "story-bible"
    story_bible.mkdir(parents=True)
    for chapter_no, title in (
        (1, "第1章 十五分钟凶宅"),
        (2, "第2章 第一名否认者"),
        (3, "第3章 第二个救不了"),
    ):
        (package / f"chapter-{chapter_no:03d}.md").write_text(
            f"# {title}\n\n正文",
            encoding="utf-8",
        )
    (story_bible / "prewrite-contract.json").write_text("{}", encoding="utf-8")

    contract = _load_module().build_prewrite_contract(package, up_to_chapter=3)

    chapter_one = contract["chapters"]["1"]
    assert chapter_one["chapter_objective"].startswith("只完成一个承诺")
    assert "张建军" in chapter_one["pressure_handoff"]
    assert chapter_one["open_question_limit"] == 2
    assert "父亲完整真相" in chapter_one["forbidden_moves"]

    chapter_three = contract["chapters"]["3"]
    assert "小雨" in chapter_three["required_payoff"]
    assert "救下" in chapter_three["required_payoff"]


def test_voice_profile_repair_only_targets_core_roles() -> None:
    manifest = [
        {"name": "林渊", "role": "protagonist"},
        {"name": "路人甲", "role": "supporting"},
    ]

    repaired, eligible, updated = _load_module().repair_identity_manifest_voice_profiles(manifest)

    assert eligible == 1
    assert updated == 1
    assert repaired[0]["voice_profile"]["role"] == "protagonist"
    assert "voice_profile" not in repaired[1]


def test_forward_state_repair_extends_post_yizhuang_promises(tmp_path: Path) -> None:
    story_bible = tmp_path / "story-bible"
    story_bible.mkdir(parents=True)
    ledger = story_bible / "event-state-ledger.md"
    ledger.write_text(
        "# Event State Ledger\n\n## Forward Promises (N+1..N+5)\nold\n",
        encoding="utf-8",
    )

    summary = _load_module().repair_forward_state_ledger(
        story_bible,
        start_chapter=118,
        end_chapter=120,
    )
    text = ledger.read_text(encoding="utf-8")

    assert summary["row_count"] == 3
    assert "第 120 章" in text
    assert "父亲第一层真相" in text
    assert "不得回滚困魂镜" in text


def test_qingnang_writing_profile_patch_removes_old_drift_vectors() -> None:
    module = _load_module()
    profile = module._deep_merge(
        {
            "market": {"content_mode": "死亡镜局规则揭示"},
            "world": {"power_system_style": "南茅传承体系和出马仙体系"},
            "character": {"golden_finger": "阴阳眼血统异能"},
        },
        module._qingnang_writing_profile_patch(),
    )

    rendered = str(profile)
    assert "三族旧契" in rendered
    assert "现实证据链" in rendered
    assert "死亡镜局规则揭示" not in rendered
    assert "血脉异能" not in rendered


def test_kernel_payload_repair_persists_systemic_contract() -> None:
    payload = {
        "crowd_size_class": "medium",
        "initial_mood": "惊疑",
        "mood_arc": ["围观", "恐慌"],
        "triggering_event": "镜债外溢",
        "resolution": "leader_emerges",
    }

    repaired = _load_module().repair_kernel_payload("crowd-scene.json", payload)

    assert repaired["systemic_repair_contract"]["source"] == "qingnang_systemic_assets_20260523"
    assert len(repaired["mood_arc"]) > len(payload["mood_arc"])
