"""Check quote-grounded semantic reopening and bounded repair rollback."""

import json
import pytest

from bookengine.client import Client
from bookengine.plot_progression import repair_plot_replay
from bookengine.q1_extract import ask as q1_ask
from bookengine.storage import save


PRIOR_ACTION = "沈策沿湿墙借回声偏开巡吏的刀，趁他退后伸手护住钥匙。"
PRIOR_OUTCOME = "巡吏说封门令暂缓执行，吩咐先去验伤，转身带人离开。"
CURRENT = "巡吏站在药房外讨钥匙。沈策借湿墙传来的回响引偏刀锋，巡吏脚下一晃，松了手。"
REPLACEMENT = "巡吏让随从立刻把封门令钉上墙。沈策没有碰刀，把钥匙举到众人都能看清的地方。"
OPENING = "药房外聚着几名邻里。"
ENDING = "沈策把钥匙交给了坐在廊下的姐姐。"
TEXT = "\n\n".join([OPENING, CURRENT, ENDING])


@pytest.fixture
def completed_previous(tmp_path):
    save(tmp_path / "chapters/002.json", dict(status="complete", fact_source="final_body"))
    save(tmp_path / "chapter-002.body.md", f"{PRIOR_ACTION}\n\n{PRIOR_OUTCOME}")
    return tmp_path


def run(out, answers):
    stages, extracted = [], []

    def ask(_client, key, instruction, sections, _schema, **_kwargs):
        stages.append(key)
        if key.endswith("-adjacent-replay-review"):
            assert PRIOR_ACTION in sections[0][1] and PRIOR_OUTCOME in sections[0][1]
            assert CURRENT in sections[1][1]
            assert "同一对手依约回来" in instruction
        if key.endswith("-adjacent-replay-repair"):
            assert dict(sections)["命中原文，只替换这里"] == CURRENT
            assert dict(sections)["未改的前后文"] == [OPENING + "\n\n", "\n\n" + ENDING]
        return next(answers)

    def extract(_client, stage, candidate, *_args, **_kwargs):
        extracted.append((stage, candidate))
        return {"events": ["new"]}, False, 0

    text, raw, failed, note = repair_plot_replay(
        None,
        "r2-ch3",
        TEXT,
        {"events": ["old"]},
        False,
        {},
        {"characters": [{"name": "沈策"}], "antagonists": [{"name": "巡吏"}]},
        {"number": 3},
        extract,
        ask,
        out=out,
    )
    return text, raw, failed, note["adjacent_semantic"], stages, extracted


def finding(**overrides):
    return {
        "status": "replay",
        "finding": {
            "first_id": 2,
            "last_id": 2,
            "prior_action": PRIOR_ACTION,
            "prior_outcome": PRIOR_OUTCOME,
            "current_quote": CURRENT,
            **overrides,
        },
    }


def test_same_scene_outcome_and_mechanism_reexamined_after_repair(completed_previous):
    answer = [
        finding(),
        {"text": REPLACEMENT},
        {"status": "clear", "preserves_facts": True, "new_action_quote": "把钥匙举到众人都能看清的地方"},
    ]
    text, raw, failed, note, stages, extracted = run(completed_previous, iter(answer))
    assert text == "\n\n".join([OPENING, REPLACEMENT, ENDING])
    assert raw == {"events": ["new"]} and not failed
    assert note["status"] == "repaired" and note["before"]["prior_outcome"] == PRIOR_OUTCOME
    assert stages == [f"r2-ch3-adjacent-replay-{suffix}" for suffix in ("review", "repair", "recheck")]
    assert extracted == [("r2-ch3-adjacent-replay-state", text)]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"status": "clear"}, "checked"),
        ({"status": "unknown"}, "unknown"),
        (finding(prior_outcome="编造的引文根本没有在上章出现"), "unknown"),
        (finding(first_id=1, last_id=1), "unknown"),
    ],
)
def test_continuation_and_unproven_replay_do_not_rewrite(completed_previous, response, expected):
    retried = response.get("status") == "replay"
    text, raw, _, note, stages, extracted = run(completed_previous, iter([response] * (2 if retried else 1)))
    assert text == TEXT and raw == {"events": ["old"]}
    assert note["status"] == expected and not extracted
    assert stages == ["r2-ch3-adjacent-replay-review"] + (["r2-ch3-adjacent-replay-review-grounded"] if retried else [])


