"""68/E4 completion: scoped offline repairs, never library mutation."""

from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest

from bookengine.rhythm_contract_repair import apply_rhythm_contracts
from bookengine.chapter_type_repair import repair_chapter_type
from bookengine.chapter_rhythm import relationship_contract
from bookengine.text import chinese_count

CONTRACT = dict(people=["沈禾", "许青"], cause="沈禾隐瞒伤势", before="彼此猜疑", after="坦白后互相信任")


class OfflineClient:
    prefix = ""
    context_limit = 10000

    def __init__(self, out, replies=()):
        self.out, self.replies, self.calls = out, iter(replies), []

    def stage(self, name, prompt, schema=None, **kwargs):
        self.calls.append(dict(stage=name, prompt=prompt, options=kwargs))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return kwargs["parser"](json.dumps(reply, ensure_ascii=False))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("rhythm completion is offline")

    monkeypatch.setattr(socket.socket, "connect", deny)


def outline(kind="情感", number=34):
    scenes = [
        dict(
            where="旧屋",
            characters=["沈禾", "许青"],
            goal="说清隐瞒",
            action="沈禾坦白隐瞒",
            choice="许青接受道歉",
            result="两人同行",
        ),
        dict(
            where="门边",
            characters=["沈禾", "许青"],
            goal="重新约定边界",
            action="许青说出顾虑",
            choice="沈禾承诺尊重对方的决定",
            result="两人和解",
        ),
    ]
    return dict(
        number=number,
        type=kind,
        rhythm_type=kind,
        relationship_change={},
        title="雨后",
        scenes=scenes,
        hook="门外有人轻叩。",
        payoff_due=False,
    )


def bible():
    return dict(
        story_layer_version=1,
        volumes=[dict(number=4, start=31, end=40)],
        characters=[dict(name=n) for n in CONTRACT["people"]],
        story_design=dict(
            relationships=[
                dict(
                    people=CONTRACT["people"],
                    steps=[dict(stage=4, event=CONTRACT["cause"], before=CONTRACT["before"], after=CONTRACT["after"])],
                )
            ]
        ),
    )


def test_restore_complete_stage_contract_without_model_or_mutation(tmp_path):
    client, original = OfflineClient(tmp_path), outline()
    frozen = deepcopy(original)
    result = apply_rhythm_contracts(client, bible(), [original], {})[0]
    assert relationship_contract(result) == CONTRACT
    assert original == frozen and not client.calls
    note = result["rhythm_contract_repair"]
    assert note["planned_type"] == "情感" and note["planned_type_source"] == "outline.type"
    assert note["source"].startswith("bible.story_design.relationships")
    assert note["status"] == "restored" and note["fulfilled"] is False
    assert {k: v for k, v in result.items() if k not in ("relationship_change", "rhythm_contract_repair")} == {
        k: v for k, v in original.items() if k != "relationship_change"
    }
    assert list((tmp_path / "rhythm-revisions").rglob("*.json"))


def test_model_fills_only_missing_contract_and_necessary_scene_fields_once(tmp_path):
    source, design = outline(), bible()
    design["story_design"] = {}
    scenes = deepcopy(source["scenes"])
    scenes[0]["choice"] = "许青允许对方同行，但先说清顾虑"
    response = dict(relationship_change=CONTRACT, scenes=scenes, title="篡改标题", type="对抗")
    client = OfflineClient(tmp_path, [response])
    result = apply_rhythm_contracts(client, design, {"chapters": [source], "normalized": True}, {})
    repaired = result["chapters"][0]
    assert relationship_contract(repaired) == CONTRACT
    assert repaired["title"] == source["title"] and repaired["type"] == "情感"
    assert repaired["scenes"][0]["choice"] == scenes[0]["choice"]
    assert repaired["rhythm_contract_repair"]["source"] == "engine.targeted_planning"
    assert client.calls[0]["options"]["max_attempts"] == 1
    again = apply_rhythm_contracts(client, design, source, {}, chapter=34)
    assert relationship_contract(again) == CONTRACT and len(client.calls) == 1
    assert source["relationship_change"] == {}


