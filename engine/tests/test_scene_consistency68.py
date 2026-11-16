"""Instruction 68 (E7, E8): sentences are from 《武馆新客·挨打成钢》 chapter 1 (r2-ch1-draft)."""

from pathlib import Path

import yaml

from bookengine.scene_consistency import (
    identity_label_findings,
    material_findings,
    restraint_findings,
    scene_consistency_checkup,
)

BIBLE = {
    "characters": [
        {"name": "石子翎", "role": "protagonist", "identity": "矿坑逃出来的陪练少年"},
        {"name": "雷九", "identity": "镇岳武馆外院管事"},
        {"name": "韩彦棕", "identity": "镇岳武馆落魄师兄"},
    ]
}
OPENING = "雷九这个镇岳武馆外院管事，把石子翎的旧衣丢在泥里时，这个少年先看见了自己那双破鞋。"
BOUND = "黑绳勒住石子翎双臂，绳结扎进腕骨。"
PUNCH = "石子翎沉腰贴地，左脚向外错开，一拳送出。"
FREED = "他先站稳，再把绑住双臂的黑绳往木桩上一压，磨断绳结。"
CHAPTER = "\n\n".join(
    [OPENING, BOUND, "铁棍正中胸骨，沉闷一响像石梁砸进矿洞。", "他手腕一翻，木棍撤向胸前，准备再补一记。", PUNCH, FREED]
)


def test_object_that_changes_material_is_reported_with_both_sentences():
    (row,) = material_findings(CHAPTER)
    assert (row["first"], row["later"]) == ("铁棍", "木棍")
    assert row["first_quote"].startswith("铁棍正中胸骨") and "木棍撤向胸前" in row["quote"]


def test_two_different_objects_far_apart_are_not_a_change():
    filler = "他继续往前走。" * 30
    assert material_findings("雷九提着铁棍进了院子。" + filler + "后院的架子上横着一根木棍。") == []
    assert material_findings("铁桩立在院中。几步外还有一排木桩。") == []


def test_bound_character_who_punches_before_being_freed_is_reported():
    (row,) = restraint_findings(CHAPTER, "石子翎")
    assert row["bound_quote"] == BOUND and row["quote"] == PUNCH
    assert restraint_findings("\n\n".join([BOUND, FREED, PUNCH]), "石子翎") == []
    assert restraint_findings("\n\n".join([BOUND, "雷九抬手就是一棍。"]), "石子翎") == []


def test_identity_run_together_with_the_name_is_reported():
    labels = {row["name"]: row["label"] for row in identity_label_findings(CHAPTER, BIBLE)}
    assert labels == {"雷九": "镇岳武馆外院管事"}
    lead = "镇岳武馆落魄师兄韩彦棕靠在院墙边，嗓音沙哑。"
    assert identity_label_findings(lead, BIBLE)[0]["name"] == "韩彦棕"
    assert identity_label_findings("韩彦棕靠在院墙边。旁人喊他一声师兄，他没应。", BIBLE) == []


def test_checkup_counts_first_appearances_only_and_is_reference_only():
    chapters = [{"number": 1, "text": CHAPTER}, {"number": 2, "text": "镇岳武馆外院管事雷九又来了。"}]
    report = scene_consistency_checkup(BIBLE, chapters)
    assert report["counts"] == {"material_change": 1, "acts_while_bound": 1, "identity_label": 1}
    assert report["reference_only"] is True


def test_writer_and_repair_prompts_keep_identity_apart_from_the_name():
    root = Path(__file__).parents[1]
    design = yaml.safe_load((root / "data/story_design.yaml").read_text())
    assert "身份和姓名分开写" in str(design)
    assert "身份和姓名分开写" in (root / "bookengine/q1_extract.py").read_text()