def test_ungrounded_citation_gets_one_opportunity_to_reanchor(completed_previous):
    answers = iter(
        [
            finding(current_quote="两处不相邻的句子"),
            finding(),
            {"text": REPLACEMENT},
            {"status": "clear", "preserves_facts": True, "new_action_quote": "把钥匙举到众人都能看清的地方"},
        ]
    )
    text, _, _, note, stages, _ = run(completed_previous, answers)
    assert stages[1] == "r2-ch3-adjacent-replay-review-grounded"
    assert note["status"] == "repaired" and REPLACEMENT in text


def test_repair_does_not_duplicate_unchanged_neighbor_paragraphs(completed_previous):
    answers = iter(
        [
            finding(),
            {"text": "\n\n".join((OPENING, REPLACEMENT, ENDING))},
            {"status": "clear", "preserves_facts": True, "new_action_quote": "把钥匙举到众人都能看清的地方"},
        ]
    )
    text, _, _, note, _, _ = run(completed_previous, answers)
    assert note["status"] == "repaired"
    assert text == "\n\n".join((OPENING, REPLACEMENT, ENDING))


def test_recheck_fixes_later_references_to_the_deleted_fight(completed_previous):
    new_ending = "沈策左手捧住钥匙，把它交给坐在廊下的姐姐。"
    answers = iter(
        [
            finding(),
            {"text": REPLACEMENT},
            {
                "status": "clear",
                "preserves_facts": True,
                "new_action_quote": "把钥匙举到众人都能看清的地方",
                "aftermath_edits": [{"id": 1, "text": new_ending}],
            },
        ]
    )
    text, _, _, note, _, extracted = run(completed_previous, answers)
    assert note["status"] == "repaired"
    assert text.endswith(new_ending) and extracted == [("r2-ch3-adjacent-replay-state", text)]


@pytest.mark.parametrize(
    "response",
    [
        {"status": "replay", "preserves_facts": True},
        {"status": "clear", "preserves_facts": False},
        {"status": "clear", "preserves_facts": True, "new_action_quote": "这句不在任何候选正文中"},
    ],
)
def test_inadequate_recheck_rolls_back_without_state_change(completed_previous, response):
    text, raw, _, note, _, extracted = run(completed_previous, iter([finding(), {"text": REPLACEMENT}, response]))
    assert text == TEXT and raw == {"events": ["old"]} and not extracted
    assert note["status"] == "rolled_back" and note["reason"] == "adjacent_replay_recheck_failed"


def test_partial_previous_checkpoint_is_not_used_for_review(completed_previous):
    save(completed_previous / "chapters/002.json", dict(status="running", fact_source="final_body"))
    text, raw, _, note, stages, extracted = run(completed_previous, iter([]))
    assert text == TEXT and raw == {"events": ["old"]}
    assert note["status"] == "not_reviewed" and not stages and not extracted


CHOICE = "沈策若转去拉小满，那只印匣就会掉进水里；若去够印匣，小满便会被浪卷走。"
PENDING = "沈策的手悬在船舷上，两个方向都只差了半尺。"
ASSUMED = "小满已经坐在药房门口，低头拧着湿透的袖子。"
DECISION = "沈策抛开印匣，攥紧小满的领口把她拽上船板。"
CONSEQUENCE = "那只木匣沉进水里，最后一点红漆也看不见了。"