def test_wrong_stage_and_incomplete_sources_do_not_count_as_restoration(tmp_path):
    design = bible()
    design["story_design"]["relationships"][0]["steps"][0]["stage"] = 3
    client = OfflineClient(tmp_path, [dict(relationship_change={})])
    result = apply_rhythm_contracts(client, design, outline(), {})
    note = result["rhythm_contract_repair"]
    assert relationship_contract(result) is None
    assert note["status"] == "failed" and note["reason"] == "invalid_model_contract"
    assert note["writer_ready"] is False and note["fulfilled"] is False
    assert len(client.calls) == 1
    apply_rhythm_contracts(client, design, result, {})
    assert len(client.calls) == 1


def test_model_failure_is_recorded_without_fabricated_completion(tmp_path):
    client = OfflineClient(tmp_path, [ValueError("bad model output")])
    result = apply_rhythm_contracts(client, enabled(), outline(), {})
    assert result["relationship_change"] == {}
    assert result["rhythm_contract_repair"]["reason"] == "source_model_failed:ValueError"
    assert result["rhythm_contract_repair"]["planned_type"] == "情感"
    apply_rhythm_contracts(client, enabled(), outline(), {})
    assert len(client.calls) == 1


def enabled():
    return dict(story_layer_version=1, characters=[dict(name=n) for n in CONTRACT["people"]])


def body_case(kind="揭秘"):
    source = outline(kind)
    text = "\n\n".join([f"沈禾在旧屋里端详第{i}块木板，许青低头守着门边，一句话也没有说。" for i in range(12)])
    text += "\n\n许青已经拿到三枚铜钱。\n\n门外有人轻叩。"
    source["word_range"] = [chinese_count(text) - 100, chinese_count(text) + 100]
    raw = dict(type="谋划", state={}, events=[], payoff=dict(fulfilled=True, quote="许青已经拿到三枚铜钱。"))
    return source, text, raw


def repair_case(client, source, text, raw, *, replacement=None, observed=None, invalid=False, alter=None):
    asked, extracted = [], []

    def ask(_client, key, instruction, sections, schema, **kwargs):
        targets = dict(sections)["可改主场景段"]
        asked.append((targets, kwargs))
        assert all(row["original"] != "门外有人轻叩。" for row in targets)
        edits = [dict(row, text=replacement or row["original"].replace("端详", "核对")) for row in targets]
        return dict(edits=alter(edits) if alter else edits)

    def extract(_client, key, candidate, *args, **kwargs):
        extracted.append(candidate)
        return dict(raw, type=observed or source["type"]), invalid, 0

    result = repair_chapter_type(client, "r2-ch34", text, raw, False, {}, {}, source, extract, ask)
    return result, asked, extracted


@pytest.mark.parametrize("kind", ["对抗", "谋划", "揭秘", "日常", "收获"])
def test_nonrelationship_main_scene_repair_changes_prose_not_labels(tmp_path, kind):
    source, text, raw = body_case(kind)
    raw["type"] = "日常" if kind == "谋划" else "谋划"
    before = deepcopy(source)
    client = OfflineClient(tmp_path)
    (changed, checked, failed, note), asked, extracted = repair_case(client, source, text, raw)
    assert changed != text and "核对" in changed
    assert checked["type"] == kind and not failed and note["status"] == "checked"
    assert source == before and note["planned_type"] == kind
    assert changed.endswith("门外有人轻叩。") and "许青已经拿到三枚铜钱。" in changed
    assert len(asked) == len(extracted) == 1 and asked[0][1]["max_attempts"] == 1
    (restored, _, _, again), calls, _ = repair_case(OfflineClient(tmp_path), source, text, raw)
    assert restored == changed and again["reused"] is True and not calls


