import json

from deai.io import process
from deai.story_deslop_evidence import chapter_actions

OLD = "她以旧剑转腕撬开木匣，取出残页。她收起纸，收进怀里。"
BAD = "她撬开木匣，取出残页，收进怀里。"
GOOD = "她以旧剑转腕撬开木匣，取出残页，收进怀里。"
REST = "屋外的风还刮着，窗棂响个不停。\n\n炉子里的余灰已经黯淡，走廊外的雨还没有停。\n\n掌柜端着油灯，没有进来。"


def record():
    return dict(
        number=3,
        status="complete",
        fact_source="final_body",
        solutions=[dict(chapter=3, noun_status="Evidence", quote=OLD, method_noun="旧剑转腕", target_noun="残页")],
    )


def test_only_current_grounded_solution_can_protect_a_paragraph():
    assert chapter_actions(record(), OLD, "003") == {"P1": ["旧剑转腕", "残页"]}
    assert not chapter_actions(record(), OLD, "004")
    assert not chapter_actions(dict(record(), state_failed=True), OLD, "003")
    assert not chapter_actions(record(), GOOD, "003")
    assert not chapter_actions(record(), OLD + "\n\n" + OLD, "003")
    mixed = record()
    mixed["solutions"][0].update(target_noun="别章的残页")
    assert chapter_actions(mixed, OLD, "003") == {"P1": ["旧剑转腕"]}


class Editor:
    def __init__(self, before, after, quote="", keep=None, pid="P1"):
        self.before, self.after, self.quote, self.keep, self.pid = before, after, quote, keep, pid
        self.calls = []

    def __call__(self, messages, *, stage, max_tokens):
        self.calls.append({"stage": stage})
        if stage.startswith("story-deslop-scan"):
            row = dict(
                id=self.pid,
                quote=self.before,
                gates=["C"],
                kind="expression",
                reason="这一段的措辞与人物说话的口气脱节",
            )
            if self.keep is not None:
                row["keep"] = self.keep
            return dict(tier="轻度", findings=[row])
        if stage.startswith("story-deslop-edit"):
            return dict(edits=[dict(id=self.pid, original=self.before, replacement=self.after)])
        assert stage.startswith("story-deslop-recheck")
        verdict = dict(id=self.pid, preserves=True, improves=True, reason="同一动作被压缩", fact_quote=self.quote)
        return dict(facts_preserved=True, verdicts=[verdict])

    def estimate(self):
        return 0


def run(root, source, model, *, preserve=False):
    path = root / "chapter-003.body.md"
    path.write_text(source)
    status = process(
        root, "003", path, None, "local", None, lambda *a, **kw: model, editor="story-deslop", preserve=preserve
    )
    assert path.read_text() == source
    return status, json.loads((root / "final/chapter-003.deai.json").read_text())


def test_grounded_method_survives_polish_and_recheck_quotes_are_checked(tmp_path):
    source = OLD + "\n\n" + REST
    (tmp_path / "chapters").mkdir()
    (tmp_path / "chapters/003.json").write_text(json.dumps(record()))
    bad = Editor(OLD, BAD)
    status, report = run(tmp_path, source, bad)
    assert status == "unchanged" and report["text"] == source and len(bad.calls) == 2
    assert report["checks"]["story_deslop"]["programmatic_rejections"][0]["reasons"] == ["missing_fact_anchor:旧剑转腕"]
    # Completed old finals remain resumable. An explicit run re-evaluates on policy or evidence change.
    policy = tmp_path / "final/.deai/003/policy.json"
    value = json.loads(policy.read_text())
    value["editor"]["adapter_version"] = 1
    policy.write_text(json.dumps(value))
    assert run(tmp_path, source, bad, preserve=True)[0] == "cached" and len(bad.calls) == 2
    unsupported = Editor(OLD, GOOD, quote="她把纸卷递给了掌柜。")
    status, report = run(tmp_path, source, unsupported)
    assert status == "unchanged" and report["text"] == source
    assert not report["checks"]["story_deslop"]["review"]["verdicts"][0]["preserves"]
    value["editor"]["adapter_version"] = 1
    policy.write_text(json.dumps(value))
    good = Editor(OLD, GOOD, quote=GOOD)
    status, report = run(tmp_path, source, good)
    assert status == "polished" and report["text"] == GOOD + "\n\n" + REST
    assert run(tmp_path, source, good)[0] == "cached" and len(good.calls) == 3


def test_ending_referent_selected_before_edit_survives_without_a_chapter_record(tmp_path):
    clue = "她举着灯，指向角门外那串湿脚印，又朝门框敲了敲。"
    source = REST + "\n\n" + clue
    bad = Editor(clue, "她举着灯，朝角门外敲了敲门框。", keep=["湿脚印"], pid="P4")
    status, report = run(tmp_path, source, bad)
    assert status == "unchanged" and report["text"] == source
    assert len(bad.calls) == 2


def test_swapping_both_named_people_and_retaining_tool_does_not_pass(tmp_path):
    old = "沈芸用铜匙撬开木匣，李明站在旁边扶着灯。"
    swapped = "李明用铜匙撬开木匣，沈芸站在旁边扶着灯。"
    source = old + "\n\n" + REST
    (tmp_path / "story_bible.yaml").write_text("characters:\n  - name: 沈芸\n  - name: 李明\n")
    model = Editor(old, swapped, swapped, ["铜匙"])
    _, report = run(tmp_path, source, model)
    assert report["text"] == source
    assert "changed_fact_operator" in report["checks"]["story_deslop"]["programmatic_rejections"][0]["reasons"]


def test_attempt_to_retrieve_the_same_object_does_not_count_as_a_completed_action(tmp_path):
    changed = GOOD.replace("取出残页", "想要取出残页")
    (tmp_path / "chapters").mkdir()
    (tmp_path / "chapters/003.json").write_text(json.dumps(record()))
    model = Editor(OLD, changed, quote=changed)
    status, report = run(tmp_path, OLD + "\n\n" + REST, model)
    assert status == "unchanged"
    assert "changed_fact_outcome" in report["checks"]["story_deslop"]["programmatic_rejections"][0]["reasons"]
