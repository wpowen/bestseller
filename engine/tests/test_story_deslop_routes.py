import json

import pytest

from deai.story_deslop_evidence import support_is_distinct
from test_story_deslop_evidence import REST, run
from test_story_deslop_promises import ApproveEdits


CALL = "“回来！”她追到岸边。"
TARGET = "小舟已经过了河心。她想叫他折回来。她隔着水又喊了一遍。"
REVISED = "小舟已经过了河心。她隔着水又喊了一遍。"
SOURCE = "\n\n".join((CALL, TARGET, REST))


def finding(**overrides):
    return dict(
        dict(
            id="P2",
            kind="redundant_explanation",
            quote="她想叫他折回来。",
            supports={"id": "P1", "quote": "“回来！”"},
            gates=["C"],
            reason="喊话已经让同伴回来，旁白随即解释她想叫他回来。",
        ),
        **overrides,
    )


class Scan(ApproveEdits):
    def __init__(self, findings, edits=None, *, reject=False):
        super().__init__(edits or {})
        self.findings = findings
        self.reject = reject

    def __call__(self, messages, *, stage, max_tokens):
        if stage == "story-deslop-scan-chapter":
            self.calls.append({"stage": stage})
            return {"tier": "轻度", "findings": self.findings}
        if stage == "story-deslop-edit-chapter":
            assert self.edits, "an unsupported finding must not spend an edit call"
        value = super().__call__(messages, stage=stage, max_tokens=max_tokens)
        if stage == "story-deslop-recheck-chapter" and self.reject:
            value["facts_preserved"] = False
        return value


def test_grounded_duplicate_keeps_witness_and_unrelated_words(tmp_path):
    scan = Scan([finding()], {"P2": (TARGET, REVISED, "")})
    status, report = run(tmp_path, SOURCE, scan)
    assert status == "polished" and len(scan.calls) == 3
    assert report["text"] == SOURCE.replace(TARGET, REVISED)
    check = report["checks"]["story_deslop"]
    assert check["findings"][0]["route"] == "local_edit"
    assert (check["prose_edits"], check["punctuation_edits"]) == (1, 0)


@pytest.mark.parametrize(
    "row, route",
    [
        (finding(supports={}), "insufficient_context"),
        (finding(supports={"id": "P2", "quote": "“回来！”"}), "insufficient_context"),
        (finding(supports={"id": "P2", "quote": "想叫他折回来"}), "insufficient_context"),
        (finding(supports={"id": [], "quote": "“回来！”"}), "insufficient_context"),
        (finding(kind="scene_structure"), "return_to_planning"),
        (finding(kind="insufficient_context"), "insufficient_context"),
        (finding(kind="unrecognized"), "keep"),
        (finding(kind="expression", gates=["A"]), "keep"),
        (finding(kind="expression", id="P1", quote="轻轻"), "keep"),
    ],
)
def test_notes_without_local_evidence_preserve_source_after_scan(tmp_path, row, route):
    source = SOURCE.replace("追到岸边", "轻轻跺脚，追到岸边——已经迟了")
    scan = Scan([row])
    _, report = run(tmp_path, source, scan)
    assert report["text"] == source and len(scan.calls) == 1
    check = report["checks"]["story_deslop"]
    assert check["findings"][0]["route"] == route
    assert check["text_effect"] == "unchanged" and check["accepted_paragraphs"] == 0


def test_ambiguous_or_overlapping_citations_cannot_authorize_deletion():
    index = {"P1": {"text": "停。停。慢慢慢走。"}}
    assert not support_is_distinct(finding(id="P1", quote="停。", supports={"id": "P1", "quote": "走。"}), index)
    assert not support_is_distinct(finding(id="P1", quote="走。", supports={"id": "P1", "quote": "慢慢"}), index)


def test_authorized_edit_cannot_delete_other_paragraphs_witness(tmp_path):
    scan = Scan(
        [finding(), finding(kind="expression", id="P1", quote=CALL)],
        {"P1": (CALL, "她一路追到岸边。", ""), "P2": (TARGET, REVISED, "")},
    )
    _, report = run(tmp_path, SOURCE, scan)
    assert report["text"] == SOURCE.replace(TARGET, REVISED)
    assert {"pid": "P1", "reasons": ["removed_support_quote"]} in report["checks"]["story_deslop"][
        "programmatic_rejections"
    ]


def test_unrelated_change_outside_cited_redundancy_keeps_paragraph(tmp_path):
    scan = Scan([finding()], {"P2": (TARGET, TARGET.replace("小舟", "竹筏"), "")})
    _, report = run(tmp_path, SOURCE, scan)
    assert len(scan.calls) == 2 and report["text"] == SOURCE
    assert report["checks"]["story_deslop"]["programmatic_rejections"][0]["reasons"] == ["untouched_target_quote"]


@pytest.mark.parametrize("reject", [False, True])
def test_punctuation_only_counted_separately_and_rollback_preserves_dashes(tmp_path, reject):
    old = "她隔着水又喊了一遍——没有应声。"
    new = old.replace("——", "。")
    source = "\n\n".join(("岸上——只留下一双鞋。", old, REST))
    row = finding(kind="expression", quote=old, gates=["B"])
    scan = Scan([row], {"P2": (old, new, "")}, reject=reject)
    _, report = run(tmp_path, source, scan)
    check = report["checks"]["story_deslop"]
    assert len(scan.calls) == 3 and check["proposed_paragraphs"] == 1
    assert report["text"] == (source if reject else source.replace(old, new))
    assert check["text_effect"] == ("unchanged" if reject else "punctuation_only")
    assert check["punctuation_edits"] == int(not reject) and check["prose_edits"] == 0


@pytest.mark.parametrize("subset_ok", [True, False])
def test_style_rejection_reviews_exact_remaining_combination_once(tmp_path, subset_ok):
    first = "他从椅背上拿起雨伞，走过去靠在门边。"
    second = "她抬头看着墙上的钟，抬了抬下巴。"
    replacements = {
        "P1": (first, first.replace("过去靠在", "到"), ""),
        "P2": (second, second.replace("抬头看着", "望了一眼"), ""),
    }
    rows = [finding(id=pid, quote=old, kind="expression") for pid, (old, _, _) in replacements.items()]

    class Mixed(Scan):
        def __call__(self, messages, *, stage, max_tokens):
            if not stage.startswith("story-deslop-recheck"):
                return super().__call__(messages, stage=stage, max_tokens=max_tokens)
            self.calls.append({"stage": stage})
            data = json.loads(messages[1]["content"].rsplit("\n", 1)[-1])
            ids = [c["id"] for c in data["changes"]]
            if stage.endswith("-chapter"):
                assert ids == ["P1", "P2"]
            else:
                assert stage.endswith("-subset") and ids == ["P2"]
                assert data["chapter"][0]["text"] == first
                assert data["chapter"][1]["text"] == replacements["P2"][1]
            return {
                "facts_preserved": True,
                "verdicts": [
                    {
                        "id": pid,
                        "preserves": True,
                        "improves": pid == "P2" and (len(ids) == 2 or subset_ok),
                        "reason": "连读两句",
                    }
                    for pid in ids
                ],
            }

    model = Mixed(rows, replacements)
    source = "\n\n".join((first, second, REST))
    _, report = run(tmp_path, source, model)
    assert len(model.calls) == 4
    assert report["text"] == (source.replace(second, replacements["P2"][1]) if subset_ok else source)
    assert report["checks"]["story_deslop"]["accepted_paragraphs"] == int(subset_ok)