def choice_finding(**overrides):
    return {
        "status": "skipped_choice",
        "finding": {
            "first_id": 1,
            "last_id": 1,
            "prior_choice": CHOICE,
            "prior_pending": PENDING,
            "current_quote": ASSUMED,
            **overrides,
        },
    }


def review_choice(tmp_path, prior, current, answers):
    save(tmp_path / "chapters/033.json", dict(status="complete", fact_source="final_body"))
    save(tmp_path / "chapter-033.body.md", prior)
    answers = iter(answers)
    stages = []

    def ask(_, stage, prompt, sections, schema, **kwargs):
        stages.append(stage)
        if stage.endswith("-adjacent-replay-review"):
            assert "skipped_choice" in prompt and "prior_pending" in schema
            assert "稍后揭示" in prompt and "换视角" in prompt and "已在上章解决" in prompt
            assert current.split("\n\n")[-1] in "\n".join(body for _, body in sections)
        return next(answers)

    def extract(_, stage, *_args, **_kwargs):
        stages.append(stage)
        return {"summary": "已复核稿"}, False, 0

    result = repair_plot_replay(
        None,
        "r2-ch34",
        current,
        {"summary": "原稿"},
        False,
        {},
        {"characters": [{"name": "沈策"}, {"name": "小满"}]},
        {"number": 34, "word_range": [10, 3000]},
        extract,
        ask,
        out=tmp_path,
    )
    return result, stages


def test_skipped_choice_restores_the_action_and_consequence_without_removing_the_result(tmp_path):
    ending = "沈策给她倒了半碗热水。"
    current = f"{ASSUMED}\n\n{ending}"
    replacement = DECISION + CONSEQUENCE + ASSUMED
    verdict = {
        "status": "clear",
        "preserves_facts": True,
        "new_action_quote": DECISION,
        "consequence_quote": CONSEQUENCE,
    }
    (text, raw, failed, note), stages = review_choice(
        tmp_path, f"{CHOICE}\n\n{PENDING}", current, [choice_finding(), {"text": replacement}, verdict]
    )
    assert text == replacement + "\n\n" + ending and raw == {"summary": "已复核稿"} and not failed
    assert note["adjacent_semantic"]["before"]["kind"] == "skipped_choice"
    assert note["adjacent_semantic"]["status"] == "repaired"
    assert stages == [f"r2-ch34-adjacent-replay-{stage}" for stage in ("review", "repair", "recheck", "state")]


@pytest.mark.parametrize("consequence", [None, "我找不到的不存在于正文的代价。", DECISION, DECISION[1:]])
def test_skipped_choice_requires_separate_grounded_consequence_or_rolls_back(tmp_path, consequence):
    current = ASSUMED + "\n\n沈策给她倒了半碗热水。"
    verdict = {
        "status": "clear",
        "preserves_facts": True,
        "new_action_quote": DECISION,
        "consequence_quote": consequence,
    }
    (text, raw, _, note), stages = review_choice(
        tmp_path, CHOICE + PENDING, current, [choice_finding(), {"text": DECISION + ASSUMED}, verdict]
    )
    assert text == current and raw == {"summary": "原稿"}
    assert note["adjacent_semantic"]["status"] == "rolled_back" and not stages[-1].endswith("-state")


def test_choice_from_the_middle_of_prior_chapter_cannot_justify_a_rewrite(tmp_path):
    prior = CHOICE + PENDING + "\n\n" + ("他们在岸边找了半天，最后都回了家。" * 90)
    current = ASSUMED + "\n\n沈策给她倒了半碗热水。"
    (text, _, _, note), stages = review_choice(tmp_path, prior, current, [choice_finding()] * 2)
    assert text == current and note["adjacent_semantic"]["status"] == "unknown"
    assert len(stages) == 2


