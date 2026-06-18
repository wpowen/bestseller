# ruff: noqa: RUF001, E501

from __future__ import annotations

from bestseller.domain.world_model import (
    WorldLaw,
    WorldModel,
    render_world_model_prompt_block,
    world_model_from_dict,
    world_model_health_summary,
    world_model_to_dict,
)
from bestseller.services.quality_levers._loader import config_path
from bestseller.services.world_dimensions import (
    corpus_distinctness,
    law_specificity,
    load_world_dimensions,
    render_dimensions_prompt_block,
    select_baseline,
    text_similarity,
)
from bestseller.services.world_model_deriver import (
    _balanced_objects_with_key,
    _salvage_payload,
    extract_axioms,
    fallback_world_model,
    parse_world_model,
)

# ---------------------------------------------------------------------------
# Dimension table — genre-neutral machine (zero hardcoded story content)
# ---------------------------------------------------------------------------


def test_dimension_table_loads_and_is_stable() -> None:
    table = load_world_dimensions()
    assert len(table.dimensions) == 14
    assert len(table.baselines) == 4
    # questions are non-empty and dimension keys unique + ascii
    keys = table.dimension_keys()
    assert len(set(keys)) == len(keys)
    for dim in table.dimensions:
        assert dim.question
        assert dim.key.isascii()
        assert dim.order >= 1


def test_dimension_config_contains_no_baked_story_content() -> None:
    """The machine must hold zero genre/story fuel (see homogenization memory).

    Known baked tokens from past homogenisation regressions must never appear in
    the dimension table — it would re-bind the engine to one story's content.
    """

    text = config_path("world_model_dimensions.yaml").read_text(encoding="utf-8")
    baked = ["青囊", "陆沉", "青崖", "蚀漏砚", "义庄", "凶手", "案卷"]
    for token in baked:
        assert token not in text, f"baked story token leaked into machine: {token}"


def test_dimensions_prompt_block_lists_questions_only() -> None:
    block = render_dimensions_prompt_block()
    assert "世界维度表" in block
    assert "value_and_currency" in block
    # questions, not answers — no concrete currency/faction names
    assert "灵石" not in block


# ---------------------------------------------------------------------------
# Baseline selection — era substrate by genre/premise (heuristic fallback)
# ---------------------------------------------------------------------------


def test_baseline_selection_diverges_by_genre() -> None:
    wuxia, _ = select_baseline(genre="武侠", premise="少年习得绝世武功行走江湖")
    scifi, _ = select_baseline(genre="科幻", premise="人类掌握超光速旅行殖民星际")
    urban, _ = select_baseline(genre="灵异都市", premise="一个神仙从古代活到现代都市")
    assert wuxia == "ancient_agrarian"
    assert scifi == "near_future_or_interstellar"
    assert urban == "modern_urban"
    # three genres → three distinct baselines (no single hardcoded default)
    assert len({wuxia, scifi, urban}) == 3


def test_baseline_falls_back_to_invented_when_no_signal() -> None:
    key, rationale = select_baseline(genre=None, premise="一段没有时代线索的抽象描述")
    assert key == "fully_invented"
    assert rationale


# ---------------------------------------------------------------------------
# Anchoring + distinctness (pure anti-homogenisation measures)
# ---------------------------------------------------------------------------


def test_law_specificity_rewards_premise_anchoring() -> None:
    axioms = ["灵力成为唯一能源", "人人可飞"]
    anchored = law_specificity("灵力替代电力,人人可飞后地面交通贬值", axioms)
    generic = law_specificity("社会出现黑市和管制法律", axioms)
    assert anchored > generic
    assert law_specificity("", axioms) == 0.0
    assert law_specificity("任意文本", []) == 0.0


def test_corpus_distinctness_high_for_different_worlds() -> None:
    a = "灵力作为能源人人可飞空域管制"
    b = "武功使个体武力不对称催生江湖镖局"
    c = "超光速旅行星际殖民算力为货币"
    assert corpus_distinctness([a, b, c]) > 0.7
    assert corpus_distinctness([a, a, a]) < 0.2
    assert corpus_distinctness([a]) == 1.0
    assert text_similarity(a, a) == 1.0


# ---------------------------------------------------------------------------
# Schema — round-trip + LLM-alias coercion + validation
# ---------------------------------------------------------------------------


