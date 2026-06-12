"""Cross-scene beat re-enactment (节拍重演) regression tests.

Real incident: output/zhaoshen-hr-v3-1781180702/chapter-001.md — the
chapter-level cut_point fanned out into both scene cards, so scene s01 wrote
the full climax (signing → golden paw-print → badge handover → 姜子牙 reveal)
and scene s02 re-staged the same beats with fresh wording. Only a few anchor
sentences survived near-verbatim (e.g.「金光顺着爪纹一道道亮起来」). The
fixtures below are a compressed replica of that structure.
"""

from __future__ import annotations

from bestseller.services.deduplication import (
    detect_cross_scene_beat_reenactment,
    remove_cross_scene_near_verbatim_repeats,
)

# ── Fixture: compressed replica of the zhaoshen-hr-v3 ch1 incident ──
# Scene A (climax) … filler … Scene B re-enacts the same beats:
#   * one paragraph repeats an anchor sentence near-verbatim (whole paragraph)
#   * one paragraph embeds the verbatim long clause inside otherwise-new prose
_SCENE_A = [
    "哮天犬把转编申请压在前台，红戳盖得歪歪扭扭，理由栏八个字烫得人睁不开眼。",
    "陈屿翻开天庭人事操作手册第七条，驳回上报会连带扣除杨戬三成镇守信力。",
    "老金从茶水间端着茶杯出来，提醒他再看一眼那只沾着黑泥的爪垫。",
    "年糕蹿上桌面，爪子按住哮天犬沾黑泥的爪垫，替它挪到了签名栏上。",
    "爪印落进新岗合同的签名栏，金光顺着爪纹一道道亮起来。",
    "哮天犬从颈圈底下叼出一个油布小包，里面是一枚天眼形状的旧徽章。",
    "老金转着茶杯盖说，上一个把这个岗干明白的人叫姜子牙。",
]
_FILLER = [
    "陈屿把徽章揣进口袋，楼下传来越野车发动的闷响，已经停了三天。",
    "走廊尽头那台永远自动出美式的咖啡机咕噜咕噜响个不停。",
    "老金绕出柜台，看着那份还差一个签名的岗位草案叹了口气。",
]
_REENACT_NEAR_VERBATIM = "爪印落进新岗合同的签名栏。"
_REENACT_CLAUSE_EMBED = (
    "金光顺着爪纹一道道亮起来——从爪心开始，一道一道，"
    "像有人蘸了金色墨汁顺着纹路描出来，热气从爪印边缘往上蒸。"
)
_REENACT_TAIL = "颈圈铜扣裂开一道缝，从缝里掉出一枚铜质徽章，骨碌碌滚了半圈停住。"

_INCIDENT_CHAPTER = "\n\n".join(
    ["# 第1章：哮天犬要辞职"]
    + _SCENE_A
    + _FILLER
    + [_REENACT_NEAR_VERBATIM, _REENACT_CLAUSE_EMBED, _REENACT_TAIL]
)

_CLEAN_CHAPTER = "\n\n".join(
    ["# 第2章：老街土地公"]
    + _SCENE_A
    + _FILLER
    + [
        "老土地公在天台上把搪瓷缸里的茶倒掉，转身往楼梯口走。",
        "白简递来的名片在巷口被年糕嗅了一遍，打出一串哈欠。",
        "工牌嗡了一下，浮出一行墨绿色的小字：候选人待背调。",
    ]
)


def test_incident_near_verbatim_sentence_is_detected_and_removed() -> None:
    findings = detect_cross_scene_beat_reenactment(_INCIDENT_CHAPTER)
    near = [f for f in findings if f["kind"] == "near_verbatim"]
    assert near, "the near-verbatim anchor paragraph must be detected"
    assert all(f["severity"] == "critical" for f in near)

    cleaned, removed = remove_cross_scene_near_verbatim_repeats(_INCIDENT_CHAPTER)
    assert removed == 1
    # later (re-enacted) copy is gone, the first occurrence survives
    assert _REENACT_NEAR_VERBATIM not in cleaned
    assert "爪印落进新岗合同的签名栏，金光顺着爪纹一道道亮起来。" in cleaned


