"""The modernity tripwire must not kill books over classical Chinese words.

Field failure (2026-07-25 00:01, custom-xuanhuan-1784908885): a 玄幻 book whose
premise involved a sect corpse-handler crashed conception outright with

    ValueError: Genre intent ontology violation: 收尸 ... unexplained modern drift

收尸 ("collect a corpse") is classical Chinese, not modern drift — and the
framework contradicts itself on it in two places:

* ``writing_presets.py`` SELLS 收尸人 as a legitimate folk-horror profession
  ("以捞尸人、赶尸匠、收尸人、阴婚媒等民间职业为切口").
* ``tests/unit/test_concept_tournament.py`` uses "收尸人看见死者明天" as a
  valid story-seed fixture.

The tripwire's own docstring states its scope: "not a blanket ban on fantasy
corpses… only a final tripwire for the recurring APP/phone/workplace/
forensic-modern drift". Words that existed for centuries before electricity
fall outside that scope. The discriminator pinned here: does the term denote
something that CANNOT exist in a pre-modern world?
"""

from __future__ import annotations

import pytest

from bestseller.services.genre_intent_contract import (
    contract_from_selection,
    detect_genre_native_ontology_violations,
)


pytestmark = pytest.mark.unit


def _native_contract():
    return contract_from_selection({"genre": "xianxia", "sub_genre": "xianxia"})


@pytest.mark.parametrize(
    "premise",
    [
        "他替宗门收尸糊口，今天认出了自己的名字。",
        "族老命他为战死的师兄入殓，棺中却多出一封信。",
        "他靠替人办丧葬事宜度日，直到抬进来一具会呼吸的尸体。",
    ],
)
def test_classical_funerary_terms_are_not_modern_drift(premise: str) -> None:
    """These are ancient rites, and exactly the kind of native premise the
    framework's own presets recommend for this lane."""

    assert detect_genre_native_ontology_violations(premise, _native_contract()) == ()


@pytest.mark.parametrize(
    "premise",
    [
        "他掏出手机，给师兄发了条微信。",
        "尸体送去做尸检，法医出具了鉴定报告。",
        "灵柩停在殡仪馆的停尸房里。",
        "他在写字楼的职场里修炼。",
        "宗门给他安排了器官移植。",
    ],
)
def test_genuinely_modern_terms_still_trip(premise: str) -> None:
    """The tripwire must keep doing its job — this is not a loosening of the
    APP/phone/workplace/forensic-modern guard."""

    assert detect_genre_native_ontology_violations(premise, _native_contract())


def test_final_surface_ignores_guardrails_and_judge_commentary() -> None:
    """Control-plane text is not story ontology.

    A tournament judge may correctly say "this is not a workplace story", and
    a profile guardrail may forbid phones/APPs.  Serialising those metadata
    fields into the final story scan turns the *prohibition* into evidence of
    drift and kills an otherwise native book.
    """

    from bestseller.services.conception import _conception_ontology_story_surface

    surface = _conception_ontology_story_surface(
        title="祖脉无名账",
        premise="宗门账房追查一笔没有署名的祖脉供奉。",
        synopsis="他沿灵契与香火流向追到被抹去的旧峰。",
        tags=["仙侠", "宗门"],
        writing_profile={
            "character": {"protagonist_archetype": "宗门底层账房"},
            "world": {"setting": "山门、灵契与祖脉构成秩序"},
            "style": {
                "taboo_topics": ["现代职场", "手机", "APP"],
                "custom_rules": ["不得写成职场故事"],
            },
        },
        high_concept={
            "concept": "底层账房发现祖脉供奉正在喂养一个不存在的峰主。",
            "opponent_system": "控制香火账与灵契的长老会",
            "judge_reason": "不像现代职场文，题材原生度合格",
            "seriality_judge": {"reason": "没有依赖APP或手机推进"},
            "rejected_reason": "职场感过强",
        },
        story_spine={"against": "控制香火账与灵契的长老会"},
    )

    assert detect_genre_native_ontology_violations(surface, _native_contract()) == ()


def test_final_surface_still_scans_story_facts() -> None:
    """Narrowing the surface must not weaken the actual ontology guard."""

    from bestseller.services.conception import _conception_ontology_story_surface

    surface = _conception_ontology_story_surface(
        title="掌门群聊",
        premise="弟子掏出手机查看宗门群聊。",
        synopsis="",
        tags=[],
        writing_profile={
            "character": {"golden_finger": "用APP查看灵脉价格"},
            "world": {"setting": "写字楼里的修仙公司"},
            "style": {"taboo_topics": ["现代职场"]},
        },
        high_concept={
            "concept": "他在职场里修炼",
            "judge_reason": "题材原生度不足",
        },
        story_spine={"who": "手机不离手的弟子"},
    )

    assert set(detect_genre_native_ontology_violations(surface, _native_contract())) >= {
        "手机",
        "写字楼",
        "职场",
        "app",
    }