def test_world_model_round_trip() -> None:
    payload = fallback_world_model(premise="一个神仙从古代活到现代", genre="灵异")
    model = world_model_from_dict(payload)
    again = world_model_from_dict(world_model_to_dict(model))
    assert again.baseline == model.baseline
    assert len(again.world_laws) == len(model.world_laws)


def test_world_law_coerces_llm_aliases() -> None:
    law = WorldLaw.model_validate(
        {
            "key": "value_and_currency",
            "rule": "灵石成为硬通货",
            "assertion": "交易须以灵石计价,法币只在旧经济残留处出现",
            "from": ["灵力是唯一能源"],
            "order": "3",
        }
    )
    assert law.dimension == "value_and_currency"
    assert law.delta == "灵石成为硬通货"
    assert "灵石" in law.enforcement
    assert law.derived_from == ["灵力是唯一能源"]
    assert law.order == 3


def test_world_model_normalizes_single_law_dict() -> None:
    model = WorldModel.model_validate(
        {
            "axioms": "唯一公理",
            "world_laws": {
                "dimension": "power_and_institutions",
                "delta": "旧制度失效",
                "enforcement": "正文须体现旧制度失效",
            },
        }
    )
    assert model.axioms == ["唯一公理"]
    assert len(model.world_laws) == 1


def test_health_summary_flags_underived_laws() -> None:
    model = world_model_from_dict(
        {
            "axioms": ["公理A"],
            "baseline": "现代都市社会",
            "world_laws": [
                {"dimension": "value_and_currency", "delta": "x", "enforcement": "y", "derived_from": ["公理A"]},
                {"dimension": "mobility_and_transport", "delta": "z", "enforcement": "w"},
            ],
        }
    )
    summary = world_model_health_summary(model)
    assert summary["law_count"] == 2
    assert summary["laws_without_derivation"] == 1
    assert summary["dimension_count"] == 2


# ---------------------------------------------------------------------------
# Deriver — axioms, fallback validity, parse merge + scoring + fallback-safety
# ---------------------------------------------------------------------------


def test_extract_axioms_splits_premise() -> None:
    axioms = extract_axioms("神仙活到现代。他法律上不存在。寿命跨越千年。")
    assert 1 <= len(axioms) <= 3
    assert extract_axioms("") == []


def test_fallback_is_valid_and_fully_anchored() -> None:
    payload = fallback_world_model(
        premise="一个神仙从古代活到现代,法律上不存在,寿命跨越千年", genre="灵异都市"
    )
    model = world_model_from_dict(payload)
    assert model.baseline == "现代都市社会"
    assert len(model.world_laws) == 5
    # every fallback law is anchored to an axiom — the core anti-homogenisation rule
    assert all(law.derived_from for law in model.world_laws)
    assert world_model_health_summary(model)["laws_without_derivation"] == 0


def test_parse_world_model_keeps_llm_laws_and_scores_them() -> None:
    fake = (
        '{"axioms":["灵力成为唯一能源","人人可飞"],"baseline":"现代都市社会",'
        '"world_laws":[{"dimension":"mobility_and_transport",'
        '"delta":"人人可飞,地面汽车贬值,空域成稀缺",'
        '"order":2,"derived_from":["人人可飞"],'
        '"enforcement":"默认出行为飞行;出现地面车辆通勤须显式给理由"}],'
        '"fault_lines":[{"name":"空域管制×自由飞行","tension":"管制与自由冲突","used_by_protagonist":true}]}'
    )
    model = parse_world_model(fake, premise="灵力成为唯一能源,人人可飞", genre="科幻")
    assert len(model.world_laws) == 1
    assert model.world_laws[0].dimension == "mobility_and_transport"
    assert model.world_laws[0].specificity > 0.0
    assert any(fl.used_by_protagonist for fl in model.fault_lines)


def test_parse_world_model_falls_back_on_garbage() -> None:
    model = parse_world_model("not json at all", premise="某前提", genre="武侠")
    assert isinstance(model, WorldModel)
    assert len(model.world_laws) >= 1  # fallback scaffold survives


def test_balanced_objects_with_key_extracts_complete_objects() -> None:
    text = '{"a":1,"items":[{"dimension":"x","v":1},{"dimension":"y","v":2},{"dimension":"z"'
    objs = _balanced_objects_with_key(text, "dimension")
    assert len(objs) == 2  # the truncated third is skipped
    assert '"dimension":"x"' in objs[0]