def test_planned_dilemma_cannot_be_cleared_by_a_new_conflict_without_textual_evidence(tmp_path):
    save(tmp_path / "writing_outlines/033.json", {"ending_type": "两难选择"})
    answers = [{"status": "clear", "finding": {"reason": "已经开始下一场冲突"}}] * 2
    current = ASSUMED + "\n\n沈策在旁边替药铺掌柜抄账。"
    (text, _, _, note), stages = review_choice(tmp_path, CHOICE + PENDING, current, answers)
    assert text == current and note["adjacent_semantic"]["status"] == "unknown"
    assert len(stages) == 2


def test_delayed_resolution_can_clear_a_planned_choice_using_its_own_two_quotes(tmp_path):
    save(tmp_path / "writing_outlines/033.json", {"ending_type": "两难选择"})
    bridge = "在药房，她终于肯开口说方才在船上是怎么回事。"
    text = ASSUMED + "\n\n" + bridge + DECISION + CONSEQUENCE
    trace = dict(
        kind="shown", action_quote=DECISION, effect_quote=CONSEQUENCE, first_state_quote=ASSUMED, bridge_quote=bridge
    )
    (result, _, _, note), stages = review_choice(
        tmp_path, CHOICE + PENDING, text, [{"status": "clear", "choice_trace": trace}]
    )
    assert result == text and note["adjacent_semantic"]["status"] == "checked"
    assert len(stages) == 1


def test_delayed_reveal_after_opening_is_visible_to_the_same_review(tmp_path):
    middle = "等到药铺门外的雨停时，药汤又重新熬开了。" * 110
    current = "\n\n".join((ASSUMED, "沈策坐在对面等她开口。", middle, DECISION + CONSEQUENCE))
    (text, raw, _, note), stages = review_choice(tmp_path, CHOICE + PENDING, current, [{"status": "clear"}])
    assert text == current and raw == {"summary": "原稿"}
    assert note["adjacent_semantic"]["status"] == "checked" and len(stages) == 1


def packed_case(tmp_path, monkeypatch, previous, current, answers, **outline):
    save(tmp_path / "chapters/033.json", dict(status="complete", fact_source="final_body"))
    save(tmp_path / "chapter-033.body.md", previous)
    monkeypatch.setattr("bookengine.client.credentials", lambda *args: ("", ""))
    client = Client(tmp_path, None, "固定写作上下文。")
    client.context_limit = 10000
    client.clip_context = True
    prompts = {}
    answers = iter(answers)

    def request(stage, prompt, *_args):
        prompts[stage] = prompt
        return json.dumps(next(answers), ensure_ascii=False)

    monkeypatch.setattr(client, "request", request)
    text, _, _, note = repair_plot_replay(
        client,
        "r2-ch34",
        current,
        {},
        False,
        {},
        {"characters": [{"name": name} for name in ("沈策", "小满", "巡吏")]},
        {"number": 34, "word_range": [1, 30000], **outline},
        lambda *_args, **_kwargs: ({}, False, 0),
        q1_ask,
        out=tmp_path,
    )
    return text, note["adjacent_semantic"], prompts


@pytest.mark.parametrize(("response", "status"), [(choice_finding(), "unknown"), ({"status": "clear"}, "checked")])
def test_truncated_late_reveal_blocks_model_choice_verdict_in_real_q1_packing(tmp_path, monkeypatch, response, status):
    current = "\n\n".join((ASSUMED, "沈策还站在门外。", "镇上的人等天亮。" * 1800, DECISION + CONSEQUENCE))
    text, note, prompts = packed_case(tmp_path, monkeypatch, CHOICE + PENDING, current, [response])
    assert list(prompts) == ["r2-ch34-adjacent-replay-review"]
    assert DECISION not in next(iter(prompts.values()))
    assert note["status"] == status and note["reason"] == "choice_context_incomplete" and text == current
    if status == "checked":
        assert note["choice_status"] == "unknown"