@pytest.mark.parametrize(
    "failure",
    [
        "after_type_mismatch",
        "state_recheck_failed",
        "word_count_out_of_range",
        "invalid_edit_target",
        "lost_grounded_fact",
        "unchanged_prose",
    ],
)
def test_bad_candidates_rollback_with_specific_reason(tmp_path, failure):
    source, text, raw = body_case()
    options = {}
    if failure == "after_type_mismatch":
        options["observed"] = "对抗"
    if failure == "state_recheck_failed":
        options["invalid"] = True
    if failure == "word_count_out_of_range":
        options["alter"] = lambda edits: [
            dict(e, text=e["original"] if "铜钱" in e["original"] else "沈禾发现了真相。") for e in edits
        ]
    if failure == "invalid_edit_target":
        options["alter"] = lambda edits: edits + [dict(id=999, original="不存在", text="伪造")]
    if failure == "lost_grounded_fact":
        raw["events"] = [dict(kind="object", name="木板", quote="许青低头守着门边", action="stay")]
        options["alter"] = lambda edits: [
            dict(e, text=e["text"].replace("许青低头守着门边", "许青沉默着")) for e in edits
        ]
    if failure == "unchanged_prose":
        options["alter"] = lambda edits: [dict(e, text=e["original"]) for e in edits]
    (changed, checked, failed, note), _, _ = repair_case(OfflineClient(tmp_path), source, text, raw, **options)
    assert changed == text and checked is raw and failed is False
    assert note["status"] == "rolled_back" and note["reason"] == failure


def test_extractor_metadata_cannot_override_actual_observed_type(tmp_path):
    source, text, raw = body_case()

    def extract(*args, **kwargs):
        return dict(raw, type="对抗", relationship_change=CONTRACT), False, 0

    def ask(_client, key, instruction, sections, schema, **kwargs):
        return dict(
            edits=[dict(row, text=row["original"].replace("端详", "核对")) for row in dict(sections)["可改主场景段"]]
        )

    changed, _, _, note = repair_chapter_type(
        OfflineClient(tmp_path), "r2-ch34", text, raw, False, {}, {}, source, extract, ask
    )
    assert changed == text and note["reason"] == "after_type_mismatch"


def test_writer_fallback_records_source_failure_and_continues(tmp_path):
    # Instruction 79 (user 2026-11-20: “修补失败就降级续写”): record the failure, write, never call twice.
    client = OfflineClient(tmp_path, [dict(relationship_change={})])
    for _ in range(2):
        note = apply_rhythm_contracts(client, enabled(), outline(), {}, chapter=34, kind="writer")[
            "rhythm_contract_repair"
        ]
        assert note["reason"] == "invalid_model_contract" and note["writer_proceeded_without_contract"] is True
    assert len(client.calls) == 1


@pytest.mark.parametrize("change", ["location", "nonrelationship_action", "missing_scene"])
def test_planner_rejects_scene_scope_or_semantic_violations(tmp_path, change):
    source = outline()
    scenes = deepcopy(source["scenes"])
    if change == "location":
        scenes[0]["where"] = "另一世界"
    elif change == "nonrelationship_action":
        scenes[0].update(action="沈禾拔剑击退守卫", choice="杀死守卫")
    else:
        scenes.pop()
    client = OfflineClient(tmp_path, [dict(relationship_change=CONTRACT, scenes=scenes)])
    result = apply_rhythm_contracts(client, enabled(), source, {})
    # Instruction 79: the rejected scenes are not applied, but the usable contract is kept for the writer.
    assert result["relationship_change"] == CONTRACT
    assert result["scenes"] == source["scenes"]
    assert result["rhythm_contract_repair"]["writer_ready"] is False
    assert result["rhythm_contract_repair"]["degraded"] is True


