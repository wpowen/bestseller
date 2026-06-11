"""P0-2 回归：大纲批次系统性缺失字段的确定性补全。

证据背景（2026-06-12 zhaoshen-hr-v3 跑书）：批次产物 opening_situation 50/50 全空、
场景参与者 50/50 仅主角、causal_contract 整批丢失——这些是物化层 causality 闸门与
商业就绪闸门的必查项，缺失导致每本书在产出者两层之外的闸门处卡死或空烧修复轮次。
"""

from bestseller.services.outline_field_enrichment import enrich_outline_batch_fields

NAMES = ["陈屿", "老金", "赵小磊", "白简"]


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
