"""Seed Qingnang resume artifacts from the locked disk story-bible.

This is a one-book recovery bridge for ``exorcist-detective-1778051012``.
The project was recovered from disk with completed prose through chapter 85,
but without DB planning artifacts. The progressive resume path requires those
artifacts before it can continue writing chapter 86 onward.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

import yaml
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bestseller.domain.enums import ArtifactType  # noqa: E402
from bestseller.domain.planning import PlanningArtifactCreate  # noqa: E402
from bestseller.domain.workflow import ChapterOutlineBatchInput  # noqa: E402
from bestseller.infra.db.models import ChapterModel, VolumeModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.narrative_contracts import (  # noqa: E402
    validate_foundation_identity_contract,
)
from bestseller.services.projects import (  # noqa: E402
    get_project_by_slug,
    import_planning_artifact,
)
from bestseller.services.story_bible import (  # noqa: E402
    parse_cast_spec_input,
    parse_volume_plan_input,
    parse_world_spec_input,
)
from bestseller.services.workflows import (  # noqa: E402
    ensure_project_identity_manifest,
    materialize_latest_chapter_outline_batch,
    materialize_latest_story_bible,
)


PROJECT_SLUG = "exorcist-detective-1778051012"
OUTPUT_DIR = Path("output") / PROJECT_SLUG
STORY_BIBLE_DIR = OUTPUT_DIR / "story-bible"
TARGET_CHAPTERS = 200
WORDS_PER_CHAPTER = 2600

MICRO_STEPS = [
    {
        "name": "入案",
        "hook_type": "case_entry",
        "action": "把新异常接到上一章证据链上",
        "pressure": "现场出现第一件可复核异常物",
    },
    {
        "name": "取证",
        "hook_type": "evidence_collection",
        "action": "调取手续、登记或人证",
        "pressure": "现实记录与青囊提示第一次不一致",
    },
    {
        "name": "反证",
        "hook_type": "contradiction",
        "action": "找到能推翻表面解释的反证",
        "pressure": "一个熟悉名字被写进错误位置",
    },
    {
        "name": "试规",
        "hook_type": "rule_test",
        "action": "用铜钱、罗盘或回执测试规则边界",
        "pressure": "测试结果证明代价比预期更高",
    },
    {
        "name": "付价",
        "hook_type": "cost_escalation",
        "action": "让一名角色付出可见代价换取下一层信息",
        "pressure": "寿数、姓名、身份或关系信任出现损耗",
    },
    {
        "name": "反扑",
        "hook_type": "countermove",
        "action": "镜债或三族压力反向污染证据",
        "pressure": "林渊的判断被现实程序质疑",
    },
    {
        "name": "汇证",
        "hook_type": "evidence_synthesis",
        "action": "把民俗线索和现实手续并列成可复查证据链",
        "pressure": "证据链只差最后一件关键物",
    },
    {
        "name": "设局",
        "hook_type": "trap_setup",
        "action": "林渊主动设下验真局引出对方破绽",
        "pressure": "诱饵可能反过来伤到自己人",
    },
    {
        "name": "揭示",
        "hook_type": "reveal_turn",
        "action": "揭开本十章的核心信息但保留终局代价",
        "pressure": "新事实证明上一层理解不完整",
    },
    {
        "name": "结算",
        "hook_type": "unit_settlement",
        "action": "完成本单元结算并交给下一单元唯一压力",
        "pressure": "结算页留下下一组不可回避的账名",
    },
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _book_spec() -> dict[str, Any]:
    premise = _read(STORY_BIBLE_DIR / "premise.md").strip()
    series_bible = _read(STORY_BIBLE_DIR / "series-bible.md").strip()
    return {
        "title": "青囊不语问阴阳",
        "logline": (
            "青年民俗调查者林渊以青囊、罗盘、账页和现实证据拆解镜债旧案，"
            "在200章内结算父亲抵债、三族旧契、真正债主和替身身份危机。"
        ),
        "genre": "exorcist-detective",
        "target_audience": "喜欢驱魔断案、规则解谜、身份危机和都市民俗悬疑的中文网文读者。",
        "tone": "紧张、克制、证据导向，灵异规则必须落到现实物证和破局代价。",
        "themes": ["因果债务", "身份验真", "父辈旧账", "三族契约", "现实证据与民俗规则互证"],
        "protagonist": {
            "name": "林渊",
            "role": "protagonist",
            "goal": "查清父亲林正淳失踪与抵债真相，完成镜债主账结算。",
            "wound": "不愿承认父亲可能为自己入镜，也不愿承认自己可能就是账本本金。",
        },
        "central_conflict": (
            "镜债系统与真正债主不断污染现实证据和身份记录，逼迫林渊在救人、验账、"
            "自证身份之间付出代价。"
        ),
        "dramatic_question": (
            "林渊能否在不逃避自身主账代价的前提下，查清父亲抵债真相并重定青囊边界？"
        ),
        "naming_pool": [
            "林渊",
            "林正淳",
            "林家辉",
            "林远山",
            "孙九斤",
            "苏婉宁",
            "钱婆婆",
            "张家开门人",
            "沈月娥",
            "陈默",
            "小雨",
            "王建业",
            "张建军",
            "周雪",
            "钱守镜",
            "张启门",
            "林朝宗",
            "许照安",
        ],
        "narrative_lines": {
            "overt": "每个单元案以可验证物证、民俗规则和破局代价推进。",
            "undercurrent": "父亲林正淳的失踪真相逐步从牺牲、误账走向核心代价。",
            "hidden": "真正债主不是困魂镜本身，镜影林渊只是替身规则的现实化压力。",
            "core_axis": "青囊只记因果，不替人赎罪；林渊不能用身份逃债，只能自选代价。",
        },
        "source_story_bible": series_bible[:6000],
        "premise_markdown": premise,
    }


def _world_spec() -> dict[str, Any]:
    world_text = _read(STORY_BIBLE_DIR / "world.md").strip()
    rule_ledger = _read(STORY_BIBLE_DIR / "rule-ledger.md").strip()
    return {
        "world_name": "现代旧城镜债暗面",
        "world_premise": (
            "旧城改造、警方证据链、医院记录、施工手续和民俗圈传闻会被镜债系统污染，"
            "但破局必须回到可复核的物证和规则。"
        ),
        "rules": [
            {
                "rule_code": "QNG-CAUSE",
                "name": "青囊只记因果",
                "description": "青囊可以核账、显因果、提示规则漏洞，但不能替任何人免费赎罪。",
                "story_consequence": "林渊每次破局都必须付出现实、寿数、身份或关系代价。",
            },
            {
                "rule_code": "MIRROR-EYE",
                "name": "反光物成镜眼",
                "description": "镜局可借反光物观察、诱导和污染证据。",
                "story_consequence": "破局必须处理镜面、监控、回执和现实记录之间的矛盾。",
            },
            {
                "rule_code": "SUBSTITUTE-DEBT",
                "name": "替认必须付价",
                "description": "镜债可以被替认或过户，但替认者必须付寿命、姓名或现实身份。",
                "story_consequence": "善意救人不能免费，终局身份恢复也必须付出明确代价。",
            },
            {
                "rule_code": "THREE-CLAN",
                "name": "三族功能边界",
                "description": "林家记账、张家开门、钱家守镜改路，各自能力边界清楚且都有代价。",
                "story_consequence": "三族不能变成万能盟友，任何协助都要留下账面后果。",
            },
        ],
        "power_system": {
            "name": "青囊镜债规则系统",
            "tiers": ["核账", "验门", "改路", "替认", "主账结算"],
            "acquisition_method": "林家青囊、张家门契、钱家镜路和现实物证共同触发。",
            "hard_limits": "规则必须有可见效果、破局方法和后续反噬；禁止口头补设定。",
            "protagonist_starting_tier": "核账与现场验真",
        },
        "locations": [
            {"name": "十七栋303", "type": "mirror_site", "story_role": "第一卷困魂镜局入口"},
            {"name": "旧城井口", "type": "legacy_site", "story_role": "半卷青囊和填井手续并线处"},
            {"name": "清水桥义庄", "type": "evidence_site", "story_role": "义庄铜镜流转和父亲签名伪造证据地"},
            {"name": "三族终账现场", "type": "finale_site", "story_role": "主账结算和青囊边界落地点"},
        ],
        "factions": [
            {"name": "林家记账人", "goal": "守住青囊因果账", "method": "核账与记录"},
            {"name": "张家开门人", "goal": "掌控门契与回执", "method": "开门但不认账"},
            {"name": "钱家守镜人", "goal": "守镜改路并收取代价", "method": "改账路但不消债"},
            {"name": "镜债经手人", "goal": "维持替认与主账污染", "method": "伪证、借脸、身份记录异常"},
        ],
        "history_key_events": [
            {"event": "三族旧契立下", "relevance": "定义林张钱三族功能边界和代价。"},
            {"event": "林正淳入镜/抵债", "relevance": "父亲真相和林渊身份危机的核心旧账。"},
            {"event": "十七栋困魂镜局", "relevance": "现世镜债重新启动并污染现实证据。"},
        ],
        "forbidden_zones": "禁止新增第四契族正典；禁止把镜主试炼写成游戏化副本；禁止200章后才解决主线。",
        "source_world": world_text[:6000],
        "source_rules": rule_ledger[:6000],
    }


def _character(
    name: str,
    role: str,
    gender: str,
    pronoun_zh: str,
    pronoun_en: str,
    *,
    aliases: list[str] | None = None,
    background: str,
    goal: str,
    fear: str = "",
    flaw: str = "",
    strength: str = "",
    secret: str = "",
    arc: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "role": role,
        "gender": gender,
        "pronoun_set_zh": pronoun_zh,
        "pronoun_set_en": pronoun_en,
        "aliases": aliases or [],
        "background": background,
        "goal": goal,
        "fear": fear,
        "flaw": flaw,
        "strength": strength,
        "secret": secret,
        "arc_trajectory": arc,
        "voice_profile": {
            "speech_register": "现代中文网文叙事口吻",
            "sentence_style": "短句推进，证据和动作先于解释",
            "emotional_expression": "压力下用行动和细节显露情绪",
        },
        "moral_framework": {
            "core_values": ["因果可核", "代价必须明账", "救人不能抹去事实"],
            "lines_never_crossed": ["不免费替人赎罪", "不伪造证据"],
            "willing_to_sacrifice": "愿为真相承担身份、寿数或关系代价。",
        },
        "methodology_overlay": {
            "ability_origin_contract": {
                "source": "林家青囊残卷、阴阳眼、罗盘方位术和现实证据链共同形成林渊的核账能力。",
                "visible_signature": "林渊会先用拇指按住铜钱边缘，再把青囊页、罗盘针位和现实手续并列核对。",
                "limit": "青囊只能指出因果账和规则漏洞，不能替人赎罪，也不能凭空替代警方手续或物证。",
                "cost": "每次动用青囊都会把林渊自己的身份、寿数或父亲旧账往主账方向推进。",
                "growth_trigger": "只有当林渊承认某一笔账的代价并完成现实证据验真时，青囊才显示下一层规则。",
                "plot_use": "能力必须制造选择压力：救人、验账、自证身份三者不能同时免费完成。",
            }
        },
    }


def _cast_spec() -> dict[str, Any]:
    cast_text = _read(STORY_BIBLE_DIR / "cast-and-promises.md").strip()
    return {
        "protagonist": _character(
            "林渊",
            "protagonist",
            "male",
            "他",
            "he/him",
            aliases=["青囊执卷人", "镜影林渊冒用的本名"],
            background="青年民俗调查者，林家青囊传人，已完成十七栋镜局到旧城井口的前85章推进。",
            goal="用青囊、罗盘、账页和现实证据拆开镜债，查清父亲林正淳抵债真相。",
            fear="自己才是账本本金，父亲的一切牺牲都被记到自己名下。",
            flaw="遇到父亲线时本能回避情绪，用工具和现场细节压住恐惧。",
            strength="能把民俗规则、物证、账印和现实手续串成可验证破局链。",
            secret="他的身份记录可能被镜影借脸污染，终局必须靠代价而不是辩解恢复。",
            arc="从核账破局者走向愿意承担主账代价的青囊边界重定者。",
        ),
        "antagonist": _character(
            "镜债经手人",
            "antagonist",
            "male",
            "他",
            "he/him",
            aliases=["真正债主", "第二经手人"],
            background="隐藏在困魂镜和三族旧契背后的主账经手压力，不等同于镜影林渊。",
            goal="维持替认规则、污染现实身份记录，并让林渊用错误身份替主账还债。",
            fear="主账结算页、替身回执和三族旧契完整版被同时验真。",
            flaw="依赖伪证和借脸，一旦现实证据链闭合就无法继续转嫁。",
            strength="能利用镜眼、回执、监控和身份系统制造互相矛盾的现实记录。",
            secret="真正债主不是困魂镜本身，镜影只是经手规则的伪证工具。",
            arc="从幕后污染者逐步被迫落到可核验的主账结算页上。",
        ),
        "supporting_cast": [
            _character(
                "林正淳",
                "supporting",
                "male",
                "他",
                "he/him",
                aliases=["林渊父亲"],
                background="林渊父亲，三年前入镜/对外死亡，父亲债和牺牲谜团核心。",
                goal="以自己的旧账为林渊争取破局窗口。",
                fear="林渊把牺牲误读为免债理由。",
                arc="不能反派化，真相必须保留牺牲与误账两层。",
            ),
            _character(
                "孙九斤",
                "supporting",
                "male",
                "他",
                "he/him",
                background="旧城民俗圈人脉持有者，懂桃木、铜钱、人情债。",
                goal="用民俗信息和人脉帮林渊补足现实证据。",
                fear="三年前没能把林正淳消息带回林家。",
                arc="从嘴硬怕死到愿意用命还债，但每次出场必须提供信息、资源或代价。",
            ),
            _character(
                "苏婉宁",
                "supporting",
                "female",
                "她",
                "she/her",
                background="现实证据边界的执行者，负责手续、记录、程序压力和可复核证据。",
                goal="让灵异破局回到可以追查的现实证据链。",
                fear="程序被镜债污染后无法保护无辜者。",
                arc="在终局替身案中承担程序压力并帮助林渊自证身份。",
            ),
            _character(
                "钱婆婆",
                "supporting",
                "female",
                "她",
                "she/her",
                background="钱家守镜改路相关人物，信息有价。",
                goal="守住钱家价码和改路边界。",
                fear="钱家被当成免费救场工具。",
                arc="提供改路信息但必须收取代价，推动寿数账与姓名损耗。",
            ),
            _character(
                "张家开门人",
                "supporting",
                "male",
                "他",
                "he/him",
                background="张家门契与回执相关人物。",
                goal="掌控开门边界，不替任何人认账。",
                fear="张家旁支被拖进林家主账。",
                arc="帮助查出义庄铜镜离城路线，但必须付价。",
            ),
            _character(
                "镜影林渊",
                "supporting",
                "male",
                "他",
                "he/him",
                aliases=["冒名林渊"],
                background="镜债系统借林渊身份制造的现实伪证源。",
                goal="污染林渊身份记录并引发替认。",
                fear="借脸破绽、指纹差异和替身回执被并列验真。",
                arc="从心理威胁升级为程序化伪证源，最终被替认规则反证。",
            ),
        ],
        "antagonist_forces": [
            {
                "name": "困魂镜局",
                "force_type": "systemic",
                "active_volumes": [1],
                "threat_description": "以反光物、回执和否认入账逼迫入局者认债。",
                "relationship_to_protagonist": "第一卷规则入口。",
                "escalation_path": "从303镜眼扩展到现实回执和父亲半卷线索。",
            },
            {
                "name": "旧城井口与义庄证据污染",
                "force_type": "environment",
                "active_volumes": [2],
                "threat_description": "利用旧城手续、义庄登记和父亲签名伪造拉扯现实证据。",
                "relationship_to_protagonist": "第86-100章续写压力。",
                "escalation_path": "把半卷青囊和义庄铜镜流转推入寿数账。",
            },
            {
                "name": "寿数账与张钱代价",
                "force_type": "faction",
                "active_volumes": [3],
                "threat_description": "三族各自功能边界迫使林渊付寿数、姓名和开门代价。",
                "relationship_to_protagonist": "父亲抵债真相第一层。",
                "escalation_path": "借命案结算后留下唯一终局入口。",
            },
            {
                "name": "替身身份危机与真正债主",
                "force_type": "systemic",
                "active_volumes": [4],
                "threat_description": "镜影借脸污染身份记录，真正债主用主账逼林渊替认。",
                "relationship_to_protagonist": "终局主账压力。",
                "escalation_path": "从伪监控、替认回执到三族旧契完整版和主账结算页。",
            },
        ],
        "conflict_map": [
            {
                "character_a": "林渊",
                "character_b": "镜债经手人",
                "conflict_type": "主账结算与替认身份",
                "trigger_condition": "当现实身份记录和镜债账名发生冲突时升级。",
            },
            {
                "character_a": "林渊",
                "character_b": "林正淳",
                "conflict_type": "父亲牺牲真相与免债诱惑",
                "trigger_condition": "父亲抵债页被验真但代价仍未结算。",
            },
            {
                "character_a": "林渊",
                "character_b": "苏婉宁",
                "conflict_type": "民俗规则与现实程序边界",
                "trigger_condition": "证据被镜债污染但仍需程序可复核。",
            },
        ],
        "source_cast": cast_text[:6000],
    }


def _volume_plan() -> list[dict[str, Any]]:
    payload = _load_yaml(STORY_BIBLE_DIR / "volume-plan-v2.yaml")
    volumes = payload.get("volumes")
    if not isinstance(volumes, list):
        raise ValueError("volume-plan-v2.yaml is missing volumes")
    parse_volume_plan_input(payload)
    return [dict(v) for v in volumes if isinstance(v, dict)]


def _chapter_outline_batch() -> dict[str, Any]:
    contract = _load_json(STORY_BIBLE_DIR / "prewrite-contract.json")
    chapters = contract.get("chapters")
    if not isinstance(chapters, dict):
        raise ValueError("prewrite-contract.json chapters must be a dict")
    volume_payload = _load_yaml(STORY_BIBLE_DIR / "volume-plan-v2.yaml")
    volume_entries = [
        dict(v)
        for v in volume_payload.get("volumes", [])
        if isinstance(v, dict)
    ]

    def milestone_for(chapter_no: int) -> dict[str, Any]:
        for volume in volume_entries:
            for milestone in volume.get("milestones") or []:
                if not isinstance(milestone, dict):
                    continue
                cr = milestone.get("chapter_range")
                if isinstance(cr, list) and len(cr) >= 2 and int(cr[0]) <= chapter_no <= int(cr[1]):
                    return milestone
        return {}

    outline_chapters: list[dict[str, Any]] = []
    for chapter_no in range(86, TARGET_CHAPTERS + 1):
        item = chapters.get(str(chapter_no)) or {}
        milestone = milestone_for(chapter_no)
        step = MICRO_STEPS[(chapter_no - 1) % 10]
        milestone_label = str(
            milestone.get("milestone_label")
            or item.get("chapter_objective")
            or "推进镜债主线并服务200章收束"
        )
        evidence_items = [
            str(e)
            for e in (
                milestone.get("required_evidence")
                if isinstance(milestone.get("required_evidence"), list)
                else []
            )
            if str(e).strip()
        ]
        if not evidence_items:
            evidence_items = [
                part.strip()
                for part in str(item.get("required_evidence") or "").replace("、", ",").split(",")
                if part.strip()
            ]
        if not evidence_items:
            evidence_items = ["青囊页", "账印", "现实手续", "镜债回执"]
        evidence_focus = evidence_items[(chapter_no - 86) % len(evidence_items)]
        reveal_items = [
            str(r)
            for r in (
                milestone.get("reveals_unlocked")
                if isinstance(milestone.get("reveals_unlocked"), list)
                else []
            )
            if str(r).strip()
        ]
        reveal_focus = reveal_items[0] if reveal_items else evidence_focus
        objective = (
            f"第{chapter_no}章【{step['name']}】：林渊必须选择并执行「{step['action']}」，"
            f"围绕「{milestone_label}」调查「{evidence_focus}」，用现场行动改变证据局面。"
        )
        anchor = (
            f"承接第{chapter_no - 1}章压力；{step['pressure']}。"
            if chapter_no > 86
            else "承接第85章城南三点定位；清水桥义庄线开始进入可复核证据坐标。"
        )
        payoff = (
            f"读者明确获得「{evidence_focus}」如何服务「{reveal_focus}」的阶段答案，"
            f"并看到一项不可免费消除的代价。"
        )
        next_step = MICRO_STEPS[chapter_no % 10]
        next_pressure = (
            f"下一章转入【{next_step['name']}】：{next_step['pressure']}；"
            f"不得跳出「{milestone_label}」另开随机怪谈。"
        )
        scene_beats = [
            f"{step['action']}：林渊先处理「{evidence_focus}」的现场异常。",
            f"现实证据介入：苏婉宁或孙九斤补出「{evidence_focus}」的手续/人证来源。",
            (
                f"林渊把「{evidence_focus}」与青囊账页、现实手续并列核验，"
                f"逼出「{reveal_focus}」对应的代价，并留下交给下一章的实物线索。"
            ),
        ]
        evidence = "、".join(evidence_items)
        volume_number = ((chapter_no - 1) // 50) + 1
        title = f"第{chapter_no}章 {step['name']}·{evidence_focus}"
        loop_position = "finale" if chapter_no >= 191 else step["hook_type"]

        scenes = []
        for idx in range(1, 4):
            beat = str(scene_beats[idx - 1] if idx - 1 < len(scene_beats) else objective)
            scene_partner = (
                "苏婉宁"
                if idx == 1
                else "孙九斤"
                if idx == 2
                else ("镜影林渊" if volume_number == 4 else "钱婆婆")
            )
            scenes.append(
                {
                    "scene_number": idx,
                    "scene_type": "development" if idx < 3 else "turn",
                    "title": f"{title} 场景{idx}",
                    "time_label": "续写当前时间线",
                    "participants": ["林渊", scene_partner],
                    "purpose": {
                        "story": f"第{chapter_no}章第{idx}场：{beat}",
                        "emotion": f"从【{step['name']}】压力推进到可见代价，不重复上一章功能。",
                    },
                    "entry_state": {"pressure": anchor, "known_evidence": evidence},
                    "exit_state": {"payoff": payoff if idx == 3 else beat, "next_pressure": next_pressure},
                    "key_dialogue_beats": [
                        f"对白必须围绕「{evidence_focus}」和【{step['name']}】推进。",
                        "禁止用解释性闲聊替代现场行动。",
                    ],
                    "sensory_anchors": {
                        "object": evidence_focus,
                        "signal": f"该物件必须证明「{reveal_focus}」的一小步，而非泛泛制造气氛。",
                    },
                    "forbidden_actions": [
                        "不得改写第1-85章既有事实",
                        "不得新增200章以后才解决的主线大坑",
                    ],
                    "concrete_goal": f"{step['action']}，并让「{evidence_focus}」产生可拍摄变化。",
                    "information_introduced": [payoff if idx == 3 else evidence],
                    "information_held_back": [next_pressure],
                    "object_signal": "青囊只提示因果边界，不替任何人免费赎罪。",
                    "target_word_count": max(700, WORDS_PER_CHAPTER // 3),
                }
            )

        outline_chapters.append(
            {
                "chapter_number": chapter_no,
                "volume_number": volume_number,
                "title": title,
                "chapter_goal": objective,
                "opening_pressure": anchor,
                "opening_situation": anchor,
                "main_conflict": f"{objective}；证据要求：{evidence}",
                "hook_type": step["hook_type"] if chapter_no < 191 else f"finale_{step['hook_type']}",
                "hook_description": next_pressure,
                "required_payoff": payoff,
                "tail_hook": next_pressure,
                "chapter_event_role": loop_position,
                "information_gap_mode": "delay_specific_evidence",
                "reveal_weight": 4 if chapter_no >= 151 else 3,
                "target_word_count": WORDS_PER_CHAPTER,
                "key_reveals": [payoff],
                "world_rule_refs": ["QNG-CAUSE", "MIRROR-EYE", "SUBSTITUTE-DEBT", "THREE-CLAN"],
                "chapter_concrete_actions": [
                    "林渊先校验证物或方位，再给出推断。",
                    "苏婉宁或孙九斤提供现实手续、人脉或民俗证据。",
                    "本章以可见代价或证据转折收束。",
                ],
                "chapter_information_introduced": [payoff],
                "chapter_information_held_back": [next_pressure],
                "methodology_contract": {
                    "chapter_function": objective,
                    "conflict_stakes": f"若不能完成「{objective}」，镜债会继续污染身份或证据链。",
                    "conflict_buffs": [
                        f"时限压力：{evidence_focus}对应记录将在本章结束前被镜债改写或失效。",
                        f"暴露风险：林渊一旦误判「{evidence_focus}」，身份记录会多出一条不利伪证。",
                        "资源不足：青囊只能提示因果边界，现实手续必须由苏婉宁或孙九斤补齐。",
                        "两难压力：救人会拖慢验账，自证身份又会暴露父亲抵债线。",
                    ],
                    "pacing_mode": f"【{step['name']}】节奏：先动作，再反应，再付价或交接。",
                    "emotion_phase": f"从{step['pressure']}推进到「{evidence_focus}」的代价选择",
                    "loop_position": loop_position,
                    "hooks_to_resolve": [payoff],
                    "hooks_to_plant": [next_pressure],
                    "protagonist_choice": "林渊选择用现实证据和青囊核账承受代价，而不是靠口头解释脱身。",
                    "visible_action": "现场验物、查账、核手续、对照回执或监控。",
                    "cost": "身份、寿数、证据完整性或关系信任至少一项受到压力。",
                    "gain_reveal": payoff,
                    "required_payoff": payoff,
                    "payoff": payoff,
                },
                "scenes": scenes,
            }
        )

    batch = {"batch_name": "qingnang-compact-86-200", "chapters": outline_chapters}
    ChapterOutlineBatchInput.model_validate(batch)
    return batch


async def _assign_existing_chapter_volumes(session: Any) -> int:
    project = await get_project_by_slug(session, PROJECT_SLUG)
    if project is None:
        raise ValueError(f"Project '{PROJECT_SLUG}' was not found")
    volumes = {
        int(volume.volume_number): volume
        for volume in await session.scalars(
            select(VolumeModel).where(VolumeModel.project_id == project.id)
        )
    }
    updated = 0
    for chapter in await session.scalars(
        select(ChapterModel).where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number <= 85,
        )
    ):
        volume_number = ((int(chapter.chapter_number) - 1) // 50) + 1
        volume = volumes.get(volume_number)
        if volume is None:
            continue
        if chapter.volume_id != volume.id:
            chapter.volume_id = volume.id
            updated += 1
    await session.flush()
    return updated


async def _run(*, apply: bool) -> dict[str, Any]:
    book_spec = _book_spec()
    world_spec = _world_spec()
    cast_spec = _cast_spec()
    volume_plan = _volume_plan()
    outline_batch = _chapter_outline_batch()

    parse_world_spec_input(world_spec)
    parse_cast_spec_input(cast_spec)
    parse_volume_plan_input(volume_plan)
    identity_report = validate_foundation_identity_contract(cast_spec)
    identity_report.raise_for_blocks(project_slug=PROJECT_SLUG, artifact="cast_spec")

    summary: dict[str, Any] = {
        "artifacts": {
            "book_spec": bool(book_spec),
            "world_spec_rules": len(world_spec["rules"]),
            "cast_characters": 2 + len(cast_spec["supporting_cast"]),
            "volume_count": len(volume_plan),
            "outline_chapters": len(outline_batch["chapters"]),
        },
        "apply": apply,
    }
    if not apply:
        return summary

    async with session_scope() as session:
        for artifact_type, content in (
            (ArtifactType.PREMISE, {"premise": _read(STORY_BIBLE_DIR / "premise.md").strip()}),
            (ArtifactType.BOOK_SPEC, book_spec),
            (ArtifactType.WORLD_SPEC, world_spec),
            (ArtifactType.CAST_SPEC, cast_spec),
            (ArtifactType.VOLUME_PLAN, volume_plan),
            (ArtifactType.CHAPTER_OUTLINE_BATCH, outline_batch),
        ):
            artifact = await import_planning_artifact(
                session,
                PROJECT_SLUG,
                PlanningArtifactCreate(
                    artifact_type=artifact_type,
                    content=content,
                    notes="seed_qingnang_resume_artifacts",
                ),
            )
            summary.setdefault("artifact_versions", {})[artifact_type.value] = artifact.version_no

        story_bible_result = await materialize_latest_story_bible(
            session,
            PROJECT_SLUG,
            requested_by="seed_qingnang_resume_artifacts",
        )
        project = await get_project_by_slug(session, PROJECT_SLUG)
        if project is None:
            raise ValueError(f"Project '{PROJECT_SLUG}' was not found after materialization")
        identity_manifest = await ensure_project_identity_manifest(
            session,
            project,
            project_slug=PROJECT_SLUG,
        )
        assigned = await _assign_existing_chapter_volumes(session)
        outline_result = await materialize_latest_chapter_outline_batch(
            session,
            PROJECT_SLUG,
            requested_by="seed_qingnang_resume_artifacts",
        )
        summary["materialized"] = {
            "story_bible_workflow_run_id": str(story_bible_result.workflow_run_id),
            "identity_manifest_count": len(identity_manifest),
            "existing_chapter_volume_assignments": assigned,
            "outline_workflow_run_id": str(outline_result.workflow_run_id),
            "chapters_created": outline_result.chapters_created,
            "scenes_created": outline_result.scenes_created,
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(_run(apply=args.apply))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