def test_parse_world_model_salvages_truncated_llm_output() -> None:
    """The critical fanren-audit bug: finish_reason=length truncation must NOT

    silently fall back to the generic scaffold — every complete law written
    before the cut is recovered.
    """

    truncated = (
        "```json\n{\n"
        '  "version": 1,\n'
        '  "axioms": ["灵根决定能否修炼", "境界决定实力与寿元"],\n'
        '  "baseline": "古代农耕社会",\n'
        '  "world_laws": [\n'
        '    {"dimension": "value_and_currency", "delta": "灵石成为硬通货", "order": 2, "derived_from": ["灵根决定能否修炼"], "enforcement": "交易以灵石计价"},\n'
        '    {"dimension": "mobility_and_transport", "delta": "御器飞行需筑基以上", "order": 2, "derived_from": ["境界决定实力与寿元"], "enforcement": "炼气期不得御器飞行"},\n'
        '    {"dimension": "life_death_and_time", "delta": "寿元随境界增长", "order": 3, "derived_from": ["境界决定实力与寿元"], "enforcement": "角色寿元须符合其境界"},\n'
        '    {"dimension": "class_and_stratification", "delta": "灵根有无划分修士与凡'  # cut mid-object
    )
    # Direct salvage: the extractor recovers the complete laws (drops the cut tail).
    salv = _salvage_payload(truncated)
    salv_dims = {law["dimension"] for law in salv["world_laws"]}
    assert {"value_and_currency", "mobility_and_transport", "life_death_and_time"} <= salv_dims
    assert salv["baseline"] == "古代农耕社会"

    # End-to-end: truncation must NOT collapse to the generic 5-dim scaffold.
    model = parse_world_model(truncated, premise="灵根决定修炼，境界决定实力寿元", genre="仙侠")
    dims = model.covered_dimensions()
    assert "mobility_and_transport" in dims  # recovered, not in the fallback set
    assert "value_and_currency" in dims
    assert len(model.world_laws) >= 3  # complete laws survived the cut
    assert model.baseline == "古代农耕社会"
    # recovered laws keep their real derived content (not the placeholder scaffold)
    assert all("待具体推演" not in law.delta for law in model.world_laws)


def test_parse_world_model_tolerates_nameless_content_setting() -> None:
    """The fanren-audit root cause: a content_setting the LLM emits as

    ``{"content": ..., "derived_from_law": ...}`` (no 'name') must NOT raise and
    collapse the whole derivation to the generic scaffold — the valid world_laws
    must survive.
    """

    payload = (
        '{"axioms":["灵根决定能否修炼"],"baseline":"古代农耕社会",'
        '"world_laws":['
        '{"dimension":"value_and_currency","delta":"灵石成为硬通货","order":2,'
        '"derived_from":["灵根决定能否修炼"],"enforcement":"交易以灵石计价"},'
        '{"dimension":"mobility_and_transport","delta":"御器飞行需筑基以上","order":2,'
        '"derived_from":["灵根决定能否修炼"],"enforcement":"炼气期不得御器飞行"}],'
        '"content_settings":[{"content":"灵石是修仙界硬通货","derived_from_law":["value_and_currency"]}]}'
    )
    model = parse_world_model(payload, premise="灵根决定能否修炼", genre="仙侠")
    assert len(model.world_laws) == 2  # NOT the 5-dim fallback
    assert "mobility_and_transport" in model.covered_dimensions()
    assert all("待具体推演" not in law.delta for law in model.world_laws)
    # the nameless content_setting survived (value pulled from 'content')
    assert model.content_settings and model.content_settings[0].value == "灵石是修仙界硬通货"


def test_world_law_parses_stringified_derived_from() -> None:
    law = WorldLaw.model_validate(
        {
            "dimension": "value_and_currency",
            "delta": "灵石计价",
            "enforcement": "交易以灵石计价",
            "derived_from": "['灵根决定修炼', '灵气是资源']",
        }
    )
    assert law.derived_from == ["灵根决定修炼", "灵气是资源"]


def test_render_prompt_block_surfaces_enforcement() -> None:
    model = world_model_from_dict(
        fallback_world_model(premise="测试前提公理", genre="玄幻")
    )
    block = render_world_model_prompt_block(model)
    assert "世界模型" in block
    assert "约束:" in block  # enforcement is surfaced for downstream prompts