def test_ambiguous_design_contract_uses_bounded_planning_instead_of_arbitrary_pair(tmp_path):
    design, source = bible(), outline()
    other = dict(people=["沈禾", "温朗"], steps=[dict(stage=4, event="说出秘密", before="有嫌隙", after="和解")])
    design["characters"].append(dict(name="温朗"))
    design["story_design"]["relationships"].append(other)
    for scene in source["scenes"]:
        scene["characters"].append("温朗")
    client = OfflineClient(tmp_path, [dict(relationship_change=CONTRACT)])
    result = apply_rhythm_contracts(client, design, source, {})
    assert result["rhythm_contract_repair"]["source"] == "engine.targeted_planning"
    assert len(client.calls) == 1 and relationship_contract(result) == CONTRACT


def test_relationship_path_still_repairs_with_tail_and_facts_protected(tmp_path):
    source, text, raw = body_case("情感")
    source["relationship_change"] = CONTRACT

    def ask(_client, key, instruction, sections, schema, **kwargs):
        assert kwargs["max_attempts"] == 1
        return dict(
            edits=[
                dict(
                    row, text=row["original"] if "铜钱" in row["original"] else row["original"].replace("端详", "坦白")
                )
                for row in dict(sections)["可改主场景段"]
            ]
        )

    def extract(*args, **kwargs):
        return dict(raw, type="情感"), False, 0

    changed, checked, failed, note = repair_chapter_type(
        OfflineClient(tmp_path), "r2-ch34", text, raw, False, {}, {}, source, extract, ask
    )
    assert changed != text and checked["type"] == "情感" and not failed and note["status"] == "checked"
    assert changed.endswith("门外有人轻叩。")


# Historical data is optional only when the actual isolated eval library is unavailable.
# Replies below are offline controls, not claims of new prose quality or a live model verdict.
EVAL68 = (
    Path(__file__).resolve().parents[2].parent / "bestseller-tmp/web68-books/webui/85ab9fc7-d694-4aba-9c80-7081467bdd47"
)


@pytest.fixture
def eval68_copy(tmp_path):
    import shutil
    import yaml

    if not EVAL68.is_dir():
        pytest.skip("real isolated eval68 source unavailable; no fabricated replacement")
    target = tmp_path / "eval68"
    target.mkdir()
    files = [EVAL68 / name for name in ("story_bible.yaml", "story_design.yaml")]
    for folder in ("steps", "writing_outlines", "outline-diversity", "chapters"):
        files.extend((EVAL68 / folder).glob("*.json"))
    files.extend(EVAL68.glob("chapter-*.body.md"))
    before = {str(p.relative_to(EVAL68)): p.read_bytes() for p in files}
    for name in before:
        destination = target / name
        destination.parent.mkdir(exist_ok=True, parents=True)
        shutil.copyfile(EVAL68 / name, destination)
    design = yaml.safe_load((target / "story_bible.yaml").read_text())
    design["story_design"] = yaml.safe_load((target / "story_design.yaml").read_text())
    yield target, design
    assert all((target / name).read_bytes() == contents for name, contents in before.items())
    assert all((EVAL68 / name).read_bytes() == contents for name, contents in before.items())


def _json(path):
    return json.loads(path.read_text())