def test_incident_reenactment_cluster_is_flagged_for_repair_not_deleted() -> None:
    findings = detect_cross_scene_beat_reenactment(_INCIDENT_CHAPTER)
    clusters = [f for f in findings if f["kind"] == "beat_reenactment"]
    assert clusters, "the re-enactment cluster must be detected"
    cluster = clusters[0]
    # recoverable repair contract — never a hard failure, never deletion
    assert cluster["repair_strategy"] == "rewrite_task"
    assert cluster["severity"] == "major"
    assert len(cluster["positions"]) >= 2

    cleaned, _removed = remove_cross_scene_near_verbatim_repeats(_INCIDENT_CHAPTER)
    # paraphrase-level re-enactment paragraphs must NOT be deleted
    assert _REENACT_CLAUSE_EMBED in cleaned
    assert _REENACT_TAIL in cleaned


def test_removal_is_idempotent() -> None:
    cleaned, removed_first = remove_cross_scene_near_verbatim_repeats(_INCIDENT_CHAPTER)
    assert removed_first == 1
    cleaned_again, removed_second = remove_cross_scene_near_verbatim_repeats(cleaned)
    assert removed_second == 0
    assert cleaned_again == cleaned


def test_clean_multi_scene_chapter_zero_false_positives() -> None:
    findings = detect_cross_scene_beat_reenactment(_CLEAN_CHAPTER)
    assert findings == []
    cleaned, removed = remove_cross_scene_near_verbatim_repeats(_CLEAN_CHAPTER)
    assert removed == 0
    assert cleaned == _CLEAN_CHAPTER


def test_character_requoting_earlier_line_is_not_removed() -> None:
    original = "哮天犬留下一句话：“老街片区的名单明天九点前必须交到我手上。”说完就走了。"
    requote = "温故在走廊尽头念道：“老街片区的名单明天九点前必须交到我手上。”"
    chapter = "\n\n".join(
        ["# 第3章：复命"]
        + [original]
        + _FILLER
        + _SCENE_A[:4]
        + [requote]
    )
    findings = detect_cross_scene_beat_reenactment(chapter)
    # a single quoted recall must not form a re-enactment cluster
    assert [f for f in findings if f["kind"] == "beat_reenactment"] == []
    # and the quoted recall must never be deleted
    cleaned, removed = remove_cross_scene_near_verbatim_repeats(chapter)
    assert removed == 0
    assert requote in cleaned


def test_short_chapter_below_gap_returns_no_findings() -> None:
    tiny = "\n\n".join(_SCENE_A[:3])
    assert detect_cross_scene_beat_reenactment(tiny) == []


def test_nearby_repeat_within_same_scene_is_left_to_layer_four() -> None:
    # An exact repeat only 2 paragraphs after the original is intra-scene
    # territory (layer 4: detect_intra_chapter_repetition) — the cross-scene
    # detector must not double-claim it.
    chapter = "\n\n".join(
        [
            _SCENE_A[4],
            _SCENE_A[0],
            _SCENE_A[4],
        ]
        + _FILLER
        + _SCENE_A[1:4]
    )
    findings = detect_cross_scene_beat_reenactment(chapter)
    assert [f for f in findings if f["kind"] == "near_verbatim"] == []


def test_real_incident_chapter_file_if_present() -> None:
    from pathlib import Path

    incident = Path(__file__).resolve().parents[2] / (
        "output/zhaoshen-hr-v3-1781180702/chapter-001.md"
    )
    if not incident.exists():  # output dirs are not committed
        return
    text = incident.read_text(encoding="utf-8")
    findings = detect_cross_scene_beat_reenactment(text)
    kinds = {f["kind"] for f in findings}
    assert "near_verbatim" in kinds
    assert "beat_reenactment" in kinds
    cleaned, removed = remove_cross_scene_near_verbatim_repeats(text)
    assert removed >= 1
    assert "爪印落进新岗合同的签名栏。\n" not in cleaned + "\n"