@pytest.mark.asyncio
async def test_late_story_drift_gets_one_genre_native_repair(monkeypatch) -> None:
    """Copywriting may introduce drift after the early scan; repair it once."""

    from bestseller.services import conception

    async def fake_llm_call_json(*args, **kwargs):
        return (
            {
                "title": "祖脉无名账",
                "premise": "宗门账房追查祖脉供奉。",
                "synopsis": "他沿灵契追到被抹去的旧峰。",
                "tags": ["仙侠", "宗门"],
                "writing_profile_story": {
                    "character": {"golden_finger": "能辨认被篡改的灵契"},
                    "world": {"setting": "山门、灵契与祖脉构成秩序"},
                    "market": {"logline": "一笔无名供奉牵出失落旧峰"},
                },
                "story_spine": {"against": "控制香火账的长老会"},
                "high_concept_story": {"mechanism": "以香火供奉考绩弟子"},
            },
            ["repair-run"],
        )

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm_call_json)
    repaired = await conception._repair_final_ontology_drift(
        object(),
        object(),
        violations=("职场",),
        title="祖脉无名账",
        premise="宗门账房追查祖脉供奉。",
        synopsis="他像处理职场工单一样处理供奉。",
        tags=["仙侠", "职场"],
        writing_profile={
            "character": {"golden_finger": "职场报表术"},
            "world": {"setting": "宗门"},
            "market": {"logline": "修真职场账房"},
            "style": {"taboo_topics": ["职场"]},
        },
        story_spine={"against": "职场式长老会"},
        # The champion is part of the verdict surface, so it is part of the
        # repair surface too — a term landing here used to be unfixable.
        high_concept={"mechanism": "以职场KPI考核弟子"},
        genre_label="仙侠",
        sub_genre_label="修真文明",
        language="zh-CN",
    )

    title, premise, synopsis, tags, profile, spine, champion, run_ids = repaired
    surface = conception._conception_ontology_story_surface(
        title=title,
        premise=premise,
        synopsis=synopsis,
        tags=tags,
        writing_profile=profile,
        high_concept=champion,
        story_spine=spine,
    )
    assert detect_genre_native_ontology_violations(surface, _native_contract()) == ()
    assert profile["style"]["taboo_topics"] == ["职场"]
    assert run_ids == ["repair-run"]
    # Scanning the repaired champion is the point: the verdict surface reads it,
    # so leaving it out of the re-scan would let this test pass while a book
    # with a modern term in high_concept still died at the gate.
    assert champion["mechanism"] == "以香火供奉考绩弟子"


def test_final_tripwire_consumes_repair_and_rechecks() -> None:
    """The helper must be wired into the production final gate."""

    import inspect

    from bestseller.services import conception

    source = inspect.getsource(conception.run_conception_pipeline)
    final_gate = source[source.index("# Final ontology tripwire") :]
    assert "await _repair_final_ontology_drift(" in final_gate
    assert final_gate.count("detect_genre_native_ontology_violations(") >= 2
    assert "if ontology_violations:" in final_gate
    assert "raise ConceptContractError" in final_gate


def test_the_exact_field_premise_survives() -> None:
    """The premise the user actually tried, verbatim in shape."""

    premise = "靠给宗门死人收尸糊口的贱籍少年，发现每具尸体死前都听到了同一句话。"

    assert detect_genre_native_ontology_violations(premise, _native_contract()) == ()


def test_pre_modern_terms_stay_out_of_the_ban_list() -> None:
    """The admission test, enforced: only things that cannot exist pre-modern.

    Scoped to terms rather than to the presets file, because that file holds
    presets for MODERN genres too — 职场/现代都市 legitimately appear there for
    urban lanes, and the tripwire never fires for those (it is gated on
    ``allowed_modernity == "genre_native"``). What must never come back are
    words that predate electricity.
    """

    from bestseller.services.genre_intent_contract import (
        _GENRE_NATIVE_MODERNITY_CJK,
    )

    classical = ["收尸", "入殓", "殡葬", "仵作", "丧葬", "棺", "验尸"]
    readmitted = [term for term in classical if term in _GENRE_NATIVE_MODERNITY_CJK]

    assert not readmitted, (
        "these are classical Chinese rites/professions, valid in a native-genre "
        f"world, and must not be treated as modern drift: {readmitted}"
    )


