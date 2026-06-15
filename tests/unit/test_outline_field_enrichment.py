"""P0-2 回归：大纲批次系统性缺失字段的确定性补全。

证据背景（2026-06-12 zhaoshen-hr-v3 跑书）：批次产物 opening_situation 50/50 全空、
场景参与者 50/50 仅主角、causal_contract 整批丢失——这些是物化层 causality 闸门与
商业就绪闸门的必查项，缺失导致每本书在产出者两层之外的闸门处卡死或空烧修复轮次。
"""

from bestseller.services.outline_field_enrichment import enrich_outline_batch_fields

NAMES = ["陈屿", "老金", "赵小磊", "白简"]


def _golden_solo_chapter(n=1):
    """A 凡人流-style golden chapter whose scenes are all protagonist-solo and
    whose scene text mentions no other cast name — the exact shape that trips
    the golden-three solo-chain hard gate and kills the whole volume outline."""
    return {
        "chapter_number": n,
        "volume_number": 1,
        "goal": "谢迟在灭镇当夜的地窖里挖出缺角古砚",
        "main_conflict": "夜火封镇，废墟塌陷，砚池吞噬寿数的秘密第一次显形",
        "hook_description": "砚底浮出第一行记寿小字",
        "faction_refs": ["雾外楼"],
        "key_reveals": ["蚀漏砚以寿数计价"],
        "scenes": [
            {
                "scene_number": 1,
                "participants": ["谢迟"],
                "purpose": {"story": "谢迟独自在塌陷地窖里摸索", "emotion": "紧张"},
                "exit_state": {"summary": "挖出古砚"},
            },
            {
                "scene_number": 2,
                "participants": [],
                "purpose": {"story": "谢迟试墨，砚池光阴加速", "emotion": "震撼"},
                "exit_state": {"summary": "首次付出寿数"},
            },
        ],
    }


def test_golden_three_solo_scene_is_rescued() -> None:
    """A golden chapter with all-solo scenes must get a named second participant
    injected so it is no longer a solo-chain (else the hard gate kills the volume)."""
    from bestseller.services.commercial_planning_readiness import (
        _chapter_is_solo_chain,
        chapter_plan_probe_from_mapping,
    )

    chapter = _golden_solo_chapter(1)
    content = {"chapters": [chapter]}
    enrich_outline_batch_fields(content, NAMES, protagonist="谢迟")

    out = content["chapters"][0]
    duo = [
        s for s in out["scenes"]
        if len({p.strip() for p in (s.get("participants") or []) if p and p.strip()}) >= 2
    ]
    assert duo, "golden solo chapter must end with at least one ≥2-participant scene"
    probe = chapter_plan_probe_from_mapping(out)
    assert not _chapter_is_solo_chain(probe), "golden chapter still flagged solo-chain after rescue"


def test_non_golden_solo_chapter_not_force_rescued() -> None:
    """Rescue is golden-three only — a later solo chapter is left as-is when no
    cast name appears in its text (no fabricated participants outside ch1-3)."""
    chapter = _golden_solo_chapter(12)
    content = {"chapters": [chapter]}
    enrich_outline_batch_fields(content, NAMES, protagonist="谢迟")
    out = content["chapters"][0]
    # scene 2 had no resolvable second name in text → stays solo (protagonist only)
    assert {p for p in out["scenes"][1]["participants"]} <= {"谢迟"}


def _diseased_chapter(n=5):
    return {
        "chapter_number": n,
        "volume_number": 1,
        "goal": "陈屿蹲点三天锁定候选人赵小磊",
        "main_conflict": "白简高价抢签；候选人母亲住院走不开；子时死线压顶",
        "hook_description": "面板上的适配度被一只看不见的手当面抹掉",
        "key_reveals": [],
        "opening_situation": None,
        "opening_pressure": None,
        "causal_contract": {},
        "scenes": [
            {
                "scene_number": 1,
                "participants": ["陈屿"],
                "purpose": {"story": "巷口对峙，老金递来情报", "emotion": "紧张"},
                "exit_state": {"summary": "候选人收下合同未签"},
            },
            {
                "scene_number": 2,
                "participants": [],
                "purpose": {"story": "赵小磊在雨里修灯", "emotion": "暖"},
                "exit_state": {"summary": "签字落定"},
            },
        ],
    }