def test_complete_choice_fits_both_production_requests_at_the_normal_cap(tmp_path, monkeypatch):
    last = "更夫挑着灯笼从药铺门前走过。"
    current = "\n\n".join((ASSUMED, "沈策还站在门外。", "镇上的人等天亮。" * 700, last))
    verdict = {
        "status": "clear",
        "preserves_facts": True,
        "new_action_quote": DECISION,
        "consequence_quote": CONSEQUENCE,
    }
    text, note, prompts = packed_case(
        tmp_path,
        monkeypatch,
        CHOICE + PENDING,
        current,
        [choice_finding(), {"text": DECISION + CONSEQUENCE + ASSUMED}, verdict],
    )
    assert note["status"] == "repaired" and text.endswith(last)
    assert all(last in prompts["r2-ch34-adjacent-replay-" + stage] for stage in ("review", "recheck"))


def test_incomplete_recheck_evidence_keeps_the_original(tmp_path, monkeypatch):
    current = ASSUMED + "\n\n沈策还站在门外。"
    text, note, prompts = packed_case(
        tmp_path,
        monkeypatch,
        CHOICE + PENDING,
        current,
        [choice_finding(), {"text": DECISION + CONSEQUENCE + ASSUMED}],
        required_event="长章既有安排。" * 1800,
    )
    assert note["reason"] == "choice_recheck_context_incomplete" and text == current
    assert list(prompts) == ["r2-ch34-adjacent-replay-review", "r2-ch34-adjacent-replay-repair"]


def test_truncated_late_prose_does_not_disable_grounded_replay(tmp_path, monkeypatch):
    current = "\n\n".join((OPENING, CURRENT, ENDING, "镇上的人等天亮。" * 1800))
    text, note, prompts = packed_case(
        tmp_path, monkeypatch, PRIOR_ACTION + PRIOR_OUTCOME, current, [finding(), {"text": ""}]
    )
    assert note["reason"] == "invalid_bounded_replacement" and text == current
    assert list(prompts) == ["r2-ch34-adjacent-replay-review", "r2-ch34-adjacent-replay-repair"]


@pytest.mark.parametrize("accept", [True, False])
def test_previous_planned_choice_can_retry_once_from_original_on_missing_participant(tmp_path, accept):
    save(tmp_path / "writing_outlines/033.json", {"ending_type": "两难选择"})
    text = ASSUMED + "\n\n沈策还站在门外。"
    missing = "他站起来伸手拉住她，木匣翻落水底。"
    good = DECISION + CONSEQUENCE + ASSUMED
    answers = [choice_finding(), {"text": missing}, {"text": good if accept else missing}]
    if accept:
        answers.append(
            {"status": "clear", "preserves_facts": True, "new_action_quote": DECISION, "consequence_quote": CONSEQUENCE}
        )
    (result, _, _, note), stages = review_choice(tmp_path, CHOICE + PENDING, text, answers)
    note = note["adjacent_semantic"]
    assert note["first_rejection"]["missing_names"] == ["小满"]
    assert stages.count("r2-ch34-adjacent-replay-retry-repair") == 1
    assert (note["status"], result) == (
        ("repaired", good + "\n\n沈策还站在门外。") if accept else ("rolled_back", text)
    )


def test_a_second_rescue_cannot_explain_being_safe_at_the_opening(tmp_path):
    save(tmp_path / "writing_outlines/033.json", {"ending_type": "两难选择"})
    current = ASSUMED + "\n\n第二天出船，又来了浪。沈策跳上船舷。" + DECISION + CONSEQUENCE
    trace = dict(kind="shown", action_quote=DECISION, effect_quote=CONSEQUENCE, first_state_quote=ASSUMED)
    reply = {"status": "clear", "choice_trace": trace}
    (result, _, _, note), stages = review_choice(tmp_path, CHOICE + PENDING, current, [reply, reply])
    assert result == current and len(stages) == 2
    assert note["adjacent_semantic"] == {"status": "unknown", "reason": "no_grounded_verdict"}
