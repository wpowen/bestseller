"""Unit tests for the personhood layer added to ``CharacterInput``.

Covers six new schemas (``CharacterPsychProfile``, ``CharacterLifeHistory``,
``CharacterSocialNetwork``, ``CharacterBelief``, ``CharacterFamilyImprint``,
``VillainCharismaProfile``) plus the two L2 Bible Gate validators that
enforce them (``CharacterPersonhoodCheck``, ``VillainCharismaCheck``) and
the ``planning_context.summarize_cast_spec`` extension that injects the
new fields into chapter prompts.

The personhood layer answers a different question from the IP anchor:
IP anchor makes characters *memorable*; personhood makes characters *real*.
Both matter for commercial-quality output, hence both gates.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.story_bible import (
    CharacterBelief,
    CharacterFamilyImprint,
    CharacterIPAnchorInput,
    CharacterInput,
    CharacterLifeHistory,
    CharacterPsychProfile,
    CharacterSocialNetwork,
    LifeEventInput,
    SocialTieInput,
    VillainCharismaProfile,
)
from bestseller.services.bible_gate import (
    BibleDraft,
    CharacterPersonhoodCheck,
    VillainCharismaCheck,
)
from bestseller.services.invariants import seed_invariants
from bestseller.services.planning_context import summarize_cast_spec

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _invariants():
    return seed_invariants(
        project_id=uuid4(),
        language="zh-CN",
        words_per_chapter=SimpleNamespace(min=5000, target=6400, max=7500),
    )


def _draft(*characters: CharacterInput) -> BibleDraft:
    return BibleDraft(
        characters=tuple(characters),
        theme_statement="真正的力量来自承认脆弱",
        dramatic_question="林奚能否两全？",
    )


def _full_protagonist(name: str = "林奚") -> CharacterInput:
    return CharacterInput(
        name=name,
        role="protagonist",
        ip_anchor=CharacterIPAnchorInput(
            quirks=["左手关节断裂", "洁癖", "口头禅 '这不对劲'"],
            core_wound="七岁目睹母亲被处决",
        ),
        psych_profile=CharacterPsychProfile(
            mbti="INTJ",
            big_five={"openness": 80, "neuroticism": 65},
            enneagram="5w4",
            attachment_style="回避",
        ),
        life_history=CharacterLifeHistory(
            formative_events=[LifeEventInput(age=7, title="目睹母亲被处决")],
            education="云隐宗外门弟子",
            career_history=["江湖游侠", "私塾代课"],
            defining_moments=["当街以一抗百"],
        ),
        family_imprint=CharacterFamilyImprint(
            parenting_style="父亲严苛",
            sibling_dynamics="长姐如母",
            inherited_values=["守诺", "护幼"],
        ),
        beliefs=CharacterBelief(
            religion="家族祖训",
            philosophical_stance="法家",
            ideology="秩序",
        ),
        social_network=CharacterSocialNetwork(
            family=[SocialTieInput(name="林晚", bond="妹妹")],
            mentors=[SocialTieInput(name="云隐道人", bond="授业恩师")],
        ),
    )


# ---------------------------------------------------------------------------
# Schema-level coercion tests.
# ---------------------------------------------------------------------------


class TestCharacterPsychProfileCoercion:
    def test_big_five_accepts_zero_to_one_floats(self) -> None:
        profile = CharacterPsychProfile.model_validate(
            {"big_five": {"openness": 0.8, "neuroticism": 0.2}}
        )
        assert profile.big_five == {"openness": 80, "neuroticism": 20}

    def test_big_five_accepts_likert_scale(self) -> None:
        profile = CharacterPsychProfile.model_validate(
            {"big_five": {"openness": 4, "agreeableness": 5}}
        )
        assert profile.big_five == {"openness": 75, "agreeableness": 100}

    def test_big_five_accepts_chinese_keys(self) -> None:
        profile = CharacterPsychProfile.model_validate(
            {"big_five": {"开放性": 80, "神经质": 60}}
        )
        assert profile.big_five == {"openness": 80, "neuroticism": 60}

    def test_big_five_drops_unknown_keys(self) -> None:
        profile = CharacterPsychProfile.model_validate(
            {"big_five": {"openness": 80, "made_up_trait": 50}}
        )
        assert profile.big_five == {"openness": 80}

    def test_big_five_clamps_out_of_range(self) -> None:
        profile = CharacterPsychProfile.model_validate(
            {"big_five": {"openness": 999, "neuroticism": -50}}
        )
        assert profile.big_five == {"openness": 100, "neuroticism": 0}

    def test_cognitive_biases_coerces_string_to_list(self) -> None:
        profile = CharacterPsychProfile.model_validate(
            {"cognitive_biases": "确认偏误"}
        )
        assert profile.cognitive_biases == ["确认偏误"]

    def test_text_fields_flatten_dict_outputs(self) -> None:
        profile = CharacterPsychProfile.model_validate(
            {"mbti": {"description": "INTJ"}}
        )
        assert profile.mbti == "INTJ"


class TestCharacterLifeHistoryCoercion:
    def test_formative_events_accepts_dict(self) -> None:
        history = CharacterLifeHistory.model_validate(
            {"formative_events": {"age": 7, "title": "母亲离世"}}
        )
        assert len(history.formative_events) == 1
        assert history.formative_events[0].title == "母亲离世"

    def test_formative_events_accepts_string(self) -> None:
        history = CharacterLifeHistory.model_validate(
            {"formative_events": "童年丧母"}
        )
        assert len(history.formative_events) == 1
        assert history.formative_events[0].title == "童年丧母"

    def test_career_history_coerces_dict_to_list(self) -> None:
        history = CharacterLifeHistory.model_validate(
            {"career_history": {"phase1": "江湖游侠", "phase2": "私塾"}}
        )
        assert "江湖游侠" in history.career_history[0] or "phase1" in history.career_history[0]


class TestCharacterSocialNetworkCoercion:
    def test_family_accepts_dict(self) -> None:
        network = CharacterSocialNetwork.model_validate(
            {"family": {"name": "林晚", "bond": "妹妹"}}
        )
        assert len(network.family) == 1
        assert network.family[0].name == "林晚"

    def test_family_accepts_bare_string(self) -> None:
        network = CharacterSocialNetwork.model_validate({"family": "林晚"})
        assert network.family[0].name == "林晚"

    def test_community_coerces_string_to_list(self) -> None:
        network = CharacterSocialNetwork.model_validate({"community": "云隐宗"})
        assert network.community == ["云隐宗"]


class TestCharacterBeliefCoercion:
    def test_superstitions_string_to_list(self) -> None:
        belief = CharacterBelief.model_validate({"superstitions": "见血则不出门"})
        assert belief.superstitions == ["见血则不出门"]

    def test_text_fields_flatten(self) -> None:
        belief = CharacterBelief.model_validate(
            {"religion": {"description": "佛教"}}
        )
        assert belief.religion == "佛教"


class TestCharacterFamilyImprintCoercion:
    def test_inherited_values_accepts_string(self) -> None:
        imprint = CharacterFamilyImprint.model_validate(
            {"inherited_values": "守诺"}
        )
        assert imprint.inherited_values == ["守诺"]


class TestVillainCharismaCoercion:
    def test_redeeming_qualities_string_to_list(self) -> None:
        v = VillainCharismaProfile.model_validate(
            {"redeeming_qualities": "对孩子温柔"}
        )
        assert v.redeeming_qualities == ["对孩子温柔"]

    def test_personal_code_string_to_list(self) -> None:
        v = VillainCharismaProfile.model_validate(
            {"personal_code": "不杀孩子"}
        )
        assert v.personal_code == ["不杀孩子"]


# ---------------------------------------------------------------------------
# CharacterInput integration — new fields default to empty objects.
# ---------------------------------------------------------------------------


class TestCharacterInputIntegration:
    def test_defaults_are_empty_objects(self) -> None:
        char = CharacterInput(name="测试", role="protagonist")
        assert char.psych_profile == CharacterPsychProfile()
        assert char.life_history == CharacterLifeHistory()
        assert char.social_network == CharacterSocialNetwork()
        assert char.beliefs == CharacterBelief()
        assert char.family_imprint == CharacterFamilyImprint()
        assert char.villain_charisma == VillainCharismaProfile()

    def test_full_payload_round_trips(self) -> None:
        protag = _full_protagonist()
        dumped = protag.model_dump()
        restored = CharacterInput.model_validate(dumped)
        assert restored.psych_profile.mbti == "INTJ"
        assert restored.life_history.formative_events[0].age == 7
        assert restored.beliefs.ideology == "秩序"


# ---------------------------------------------------------------------------
# CharacterPersonhoodCheck.
# ---------------------------------------------------------------------------


class TestCharacterPersonhoodCheck:
    def test_full_protagonist_passes(self) -> None:
        deficiencies = list(
            CharacterPersonhoodCheck().check(
                _draft(_full_protagonist()), _invariants()
            )
        )
        assert deficiencies == []

    def test_protagonist_missing_psych_fails(self) -> None:
        protag = _full_protagonist()
        protag = protag.model_copy(update={"psych_profile": CharacterPsychProfile()})
        deficiencies = list(
            CharacterPersonhoodCheck().check(_draft(protag), _invariants())
        )
        assert len(deficiencies) == 1
        assert deficiencies[0].code == "CHARACTER_PERSONHOOD_INCOMPLETE"
        assert "psych_profile" in deficiencies[0].detail

    def test_protagonist_missing_history_fails(self) -> None:
        protag = _full_protagonist()
        protag = protag.model_copy(update={"life_history": CharacterLifeHistory()})
        deficiencies = list(
            CharacterPersonhoodCheck().check(_draft(protag), _invariants())
        )
        assert len(deficiencies) == 1
        assert "life_history" in deficiencies[0].detail

    def test_supporting_cast_exempt(self) -> None:
        # A role of "supporting" should not trigger the check.
        supporting = CharacterInput(name="侍女", role="supporting")
        deficiencies = list(
            CharacterPersonhoodCheck().check(_draft(supporting), _invariants())
        )
        assert deficiencies == []

    def test_antagonist_exempt_from_personhood_check(self) -> None:
        # Antagonists are validated by VillainCharismaCheck, not this check.
        antag = CharacterInput(name="敌人", role="antagonist")
        deficiencies = list(
            CharacterPersonhoodCheck().check(_draft(antag), _invariants())
        )
        assert deficiencies == []


# ---------------------------------------------------------------------------
# VillainCharismaCheck.
# ---------------------------------------------------------------------------


class TestVillainCharismaCheck:
    def test_antagonist_with_four_fields_passes(self) -> None:
        antag = CharacterInput(
            name="裴砚舟",
            role="antagonist",
            villain_charisma=VillainCharismaProfile(
                noble_motivation="为寒门改命",
                pain_origin="被门阀羞辱",
                redeeming_qualities=["对孩子温柔"],
                personal_code=["不杀孩子"],
            ),
        )
        deficiencies = list(
            VillainCharismaCheck().check(_draft(antag), _invariants())
        )
        assert deficiencies == []

    def test_antagonist_with_two_fields_fails(self) -> None:
        antag = CharacterInput(
            name="裴砚舟",
            role="antagonist",
            villain_charisma=VillainCharismaProfile(
                noble_motivation="为寒门改命",
                pain_origin="被门阀羞辱",
            ),
        )
        deficiencies = list(
            VillainCharismaCheck().check(_draft(antag), _invariants())
        )
        assert len(deficiencies) == 1
        assert deficiencies[0].code == "VILLAIN_CHARISMA_MISSING"
        assert "2/7" in deficiencies[0].detail

    def test_antagonist_lieutenant_exempt(self) -> None:
        lieut = CharacterInput(
            name="副反派",
            role="antagonist_lieutenant",
        )
        deficiencies = list(
            VillainCharismaCheck().check(_draft(lieut), _invariants())
        )
        assert deficiencies == []

    def test_protagonist_exempt(self) -> None:
        protag = CharacterInput(name="林奚", role="protagonist")
        deficiencies = list(
            VillainCharismaCheck().check(_draft(protag), _invariants())
        )
        assert deficiencies == []


# ---------------------------------------------------------------------------
# planning_context.summarize_cast_spec — personhood lines surface in the
# chapter prompt summary.
# ---------------------------------------------------------------------------


class TestSummarizeCastSpecPersonhood:
    def test_personhood_lines_render_for_protagonist(self) -> None:
        cs = {
            "protagonist": _full_protagonist().model_dump(),
            "antagonist": {
                "name": "裴砚舟",
                "role": "antagonist",
                "villain_charisma": {
                    "noble_motivation": "为寒门改命",
                    "pain_origin": "被门阀羞辱",
                    "personal_code": ["不杀孩子"],
                    "protagonist_mirror": "同为底层挣扎",
                },
            },
        }
        summary = summarize_cast_spec(cs, language="zh-CN")
        assert "MBTI=INTJ" in summary
        assert "九型=5w4" in summary
        assert "目睹母亲被处决" in summary
        assert "原生家庭" in summary
        assert "信仰" in summary
        assert "云隐道人" in summary

    def test_villain_charisma_renders_for_antagonist(self) -> None:
        cs = {
            "protagonist": {"name": "林奚", "role": "protagonist"},
            "antagonist": {
                "name": "裴砚舟",
                "role": "antagonist",
                "villain_charisma": {
                    "noble_motivation": "为寒门改命",
                    "pain_origin": "被门阀羞辱",
                    "personal_code": ["不杀孩子"],
                    "protagonist_mirror": "同为底层挣扎",
                },
            },
        }
        summary = summarize_cast_spec(cs, language="zh-CN")
        assert "反派魅力" in summary
        assert "为寒门改命" in summary
        assert "不杀孩子" in summary

    def test_empty_personhood_does_not_emit_blank_lines(self) -> None:
        cs = {
            "protagonist": {"name": "林奚", "role": "protagonist"},
            "antagonist": {"name": "敌人", "role": "antagonist"},
        }
        summary = summarize_cast_spec(cs, language="zh-CN")
        # No personhood data at all → only the two _char_line outputs.
        # Sanity: should NOT include any of the personhood tags.
        for tag in ("人格[", "生平[", "原生家庭[", "信仰[", "关键关系[", "反派魅力"):
            assert tag not in summary
