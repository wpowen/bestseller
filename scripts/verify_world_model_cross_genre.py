"""Prove the world-model engine is GENRE-GENERAL — one machine, many fuels.

Two requirements, two parts (deterministic; no LLM / no DB needed):

PART A — engine differentiation: feed 灵异 / 科幻 / 武侠 premises through the
  deterministic engine (``fallback_world_model``) and show that changing the
  genre/premise changes the baseline + axioms + laws. Nothing is hardcoded, so
  different inputs cannot collapse to the same world.

PART B — pipeline logic on representative derivations: run three realistic,
  genre-appropriate derivations (stand-ins for live LLM output) through the REAL
  parser/scorer (``parse_world_model``) and validate the anti-homogenisation
  contract: every law is anchored to an axiom, dimensions are covered, and the
  three worlds are highly distinct from one another.

A live-LLM single-volume run goes through the planner integration
(``derive_world_model`` is wired into ``_generate_story_design_kernel``); this
harness validates the engine + pipeline logic that such a run depends on.

Run:  .venv/bin/python scripts/verify_world_model_cross_genre.py
"""

# ruff: noqa: RUF001, E501

from __future__ import annotations

import json

from bestseller.domain.world_model import world_model_from_dict, world_model_health_summary
from bestseller.services.world_dimensions import corpus_distinctness
from bestseller.services.world_model_deriver import fallback_world_model, parse_world_model

# Three genres x three premises each - deliberately varied situations.
GENRE_PREMISES: dict[str, list[str]] = {
    "灵异都市": [
        "一个神仙从古代活到现代，他法律上不存在，寿命跨越千年。",
        "城市每到午夜会多出一条不在地图上的街，只有死过一次的人能走进去。",
        "她继承了一间能寄存‘记忆’的当铺，赎当的人却越来越少。",
    ],
    "科幻": [
        "人类掌握超光速旅行，殖民星际，但意识可被备份与重启。",
        "全球算力成为唯一硬通货，没有算力配额的人被踢出经济系统。",
        "一种菌群让人共享感官，私人体验成了奢侈品。",
    ],
    "武侠": [
        "少年习得绝世武功，行走江湖，官府的刀追不上会轻功的人。",
        "镖局世家的女儿发现，父亲护送的从来不是货物而是秘密。",
        "一部失传剑谱重现江湖，谁练谁折寿，却人人争抢。",
    ],
}

# Representative derivations (stand-in for live LLM output) — used to validate
# the REAL parser/scorer + the anti-homogenisation contract on realistic data.
REPRESENTATIVE: dict[str, dict] = {
    "灵异都市·神仙活到现代": {
        "axioms": ["一个体拥有古代神仙之力与认知", "其寿命跨越千年", "他在现代法律体系中不存在"],
        "baseline": "现代都市社会",
        "world_laws": [
            {"dimension": "power_and_institutions", "delta": "现代户籍/身份系统无法登记一个千年存在，他是制度盲区里的黑户", "order": 2, "derived_from": ["他在现代法律体系中不存在"], "enforcement": "凡涉及身份核验(银行/医院/高铁)，神仙须以变通手段绕过，不得直接持有合法身份证件"},
            {"dimension": "value_and_currency", "delta": "他囤积的古董与黄金成为接入现代财富的唯一入口", "order": 2, "derived_from": ["其寿命跨越千年"], "enforcement": "其资金来源须可追溯到古物变现，不得凭空获得现代法币"},
            {"dimension": "life_death_and_time", "delta": "故人皆逝，永生者与速朽世界形成情感落差", "order": 3, "derived_from": ["其寿命跨越千年"], "enforcement": "涉及人际关系时须体现其‘看着人老去’的时间错位，不得写成与凡人等同的当下感"},
        ],
        "fault_lines": [{"name": "永生 × 户籍系统", "tension": "千年存在与现代身份登记制度互斥", "used_by_protagonist": True}],
    },
    "科幻·算力即货币": {
        "axioms": ["全球算力成为唯一硬通货", "无算力配额者被踢出经济系统", "意识可被托管在算力上"],
        "baseline": "近未来 / 星际文明",
        "world_laws": [
            {"dimension": "value_and_currency", "delta": "算力配额取代法币，余额即生存权", "order": 2, "derived_from": ["全球算力成为唯一硬通货"], "enforcement": "一切交易以算力计价；出现法币现金须解释为黑市或怀旧物"},
            {"dimension": "class_and_stratification", "delta": "有配额者与零配额‘离线者’成为新的阶级鸿沟", "order": 3, "derived_from": ["无算力配额者被踢出经济系统"], "enforcement": "离线者不得使用任何需联网结算的公共服务，除非有人替其垫付算力"},
            {"dimension": "life_death_and_time", "delta": "意识可托管，死亡变成‘停止续费’", "order": 3, "derived_from": ["意识可被托管在算力上"], "enforcement": "角色死亡须区分肉体死亡与意识停托；复活须有算力代价"},
        ],
        "fault_lines": [{"name": "算力余额 × 生存权", "tension": "把生命权绑定到可被剥夺的算力配额上", "used_by_protagonist": True}],
    },
    "武侠·武力不对称": {
        "axioms": ["武功使个体武力极度不对称", "高手不依赖官方授权即可施暴", "武学需师承传递"],
        "baseline": "古代农耕社会",
        "world_laws": [
            {"dimension": "violence_and_security", "delta": "官府丧失暴力垄断，催生法外的江湖秩序", "order": 3, "derived_from": ["高手不依赖官方授权即可施暴"], "enforcement": "高手冲突由江湖规矩而非官府裁断；写官兵压制高手须给出特殊手段(数量/火器/人质)"},
            {"dimension": "exchange_and_market", "delta": "武力可变现，催生镖局、悬赏与保护费经济", "order": 2, "derived_from": ["武功使个体武力极度不对称"], "enforcement": "重要财货运输须出现武力护送或保护费安排，不得当作普通商旅"},
            {"dimension": "knowledge_and_transmission", "delta": "武学经师承垄断，门派成为权力组织", "order": 2, "derived_from": ["武学需师承传递"], "enforcement": "高深武功的获得须有师承/秘籍来源，不得凭空自悟顶级武学"},
        ],
        "fault_lines": [{"name": "江湖道义 × 官府王法", "tension": "法外武力秩序与官方法律权威的冲突", "used_by_protagonist": True}],
    },
}


