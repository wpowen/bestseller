"""Production audit evidence is complete before exact paragraph patching."""

# ruff: noqa: RUF001 -- Preserve Chinese narrative punctuation in unrelated fixtures.

from hashlib import sha256
from types import SimpleNamespace

import pytest

from bestseller.services.deterministic_post_write_audit import audit_chapter_prose
from bestseller.services.retained_candidate_patch import FINDING_CODE, prepare_candidate_patch

pytestmark = pytest.mark.unit

ENGLISH = [
    "The astronomer folded the midnight chart beneath a cracked brass compass.",
    "A courier balanced three sealed parcels while the last ferry pulled away.",
    "Under the greenhouse roof a botanist counted the pale roots in silence.",
    "The mechanic wiped engine oil from the emergency transmitter before answering.",
    "Across the archive floor a narrow beam illuminated the missing ledger's outline.",
]
CHINESE = [
    "测绘员收起刚描完的海岸图，把那支铜笔压在玻璃镇纸下面。",
    "修船匠蹲在船坞尽头，用沾满油渍的袖口擦净了断裂铆钉。",
    "候车室里的姑娘扶起歪倒的手提箱，将蓝色车票折进衣袋。",
    "守塔人举着罩住火苗的旧灯，沿石阶逐级检查昨天留下的水印。",
    "最后一班渡船离岸以后，码头上的小贩默默合上了赊账簿子。",
]


def scene(number, source):
    return SimpleNamespace(
        scene_number=number,
        key_dialogue_beats=[source],
        sensory_anchors={},
        rewrite_hint=None,
        metadata_json={},
    )


def audit(tmp_path, paragraphs, scenes):
    body = "# Observatory records\n\n" + "\n\n".join(paragraphs) + "\n"
    report = audit_chapter_prose(
        chapter_text=body, chapter_number=4, project_dir=tmp_path, scenes=scenes
    )
    copies = [finding for finding in report.findings if finding.code == FINDING_CODE]
    return body, report, copies


@pytest.mark.parametrize("paragraphs", [ENGLISH[:4], ENGLISH, CHINESE])
def test_actual_audit_and_legacy_prepare_include_every_unique_paragraph(tmp_path, paragraphs):
    body, report, copies = audit(
        tmp_path, paragraphs, [scene(index, text) for index, text in enumerate(paragraphs, 1)]
    )
    assert len(copies) == len(paragraphs) > 3
    assert report.summary[FINDING_CODE] == len(paragraphs)
    assert report.passed is False
    assert [finding.matched_text for finding in copies] == [text[:80] for text in paragraphs]
    assert [finding.line_number for finding in copies] == list(range(3, 2 * len(paragraphs) + 2, 2))
    prepared = prepare_candidate_patch(body, copies)
    assert len(prepared["targets"]) == len(paragraphs)
    assert [target["original_text"] for target in prepared["targets"]] == paragraphs
    assert len({target["target_id"] for target in prepared["targets"]}) == len(paragraphs)
    assert prepared["body_sha256"] == sha256(body.encode()).hexdigest()


def test_many_scenes_remain_complete_deduplicated_and_in_original_source_order(tmp_path):
    paragraphs = [
        f"Archive entry {sha256(str(index).encode()).hexdigest()} was sealed."
        for index in range(24)
    ]
    scenes = [scene(index, text) for index, text in enumerate(reversed(paragraphs), 1)]
    scenes.extend(scene(100 + index, text) for index, text in enumerate(paragraphs))
    body, report, copies = audit(tmp_path, paragraphs, scenes)
    assert len(copies) == report.summary[FINDING_CODE] == 24
    assert [finding.matched_text for finding in copies] == [
        text[:80] for text in reversed(paragraphs)
    ]
    assert [finding.line_number for finding in copies] == list(range(49, 2, -2))
    prepared = prepare_candidate_patch(body, copies)
    assert [target["original_text"] for target in prepared["targets"]] == paragraphs
    assert [target["finding_indexes"] for target in prepared["targets"]] == [
        [i] for i in range(23, -1, -1)
    ]


def test_multiple_sources_in_one_paragraph_preserve_all_findings_and_one_target(tmp_path):
    paragraphs = [ENGLISH[0] + " " + ENGLISH[1], *ENGLISH[2:], CHINESE[0]]
    sources = [*ENGLISH, CHINESE[0]]
    body, report, copies = audit(
        tmp_path, paragraphs, [scene(index, text) for index, text in enumerate(sources, 1)]
    )
    assert len(copies) == report.summary[FINDING_CODE] == 6
    prepared = prepare_candidate_patch(body, copies)
    assert len(prepared["targets"]) == 5
    assert prepared["targets"][0]["finding_indexes"] == [0, 1]
    assert [target["original_text"] for target in prepared["targets"]] == paragraphs


def test_short_facts_and_unscanned_purpose_do_not_gain_new_authority(tmp_path):
    scenes = [scene(index, text) for index, text in enumerate(ENGLISH, 1)]
    scenes.append(scene(10, "red lamp"))
    scenes.append(
        SimpleNamespace(
            scene_number=11,
            purpose=CHINESE[0],
            key_dialogue_beats=[],
            sensory_anchors={},
            metadata_json={},
        )
    )
    _, report, copies = audit(tmp_path, [*ENGLISH, "red lamp", CHINESE[0]], scenes)
    assert len(copies) == report.summary[FINDING_CODE] == 5
    assert all(finding.matched_text not in {"red lamp", CHINESE[0]} for finding in copies)
