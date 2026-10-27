"""Every matching segment survives production evidence and paragraph grouping."""

# ruff: noqa: RUF001 -- Preserve realistic Chinese prose punctuation.

from types import SimpleNamespace

import pytest

from bestseller.services.deterministic_post_write_audit import (
    _find_near_copy_offset,
    _normalize_copy_text,
    audit_chapter_prose,
)
from bestseller.services.retained_candidate_patch import FINDING_CODE, prepare_candidate_patch

pytestmark = pytest.mark.unit

SOURCES = [
    (
        "The astronomer folded the midnight chart beneath a cracked brass compass.",
        "The astronomer folded the midnighx chart beneath a cracked brass compass.",
    ),
    (
        "测绘员收起刚描完的海岸图，把那支铜笔压在玻璃镇纸下面。",
        "测绘员收起刚描完的海岸图，把那支铜笔压在琉璃镇纸下面。",
    ),
]


def scenes(source):
    # All three scenes declare the same source. They must not multiply hits.
    return [
        SimpleNamespace(
            scene_number=i,
            key_dialogue_beats=[source],
            sensory_anchors={},
            rewrite_hint=None,
            metadata_json={},
        )
        for i in range(1, 4)
    ]


def findings(tmp_path, body, source):
    report = audit_chapter_prose(
        chapter_text=body, chapter_number=8, project_dir=tmp_path, scenes=scenes(source)
    )
    hits = [finding for finding in report.findings if finding.code == FINDING_CODE]
    assert report.passed is False
    assert report.summary[FINDING_CODE] == len(hits)
    return hits


@pytest.mark.parametrize(("source", "near"), SOURCES)
def test_same_source_two_exact_paragraphs_and_later_near_copy_all_bound(tmp_path, source, near):
    # Prove the existing matcher already recognizes this near copy; do not
    # introduce a new threshold or normalize any story values in production.
    assert _find_near_copy_offset(_normalize_copy_text(source), [(near, 23)]) == 23
    body = "# Archive voyage\n\n" + "\n\n".join([source, source, near]) + "\n\nUnrelated tail.\n"
    hits = findings(tmp_path, body, source)
    assert len(hits) == 3
    assert [finding.line_number for finding in hits] == [3, 5, 7]
    assert [finding.column for finding in hits] == [1, 1, 1]
    assert [finding.matched_text for finding in hits] == [source[:80]] * 3
    prepared = prepare_candidate_patch(body, hits)
    assert len(prepared["targets"]) == 3
    assert [target["original_text"] for target in prepared["targets"]] == [source, source, near]
    assert [target["finding_indexes"] for target in prepared["targets"]] == [[0], [1], [2]]
    assert len({target["target_id"] for target in prepared["targets"]}) == 3


@pytest.mark.parametrize(("source", "near"), SOURCES)
def test_multiple_matching_segments_in_same_paragraph_group_one_patch(tmp_path, source, near):
    body = f"# Another chronicle\n\n{source}\n{source}\n{near}\n\nTail remains.\n"
    hits = findings(tmp_path, body, source)
    assert len(hits) == 3
    assert [finding.line_number for finding in hits] == [3, 4, 5]
    prepared = prepare_candidate_patch(body, hits)
    assert len(prepared["targets"]) == 1
    target = prepared["targets"][0]
    assert target["finding_indexes"] == [0, 1, 2]
    assert target["original_text"] == f"{source}\n{source}\n{near}"
    assert (target["start_line"], target["end_line"]) == (3, 5)