def _part_a() -> bool:
    print("\n=== PART A · 引擎差分(换题材/前提 → 换基线/公理/规律) ===\n")
    all_law_texts: list[str] = []
    baselines: dict[str, set[str]] = {}
    within_distinctness: list[float] = []
    for genre, premises in GENRE_PREMISES.items():
        genre_law_texts: list[str] = []
        bset: set[str] = set()
        for p in premises:
            model = world_model_from_dict(fallback_world_model(premise=p, genre=genre))
            bset.add(model.baseline)
            law_blob = " ".join(law.delta for law in model.world_laws)
            genre_law_texts.append(law_blob)
            all_law_texts.append(law_blob)
        baselines[genre] = bset
        d = corpus_distinctness(genre_law_texts)
        within_distinctness.append(d)
        print(f"[{genre}] 基线={sorted(bset)}  同题材内不同前提的规律 distinctness={d}")
    cross = corpus_distinctness(all_law_texts)
    distinct_baselines = {next(iter(b)) for b in baselines.values() if len(b) == 1}
    print(f"\n跨题材规律 distinctness = {cross}")
    print(f"三题材基线 = {[sorted(b)[0] for b in baselines.values()]}  (互不相同: {len(distinct_baselines) == 3})")
    ok = cross > 0.5 and len(distinct_baselines) == 3 and all(d > 0 for d in within_distinctness)
    print(f"PART A 判定: {'PASS — 换题材/前提确实换世界，无写死' if ok else 'FAIL'}")
    return ok


def _part_b() -> bool:
    print("\n=== PART B · 代表性推演过真 parser，校验反同质化契约 ===\n")
    summaries: dict[str, dict] = {}
    law_blobs: list[str] = []
    all_anchored = True
    for label, payload in REPRESENTATIVE.items():
        premise = "。".join(payload["axioms"])
        model = parse_world_model(json.dumps(payload, ensure_ascii=False), premise=premise)
        summary = world_model_health_summary(model)
        summaries[label] = summary
        law_blobs.append(" ".join(law.delta + law.enforcement for law in model.world_laws))
        anchored = summary["laws_without_derivation"] == 0
        all_anchored = all_anchored and anchored
        print(f"[{label}]")
        print(f"    基线={summary['baseline']}  规律数={summary['law_count']}  覆盖维度={summary['dimension_count']}")
        print(f"    每条规律均锚定公理: {anchored}  平均 specificity={summary['mean_specificity']}")
        print(f"    主角断层线={summary['protagonist_fault_lines']}\n")
    cross = corpus_distinctness(law_blobs)
    mean_spec_ok = all(s["mean_specificity"] > 0 for s in summaries.values())
    print(f"跨题材代表性世界 distinctness = {cross}")
    ok = all_anchored and cross > 0.6 and mean_spec_ok
    print(f"PART B 判定: {'PASS — 锚定/覆盖/多样性全部达标' if ok else 'FAIL'}")
    return ok


def main() -> None:
    print("\n############ 世界模型 · 通用性(跨题材)验证 ############")
    a = _part_a()
    b = _part_b()
    print("\n=== 总判定 ===")
    verdict = "PASS — 世界模型是通用引擎:同一机器,不同题材推出不同且自洽的世界" if (a and b) else "FAIL — 见上方未通过项"
    print(verdict + "\n")


if __name__ == "__main__":
    main()
