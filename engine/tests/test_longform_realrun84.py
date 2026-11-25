"""Regressions from the instruction 84 real-book run (e09db97a, chapters 1–17)."""

from bookengine.chapter_callbacks import outline_callbacks
from bookengine.story_duties import record_story_duties

VERIFIED = "老证人将旧账压在残页旁，没有再收回去：“我作证。”"


def test_paraphrased_callback_is_replaced_by_the_verified_quote_of_that_chapter():
    candidates = [dict(chapter=12, source_quote=VERIFIED)]
    chapter = dict(
        number=13,
        callback_events=[
            dict(chapter=12, source_quote="陆厌以谱页缺位困杀陆沉锋，老证人作证。"),
            dict(chapter=13, source_quote="同卷上一章的梗概。"),
        ],
    )
    assert outline_callbacks(chapter, candidates, 25, 24) == candidates


def test_callback_to_a_chapter_without_a_verified_candidate_is_dropped():
    chapter = dict(number=15, callback_events=[dict(chapter=13, source_quote="同卷梗概。")])
    assert outline_callbacks(chapter, [dict(chapter=12, source_quote=VERIFIED)], 25, 24) == []


def _review(tmp_path, kind, anchors, text, quote, number=9):
    # The real chapters name the anchor elsewhere, which is what sends the duty to review.
    text = f"{anchors[0]}的事还没人提。{text}"
    duty = dict(id=f"x-{kind}", kind=kind, chapter=number, anchors=anchors, names=[], text="要求")

    def ask(*args, **kwargs):
        return {"items": [dict(id=duty["id"], chapter=number, status="fulfilled", quote=quote)]}

    return record_story_duties(tmp_path, number, text, {"story_duties": [duty]}, ask=ask)[0]["status"]


def test_plant_review_accepts_a_shortened_anchor_in_scene(tmp_path):
    text = "荒沟里的落锁声追在身后。陆厌没有回头，贴着残墙绕过两道断阶，半截焦黑灯芯夹在指间。"
    assert _review(tmp_path, "plant", ["灯芯残片"], text, text) == "done"


def test_plant_review_accepts_scene_verbs_outside_the_fast_path(tmp_path):
    text = "陆厌没有动怒。他将祖灯后殿捡到的焦黑灯芯残片放在祭器旁，又以半截真灯芯贴近灯座。"
    assert _review(tmp_path, "plant", ["灯芯残片"], text, text) == "done"


def test_truth_review_accepts_a_realization_without_the_anchor(tmp_path):
    text = "他终于明白，曾祖真正想说的是：“换谱者，非陆澜。”"
    assert _review(tmp_path, "truth", ["祖灯指印"], text, text, number=5) == "done"


def test_payoff_review_still_needs_an_action_or_result(tmp_path):
    text = "蓝信封摆在桌上。"
    assert _review(tmp_path, "payoff", ["蓝信封"], text, text) == "unverified"


def test_remind_review_accepts_an_effect_named_with_other_verbs(tmp_path):
    text = "金辉随他的手掌向下偏移，原本冲向陆霜的银白灵流被引向门轴。"
    assert _review(tmp_path, "remind", ["祖脉金辉"], text, text, number=15) == "done"


def test_review_accepts_two_sentences_across_a_paragraph_break(tmp_path):
    first = "纸上残存的气息与祖阵金辉相触，将他们脚边一处正在倒卷的裂纹照亮。"
    second = "裂纹随即改变方向，从原本通向二房秘库，转而绕向下方。"
    text = f"{first}\n\n{second}"
    assert _review(tmp_path, "remind", ["祖脉金辉"], text, f"{first}\n\n{second}", number=14) == "done"


def test_truth_review_accepts_a_spoken_reversal(tmp_path):
    text = "“原簿有令，另有准他替换的人。此前说罪证全由他一人伪造，不对。”"
    assert _review(tmp_path, "truth", ["焦黑罪证"], text, text, number=15) == "done"


def test_review_accepts_the_tail_of_a_sentence_without_negation(tmp_path):
    text = "陆厌盯着残存印记，“原簿有令，另有准他替换的人。此前说罪证全由他一人伪造，不对。”"
    quote = "“原簿有令，另有准他替换的人。此前说罪证全由他一人伪造，不对。”"
    assert _review(tmp_path, "truth", ["焦黑罪证"], text, quote, number=15) == "done"


def test_review_rejects_a_fragment_cut_from_a_negated_sentence(tmp_path):
    text = "她并没有拆开蓝信封，只把它压在砚台下。"
    assert _review(tmp_path, "plant", ["蓝信封"], text, "拆开蓝信封，只把它压在砚台下。") == "unverified"


def test_payoff_review_accepts_a_question_followed_by_a_completed_action(tmp_path):
    text = "“原来是你？”他拆开蓝信封，落款正是韩三。"
    assert _review(tmp_path, "payoff", ["蓝信封"], text, text) == "done"


def test_payoff_review_accepts_unrelated_negation_before_the_action(tmp_path):
    text = "他不敢再拖，拆开蓝信封，看清落款是韩三。"
    assert _review(tmp_path, "payoff", ["蓝信封"], text, text) == "done"


def test_review_rejects_a_question_without_a_completed_action(tmp_path):
    text = "他问：“拆开蓝信封后，落款真是韩三？”"
    assert _review(tmp_path, "payoff", ["蓝信封"], text, text) == "unverified"


def test_review_rejects_an_action_subject_to_a_prior_condition(tmp_path):
    text = "如果他再敢说谎，她就拆开蓝信封，落款会证明一切。"
    assert _review(tmp_path, "payoff", ["蓝信封"], text, "拆开蓝信封，落款会证明一切。") == "unverified"


def test_review_rejects_a_negated_opening_even_with_a_sender_named(tmp_path):
    text = "她没有拆开蓝信封，外头的署名写着韩三。"
    assert _review(tmp_path, "payoff", ["蓝信封"], text, text) == "unverified"
