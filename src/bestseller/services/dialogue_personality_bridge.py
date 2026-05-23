from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any

from bestseller.services.writing_profile import is_english_language

_BIG_FIVE_ALIASES: dict[str, tuple[str, ...]] = {
    "openness": ("openness", "open", "开放性", "开放"),
    "conscientiousness": (
        "conscientiousness",
        "conscientious",
        "尽责性",
        "责任心",
    ),
    "extraversion": ("extraversion", "extraverted", "外向性", "外向"),
    "agreeableness": ("agreeableness", "agreeable", "宜人性", "亲和"),
    "neuroticism": ("neuroticism", "emotional_stability", "神经质", "情绪稳定"),
}


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    return [value]


def _text(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _short(value: Any, *, limit: int = 54) -> str:
    text = _text(value)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _short_join(value: Any, *, limit: int = 3) -> str:
    items = [_short(item, limit=28) for item in _as_list(value) if _text(item)]
    return "、".join(items[:limit])


def _score_level(value: Any) -> str:
    text = _text(value).lower()
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        if number > 1:
            number = number / 100
        if number >= 0.66:
            return "high"
        if number <= 0.34:
            return "low"
        return "mid"
    if any(token in text for token in ("高", "强", "high", "strong")):
        return "high"
    if any(token in text for token in ("低", "弱", "low", "weak")):
        return "low"
    return "mid"


def _first_big_five_value(big_five: Mapping[str, Any], trait: str) -> Any:
    lowered = {str(key).strip().lower(): value for key, value in big_five.items()}
    for alias in _BIG_FIVE_ALIASES[trait]:
        if alias in big_five:
            return big_five[alias]
        value = lowered.get(alias.lower())
        if value is not None:
            return value
    return None


def _big_five_rules(big_five: Mapping[str, Any], *, is_en: bool) -> list[str]:
    rules: list[str] = []
    templates = {
        "openness": {
            "high": (
                "allows metaphor, analogy, possibility language, and unusual links"
                if is_en
                else "允许隐喻、类比、可能性语言和非常规联想"
            ),
            "low": (
                "keeps speech concrete, familiar, procedural, and evidence-bound"
                if is_en
                else "对白更具体、熟悉、按流程、依证据推进"
            ),
        },
        "conscientiousness": {
            "high": (
                "organizes claims, names constraints, and follows through on promises"
                if is_en
                else "说话会整理条件、明确约束，并把承诺落实到行动"
            ),
            "low": (
                "jumps steps, speaks in impulses, and leaves cleanup to later"
                if is_en
                else "容易跳步骤、凭冲动开口，把收尾留到之后"
            ),
        },
        "extraversion": {
            "high": (
                "externalizes energy, initiates contact, and thinks aloud under pressure"
                if is_en
                else "压力下外放能量、主动接触，并把思考说出来"
            ),
            "low": (
                "observes first, withholds surplus words, and lets action carry intent"
                if is_en
                else "先观察、少说多留白，让动作承载意图"
            ),
        },
        "agreeableness": {
            "high": (
                "softens refusal, repairs tension, and protects face before pushing back"
                if is_en
                else "拒绝会缓冲，先修复关系/保全面子再反推"
            ),
            "low": (
                "challenges premises directly and tolerates social friction"
                if is_en
                else "直接挑战前提，能承受关系摩擦"
            ),
        },
        "neuroticism": {
            "high": (
                "scans for threat, hedges certainty, and seeks reassurance or control"
                if is_en
                else "会扫描威胁、收紧确定性，并寻求保证或控制感"
            ),
            "low": (
                "stays even under pressure and answers with fewer defensive moves"
                if is_en
                else "压力下更稳定，防御性话术更少"
            ),
        },
    }
    for trait in _BIG_FIVE_ALIASES:
        level = _score_level(_first_big_five_value(big_five, trait))
        if level in {"high", "low"}:
            rules.append(templates[trait][level])
    return rules


def _mbti_rules(value: Any, *, is_en: bool) -> list[str]:
    mbti = _text(value).upper()
    if not re.fullmatch(r"[IE][NS][TF][JP]", mbti):
        return []
    pairs = {
        "I": "keeps more interior processing offstage" if is_en else "更多内在加工不直接说破",
        "E": "uses interaction to discover the next move" if is_en else "通过互动试出下一步",
        "N": "tracks patterns, implications, and future consequences" if is_en else "关注模式、含义与后果",
        "S": "anchors speech in present facts and sensory details" if is_en else "把对白锚定在当下事实和感官细节",
        "T": "frames conflict as logic, cost, or principle" if is_en else "用逻辑、成本或原则处理冲突",
        "F": "frames conflict through impact, loyalty, and hurt" if is_en else "用影响、忠诚和受伤感处理冲突",
        "J": "pushes toward closure, sequence, and decision" if is_en else "推动收束、顺序和决策",
        "P": "keeps options open and tests through improvisation" if is_en else "保留选项，通过即兴试探",
    }
    return [pairs[letter] for letter in mbti]


def _enneagram_rules(value: Any, *, is_en: bool) -> list[str]:
    text = _text(value)
    match = re.search(r"\b([1-9])\b", text) or re.search(r"type\s*([1-9])", text, re.I)
    if not match:
        return []
    code = match.group(1)
    rules = {
        "1": "tracks right/wrong and leaks irritation at disorder",
        "2": "offers help while testing whether they are needed",
        "3": "manages image, proof, and visible results",
        "4": "protects uniqueness and names emotional texture obliquely",
        "5": "conserves energy, hoards knowledge, and answers by narrowing scope",
        "6": "tests loyalty, risks, and hidden failure modes",
        "7": "deflects pain with options, motion, and reframing",
        "8": "challenges control, strength, and surrender directly",
        "9": "defuses friction and delays naming their own want",
    }
    zh_rules = {
        "1": "关注对错与秩序，混乱时会泄露不耐",
        "2": "先提供帮助，同时试探自己是否被需要",
        "3": "管理形象、证明与可见成果",
        "4": "保护独特性，间接命名情绪质地",
        "5": "节省能量、保留信息，用缩小范围来回答",
        "6": "测试忠诚、风险与隐藏失败模式",
        "7": "用选项、行动和重新解释转移痛感",
        "8": "直接挑战控制、力量和屈服",
        "9": "缓和摩擦，并延后说出自己的真实需求",
    }
    return [rules[code] if is_en else zh_rules[code]]


def _attachment_rules(value: Any, *, is_en: bool) -> list[str]:
    text = _text(value).lower()
    if not text:
        return []
    if "secure" in text or "安全" in text:
        return [
            "repairs conflict directly and can state needs without a test"
            if is_en
            else "能直接修复冲突，不必用试探来说需求"
        ]
    if "anxious" in text or "焦虑" in text:
        return [
            "seeks reassurance, over-reads distance, and protests silence"
            if is_en
            else "寻求保证、过度解读距离，并抗议沉默"
        ]
    if "avoid" in text or "回避" in text:
        return [
            "minimizes need, exits intimacy through facts or tasks"
            if is_en
            else "压低需求，用事实或任务退出亲密压力"
        ]
    if "disorganized" in text or "混乱" in text:
        return [
            "approaches and withdraws in the same beat when closeness spikes"
            if is_en
            else "亲密压力升高时，同一拍里靠近又撤退"
        ]
    return []


def _character_engine_rules(engine: Mapping[str, Any], *, is_en: bool) -> tuple[list[str], list[str]]:
    dialogue: list[str] = []
    action: list[str] = []
    want_need = _as_mapping(engine.get("want_vs_need"))
    motivation = _as_mapping(engine.get("three_layer_motivation"))
    values = _as_mapping(engine.get("values_and_redlines"))
    response_chain = _as_mapping(engine.get("unique_response_chain"))
    signature = _as_mapping(engine.get("signature_assets"))
    weakness = _as_mapping(engine.get("weakness_and_killshot"))
    voice = _as_mapping(engine.get("voice_dna"))

    if want_need:
        dialogue.append(
            (
                "spoken wants should expose the unspoken need: "
                if is_en
                else "表层说出口的目标要暴露未说出口的内在需求："
            )
            + f"{_short(want_need.get('want'))} / {_short(want_need.get('need'))}"
        )
    if motivation:
        dialogue.append(
            
                "surface motive can speak; hidden/suppressed motive must leak as subtext"
                if is_en
                else "表层动机可以说，隐藏/压抑动机必须以潜台词泄露"
            
        )
    if values:
        redlines = _short_join(values.get("absolute_no") or values.get("redlines"))
        if redlines:
            action.append(
                (
                    "choices cannot casually cross these red lines: "
                    if is_en
                    else "行动不能随意跨过这些红线："
                )
                + redlines
            )
        core_value = _text(values.get("core_value"))
        if core_value:
            dialogue.append(
                ("pressure speech bends around value: " if is_en else "受压对白围绕核心价值变形：")
                + _short(core_value)
            )
    if response_chain:
        first_key = next(iter(response_chain))
        chain = _as_mapping(response_chain.get(first_key))
        if chain:
            action.append(
                (
                    f"when hit by {first_key}, response must follow perceptible steps: "
                    if is_en
                    else f"遭遇「{first_key}」时，反应要有可感知步骤："
                )
                + " -> ".join(
                    _short(chain.get(key), limit=34)
                    for key in ("step_1", "step_2", "step_3")
                    if _text(chain.get(key))
                )
            )
    if signature:
        bits = _short_join(
            [
                signature.get("object"),
                signature.get("action"),
                signature.get("tic"),
            ],
            limit=3,
        )
        if bits:
            action.append(
                ("signature behavior can recur as evidence, not as a quota: " if is_en else "标志行为可作为证据复现，不设固定配额：")
                + bits
            )
    if weakness:
        killshot = _text(weakness.get("external_killshot"))
        if killshot:
            dialogue.append(
                (
                    "under attack, dialogue should defend against the killshot: "
                    if is_en
                    else "被击中弱点时，对白应防御这个杀伤点："
                )
                + _short(killshot)
            )
    if voice:
        voice_bits = _short_join(
            [
                voice.get("sentence_length_preference"),
                voice.get("vocabulary_register"),
                voice.get("response_pattern_to_question"),
                voice.get("anger_expression"),
                voice.get("lie_pattern"),
            ],
            limit=5,
        )
        if voice_bits:
            dialogue.append(
                ("voice DNA shapes syntax and evasions: " if is_en else "VoiceDNA 约束句法与回避方式：")
                + voice_bits
            )
    return dialogue, action


def derive_personality_bound_dialogue_contract(
    participant: Mapping[str, Any],
    *,
    language: str | None = None,
) -> dict[str, Any]:
    """Derive dialogue/action obligations from mature personality systems.

    The contract is intentionally behavioral. It describes how personality
    should shape speech, action, subtext, and choices without requiring exact
    catchphrases or brittle word matching.
    """

    is_en = is_english_language(language)
    psych = _as_mapping(participant.get("psych_profile"))
    moral = _as_mapping(participant.get("moral_framework"))
    engine = _as_mapping(participant.get("character_engine_profile"))
    voice_profile = _as_mapping(participant.get("voice_profile"))
    inner = _as_mapping(participant.get("inner_structure"))

    dialogue_rules: list[str] = []
    action_rules: list[str] = []
    inference_targets: list[str] = []

    big_five = _as_mapping(psych.get("big_five"))
    dialogue_rules.extend(_big_five_rules(big_five, is_en=is_en))
    dialogue_rules.extend(_mbti_rules(psych.get("mbti"), is_en=is_en))
    dialogue_rules.extend(_enneagram_rules(psych.get("enneagram"), is_en=is_en))
    dialogue_rules.extend(_attachment_rules(psych.get("attachment_style"), is_en=is_en))

    if psych.get("personality_label"):
        inference_targets.append(_short(psych["personality_label"], limit=32))
    for key, label in (
        ("mbti", "MBTI"),
        ("enneagram", "Enneagram" if is_en else "九型"),
        ("attachment_style", "Attachment" if is_en else "依恋"),
    ):
        if psych.get(key):
            inference_targets.append(f"{label}={_short(psych[key], limit=18)}")
    if big_five:
        inference_targets.append("Big Five/OCEAN")

    if moral:
        values = _short_join(moral.get("core_values"), limit=3)
        if values:
            dialogue_rules.append(
                ("values should steer what the character notices and defends: " if is_en else "价值观决定角色注意什么、捍卫什么：")
                + values
            )
        redlines = _short_join(
            moral.get("lines_never_crossed") or moral.get("lines_will_not_cross"),
            limit=2,
        )
        if redlines:
            action_rules.append(
                ("red lines must shape refusal, compromise, and sacrifice: " if is_en else "底线必须塑造拒绝、妥协和牺牲：")
                + redlines
            )

    engine_dialogue, engine_action = _character_engine_rules(engine, is_en=is_en)
    dialogue_rules.extend(engine_dialogue)
    action_rules.extend(engine_action)

    if voice_profile:
        vp_bits = _short_join(
            [
                voice_profile.get("speech_register"),
                voice_profile.get("sentence_style"),
                voice_profile.get("emotional_expression"),
            ],
            limit=3,
        )
        if vp_bits:
            dialogue_rules.append(
                ("existing voice profile remains the surface delivery: " if is_en else "现有语言画像负责表层呈现：")
                + vp_bits
            )
        mannerisms = _short_join(voice_profile.get("mannerisms"), limit=2)
        if mannerisms:
            action_rules.append(
                ("mannerisms should reveal pressure state, not decorate: " if is_en else "习惯动作要揭示压力状态，不做装饰：")
                + mannerisms
            )

    if inner:
        lie = _text(inner.get("lie_believed"))
        need = _text(inner.get("need_internal"))
        if lie or need:
            dialogue_rules.append(
                
                    "dialogue should let the believed lie collide with the unmet need"
                    if is_en
                    else "对白要让“相信的谎言”和“未满足的需求”发生碰撞"
                
            )

    name = _text(participant.get("name") or participant.get("display_name"), default="角色")
    return {
        "name": name,
        "dialogue_rules": dialogue_rules,
        "action_rules": action_rules,
        "inference_targets": inference_targets,
    }


def render_dialogue_personality_bridge_block(
    participants: Iterable[Mapping[str, Any]],
    *,
    language: str | None = None,
    max_profiles: int = 4,
) -> str:
    is_en = is_english_language(language)
    contracts = [
        derive_personality_bound_dialogue_contract(participant, language=language)
        for participant in participants
        if isinstance(participant, Mapping)
    ]
    contracts = [
        contract
        for contract in contracts
        if contract["dialogue_rules"]
        or contract["action_rules"]
        or contract["inference_targets"]
    ][:max_profiles]
    if not contracts:
        return ""

    lines = [
        (
            "【personality-bound dialogue/action contract】"
            if is_en
            else "【性格绑定的对白/动作契约】"
        ),
        (
            "Use mature personality systems as behavioral constraints. Do not force exact catchphrases; readers should infer personality from dialogue, action, silence, and choices."
            if is_en
            else "使用成熟性格体系作为行为约束。不要强塞固定口头禅；读者应能从对白、动作、沉默和选择反推出性格。"
        ),
    ]
    for contract in contracts:
        lines.append(f"- {contract['name']}")
        if contract["inference_targets"]:
            lines.append(
                (
                    "  inferable traits: "
                    if is_en
                    else "  可被反推的人格特征："
                )
                + " / ".join(contract["inference_targets"][:5])
            )
        if contract["dialogue_rules"]:
            lines.append(
                ("  dialogue: " if is_en else "  对白：")
                + "；".join(contract["dialogue_rules"][:5])
            )
        if contract["action_rules"]:
            lines.append(
                ("  action/body: " if is_en else "  动作/身体：")
                + "；".join(contract["action_rules"][:4])
            )
    return "\n".join(lines)