def test_fills_all_three_field_families():
    content = {"chapters": [_diseased_chapter()]}
    content, stats = enrich_outline_batch_fields(content, NAMES, protagonist="陈屿")
    ch = content["chapters"][0]
    assert ch["opening_situation"] and "开章即事中" in ch["opening_situation"]
    assert ch["causal_contract"] and len(ch["causal_contract"]) >= 8
    # 场景1文本含老金、场景2文本含赵小磊 → 参与者补到≥2人且含主角
    for scene in ch["scenes"]:
        assert scene["participants"][0] == "陈屿"
        assert len(scene["participants"]) >= 2
    assert stats["total"] > 0


def test_never_overwrites_planner_values():
    ch = _diseased_chapter()
    ch["opening_situation"] = "planner写的开场"
    ch["causal_contract"] = {"pressure": "planner写的压力"}
    ch["scenes"][0]["participants"] = ["陈屿", "白简"]
    content, stats = enrich_outline_batch_fields({"chapters": [ch]}, NAMES)
    out = content["chapters"][0]
    assert out["opening_situation"] == "planner写的开场"
    assert out["causal_contract"] == {"pressure": "planner写的压力"}
    assert out["scenes"][0]["participants"] == ["陈屿", "白简"]


def test_contract_skipped_when_too_thin():
    ch = {
        "chapter_number": 9,
        "goal": None,
        "main_conflict": None,
        "hook_description": None,
        "causal_contract": {},
        "scenes": [],
    }
    content, stats = enrich_outline_batch_fields({"chapters": [ch]}, NAMES)
    assert not content["chapters"][0]["causal_contract"]
    assert stats["causal_contract"] == 0


def test_golden_hype_only_for_first_three():
    chapters = [_diseased_chapter(n) for n in (1, 2, 3, 4)]
    content, stats = enrich_outline_batch_fields({"chapters": chapters}, NAMES)
    for ch in content["chapters"]:
        if ch["chapter_number"] <= 3:
            assert ch["hype_type"] and ch["hype_intensity"] is not None
        else:
            assert "hype_type" not in ch or not ch.get("hype_type")
    assert stats["golden_hype"] == 3


# ── 网文方法卡：target_emotion 兜底 + hook_type 归一化 ──────────────────────


def test_target_emotion_golden_defaults_to_shuang():
    """黄金三章缺 target_emotion 时按位置兜底为爽（盲评 4:0 输局的病因回归）。"""
    chapters = [_diseased_chapter(n) for n in (1, 4)]
    content, stats = enrich_outline_batch_fields({"chapters": chapters}, NAMES)
    assert content["chapters"][0]["target_emotion"] == "爽"
    assert content["chapters"][1]["target_emotion"] == "紧张"
    assert stats["target_emotion"] == 2


def test_target_emotion_keeps_planner_value_and_uses_hype_hint():
    ch1 = _diseased_chapter(1)
    ch1["target_emotion"] = "暖"  # planner 明确给的值不被位置默认覆盖
    ch7 = _diseased_chapter(7)
    ch7["hype_type"] = "热血对决"
    content, stats = enrich_outline_batch_fields({"chapters": [ch1, ch7]}, NAMES)
    assert content["chapters"][0]["target_emotion"] == "暖"
    assert content["chapters"][1]["target_emotion"] == "燃"
    assert stats["target_emotion"] == 1


def test_hook_type_normalized_to_canonical_key():
    ch = _diseased_chapter(5)
    ch["hook_type"] = "身份反转"
    content, stats = enrich_outline_batch_fields({"chapters": [ch]}, NAMES)
    assert content["chapters"][0]["hook_type"] == "identity_reversal"
    assert stats["hook_type_normalized"] == 1


def test_hook_type_unmatched_kept_verbatim():
    """映射不上保留原值不阻断（soft 契约）。"""
    ch = _diseased_chapter(5)
    ch["hook_type"] = "天降外星人"
    canonical = _diseased_chapter(6)
    canonical["hook_type"] = "countdown"
    content, stats = enrich_outline_batch_fields(
        {"chapters": [ch, canonical]}, NAMES
    )
    assert content["chapters"][0]["hook_type"] == "天降外星人"
    assert content["chapters"][1]["hook_type"] == "countdown"
    assert stats["hook_type_normalized"] == 0