@pytest.mark.parametrize("number", [34, 44])
def test_eval68_empty_contract_recovery_and_writer_seam_on_temporary_copy(eval68_copy, number):
    root, design = eval68_copy
    source = _json(root / f"writing_outlines/{number:03d}.json")
    assert source["relationship_change"] == {} and source["type"] == "情感"
    stage = next(s["number"] for s in design["volumes"] if s["start"] <= number <= s["end"])
    relation = design["story_design"]["relationships"][0]
    step = next(s for s in relation["steps"] if s["stage"] == stage)
    contract = dict(people=relation["people"], cause=step["event"], before=step["before"], after=step["after"])
    scenes = deepcopy(source["scenes"])
    for scene in scenes:
        scene.update(
            goal="说清顾虑并约定彼此边界",
            action="周铁匠坦白隐瞒，唐岚说出自己的顾虑",
            choice="唐岚接受坦白，允许共同承担风险",
        )
    client = OfflineClient(root, [dict(relationship_change=contract, scenes=scenes)])
    state = _json(root / f"chapters/{number - 1:03d}.json")["state"]
    repaired = apply_rhythm_contracts(client, design, source, state)
    assert relationship_contract(repaired) == contract
    assert repaired["rhythm_contract_repair"]["fulfilled"] is False
    assert len(client.calls) == 1 and repaired["rhythm_contract_repair"]["status"] == "repaired"
    if number == 34:
        assert repaired["rhythm_contract_repair"]["contract_source"].startswith("bible.story_design.relationships")
    assert apply_rhythm_contracts(client, design, repaired, state, kind="writer") == repaired
    assert len(client.calls) <= 1


@pytest.mark.parametrize(
    "number,planned,observed,step_name",
    [
        (1, "对抗", "谋划", "final-state"),
        (6, "揭秘", "谋划", "finish"),
        (8, "收获", "谋划", "momentum-payoff-recheck"),
        (32, "日常", "对抗", "continuity-state"),
        (34, "情感", "谋划", "final-state"),
        (44, "情感", "对抗", "dialogue-recheck"),
    ],
)
def test_eval68_saved_actual_type_cannot_be_replaced_by_planned_metadata(
    eval68_copy, number, planned, observed, step_name
):
    from bookengine.q1_extract import extract_state
    from bookengine.chapter_type_repair import _facts

    root, design = eval68_copy
    source = _json(root / f"writing_outlines/{number:03d}.json")
    text = (root / f"chapter-{number:03d}.body.md").read_text()
    raw = _json(root / f"steps/r2-ch{number}-{step_name}.json")
    assert source["type"] == planned and raw["type"] == observed
    if planned == "情感":
        stage = next(s["number"] for s in design["volumes"] if s["start"] <= number <= s["end"])
        relation = design["story_design"]["relationships"][0]
        step = next(s for s in relation["steps"] if s["stage"] == stage)
        source["relationship_change"] = dict(
            people=relation["people"], cause=step["event"], before=step["before"], after=step["after"]
        )
    source["word_range"] = [1870, 2700]
    client = OfflineClient(root, [raw])
    offered = []

    def ask(_client, key, instruction, sections, schema, **kwargs):
        assert kwargs["max_attempts"] == 1
        targets = dict(sections)["可改主场景段"]
        offered.extend(targets)
        # Preserve all authentic facts verbatim; the saved extractor still sees the old
        # actual type and MUST reject this superficial change even with a valid contract.
        from bookengine.text import _paragraph_spans

        spans = _paragraph_spans(text)
        facts = [(text.index(q), text.index(q) + len(q)) for q in _facts(raw, text)]
        return dict(
            edits=[
                dict(
                    row,
                    text=row["original"]
                    if any(start <= spans[row["id"] - 1][1] < end for start, end in facts)
                    else row["original"] + "他没有再说话。",
                )
                for row in targets
            ]
        )

    state = _json(root / f"chapters/{number - 1:03d}.json")["state"] if number > 1 else {}
    changed, checked, failed, note = repair_chapter_type(
        client, f"r2-ch{number}", text, raw, False, state, design, source, extract_state, ask
    )
    assert offered and changed == text and checked is raw and failed is False
    assert note["status"] == "rolled_back" and note["reason"] == "after_type_mismatch"
    assert note["after_type"] == observed and len(client.calls) == 1
    assert all(fact in changed for fact in _facts(raw, text))
    attempted = repair_chapter_type(
        client, f"r2-ch{number}", text, raw, False, state, design, source, extract_state, ask
    )
    assert attempted[-1]["reason"] == "already_attempted" and len(client.calls) == 1
