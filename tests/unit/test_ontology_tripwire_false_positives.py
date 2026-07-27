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
    region = source[idx : idx + 1400]

    assert "ConceptContractError" in region, (
        "the ontology tripwire must raise the graceful block exception, not "
        "a bare ValueError that surfaces as a traceback"
    )
    assert "raise ValueError" not in region
    assert "整改方向" in region, "the block must tell the user what to change"