def test_framework_does_not_sell_a_profession_it_then_bans() -> None:
    """The specific self-contradiction that caused the field crash: the
    folk-horror preset recommends 收尸人 as a genre-native profession."""

    from pathlib import Path

    from bestseller.services.genre_intent_contract import (
        _GENRE_NATIVE_MODERNITY_CJK,
    )

    presets = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "bestseller"
        / "services"
        / "writing_presets.py"
    ).read_text(encoding="utf-8")

    assert "收尸人" in presets, "fixture assumption: the preset sells this lane"
    assert "收尸" not in _GENRE_NATIVE_MODERNITY_CJK, (
        "the framework recommends 收尸人 as a genre-native profession; banning "
        "the word kills exactly the books it advertises"
    )


def test_final_tripwire_honors_explicit_user_intent_like_the_early_one() -> None:
    """The same detector must not contradict itself at two call sites.

    The EARLY call site (tournament winner, conception.py ~4185) already gets
    this right on both counts: it exempts terms the user typed into their own
    seed (``term not in _explicit_seed_text``) and it RETRIES with feedback
    instead of killing the book.

    The FINAL tripwire had neither. So a user who deliberately writes a
    corpse-handler premise into the 故事创意 field would be exempted by the
    early gate and then killed by the final one — the framework overruling an
    explicit user choice at the last step, after ~15 minutes of work. With the
    concept-seed input now shipped on the create form, that contradiction is
    reachable by design, not by accident.
    """

    import inspect

    from bestseller.services import conception

    source = inspect.getsource(conception.run_conception_pipeline)
    idx = source.index("ontology_violations = detect_genre_native_ontology_violations")
    region = source[idx : idx + 1800]

    assert "explicit_concept_seed" in region, (
        "the final tripwire must consult what the user explicitly asked for, "
        "exactly as the early tournament gate does"
    )
    assert "concept_bundle" in region or "selected_hook_spec" in region, (
        "user intent also arrives via a concept-lab bundle / hook spec"
    )


def test_every_ontology_call_site_applies_the_user_intent_exemption() -> None:
    """Systemic guard: one detector, three call sites, ONE rule.

    The recurring defect class in this pipeline is not "a wrong word in a
    list" — it is the same detector being consumed with different semantics at
    different layers, so a book passes one gate and dies at the next. When this
    was written the three call sites disagreed:

      * tournament-winner gate  — exempted the user's seed, retried
      * concept-echo retry      — no exemption (burned a regen round)
      * final tripwire          — no exemption, killed the book outright

    Its two motif siblings (debt / death-revival) both already honored user
    intent via ``_user_requested_*``. Ontology was the odd one out. Pin that
    every call site now reads the user's own seed before calling it drift.
    """

    import inspect

    from bestseller.services import conception

    source = inspect.getsource(conception.run_conception_pipeline)

    call_sites = [
        idx
        for idx in range(len(source))
        if source.startswith("detect_genre_native_ontology_violations(", idx)
    ]
    assert len(call_sites) >= 3, (
        f"expected the known call sites, found {len(call_sites)}"
    )

    seed_markers = ("_explicit_seed_text", "_final_seed_text", "_ontology_user_seed")
    for idx in call_sites:
        window = source[max(0, idx - 900) : idx + 1500]
        assert any(marker in window for marker in seed_markers), (
            "an ontology call site with no user-intent exemption in scope: "
            f"...{source[max(0, idx - 120): idx + 80]!r}"
        )


def test_ontology_block_is_actionable_not_a_raw_crash() -> None:
    """A content block must reach the user as an explanation, not a traceback.

    The tripwire used to ``raise ValueError``, which fell through to the
    generic handler and showed the user a raw Python stack — indistinguishable
    from a framework bug, and (before the row-close fix) it also leaked the
    conception workflow row. It must raise the same deliberate-block exception
    the other conception gates use.
    """

    import inspect

    from bestseller.services import conception

    source = inspect.getsource(conception.run_conception_pipeline)
    idx = source.index("ontology_violations:")
    # The bounded late-drift repair now sits between detection and the final
    # deliberate block, so keep the assertion window wide enough to include
    # both the repair and its fail-closed fallback.
    region = source[idx : idx + 5200]

    assert "ConceptContractError" in region, (
        "the ontology tripwire must raise the graceful block exception, not "
        "a bare ValueError that surfaces as a traceback"
    )
    assert "raise ValueError" not in region
    assert "整改方向" in region, "the block must tell the user what to change"
