"""Repair front-10 framework inputs for 《青囊不语问阴阳》 regeneration.

This script does not write chapter prose. It repairs the persisted chapter and
scene-card assets that the generation pipeline consumes, then optionally forces
fresh scene/chapter draft creation by archiving current draft flags.
"""

from __future__ import annotations

# ruff: noqa: E501, RUF001
import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import delete, select

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import (  # noqa: E402
    ArcBeatModel,
    CanonFactModel,
    ChapterContractModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    ReaderKnowledgeEntryModel,
    RelationshipEventModel,
    RetrievalChunkModel,
    RewriteTaskModel,
    SceneCardModel,
    SceneDraftVersionModel,
    TimelineEventModel,
)
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.methodology_application_gate import (  # noqa: E402
    build_methodology_application_contract,
)
from bestseller.settings import load_settings  # noqa: E402

PROJECT_SLUG = "exorcist-detective-1778051012"
SNAPSHOT_KEY = "front10_framework_regen_snapshot_20260524"
SOURCE = "front10_framework_inputs_20260525_logic_fix"
CHAPTER_RANGE = range(1, 11)
CHAPTER_TARGET_WORD_COUNT = 2200
SCENE_TARGET_WORD_COUNT = 550
CHAPTER_EXTRA_FORBIDDEN_ACTIONS: dict[int, list[str]] = {
    1: [
        "不得写电话、来电、手机、微信、短信、语音、录音、快递、快递员、配送单、寄件、配送或物流；第一章只在十七栋现场推进。",
        "不得写困魂镜、账页、入账、收账、否认者先入账、第七面、七个人影、七个模糊的人形、八个人影、第三十七号或第三十八号；父亲线只保留旧物压力，不说具体面数。",
        "不得写铜钱发烫、滚烫、烫得像炭火、账页烫、铜钱自动指路或铜钱替林渊解释规则；铜钱在第一章只能冷感定位、压镜脚震动、子时崩角。",
    ]
}
STALE_METADATA_KEYS = {
    "auto_repair_adjusted_target_word_count",
    "auto_repair_attempts",
    "auto_repair_block_codes",
    "auto_repair_exhausted",
    "auto_repair_length_scale",
    "auto_repair_hint",
    "auto_repair_in_progress",
    "auto_repair_last_block_codes",
    "auto_repair_last_resolved_block_codes",
    "auto_repair_last_successful_attempts",
    "auto_repair_attempt",
    "auto_repair_source_block_code",
    "auto_repair_original_target_word_count",
    "auto_repair_resolved_by_clean_quality_report",
    "auto_repair_total_attempts",
    "blocked_by_chapter_outline_readiness_gate",
    "blocked_by_write_safety_gate",
    "chapter_outline_readiness_block_codes",
    "chapter_outline_readiness_hint",
    "chapter_outline_readiness_report",
    "chapter_first_generation",
    "chapter_generation_input_bundle",
    "chapter_predraft_quality_block_codes",
    "chapter_predraft_quality_hint",
    "chapter_predraft_quality_report",
    "chapter_review_attempts_active",
    "generation_input_bundle",
    "production_block_code",
    "quality_bundle",
    "quality_bundle_blocking_codes",
    "quality_bundle_passed",
    "quality_closure",
    "quality_findings",
    "quality_gate_block_code",
    "quality_gate_block_codes",
    "quality_gate_block_hint",
    "quality_gate_block_source",
    "requires_human_review",
    "write_safety_block_code",
    "write_safety_hint",
    "action_sequence",
    "cut_point",
    "ending_hook_payload",
    "gate_function",
    "information_control_mode",
    "reader_payoff",
    "relationship_debts",
    "signature_image",
    "visible_progress",
}


def _scene_contract(
    *,
    stakes: str,
    pressure: list[str],
    focus: str,
    hook: str,
    reveal: str,
    image: str,
    cut: str,
    relationship: list[dict[str, Any]] | list[str],
) -> dict[str, Any]:
    return {
        "conflict_stakes": stakes,
        "stakes": stakes,
        "conflict_buffs": pressure,
        "pressure_stack": pressure,
        "hook_type": hook,
        "spotlight_character": focus,
        "focus_character": focus,
        "information_control_mode": reveal,
        "camera_distance": "贴身近景，先写物件异样，再写人物反应，最后给半截答案",
        "reveal_mode": reveal,
        "signature_image": image,
        "cut_point": cut,
        "breakpoint": cut,
        "relationship_debts": _normalize_relationship_debts(
            relationship,
            focus=focus,
            handle=image,
        ),
    }


def _chapter_contract(
    *,
    stakes: str,
    visible_action: str,
    pressure: list[str],
    pacing: str,
    emotion: str,
    resolve: list[str],
    plant: list[str],
    debts: list[dict[str, Any]] | list[str],
    loop: str,
    climax: bool = False,
) -> dict[str, Any]:
    return {
        "conflict_stakes": stakes,
        "visible_action_or_reaction": visible_action,
        "conflict_buffs": pressure,
        "pacing_mode": pacing,
        "emotion_phase": emotion,
        "hooks_to_resolve": resolve,
        "hooks_to_plant": plant,
        "relationship_debts": _normalize_relationship_debts(
            debts,
            focus="林渊",
            handle=visible_action,
        ),
        "loop_position": loop,
        "is_climax": climax,
    }


def _debt(
    *,
    debtor: str,
    creditor: str,
    evidence: str,
    due: str,
    consequence: str,
    repayment: list[str],
) -> dict[str, Any]:
    return {
        "debtor": debtor,
        "creditor": creditor,
        "evidence_or_handle": evidence,
        "due_condition": due,
        "breach_consequence": consequence,
        "repayment_modes": repayment,
    }


def _normalize_relationship_debts(
    debts: list[dict[str, Any]] | list[str],
    *,
    focus: str,
    handle: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in debts:
        if isinstance(item, dict):
            normalized.append(item)
        elif str(item).strip():
            normalized.append(
                _debt(
                    debtor=focus,
                    creditor="下一场共同目标",
                    evidence=str(item).strip(),
                    due=f"下一场出现“{handle}”的回响时",
                    consequence=f"{focus}失去一次解释或行动主动权",
                    repayment=["用行动补偿这笔关系亏欠", "让下一场状态变化可见"],
                )
            )
    return normalized


def _chapter_relationship_debts(chapter_number: int) -> list[dict[str, Any]]:
    by_chapter: dict[int, list[dict[str, Any]]] = {
        1: [
            _debt(
                debtor="林渊",
                creditor="王建业",
                evidence="子时后只抢下缺角铜钱，没能救回王建业",
                due="张建军带同款旧镜钥匙上门时",
                consequence="林渊必须承认第一场失败，不能把王建业写成一次性尸体",
                repayment=["用张建军线补回王建业死前动作", "保留缺角铜钱作为失败凭据"],
            )
        ],
        2: [
            _debt(
                debtor="林渊",
                creditor="小雨",
                evidence="他逼问过急，镜中点头抢先，小雨半账线上爬",
                due="第3章小雨半账线继续加深时",
                consequence="小雨不再天然信任林渊，读者能看见主角错判代价",
                repayment=["主动解释错判", "放弃继续用铜钱硬救", "改用陈默删证线补因果"],
            )
        ],
        3: [
            _debt(
                debtor="林渊",
                creditor="陈默",
                evidence="他接过陈默手机倒放缓存视频，承担证据污染风险",
                due="苏婉宁要求现实证据闭环时",
                consequence="陈默可反咬林渊动过手机，苏婉宁信任下降",
                repayment=["让苏婉宁旁观关键验证", "把陈默删证行为留作可追证据"],
            )
        ],
        4: [
            _debt(
                debtor="林渊",
                creditor="父亲旧训",
                evidence="他把旧罗盘押给钱婆婆换守镜时间",
                due="缺角铜钱不再回应时",
                consequence="父亲线必须从抽象警告变成具体旧物代价",
                repayment=["追问旧罗盘背面刻痕", "在第7章兑现父亲押物画面"],
            )
        ],
        5: [
            _debt(
                debtor="小雨",
                creditor="苏婉宁",
                evidence="她为了不被带走问话，隐瞒袖口下黑线与证物袋指纹同步跳动",
                due="证物袋再次出现内侧指纹时",
                consequence="苏婉宁少拿一条证词，小雨要承担后续半账误判",
                repayment=["主动补说自己看见的同步变化", "用手腕黑线帮证物链定位"],
            )
        ],
        6: [
            _debt(
                debtor="孙九斤",
                creditor="林渊",
                evidence="他把碰过回执的破木匣藏到身后，先想卖钱脱身",
                due="旧货市场红线勒出入口时",
                consequence="林渊误判旧镜流转路线，孙九斤被迫入伙",
                repayment=["交出木匣来源", "用市井识物能力辨认旧镜流向"],
            )
        ],
        7: [
            _debt(
                debtor="林渊",
                creditor="父亲",
                evidence="缺角铜钱背面露出父亲用指甲刻下的二十三年前日期",
                due="林渊主动把缺角贴上回执碎片时",
                consequence="他会被回执记名，重复父亲当年押名救人的旧路",
                repayment=["承认父亲不是谜语工具", "用父亲旧痕解释押物代价"],
            )
        ],
        8: [
            _debt(
                debtor="苏婉宁",
                creditor="林渊",
                evidence="她发现尸牌提前七分钟后，仍只把钥匙递出半寸",
                due="现实记录和镜中尸体再次冲突时",
                consequence="她必须在程序正义和救人窗口之间作选择",
                repayment=["保留证物口子", "让林渊在她监督下判断尸体真假"],
            )
        ],
        9: [
            _debt(
                debtor="陈默",
                creditor="小雨",
                evidence="他为自保撕下寄件联，导致小雨半账线随封口收紧",
                due="第10章删证视频自动播放时",
                consequence="继续否认会让小雨半账闭合，也暴露陈默删证",
                repayment=["交出寄件联", "主动承认证据半账"],
            )
        ],
        10: [
            _debt(
                debtor="林渊",
                creditor="陈默和小雨",
                evidence="对称半账线把两人绑成同一救援窗口",
                due="第11章303和306路径同时打开时",
                consequence="只救一个会让另一个成为完整半账",
                repayment=["用非铜钱手段判断303/306顺序", "把陈默口供交给苏婉宁闭环"],
            )
        ],
    }
    return by_chapter[chapter_number]


def _decision_protocol(chapter_number: int, chosen_action: str) -> dict[str, Any]:
    specific: dict[int, dict[str, Any]] = {
        1: {
            "alternatives_rejected": ["立刻报警等封楼", "直接进空电梯", "逼王建业说完整镜子来源"],
            "why_this_not_that": "子时只剩十几分钟，报警赶不到现场；进空电梯会把林渊影子暴露；王建业恐惧中说出的完整解释可能被镜子当成承认动作。",
            "constraint": "林渊只能先用现场物证判断边界，铜钱只能压镜脚几秒。",
            "wrong_choice_loss": "王建业被收账，林渊自己的影子也会被记名。",
        },
        2: {
            "alternatives_rejected": ["先安抚小雨等天亮", "继续追问张建军完整经历", "再用缺角铜钱硬压异常"],
            "why_this_not_that": "小雨半账线正在闭合，等天亮会失去救援窗口；继续追问完整经历会刺激镜中点头抢答；第一章铜钱只裂出缺口、没有碎毁，再硬用会把解法写成万能物件。",
            "constraint": "林渊只能用旧铜钥匙齿口、门缝镜光和问话顺序抢时间，裂出缺口的铜钱本章只能作为上一章失败代价提示，不能触碰张建军或小雨，也不能被写成完全碎毁。",
            "wrong_choice_loss": "小雨由路人变成替账对象，张建军彻底失声。",
        },
        3: {
            "alternatives_rejected": ["直接问陈默有没有删视频", "报警交手机做证物", "继续用缺角铜钱压半账线"],
            "why_this_not_that": "陈默已经撒谎，直接问只会得到否认；报警会让手机离开现场，半账线同步关系无法验证；铜钱在第2章裂损后再用会把解法写成万能物件。",
            "constraint": "只有当场恢复缓存视频并让屏幕照着小雨手腕，才能把陈默删证和半账变化绑成可见因果。",
            "wrong_choice_loss": "小雨半账线继续上爬，陈默删证线被洗掉，林渊欠苏婉宁证据污染债。",
        },
        7: {
            "alternatives_rejected": ["只听回执碎片声音", "让苏婉宁独自取证", "把碎片继续封存"],
            "why_this_not_that": "只听声音无法对应现实证据；让苏婉宁独自取证会把她拖进记名风险；继续封存会错过王建业最后十秒。",
            "constraint": "缺角铜钱前十章最后一次主动使用，必须换来可推理线索而不是免费灵感。",
            "wrong_choice_loss": "林渊被回执记名，且父亲当年的押名痕迹会被迫浮出。",
        },
        10: {
            "alternatives_rejected": ["继续逼陈默完整替认", "只救小雨不管陈默", "继续依赖铜钱处理危机"],
            "why_this_not_that": "完整替认会让陈默变成新受害者；只救小雨会断掉删证证据链；铜钱主动使用次数已归零。",
            "constraint": "只能让陈默主动交出寄件联和删证事实，分担证据半账而非替命。",
            "wrong_choice_loss": "小雨半账闭合，陈默继续被幕后秩序者控制。",
        },
    }
    base = specific.get(
        chapter_number,
        {
            "alternatives_rejected": ["直接解释规则", "等待外援", "重复使用铜钱"],
            "why_this_not_that": "本章必须用现场证据和人物选择推进，只能兑现一个主规则，不能靠术语旁白或万能物件越级。",
            "constraint": "每章只允许一个主规则显影和一条关系状态变化。",
            "wrong_choice_loss": "证据链断裂，人物信任下降，下一章救援窗口缩短。",
        },
    )
    return {"chosen_action": chosen_action, **base}


def _chapter_cost(chapter_number: int) -> str:
    costs = {
        1: "缺角铜钱崩掉一角，王建业没救回，林渊第一次被镜面记住影子。",
        2: "林渊逼问过急，小雨半账线上爬，张建军失声；他没敢再用铜钱，掌心旧伤却沿着林字缺口裂开。",
        3: "林渊亲手接触陈默手机，承担证据污染风险；屏幕冷雾割开掌心旧伤。",
        4: "父亲旧罗盘被钱婆婆锁进抽屉，缺角铜钱不再回应林渊。",
        5: "小雨主动隐瞒同步证词，苏婉宁只给林渊半个证物口子。",
        6: "孙九斤藏匣自保失败，被拖进入局，林渊多背一份市井人牵连债。",
        7: "缺角铜钱前十章主动使用次数归零，林渊被回执记名。",
        8: "苏婉宁只半步相信，现实证据链仍可能反咬林渊。",
        9: "陈默撕下寄件联自保，导致小雨半账线随封口收紧。",
        10: "陈默承担证据半账后与小雨形成对称黑线，第11章必须同时救两人。",
    }
    return costs[chapter_number]


def _scene_emotion_task(
    *,
    chapter_number: int,
    scene_number: int,
    scene_title: str,
    scene_type: str,
    focus: str,
) -> str:
    if scene_type in {"character", "market"}:
        return (
            f"{focus}在“{scene_title}”里暴露一个具体隐瞒或私心，"
            "关系距离必须改变，不能只用冷峻对白推进。"
        )
    if scene_type in {"investigation", "evidence", "impossible_evidence"}:
        return (
            f"{focus}在“{scene_title}”里把恐惧压成一次可见判断，"
            "证据要改变下一步行动，而不是只补设定。"
        )
    if scene_type in {"ritual", "memory", "horror", "screen_horror"}:
        return (
            f"{focus}在“{scene_title}”里付出身体、信任或名声代价，"
            "让规则通过反应落地。"
        )
    if chapter_number <= 3 and scene_number == 1:
        return (
            f"{focus}在“{scene_title}”开场先被现场压力逼出破绽，"
            "读者必须立刻知道这场救援会失手。"
        )
    return (
        f"{focus}在“{scene_title}”里从侥幸转为必须选择，"
        "本场退出时留下新的亏欠或压力。"
    )


def _methodology_application_contract(chapter_number: int, spec: dict[str, Any]) -> dict[str, Any]:
    scene_payloads = [
        {
            "scene_number": index,
            "title": scene_spec["title"],
            "purpose": {
                "story": scene_spec["purpose"],
                "emotion": scene_spec.get(
                    "emotion",
                    _scene_emotion_task(
                        chapter_number=chapter_number,
                        scene_number=index,
                        scene_title=scene_spec["title"],
                        scene_type=scene_spec["type"],
                        focus=scene_spec["contract"]["spotlight_character"],
                    ),
                ),
            },
            "hook_requirement": scene_spec["hook"],
            "metadata_json": {"methodology_contract": scene_spec["contract"]},
        }
        for index, scene_spec in enumerate(spec["scenes"], start=1)
    ]
    return build_methodology_application_contract(
        chapter_number=chapter_number,
        chapter_title=spec["title"],
        chapter_contract=spec["methodology"],
        scene_cards=scene_payloads,
    )


def _scene_relationship_debt(
    *,
    chapter_number: int,
    scene_number: int,
    scene_title: str,
    focus: str,
) -> list[dict[str, Any]]:
    specific: dict[tuple[int, int], list[dict[str, Any]]] = {
        (3, 1): [
            _debt(
                debtor="林渊",
                creditor="小雨",
                evidence="他看见账线因第2章错判加速，却暂时不敢说出原因",
                due="小雨追问半账是否会长满时",
                consequence="小雨从依赖转成怀疑，林渊救援话语失效",
                repayment=["在第3章末承认自己昨夜逼问太急", "把证据人陈默带入补救链"],
            )
        ],
        (3, 3): [
            _debt(
                debtor="林渊",
                creditor="苏婉宁",
                evidence="他亲自接过陈默手机倒放视频，承担证据污染风险",
                due="苏婉宁审视手机取证合法性时",
                consequence="林渊被现实证据链反咬，失去警方半信窗口",
                repayment=["让苏婉宁旁观倒放验证", "保留缓存视频作为后续口供支点"],
            )
        ],
        (4, 1): [
            _debt(
                debtor="钱婆婆",
                creditor="小雨",
                evidence="她把救命明码标价，小雨手腕黑线随报价加深",
                due="小雨发现自己被当成交易筹码时",
                consequence="小雨对林渊求援路线降信任，钱婆婆必须给出守镜边界",
                repayment=["演示黑布盖镜只能拖延", "说清不卖没事只卖晚一点"],
            )
        ],
        (4, 4): [
            _debt(
                debtor="林渊",
                creditor="父亲旧训",
                evidence="父亲旧物被钱婆婆锁进抽屉，缺角铜钱不再回应",
                due="第7章缺角铜钱再次被主动贴上回执时",
                consequence="林渊重复父亲押物旧路，父亲线必须兑现具体画面",
                repayment=["追出旧罗盘背面指甲刻痕", "把父亲押名救人的代价写成可见记忆"],
            )
        ],
        (5, 2): [
            _debt(
                debtor="小雨",
                creditor="苏婉宁",
                evidence="她为了明早还能回馄饨摊，不被带走问话，主动说没看见证物袋内指纹",
                due="碎镜片再次映出缺席者时",
                consequence="苏婉宁少拿一条同步证词，小雨半账误差扩大",
                repayment=["在第10章先开口说别替我担", "用手腕黑线补回同步证据"],
            )
        ],
        (6, 1): [
            _debt(
                debtor="孙九斤",
                creditor="林渊",
                evidence="他用讨价还价掩盖恐惧，把破木匣往身后藏",
                due="红线勒出旧货市场入口时",
                consequence="林渊误判旧镜来源，孙九斤从向导变成入局者",
                repayment=["交代破木匣来源", "用识货能力分辨旧镜流转"],
            )
        ],
        (6, 4): [
            _debt(
                debtor="林渊",
                creditor="孙九斤",
                evidence="无镜空框里的后脑勺转过来，孙九斤被拖进镜债",
                due="第7章回执碎片需要旧货来源解释时",
                consequence="市井缓冲变成真实牵连，孙九斤可反咬林渊害他入局",
                repayment=["给孙九斤一条可执行逃生边界", "让其贡献旧货识别价值"],
            )
        ],
        (7, 3): [
            _debt(
                debtor="林渊",
                creditor="父亲",
                evidence="缺角铜钱背面露出父亲指甲刻的日期，提醒当年也押过名字",
                due="铜钱贴上回执背面后不再响应时",
                consequence="林渊被记名，父亲线不能继续只当谜语钩子",
                repayment=["闪回父亲用罗盘换回半张回执", "承认父亲押的是名字不是器物"],
            )
        ],
        (9, 2): [
            _debt(
                debtor="陈默",
                creditor="小雨",
                evidence="他为自保撕下寄件联藏进袖口，导致封口自合时小雨半账收紧",
                due="第10章删证视频自动播放时",
                consequence="陈默继续否认会直接害小雨，也暴露自己删证动机",
                repayment=["主动交出寄件联", "只认自己造成的证据半账"],
            )
        ],
        (9, 3): [
            _debt(
                debtor="小雨",
                creditor="林渊",
                evidence="她从被动受害者变成空间锚点证人，指出条码眼一直朝十七栋看",
                due="秩序者再次利用十七栋方向压迫时",
                consequence="林渊必须保护她不被当成人形定位器",
                repayment=["让小雨的观察变成关键证据", "不再只把她写成被救对象"],
            )
        ],
        (10, 2): [
            _debt(
                debtor="陈默",
                creditor="小雨",
                evidence="他交出藏起的寄件联，承认删证造成半账，但不替命",
                due="三分钟回303倒计时启动时",
                consequence="对称半账会把陈默也拖成可救可失控样本",
                repayment=["承担证据半账", "给苏婉宁完整口供"],
            )
        ],
        (10, 4): [
            _debt(
                debtor="林渊",
                creditor="陈默和小雨",
                evidence="两只手腕半圈黑线隔空对齐，306已结清被划掉",
                due="第11章303与306同时开路时",
                consequence="只救一人会让另一人成为完整半账",
                repayment=["不用铜钱判断救援顺序", "把两条半账写成同一行动目标"],
            )
        ],
    }
    if (chapter_number, scene_number) in specific:
        return specific[(chapter_number, scene_number)]
    creditor = "林渊" if focus != "林渊" else "现场证人"
    return [
        _debt(
            debtor=focus,
            creditor=creditor,
            evidence=f"“{scene_title}”退出时留下的证据、隐瞒或失误",
            due=f"下一场追查“{scene_title}”留下的证据时",
            consequence=f"{focus}失去一次自证或被信任的机会",
            repayment=["交出一条具体线索", "让关系状态在下一场发生可见转向"],
        )
    ]


FRONT10: dict[int, dict[str, Any]] = {
    1: {
        "title": "子时前，镜中缺一张脸",
        "goal": "用十五分钟倒计时、镜中无影和第一声惨叫建立黄金三章钩子，不提前泄露钱婆婆、扣账人和302反位。",
        "opening": "23:43，林渊赶到十七栋楼下，王建业站在雨棚下等他；电梯门开着，里面没有轿厢。",
        "conflict": "林渊必须在子时前判断王建业到底做过什么动作，同时不能让王建业在恐惧中随口答应镜里的声音。",
        "hook_type": "countdown_identity",
        "hook_description": "王建业惨叫后，门外响起三短一长的敲门声，张建军攥着同款旧镜钥匙站在门口。",
        "revealed": ["镜里的东西会借动作逼人答应", "林渊只能用康熙铜钱验证边界，不能靠它直接给答案", "镜中少一张脸比见鬼更危险"],
        "withheld": ["完整名单", "钱家守镜规则", "扣账人身份", "302反位"],
        "emotion": "从现实急迫推进到身份恐惧，结尾给第二名受害者上门压力。",
        "object_signal": {
            "chapter_mode": "康熙铜钱只用于冷感定位和短暂压镜脚，子时崩角后证明代价，不负责直接给答案。",
            "allowed_signals": ["掌心冷感", "压镜脚震动", "缺角渗黑水", "镜影错位"],
            "forbidden_signals": ["铜钱发烫", "铜钱自动指路", "铜钱替主角解释规则"],
        },
        "methodology": _chapter_contract(
            stakes="林渊如果在子时前误判，王建业会被镜里的东西带走，林渊自己的影子也会被镜面记住。",
            visible_action="林渊赶到十七栋，先查空电梯、303门缝逆流血和穿衣镜无影，用康熙铜钱压镜脚验边界，阻止王建业随口答应，子时只抢下一枚缺角血钱。",
            pressure=["23:43到00:00的十五分钟时限", "电梯门开着却没有轿厢", "王建业隐瞒旧镜来源且不断催林渊上楼"],
            pacing="强钩子开场，四个核心信号递进，不堆叠家族大设定",
            emotion="急迫、疑惧、第一次被盯上",
            resolve=["王建业求救是真是假"],
            plant=["镜中缺脸", "张建军带同款旧镜钥匙敲门"],
            debts=_chapter_relationship_debts(1),
            loop="危机来临-用民俗物证判断-付出身份风险-章末换更大危机",
        ),
        "scenes": [
            {
                "title": "十七栋楼下的空电梯",
                "type": "suspense",
                "time": "23:43",
                "participants": ["林渊", "王建业"],
                "purpose": "林渊到十七栋现场见到王建业，空电梯和王建业慌张隐瞒旧镜来源，直接把读者带进现场压力。",
                "entry": "林渊骑电动车赶到十七栋楼下，王建业在雨棚下等他，裤脚沾着303门口才有的黑水。",
                "exit": "电梯门打开，里面没有轿厢，井壁镜面却映出林渊身后多出一张无脸影子。",
                "hook": "电梯门开着，里面没有轿厢。",
                "sensory": {"sound": "空井里传来硬币滚落的回声", "smell": "雨水混着楼道84消毒水味", "touch": "康熙铜钱贴在掌心，冷得像从井水里捞出"},
                "contract": _scene_contract(
                    stakes="林渊如果不上楼，王建业会在倒计时内消失；如果贸然上楼，自己的影子也会暴露。",
                    pressure=["子时倒计时只剩十七分钟", "电梯门开着却没有轿厢", "王建业催他上楼却不肯说镜子来源"],
                    focus="林渊",
                    hook="现场异常",
                    reveal="现场证据先行，林渊只判断危险，不解释完整规则",
                    image="空电梯井壁映出一张无脸影子",
                    cut="电梯井里传出第二个王建业的笑声。",
                    relationship=["林渊和父亲的旧物关系只通过铜钱动作呈现"],
                ),
            },
            {
                "title": "十七栋电梯口的空影",
                "type": "suspense",
                "time": "23:51",
                "participants": ["林渊", "王建业"],
                "purpose": "林渊不进空电梯，只在电梯口和楼梯间确认王建业的影子被镜面抹掉，门缝逆流血把危险指向303。",
                "entry": "十七栋楼道灯一明一灭，林渊停在空电梯门外，用伞柄抵住门缝，不让王建业靠近井口。",
                "exit": "他确认王建业不是单纯被鬼缠，而是被镜里的东西逼着做出一个答应动作。",
                "hook": "电梯门开时，303门缝里有血往上爬。",
                "sensory": {"sight": "血珠沿门缝反向爬升", "sound": "电梯报层音拖长成喘息", "smell": "84消毒水混着腐桂花味"},
                "contract": _scene_contract(
                    stakes="林渊若进错门，会把自己的影子也暴露给镜债。",
                    pressure=["楼道监控损坏", "电梯镜面不映王建业", "门缝血迹反重力上爬"],
                    focus="林渊",
                    hook="镜面异常",
                    reveal="用环境异常证明危险边界存在，不命名完整规则",
                    image="电梯镜面里只有林渊半截肩膀，王建业的位置是一块空白",
                    cut="303门内传出王建业压低的哭声：别让它替我答应。",
                    relationship=["王建业从客户变成林渊必须救的第一笔活账"],
                ),
            },
            {
                "title": "303穿衣镜",
                "type": "investigation",
                "time": "23:56",
                "participants": ["林渊", "王建业"],
                "purpose": "林渊用铜钱压镜脚，发现镜中有人替王建业点头，第一次让读者看见“动作会被镜子抢走”的危险，不显性命名规则。",
                "entry": "王建业躲在沙发后，不敢看穿衣镜。",
                "exit": "林渊阻止王建业随口撇清，却没来得及拦住镜里的点头。",
                "hook": "镜中那张脸点头后，王建业的喉咙里响起纸页摩擦声。",
                "sensory": {"sound": "纸页摩擦声从喉管里挤出来", "touch": "镜脚下铜钱不断震动", "sight": "镜中脸比真人慢半拍点头"},
                "contract": _scene_contract(
                    stakes="王建业一旦被镜中脸替他点头，就会失去自己解释的机会。",
                    pressure=["王建业恐慌乱喊", "铜钱只能压住镜脚几秒", "镜中脸正在模仿真人动作"],
                    focus="王建业",
                    hook="替认动作",
                    reveal="通过镜中点头展示认账规则，不讲术语表",
                    image="镜中人点头时，王建业脖子上浮出一圈细黑线",
                    cut="王建业喉咙里翻页声停住，镜中人替他说了一个好字。",
                    relationship=["林渊救人失败的私人代价开始压到他身上"],
                ),
            },
            {
                "title": "第一声惨叫之后",
                "type": "suspense",
                "time": "00:00",
                "participants": ["林渊", "王建业", "张建军"],
                "purpose": "王建业被镜子带走，林渊只抢下一枚带血铜钱，门外张建军上门成为第二个危机。",
                "entry": "子时到，303的灯全部熄灭。",
                "exit": "林渊握住裂出缺口的铜钱，意识到这不是单人凶案。",
                "hook": "门外张建军敲了三短一长，手里攥着一枚和王建业同款的旧镜钥匙。",
                "sensory": {"sound": "三短一长敲门声", "sight": "穿衣镜里空出一个人形水印", "touch": "铜钱边缘崩掉一角"},
                "contract": _scene_contract(
                    stakes="林渊没救下王建业，还损失父亲留下铜钱的一角。",
                    pressure=["子时已到", "王建业的惨叫被镜子吞掉", "张建军立刻敲门续压"],
                    focus="林渊",
                    hook="连续受害者",
                    reveal="用失败和物件损耗证明主角不能无代价解决事件",
                    image="康熙铜钱缺了一角，缺口里渗出黑红色水珠",
                    cut="张建军在门外发抖，说自己刚才看见王建业站在镜子里朝他招手。",
                    relationship=["林渊对王建业的失败形成第一笔愧疚债"],
                ),
            },
        ],
    },
    2: {
        "title": "第一名否认者",
        "goal": "承接张建军拿旧铜钥匙上门，把否认的外溢代价写清楚，同时给林渊一个会慌、会错判的人性裂口。",
        "opening": "张建军攥着老式铜钥匙站在303门外，刚敲完三短一长；他说自己看见王建业站在镜子里朝他招手，还说只要他答应就能开门。",
        "conflict": "张建军急着否认自己进过空电梯和303，林渊必须在不讲完整术语的情况下判断他撒谎；越追问完整因果，镜中点头越容易抢先。",
        "hook_type": "rule_pressure",
        "hook_description": "小雨手腕出现半圈账线，证明否认会把压力推给弱者。",
        "revealed": ["否认不是逃脱，会把账推向更近的人", "小雨被卷入不是随机惊吓", "林渊会因为救人心切做错半步"],
        "withheld": ["代认全规则", "三姓钱", "父亲当年选择"],
        "emotion": "救人压力、错判懊悔、对小人物心软。",
        "object_signal": {
            "chapter_mode": "缺角铜钱本章不主动触碰张建军或小雨，只作为上一章失败代价提示；铜钱状态是裂出缺口、未碎毁；本章主信号是老式铜钥匙、门缝镜光、半圈黑线和掌心旧伤。",
            "allowed_signals": ["老式铜钥匙齿口错位", "门缝镜光偏移", "半圈黑线暂停闭合", "掌心旧伤沿林字缺口裂开"],
            "forbidden_signals": [
                "铜钱发烫",
                "铜钱接触黑水",
                "铜钱按在腿上",
                "铜钱按在手腕上",
                "铜钱吸力",
                "铜钱机制",
                "铜钱损伤",
                "铜钱已经碎了",
                "铜钱碎了",
                "铜钱碎裂失效",
                "铜钱完全损毁",
                "铜钱碎成",
                "铜钱连续破解否认规则",
                "电话带人入场",
                "打电话托寄",
                "半夜寄件",
                "送个单",
                "配送单",
                "湿纸条",
                "湿票据",
                "票据",
                "跑腿转交",
                "镜债递刀子",
                "账本找最近的人",
                "先认动作再认因果",
            ],
        },
        "methodology": _chapter_contract(
            stakes="张建军若继续否认，小雨会替他承受第一道账线；林渊若救人心切问错顺序，就会把问话变成镜子的抢答机会。",
            visible_action="林渊用张建军手里的旧铜钥匙、三短一长敲门节奏、空电梯镜箱锁痕和门缝镜光反证撒谎；小雨被镜光扫到后，他不用铜钱硬压，只调整问话顺序，却仍因逼问过急被镜中点头抢先反噬。",
            pressure=["张建军不断否认", "旧铜钥匙证明他靠近过镜箱", "小雨手腕账线变深", "303门缝镜光正在外溢"],
            pacing="规则演示章，降低术语，只让读者看懂否认的坏处",
            emotion="紧张、懊悔、保护欲",
            resolve=["张建军是否是第二笔账"],
            plant=["小雨被半账牵连", "林渊对父亲旧训动摇"],
            debts=_chapter_relationship_debts(2),
            loop="受害者否认-规则反噬-主角错判-弱者受伤-章末更难选择",
        ),
        "scenes": [
            {
                "title": "旧铜钥匙的齿口",
                "type": "investigation",
                "time": "00:04",
                "participants": ["林渊", "张建军"],
                "purpose": "直接承接第1章章末：张建军拿着老式铜钥匙敲三短一长，说王建业在镜子里朝他招手。林渊用钥匙齿口、空电梯镜箱锁痕和门缝镜光判断张建军撒谎。",
                "entry": "张建军堵在303门外，左手攥着老式铜钥匙，刚敲完三短一长；他哭着说王建业在镜子里让他答应。",
                "exit": "林渊确认张建军三天前进过空电梯，也靠近过303镜箱；他把张建军留在门口继续问，不让他进屋也不让他离开。",
                "hook": "旧铜钥匙齿口自己错开半格，像刚被一把看不见的锁试过。",
                "sensory": {"sight": "钥匙齿口边缘有新鲜刮痕", "sound": "三短一长的尾音还贴在门板上", "smell": "夹克袖口有旧电梯井的铁锈味"},
                "forbidden_actions": [
                    "不得让林渊蹲下摸黑水、不得把铜钱按在张建军腿上、不得写黑水爬上小腿。",
                    "不得让张建军像懂行人一样解释认账、入账、镜债或账线。",
                    "必须承接第1章章末的旧铜钥匙、三短一长敲门、王建业在镜中招手；不得改成湿纸条、父亲声音、正淳、第七面镜或七人名单。",
                    "不得写电话、来电、手机通知、寄件、快递、外卖、配送、物流、跑腿、半夜等单、送个单、配送单、票据、单子或帮忙寄件。",
                    "不得让张建军离场、回店、下楼、坐电梯或写“明天再找你”；本章张建军必须一直被困在303门口问话压力里。",
                ],
                "contract": _scene_contract(
                    stakes="张建军继续撒谎会让镜债把他标成否认者；林渊如果只追问完整原因，会丢掉眼前救援窗口。",
                    pressure=["王建业刚消失现场未封", "张建军急着撇清", "旧铜钥匙齿口正在错位", "303门缝镜光开始偏移"],
                    focus="张建军",
                    hook="否认者证据",
                    reveal="用物证反证口供，让规则压力从人话里冒出来",
                    image="旧铜钥匙齿口错开半格，像被看不见的锁咬过",
                    cut="张建军还说没进过电梯，钥匙齿口却对上了电梯镜箱锁痕。",
                    relationship=["林渊第一次对张建军失去耐心"],
                ),
            },
            {
                "title": "小雨的半圈账线",
                "type": "suspense",
                "time": "00:10",
                "participants": ["林渊", "张建军", "小雨"],
                "purpose": "小雨不是配送员，她是楼下馄饨摊收摊后上楼追餐盒押金的邻近人物；她被303门缝镜光扫到，手腕出现半圈黑线，说明否认会外溢到无辜者。",
                "entry": "楼下馄饨摊刚收摊，小雨抱着空餐盒上楼找张建军退押金，正好踩进303门缝漏出的镜光。",
                "exit": "林渊没有把铜钱按到她身上，只用自己的外套挡住门缝镜光，小雨手腕黑线停在半圈以上。",
                "hook": "小雨看着手腕问：我只是来要押金，为什么它写到我身上？",
                "sensory": {"smell": "空餐盒里残着葱油和碱水味", "touch": "小雨手腕冰得像浸过井水", "sight": "半圈黑线像没闭合的手镯"},
                "forbidden_actions": [
                    "不得写小雨是外卖员、快递员、配送员、送夜宵、接配送单或半夜派单。",
                    "不得把铜钱按到小雨手腕上；不得把湿纸条按在、贴在或压在小雨手腕上；本场只能用外套、门缝镜光和人物动作暂时挡住外溢。",
                    "不得让小雨主动理解认账、入账、替认、镜债、账线等专业规则词。",
                ],
                "contract": _scene_contract(
                    stakes="小雨只是来追押金的邻近小人物，却可能被张建军的否认拖成替账对象。",
                    pressure=["张建军还在否认", "小雨手腕黑线正在闭合", "外套只能挡住门缝镜光几秒"],
                    focus="小雨",
                    hook="无辜者被牵连",
                    reveal="通过弱者受伤展示否认规则的代价",
                    image="小雨手腕上浮出一只没闭合的黑线手镯",
                    cut="林渊用外套压住门缝镜光，黑线停了一瞬，却没有从小雨手腕上退下去。",
                    relationship=["林渊对小雨产生保护债，小雨从路人成为证人"],
                ),
            },
            {
                "title": "林渊错判半步",
                "type": "character",
                "time": "00:16",
                "participants": ["林渊", "张建军", "小雨"],
                "purpose": "林渊为了救小雨，把问题从完整因果压成具体动作，却因为急于让张建军改口而忽略了承认动作会被镜子抢先模仿。",
                "entry": "小雨账线逼近闭合，张建军脸色发青。",
                "exit": "镜中张建军先一步点头，林渊掌心旧伤沿着林字缺口裂开，小雨手腕黑线猛地往上爬。",
                "hook": "张建军还没开口，镜子里的他已经替他点了头。",
                "sensory": {"sound": "镜面里传出咔哒一声点头骨响", "sight": "林渊指节因用力发白", "touch": "掌心旧伤被指甲掐开"},
                "forbidden_actions": [
                    "不得再次使用铜钱接触黑水、腿、手腕或半账线。",
                    "不得让林渊讲完整规则课；只能用问话顺序和动作结果让读者看懂风险；不得写“镜债递刀子”“账本找最近的人”“先认动作再认因果”等总结式规则句。",
                    "不得把张建军写成主动完成全套认账术语的人。",
                ],
                "contract": _scene_contract(
                    stakes="林渊的逼问如果被镜子利用，会把张建军推入替认陷阱。",
                    pressure=["小雨黑线闭合倒计时", "张建军心理崩溃", "林渊救人心切问得太急", "镜中倒影抢动作"],
                    focus="林渊",
                    hook="主角错判",
                    reveal="让主角的救人心切变成规则漏洞",
                    image="林渊掌心旧伤沿着林字缺口裂开，血没有碰到铜钱",
                    cut="林渊低声骂了自己一句：我急了。小雨下意识把手腕往身后藏。",
                    relationship=["林渊对小雨和张建军都新增亏欠感"],
                ),
            },
            {
                "title": "否认者的回声",
                "type": "suspense",
                "time": "00:23",
                "participants": ["林渊", "张建军", "小雨"],
                "purpose": "镜中点头夺走张建军的说话权，把他标成第一名否认者；林渊只来得及保住小雨半条账线，不能把危机升级成门吞人或确认死亡。",
                "entry": "镜中张建军已经替真人完成点头。",
                "exit": "小雨账线停在半圈以上，张建军站在303门外彻底失声；303门保持半开，镜里的声音从小雨手腕黑线里回响。",
                "hook": "小雨手腕黑线末端多出一个钱孔形缺口，正对上林渊掌心裂纹。",
                "sensory": {"sound": "镜中回声像隔着水", "sight": "小雨手腕黑线停在缺口处", "smell": "空餐盒里残着酸葱味"},
                "forbidden_actions": [
                    "不得写张建军被拖进门、被门吞掉、被镜子吞掉、确认死亡或303门合拢。",
                    "不得重复第1章门关闭/镜吞人的高潮动作；本场危机只落在失声、回声和小雨半账未解。",
                    "不得让小雨或张建军主动说出认账、入账、镜债、账线等完整规则词，除非写成被镜中声音逼迫复述。",
                    "不得引出陈默、七号入账、代父、入门、归人等第3章以后才处理的新人物或术语；章末最后一帧只落在小雨钱孔形缺口和林渊掌心裂纹。",
                    "不得让张建军离场、坐电梯或回店；不得让小雨下楼；不得进入303室内另起调查；不得用电梯脚印、黑泥鞋印、水渍脚印或新脚从门缝探出作为结尾钩子。",
                    "不得写张家门契、三代以内、血债血偿、八个人影、七行名单、病号服父亲、父亲声音、正淳或父亲实体登场。",
                ],
                "contract": _scene_contract(
                    stakes="张建军失去主动权，小雨只能暂时保住半账状态。",
                    pressure=["镜中点头已完成", "小雨仍有半圈账线", "林渊掌心裂纹扩大"],
                    focus="林渊",
                    hook="半账未解",
                    reveal="否认者不是死亡标签，而是后续回执压力",
                    image="镜子里张建军的嘴没动，声音却从小雨手腕黑线里冒出来",
                    cut="小雨手腕黑线末端多出一个钱孔形缺口，正对上林渊掌心裂纹。",
                    relationship=["小雨开始依赖林渊，林渊必须承担解释责任"],
                ),
            },
        ],
    },
}


def _chapter_numbers_from_arg(raw: str | None) -> tuple[int, ...]:
    if raw is None or not raw.strip():
        return tuple(CHAPTER_RANGE)
    selected: set[int] = set()
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        if "-" in item:
            left, right = item.split("-", 1)
            start = int(left.strip())
            end = int(right.strip())
            if end < start:
                start, end = end, start
            selected.update(range(start, end + 1))
        else:
            selected.add(int(item))
    invalid = sorted(number for number in selected if number not in CHAPTER_RANGE)
    if invalid:
        raise ValueError(f"chapters out of front-10 range: {invalid}")
    return tuple(sorted(selected))


_CHAPTER_EXECUTION_PROFILES: dict[int, dict[str, Any]] = {
    3: {
        "visible_action": "林渊不再硬用缺角铜钱，而是让陈默当场恢复已删视频，用屏幕倒放和小雨半账线的同步变化确认半账正在寻找证据人。",
        "pressure": ["小雨半账加深", "陈默删证后手机不受控亮屏", "铜钱被林渊收起，不能再承担本章解法"],
        "loop": "半账恶化-证据人隐瞒-手机倒放验因果-306门牌介入",
        "object_signal": {
            "chapter_mode": "铜钱禁用，手机屏幕倒放和半账线同步是本章主信号。",
            "allowed_signals": ["屏幕倒放", "半账线同步收紧", "306收件人自亮"],
            "forbidden_signals": ["缺角铜钱主动验法", "铜钱发烫"],
        },
    },
    4: {
        "visible_action": "林渊带小雨找钱婆婆开价，钱婆婆用黑布、红线和镜背灰印示范压镜边界，林渊必须把父亲旧物押给她换一条路。",
        "pressure": ["钱婆婆只卖时间不卖答案", "小雨半账线随报价收紧", "父亲旧物被迫抵价"],
        "loop": "求援-交易压价-守镜示范-父亲旧物抵押",
        "object_signal": {
            "chapter_mode": "红线与黑布是守镜信号；铜钱只作为抵价旧物被收走，不参与验证。",
            "allowed_signals": ["红线绷紧", "黑布吸水", "镜背灰印浮出"],
            "forbidden_signals": ["用铜钱替代钱婆婆的守镜法"],
        },
    },
    5: {
        "visible_action": "林渊和苏婉宁围绕周雪证物袋建立现实证据链，用封条、碎镜片和监控缺帧互相校验，而不是靠民俗物件直接下结论。",
        "pressure": ["证物袋可能被反咬成伪证", "苏婉宁只相信可封存证据", "周雪的身份不能被写成一次性受害者"],
        "loop": "证物封存-现实质疑-碎镜反证-苏婉宁半信",
        "object_signal": {
            "chapter_mode": "证物袋封条和碎镜片是主信号；铜钱不出手。",
            "allowed_signals": ["封条反贴", "碎镜片映出缺席者", "监控缺帧"],
            "forbidden_signals": ["铜钱验警方证物"],
        },
    },
    6: {
        "visible_action": "林渊在旧货市场逼孙九斤辨认旧镜流转线，用黑布遮镜、红线试框和摊主口供拼出市场入口。",
        "pressure": ["孙九斤想卖钱脱身", "旧镜没有镜面却映后脑", "市场人情账会反过来拖住林渊"],
        "loop": "市井误打误撞-旧物辨认-红线试框-搭档入局",
        "object_signal": {
            "chapter_mode": "黑布、红线和残铜钱是民俗工具组合；缺角铜钱不再继续损耗。",
            "allowed_signals": ["黑布显影", "红线勒出框痕", "残铜钱只用于比对来源"],
            "forbidden_signals": ["主角缺角铜钱主动验法"],
        },
    },
    7: {
        "visible_action": "林渊只在回执碎片上消耗一次缺角铜钱，听取王建业最后十秒，同时承担自己被回执记名的风险。",
        "pressure": ["回执碎片能还原动作但会记住听者", "苏婉宁要求现实证据闭环", "这是缺角铜钱前十章唯一一次主动消耗"],
        "loop": "回执入手-主动消耗-最后十秒还原-林渊被记名",
        "object_signal": {
            "chapter_mode": "缺角铜钱主动使用一次，剩余主动使用次数归零；之后必须改用其他证据。",
            "allowed_signals": ["缺角贴回执", "指纹反按", "听见最后十秒"],
            "forbidden_signals": ["继续扩大缺角来重复验法"],
        },
    },
    8: {
        "visible_action": "林渊和苏婉宁在太平间核对尸牌、冷柜记录和尸体镜像，判断假王建业如何反咬现实死亡时间。",
        "pressure": ["尸牌时间早于死亡时间七分钟", "法医记录不能随便推翻", "镜中尸体会说话但现实尸体沉默"],
        "loop": "冷柜核验-现实记录冲突-镜中尸体开口-苏婉宁共同判断",
        "object_signal": {
            "chapter_mode": "尸牌、冷柜温度和镜中尸体是主信号；铜钱禁用。",
            "allowed_signals": ["尸牌提前", "冷柜温度跳格", "镜中尸体说话"],
            "forbidden_signals": ["铜钱验尸"],
        },
    },
    9: {
        "visible_action": "林渊让陈默现场拆开明天凌晨寄出的快递，用寄件时间、陈默撕下的寄件联和热敏条码眼证明有人在维持镜债秩序。",
        "pressure": ["快递时间必须被明确标成不可能证据", "陈默不能像专家一样解释规则，只能为自保藏证", "小雨半账线随快递封口收紧"],
        "loop": "不可能快递-陈默藏寄件联-热敏条码眼-秩序者压迫",
        "object_signal": {
            "chapter_mode": "快递时间戳、湿寄件联和热敏条码眼是主信号；铜钱禁用。",
            "allowed_signals": ["明日寄件时间", "湿寄件联", "热敏条码眼", "封口自合"],
            "forbidden_signals": ["用铜钱解释快递异常"],
        },
    },
    10: {
        "visible_action": "林渊提前堵住删证链，把继续否认会害小雨、交出寄件联会自证删证两条路摊开，陈默主动交出昨晚藏起的寄件联并承担证据半账。",
        "pressure": ["陈默继续否认会让小雨半账闭合", "陈默交出寄件联会暴露自己删证和藏证", "林渊必须不用铜钱完成一次判断"],
        "loop": "删证视频自播-林渊先手堵证-陈默交出寄件联-对称半账打开306路径",
        "object_signal": {
            "chapter_mode": "自动视频、对称半账线和303门内笑声是主信号；铜钱归零禁用。",
            "allowed_signals": ["视频自播", "对称半账线", "门内小雨笑声"],
            "forbidden_signals": ["铜钱救场"],
        },
    },
}


def _extend_front10() -> None:
    chapter_specs = [
        (3, "半账不能替", "让陈默入场，围绕小雨半账做第一次救援失败后的补救；不引入孙九斤、不讲扣账人、不写三代为一户。", "小雨半账加深，陈默因删过王建业偷拍视频而被牵连。", "陈默的手机屏幕自己亮起，收件人显示为306。", ["半账会寻找相邻证据人", "陈默删证导致自己被镜债盯上"], ["306背后的价码", "扣账人", "老年林渊"], "急救、互不信任、临时结盟。"),
        (4, "钱婆婆开价", "钱婆婆正式登场，只交代守镜价码和边界，让306从恐怖空间变成可追现场。", "钱婆婆愿意教压镜，但要林渊拿父亲旧物抵价。", "钱婆婆说：守镜不是救命，是让债晚一点来。", ["钱婆婆守镜但不消债", "306有王建业留下的回执痕"], ["三姓钱完整来历", "母镜源门"], "交易、压价、第一次拿到外援。"),
        (5, "周雪的证物袋", "周雪线和苏婉宁现实压力线入场，把灵异危险拉进证据链。", "苏婉宁封存证物时发现镜债会反咬现实证词。", "证物袋里的碎镜片映出一个不在现场的人。", ["苏婉宁有现实封存能力", "周雪不是工具人而是证物链关键"], ["警方完整态度", "幕后操控者身份"], "现实压迫、证据焦虑、信任试探。"),
        (6, "旧货市场的红线", "孙九斤提前混脸熟并入伙，用旧货市场红线、黑布、残铜钱补足民俗行动层。", "孙九斤想把旧物卖钱，却发现自己也碰过王建业回执。", "黑布下的旧镜没有镜面，却映出孙九斤的后脑勺。", ["孙九斤有市井识物能力", "旧货市场是镜债流转入口"], ["孙家背景", "完整三姓结构"], "市井缓冲、危险复燃、搭档磨合。"),
        (7, "王建业的回执碎片", "围绕王建业回执碎片做一次可推理追索，让读者看到规则能破案。", "回执碎片会让持有人听到王建业最后十秒，但听完也会被记住。", "碎片上王建业的指纹反向按住林渊的指腹。", ["回执碎片可还原最后动作", "林渊必须付出被记名风险"], ["父亲当年是否也听过回执"], "推理爽点、代价加码、父亲钩子。"),
        (8, "太平间里的假王建业", "太平间假王建业制造现实证据反咬，逼苏婉宁和林渊共同判断尸体真假。", "尸体在法医记录里存在，但镜中尸体会说话。", "冷柜拉开时，王建业的尸牌号码比死亡时间早了七分钟。", ["镜债能伪造现实记录", "苏婉宁开始相信林渊一半"], ["假尸来源", "完整操控者"], "冷感恐惧、现实协作、证据反咬。"),
        (9, "屏幕外的脸", "快递单、陈默手机和模糊维持秩序者形成压力，但不说扣账人。", "陈默收到自己寄出的快递，寄件时间却是明天凌晨。", "快递封口自己合上，箱底浮出陈默明天才会签下的名字。", ["有人在维持镜债秩序", "陈默成为第三个可救可失控样本"], ["扣账人名称", "母镜源门"], "信息恐惧、时间错位、幕后压迫。"),
        (10, "陈默的半账", "陈默否认线收束并桥接第11章：三分钟内必须回303，否则小雨半账闭合。", "陈默先否认删证，随后为自保和补偿小雨主动交出藏起的寄件联，只承担自己造成的证据半账。", "陈默手腕浮出和小雨对称的半圈黑线，303门内传出小雨的笑声，门缝回执写着306已结清又被划掉。", ["陈默主动承担证据半账触发对称半账", "303和306形成下一阶段路径"], ["第11章具体解法", "终局家族答案"], "前十章小高潮、救援倒计时、桥接后续。"),
    ]
    for chapter_number, title, goal, conflict, hook, revealed, withheld, emotion in chapter_specs:
        profile = _CHAPTER_EXECUTION_PROFILES[chapter_number]
        FRONT10[chapter_number] = {
            "title": title,
            "goal": goal,
            "opening": (
                "第3章开场在十七栋楼道，小雨手腕半账线当场加深，现场承接上一章危机。"
                if chapter_number == 3
                else f"第{chapter_number}章开场直接承接上一章章末危机，不另起新案。"
            ),
            "conflict": conflict,
            "hook_type": "rule_payoff",
            "hook_description": hook,
            "revealed": revealed,
            "withheld": withheld,
            "emotion": emotion,
            "methodology": _chapter_contract(
                stakes=conflict,
                visible_action=profile["visible_action"],
                pressure=profile["pressure"],
                pacing="一章只推进一个主规则和一个人物关系变化",
                emotion=emotion,
                resolve=[revealed[0]],
                plant=[hook],
                debts=_chapter_relationship_debts(chapter_number),
                loop=profile["loop"],
                climax=chapter_number == 10,
            ),
            "object_signal": profile["object_signal"],
            "scenes": _generic_scenes(chapter_number, title, conflict, hook, revealed, withheld),
        }


def _generic_scenes(
    chapter_number: int,
    title: str,
    conflict: str,
    hook: str,
    revealed: list[str],
    withheld: list[str],
) -> list[dict[str, Any]]:
    focus_by_chapter = {
        3: ["小雨", "陈默", "林渊", "陈默"],
        4: ["钱婆婆", "林渊", "小雨", "林渊"],
        5: ["周雪", "小雨", "林渊", "苏婉宁"],
        6: ["孙九斤", "林渊", "孙九斤", "林渊"],
        7: ["林渊", "王建业", "苏婉宁", "林渊"],
        8: ["苏婉宁", "林渊", "王建业", "苏婉宁"],
        9: ["陈默", "林渊", "小雨", "陈默"],
        10: ["陈默", "林渊", "小雨", "林渊"],
    }
    participants_by_chapter = {
        3: ["林渊", "小雨", "陈默"],
        4: ["林渊", "小雨", "钱婆婆"],
        5: ["林渊", "苏婉宁", "周雪", "小雨"],
        6: ["林渊", "孙九斤"],
        7: ["林渊", "苏婉宁", "王建业"],
        8: ["林渊", "苏婉宁", "王建业"],
        9: ["林渊", "小雨", "陈默"],
        10: ["林渊", "小雨", "陈默"],
    }
    variations = {
        3: {
            "titles": ["小雨手腕的第二道线", "陈默删掉的视频", "屏幕倒放十秒", "306收件人"],
            "types": ["suspense", "character", "investigation", "suspense"],
            "purposes": [
                "十七栋楼道里，小雨手腕黑线因林渊昨夜错判从半圈长到三分之二，林渊确认半账正在找相邻证据人。",
                "陈默不承认删过王建业偷拍视频，手机相册空白反而成为破绽。",
                f"林渊亲自接过陈默手机倒放缓存视频，用屏幕和半账线同步验证：{revealed[0]}。",
                f"306收件人自亮，把补救失败落到下一章路径：{hook}",
            ],
            "entries": ["小雨坐在十七栋楼道台阶上，手腕黑线从半圈长到三分之二，林渊看见第2章错判留下的裂纹。", "陈默赶来时先把手机扣在掌心。", "林渊关掉走廊灯，亲自接过手机，只让屏幕照着小雨手腕。", "缓存视频停在王建业回头的最后一秒。"],
            "exits": ["林渊确认半账在找相邻证据人，也确认这是自己昨夜逼问太急的后果。", "陈默删证被识破，但他仍不肯说视频来源。", f"{revealed[0]}被证实，林渊掌心旧伤被屏幕冷雾重新割开，{withheld[0]}继续保留。", "手机收件人自动跳成306。"],
            "hooks": ["小雨手腕黑线尽头浮出306三个湿字。", f"{revealed[0]}被证实，但陈默手机开始替他说话。", "屏幕倒放到第十秒，王建业在视频里看向拍摄者。", hook],
            "images": ["小雨手腕黑线像被人用针重新缝了一圈", "空白相册里残留一格灰色缩略图", "手机屏幕里的楼道时间倒着走", hook],
            "sounds": ["楼梯间灯管滋滋响", "手机震动声被陈默用掌心闷住", "视频倒放时人声像吞回喉咙", "306门内传来空盒落地声"],
            "touches": ["小雨手腕冷得发麻", "陈默掌心全是汗", "屏幕玻璃贴着半账线发凉", "手机边框渗出水汽"],
        },
        4: {
            "titles": ["钱婆婆门口的价码", "黑布盖镜", "红线试框", "父亲旧物抵价"],
            "types": ["character", "ritual", "investigation", "suspense"],
            "purposes": [
                f"钱婆婆拒绝免费救人，把守镜价码和边界摆到明处：{conflict}",
                "钱婆婆用黑布盖镜，只演示拖延，不承诺消债。",
                f"红线试出306门框上的回执痕，落地：{revealed[0]}。",
                "林渊用父亲旧物抵价，钱婆婆暗示父亲当年也押过同样东西。",
            ],
            "entries": ["钱婆婆不开灯，只把门链留出一掌宽。", "小雨把手腕藏进袖口，黑线仍从布料下鼓起来。", "钱婆婆把红线绕在旧镜空框上。", "钱婆婆盯住林渊包里的旧罗盘，林渊指腹反复摸过缺角铜钱。"],
            "exits": ["林渊明白钱婆婆只认价码，但小雨手腕黑线在钱婆婆看他时加深一圈，他分不清这是规则反应还是小雨不信他。", "黑布被镜面吸出一个湿手印。", f"{revealed[0]}成立，{withheld[0]}仍不说。", "父亲旧物被钱婆婆锁进抽屉，缺角铜钱没有再回应林渊。"],
            "hooks": ["钱婆婆说：能让镜子晚一点动，就晚一点动。它要收的东西，我挡不住。", "黑布下有人从里面敲了三下。", "红线在306方向自己绷紧。", "钱婆婆盯着旧罗盘说：你父亲当年也押过同样的东西。"],
            "images": ["门链缝里露出一枚磨平的算盘珠", "黑布贴住镜面后陷出人脸轮廓", "红线勒出一圈看不见的门框", "旧罗盘背面有一条被指甲反复抠过的刻痕"],
            "sounds": ["算盘珠碰在瓷碗里", "黑布下有指甲刮布声", "红线绷紧时像琴弦断音", "抽屉锁舌咬合"],
            "touches": ["门缝风凉得刺手", "黑布湿得像刚从井里捞出", "红线勒进林渊指腹", "旧罗盘离手时沉了一下"],
        },
        5: {
            "titles": ["周雪的证物袋", "小雨压住的袖口", "碎镜片里的缺席者", "苏婉宁留出的证物口"],
            "types": ["investigation", "character", "evidence", "suspense"],
            "purposes": [
                f"周雪证物袋入场，把灵异压力压到现实证据链上：{conflict}",
                "苏婉宁封存证物时，小雨为保住明早摊位，主动隐瞒袖口黑线和袋内指纹同步跳动。",
                f"碎镜片映出不在现场的人，证明：{revealed[0]}。",
                f"苏婉宁不完全相信林渊，但选择保留一个证物口子：{hook}",
            ],
            "entries": ["周雪的证物袋放在警车后座，封口还没贴死。", "小雨把袖口按在手腕上，眼睛却一直躲着证物袋内侧。", "林渊隔着透明袋看碎镜片。", "警灯把楼道照成一截一截的红蓝色。"],
            "exits": ["证物袋里的碎镜片自己转了半寸。", "小雨说我没看见，袖口下的黑线却跟封条同时跳了一寸。", f"{revealed[0]}被证实，{withheld[0]}继续不揭。", "苏婉宁把证物袋单独收进内袋，并记下小雨刚才移开的视线。"],
            "hooks": ["证物袋内壁出现从里面按出的指纹。", "小雨低声说：我没看见。袖口下黑线往上跳了一寸。", "碎镜片映出一个不在现场的人。", hook],
            "images": ["透明袋角沾着周雪指甲油碎屑", "小雨袖口边缘露出一圈被热汤烫过的旧疤", "碎镜片里站着一个没有登记的人影", hook],
            "sounds": ["警车电台沙沙响", "小雨咽口水时塑料餐盒轻轻一响", "碎镜片碰袋壁叮了一声", "警灯继电器啪嗒跳动"],
            "touches": ["证物袋外壁冰凉", "袖口被小雨攥得发湿", "袋口有一股冷气顶出来", "林渊手背被警灯照得发冷"],
        },
        6: {
            "titles": ["旧货市场的黑布摊", "孙九斤认货", "红线勒出的空框", "没有镜面的后脑勺"],
            "types": ["market", "character", "ritual", "suspense"],
            "purposes": [
                f"旧货市场开场先让孙九斤讨价还价耍滑，再让市井物件急转恐怖：{conflict}",
                "孙九斤想卖旧物脱身，却暴露自己碰过王建业回执。",
                f"林渊用红线和摊主口供拼出入口，兑现：{revealed[0]}。",
                f"黑布下的旧镜无镜面却映后脑，逼孙九斤入伙：{hook}",
            ],
            "entries": ["旧货市场收摊，孙九斤还在跟摊主为三块钱抹零，雨棚下只剩黑布摊没走。", "孙九斤把一只破木匣往身后藏。", "林渊让孙九斤把红线绕过空镜框。", "黑布摊主突然不认自己刚才说过的话。"],
            "exits": ["黑布下的空框滴出旧水。", "孙九斤承认见过同款旧镜。", f"{revealed[0]}被证实，{withheld[0]}仍被压住。", "孙九斤看见自己的后脑勺在无镜空框里转过来，卷帘门电子钟跳到00:17。"],
            "hooks": ["黑布下的旧镜没有镜面，却有水声。", "孙九斤袖口的赊账红纸自己烧出一个洞。", "红线勒进空气里，像勒住一扇门。", "无镜空框里的后脑勺转过来，卷帘门电子钟跳到00:17。"],
            "images": ["黑布摊上压着三枚残铜钱", "赊账红纸边缘烧出铜钱大小的洞", "空气里出现一圈红线勒痕", "卷帘门电子钟在空框倒影里跳到00:17"],
            "sounds": ["市场卷帘门一扇扇落下", "孙九斤咽口水的声音很响", "红线摩擦空气发出细响", "空框里有人吸了一口气"],
            "touches": ["黑布潮得粘手", "破木匣边角扎人", "红线勒得指节发白", "孙九斤后颈起了一层冷汗"],
        },
        7: {
            "titles": ["回执碎片", "最后十秒", "缺角背面的刻日", "林渊被记名"],
            "types": ["investigation", "memory", "ritual", "suspense"],
            "purposes": [
                f"回执碎片入手，把王建业死亡前最后动作变成可推理线索：{conflict}",
                "苏婉宁要求这十秒必须能对应现实证据。",
                f"林渊看见缺角背面的父亲刻日，仍把铜钱贴上回执作诱饵，兑现：{revealed[0]}。",
                f"代价不是疼痛，而是林渊自己被回执记名：{hook}",
            ],
            "entries": ["回执碎片夹在证物袋第二层封口里。", "苏婉宁把录音笔按在桌边。", "缺角铜钱背面露出父亲指甲刻下的二十三年前日期。", "最后十秒放完，房间安静得像断电。"],
            "exits": ["碎片上浮出王建业反向指纹。", "最后十秒出现一个被删掉的敲门动作。", f"林渊想起父亲把旧罗盘押在同样的桌边，只换回半张回执；{revealed[0]}被证实，{withheld[0]}仍不回答。", "回执碎片灰里浮出半个林字，像在账本上给林渊开了一页。"],
            "hooks": ["王建业的指纹从碎片里反向按住林渊指腹。", "录音笔里多出一个没有人在场的呼吸声。", "缺角铜钱背面那道指甲刻日渗出灰水。", "碎片灰里浮出半个林字，像在账本上给他开了一页。"],
            "images": ["碎片边缘像账页烧剩的灰", "录音笔红点一明一灭", "缺角铜钱背面有父亲用指甲反复抠出的细小日期", "碎片灰里露出一枚新页码"],
            "sounds": ["证物袋轻轻鼓起", "王建业最后十秒的喘息倒灌出来", "记忆里旧罗盘压上桌面时发出闷响", "账页翻动声停在林字前"],
            "touches": ["碎片边缘割手", "录音笔外壳发冷", "缺角背面的刻痕硌进林渊指腹", "林渊指腹失去知觉"],
        },
        8: {
            "titles": ["太平间冷柜", "提前七分钟的尸牌", "镜中尸体开口", "苏婉宁半步相信"],
            "types": ["evidence", "investigation", "horror", "character"],
            "purposes": [
                f"太平间开场，把假王建业和现实死亡记录冲突摆出来：{conflict}",
                "尸牌时间早于死亡时间七分钟，迫使苏婉宁承认记录被污染。",
                f"镜中尸体说话，证明：{revealed[0]}。",
                f"苏婉宁选择和林渊共同判断尸体真假：{hook}",
            ],
            "entries": ["太平间冷柜灯一排排亮起。", "苏婉宁把尸牌和法医记录并排放在钢台上。", "林渊站在不锈钢柜门前，不看尸体，只看倒影。", "冷柜门半开，白雾贴着地面爬。"],
            "exits": ["假王建业尸袋编号和记录对不上。", "苏婉宁发现尸牌号码提前七分钟。", f"{revealed[0]}被证实，{withheld[0]}仍被隐藏。", "苏婉宁把钥匙递给林渊半寸，又收回半寸。"],
            "hooks": ["冷柜拉开时，尸牌号码比死亡时间早了七分钟。", "尸牌背面多出一枚湿指印。", "镜中尸体张口，说自己还没死。", hook],
            "images": ["尸袋标签在白雾里发灰", "尸牌数字被水汽洇开", "柜门倒影里的尸体睁着眼", hook],
            "sounds": ["冷柜压缩机低鸣", "金属尸牌碰钢台", "镜中尸体的声音像隔着冰", "苏婉宁手套摩擦钥匙圈"],
            "touches": ["冷气扎进袖口", "尸牌冻得粘住手套", "柜门边缘凝着霜", "钥匙柄湿冷"],
        },
        9: {
            "titles": ["明天凌晨的快递", "陈默撕下的寄件联", "热敏条码里的眼", "封口自己合上"],
            "types": ["impossible_evidence", "character", "screen_horror", "suspense"],
            "purposes": [
                f"把快递设为明确不可能证据，而不是现实物流硬伤：{conflict}",
                "陈默为自保主动撕下寄件联藏进袖口，想切断自己删证和快递的联系，但这一动作让小雨半账线收紧。",
                f"快递面单热敏条码扭成一只眼，瞳孔朝十七栋方向偏转，证明快递异常背后有人维持秩序：{revealed[0]}。",
                f"快递封口自己合上，幕后秩序者逼近：{hook}",
            ],
            "entries": ["快递单放在门口，寄件时间写着明天凌晨。", "陈默蹲下去拆封时，手指先摸到寄件联边角。", "陈默把快递面单压在手机背面，热敏条码开始褪色。", "纸箱里空空的，只剩一张潮湿回执。"],
            "exits": ["林渊让所有人看清：这不是正常派送。", "陈默把撕下的寄件联塞进袖口，小雨手腕黑线随封口收紧。", f"{revealed[0]}被证实，{withheld[0]}仍不命名。", "快递封口从里面自己粘回去。"],
            "hooks": ["寄件时间是明天凌晨，派送状态却显示已签收。", "陈默说：我没寄，可他袖口里多了一截湿寄件联。", "快递面单的条码扭成一只眼，瞳孔朝十七栋方向歪过去。", hook],
            "images": ["快递单时间栏洇着黑水", "湿寄件联贴在陈默袖口内侧，字迹反着透出来", "热敏条码扭成一只朝十七栋偏转的黑眼", hook],
            "sounds": ["纸箱胶带自己轻轻翘起", "寄件联被撕开时声音像揭皮", "热敏纸褪色时发出细小噼啪声", "胶带啪一声重新合上"],
            "touches": ["快递单湿得像刚被雨淋过", "湿寄件联黏住陈默袖口", "热敏纸边缘卷得发硬", "胶带黏住陈默指尖"],
        },
        10: {
            "titles": ["删证视频自播", "陈默交出的寄件联", "三分钟回303", "对称半账线"],
            "types": ["evidence", "character", "countdown", "suspense"],
            "purposes": [
                "自动播放的视频把陈默的删证否认推到小高潮。",
                "陈默交出第9章藏起的寄件联，承认删证和藏证，只承担证据半账。",
                f"三分钟回303，小雨说不想让别人替她担着，林渊不动用铜钱处理危机，兑现：{revealed[0]}。",
                "陈默手腕浮出对称半账线，门缝回执写着306已结清又被划掉。",
            ],
            "entries": ["陈默手机在没有触碰的情况下自动亮起。", "小雨靠墙站着，手腕黑线只剩一道缺口，陈默袖口里湿寄件联反着透字。", "林渊看一眼时间，离半账闭合只剩三分钟，小雨先开口说别替我担。", "他们冲回303门口，门牌开始滴水。"],
            "exits": ["删掉的视频完整播出。", "陈默主动把湿寄件联和手机一起递给苏婉宁，说自己只认删证和藏证这一半。", f"{revealed[0]}被证实，{withheld[0]}留给第11章。", "陈默手腕浮出和小雨对称的半圈黑线，门缝里一张回执写着306已结清又被划掉。"],
            "hooks": ["手机自动播放陈默删除的视频。", "陈默说：这联是我藏的，我认这一半。", "三分钟内必须回303，否则小雨半账闭合。", hook],
            "images": ["视频里王建业回头看向拍摄者", "湿寄件联贴在陈默掌心，号码反着印进皮肤", "电梯数字停在3不动", "两只手腕上的半圈黑线隔空对齐，门缝回执上306已结清被划掉"],
            "sounds": ["视频外放杂音刺耳", "湿纸从袖口抽出时轻轻一响", "楼道里倒计时滴答", "303门内传出小雨的笑声"],
            "touches": ["手机外壳烫得不合理但林渊不碰它", "湿寄件联贴得陈默掌心发皱", "林渊掌心旧伤裂开但没有拿铜钱", "陈默手腕黑线像细线勒进肉里"],
        },
    }
    variation = variations[chapter_number]
    scene_items: list[dict[str, Any]] = []
    for index in range(4):
        scene_items.append(
            {
                "title": variation["titles"][index],
                "type": variation["types"][index],
                "time": f"第{chapter_number}章-{index + 1}",
                "participants": participants_by_chapter[chapter_number],
                "purpose": variation["purposes"][index],
                "emotion": _scene_emotion_task(
                    chapter_number=chapter_number,
                    scene_number=index + 1,
                    scene_title=variation["titles"][index],
                    scene_type=variation["types"][index],
                    focus=focus_by_chapter[chapter_number][index],
                ),
                "entry": variation["entries"][index],
                "exit": variation["exits"][index],
                "hook": variation["hooks"][index],
                "sensory": {
                    "sight": variation["images"][index],
                    "sound": variation["sounds"][index],
                    "touch": variation["touches"][index],
                },
                "contract": _scene_contract(
                    stakes=conflict if index == 0 else variation["purposes"][index],
                    pressure=[
                        variation["hooks"][index],
                        "人物选择会改变下一场可用证据",
                        "规则只能通过本场动作显影，不能靠旁白讲解",
                    ],
                    focus=focus_by_chapter[chapter_number][index],
                    hook=_scene_hook_type(
                        chapter_number=chapter_number,
                        scene_number=index + 1,
                    ),
                    reveal="本场只兑现一个可见事实，并保留下一层解释。",
                    image=variation["images"][index],
                    cut=variation["hooks"][index],
                    relationship=_scene_relationship_debt(
                        chapter_number=chapter_number,
                        scene_number=index + 1,
                        scene_title=variation["titles"][index],
                        focus=focus_by_chapter[chapter_number][index],
                    ),
                ),
            }
        )
    return scene_items


def _scene_hook_type(*, chapter_number: int, scene_number: int) -> str:
    overrides = {
        (5, 2): "情绪反转钩子",
        (6, 1): "市井反差钩子",
        (7, 3): "父亲记忆钩子",
        (9, 2): "自保选择钩子",
        (9, 3): "秩序视线钩子",
        (10, 2): "主动认错钩子",
    }
    if (chapter_number, scene_number) in overrides:
        return overrides[(chapter_number, scene_number)]
    return (
        "物证钩子"
        if scene_number == 1
        else "人物选择"
        if scene_number == 2
        else "规则显影"
        if scene_number == 3
        else "章末钩子"
    )


_extend_front10()


def _clean_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    cleaned = dict(metadata or {})
    for key in STALE_METADATA_KEYS:
        cleaned.pop(key, None)
    cleaned["front10_framework_repair_source"] = SOURCE
    cleaned["front10_framework_repaired_at"] = datetime.now(UTC).isoformat()
    return cleaned


def _repair_stamp() -> dict[str, Any]:
    return {
        "front10_framework_repair_source": SOURCE,
        "front10_framework_repaired_at": datetime.now(UTC).isoformat(),
    }


def _chapter_supporting_active_choices(chapter_number: int) -> list[str]:
    choices = {
        5: ["小雨为自保主动隐瞒证物袋同步指纹，但不能理解专业规则。"],
        9: ["陈默为自保主动藏起寄送凭证，让小雨半账线收紧。"],
        10: ["陈默主动交出藏起的凭证，只承认自己造成的证据半账。"],
    }
    return choices.get(chapter_number, [])


async def _sync_chapter_contract(
    session,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    spec: dict[str, Any],
) -> None:
    contract = await session.scalar(
        select(ChapterContractModel).where(
            ChapterContractModel.project_id == project.id,
            ChapterContractModel.chapter_id == chapter.id,
        )
    )
    if contract is None:
        contract = ChapterContractModel(
            project_id=project.id,
            chapter_id=chapter.id,
            chapter_number=chapter.chapter_number,
            contract_summary=spec["goal"],
            opening_state={"opening_situation": spec["opening"]},
            core_conflict=spec["conflict"],
            emotional_shift=spec["emotion"],
            information_release="；".join(spec["revealed"]),
            closing_hook=spec["hook_description"],
            primary_arc_codes=["main_plot", "mystery_arc", "growth_arc"],
            supporting_arc_codes=[],
            active_arc_beat_ids=[],
            planted_clue_codes=[],
            due_payoff_codes=[],
            metadata_json=_repair_stamp(),
        )
        session.add(contract)
        return

    contract.contract_summary = spec["goal"]
    contract.opening_state = {"opening_situation": spec["opening"]}
    contract.core_conflict = spec["conflict"]
    contract.emotional_shift = spec["emotion"]
    contract.information_release = "；".join(spec["revealed"])
    contract.closing_hook = spec["hook_description"]
    contract.primary_arc_codes = ["main_plot", "mystery_arc", "growth_arc"]
    contract.supporting_arc_codes = []
    contract.metadata_json = {**(contract.metadata_json or {}), **_repair_stamp()}


async def _sync_chapter_arc_beats(
    session,
    *,
    project: ProjectModel,
    chapter_number: int,
    spec: dict[str, Any],
) -> int:
    beats = (
        await session.scalars(
            select(ArcBeatModel).where(
                ArcBeatModel.project_id == project.id,
                ArcBeatModel.scope_chapter_number == chapter_number,
            )
        )
    ).all()
    for beat in beats:
        beat.title = spec["title"]
        beat.summary = spec["goal"]
        beat.information_release = "；".join(spec["revealed"])
        beat.expected_payoff = spec["hook_description"]
        beat.emotional_shift = spec["emotion"]
        beat.status = "planned"
        beat.metadata_json = {**(beat.metadata_json or {}), **_repair_stamp()}
    return len(beats)


async def _purge_current_chapter_derived_context(
    session,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    scene_ids: list[Any],
) -> dict[str, int]:
    counts = {
        "timeline_events": 0,
        "reader_knowledge_entries": 0,
        "relationship_events": 0,
        "scene_summary_facts": 0,
        "retrieval_chunks": 0,
    }
    counts["timeline_events"] = (
        await session.execute(
            delete(TimelineEventModel).where(
                TimelineEventModel.project_id == project.id,
                TimelineEventModel.chapter_id == chapter.id,
            )
        )
    ).rowcount or 0
    counts["reader_knowledge_entries"] = (
        await session.execute(
            delete(ReaderKnowledgeEntryModel).where(
                ReaderKnowledgeEntryModel.project_id == project.id,
                ReaderKnowledgeEntryModel.chapter_number == chapter.chapter_number,
            )
        )
    ).rowcount or 0
    counts["relationship_events"] = (
        await session.execute(
            delete(RelationshipEventModel).where(
                RelationshipEventModel.project_id == project.id,
                RelationshipEventModel.chapter_number == chapter.chapter_number,
            )
        )
    ).rowcount or 0
    if scene_ids:
        counts["scene_summary_facts"] = (
            await session.execute(
                delete(CanonFactModel).where(
                    CanonFactModel.project_id == project.id,
                    CanonFactModel.fact_type == "scene_summary",
                    CanonFactModel.subject_id.in_(scene_ids),
                )
            )
        ).rowcount or 0
        counts["retrieval_chunks"] = (
            await session.execute(
                delete(RetrievalChunkModel).where(
                    RetrievalChunkModel.project_id == project.id,
                    RetrievalChunkModel.source_id.in_(scene_ids),
                )
            )
        ).rowcount or 0
    counts["retrieval_chunks"] += (
        await session.execute(
            delete(RetrievalChunkModel).where(
                RetrievalChunkModel.project_id == project.id,
                RetrievalChunkModel.source_id == chapter.id,
            )
        )
    ).rowcount or 0
    return counts


async def run(
    *,
    apply: bool,
    force_fresh_drafts: bool,
    chapters: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    settings = load_settings()
    selected_chapters = chapters or tuple(CHAPTER_RANGE)
    report: dict[str, Any] = {
        "project_slug": PROJECT_SLUG,
        "applied": apply,
        "force_fresh_drafts": force_fresh_drafts,
        "selected_chapters": list(selected_chapters),
        "chapters": [],
        "archived_current_drafts": {"chapters": 0, "scenes": 0},
        "superseded_rewrite_tasks": 0,
        "synced_arc_beats": 0,
        "purged_current_chapter_context": {},
    }
    async with session_scope(settings) as session:
        project = (
            await session.scalars(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
        ).one()
        project_metadata = dict(project.metadata_json or {})
        snapshot = project_metadata.get(SNAPSHOT_KEY)
        if not isinstance(snapshot, dict):
            snapshot = {
                "created_at": datetime.now(UTC).isoformat(),
                "chapters": {},
                "current_chapter_draft_ids": [],
                "current_scene_draft_ids": [],
            }

        for chapter_number in selected_chapters:
            spec = FRONT10[chapter_number]
            chapter = (
                await session.scalars(
                    select(ChapterModel).where(
                        ChapterModel.project_id == project.id,
                        ChapterModel.chapter_number == chapter_number,
                    )
                )
            ).one()
            chapter_snapshot = snapshot["chapters"].setdefault(
                str(chapter_number),
                {
                    "status": chapter.status,
                    "production_state": chapter.production_state,
                    "current_word_count": chapter.current_word_count,
                },
            )
            scenes = (
                await session.scalars(
                    select(SceneCardModel)
                    .where(SceneCardModel.chapter_id == chapter.id)
                    .order_by(SceneCardModel.scene_number)
                )
            ).all()
            scenes_by_number = {scene.scene_number: scene for scene in scenes}
            before = {
                "title": chapter.title,
                "status": chapter.status,
                "production_state": chapter.production_state,
                "scene_count": len(scenes),
            }

            if apply:
                decision_protocol = _decision_protocol(
                    chapter_number,
                    spec["methodology"]["visible_action_or_reaction"],
                )
                methodology_contract = {
                    **spec["methodology"],
                    "decision_protocol": decision_protocol,
                    "relationship_debt_protocol": {
                        "schema": "debtor-creditor-evidence-due-breach-repayment",
                        "required_per_scene": True,
                        "chapter_debts": spec["methodology"]["relationship_debts"],
                    },
                    "agency_contract": {
                        "protagonist": decision_protocol,
                        "supporting_active_choices": _chapter_supporting_active_choices(
                            chapter_number
                        ),
                    },
                }
                chapter.title = spec["title"]
                chapter.chapter_goal = spec["goal"]
                chapter.opening_situation = spec["opening"]
                chapter.main_conflict = spec["conflict"]
                chapter.hook_type = spec["hook_type"]
                chapter.hook_description = spec["hook_description"]
                chapter.information_revealed = spec["revealed"]
                chapter.information_withheld = spec["withheld"]
                chapter.chapter_emotion_arc = spec["emotion"]
                chapter.target_word_count = CHAPTER_TARGET_WORD_COUNT
                chapter.current_word_count = 0
                chapter.status = "planned"
                chapter.production_state = "pending"
                chapter.foreshadowing_actions = {
                    "keep_front10_terms_slim": True,
                    "forbidden_early_leaks": [
                        "扣账人",
                        "母镜",
                        "源门",
                        "林正淳",
                        "林远山",
                        "林家辉",
                        "归人",
                        "入门",
                        "代父",
                        "困魂镜",
                        "祖父",
                        "爷爷",
                        "七号入账",
                        "号入账",
                        "第三十七号",
                        "第三十八号",
                        "张家门契",
                        "三代以内",
                        "血债血偿",
                        "七人完整名单",
                        "七个人影",
                        "七个模糊的人形",
                        "第七面",
                        "第八个",
                        "七行名单",
                        "八个人影",
                        "病号服",
                        "玩家",
                        "副本",
                        "游戏",
                    ],
                }
                chapter_causal_contract = {
                    "chapter_function": "golden_three" if chapter_number <= 3 else "front_ten",
                    "disturbance": spec["opening"],
                    "pressure": spec["conflict"],
                    "protagonist_choice": decision_protocol,
                    "visible_action_or_reaction": spec["methodology"]["visible_action_or_reaction"],
                    "cost_or_tradeoff": _chapter_cost(chapter_number),
                    "gain_or_reveal": "；".join(spec["revealed"]),
                    "state_change": spec["emotion"],
                    "next_reader_desire": spec["hook_description"],
                }
                chapter.metadata_json = {
                    **_repair_stamp(),
                    "key_reveals": spec["revealed"],
                    "causal_contract": chapter_causal_contract,
                    "event_cycle_contract": {
                        "disturbance": spec["opening"],
                        "choice": spec["methodology"]["visible_action_or_reaction"],
                        "decision_protocol": decision_protocol,
                        "resistance": spec["conflict"],
                        "cost": chapter_causal_contract["cost_or_tradeoff"],
                        "payoff": chapter_causal_contract["gain_or_reveal"],
                        "turn": spec["hook_description"],
                        "visible_action_or_reaction": spec["methodology"]["visible_action_or_reaction"],
                    },
                    "object_signal_contract": {
                        "rule": (
                            "物件异常必须有稳定含义和限制，不能反复用发烫替代推理；"
                            "缺角铜钱前十章只能作为稀缺资源，ch1压镜脚损耗、"
                            "ch2只作旧物和代价提示不得主动触碰救人、ch7主动听回执后主动使用次数归零。"
                        ),
                        "chapter_mode": spec.get("object_signal", {}).get("chapter_mode"),
                        "allowed_signals": spec.get("object_signal", {}).get("allowed_signals", []),
                        "preferred_signals": spec.get("object_signal", {}).get(
                            "allowed_signals",
                            ["冷感定位", "缺角代价", "血点接触", "镜影错位"],
                        ),
                        "forbidden_shortcut": "不得把铜钱/青囊/罗盘的一切异常都写成发烫。",
                        "forbidden_signals": spec.get("object_signal", {}).get(
                            "forbidden_signals",
                            ["铜钱发烫"],
                        ),
                        "copper_coin_active_uses_remaining_after_chapter": 0
                        if chapter_number >= 7
                        else max(0, 7 - chapter_number),
                    },
                    "methodology_contract": methodology_contract,
                    "methodology_application_contract": _methodology_application_contract(
                        chapter_number,
                        {**spec, "methodology": methodology_contract},
                    ),
                    "front10_regen_chapter_snapshot": chapter_snapshot,
                    "framework_regeneration_candidate": True,
                }
                await _sync_chapter_contract(
                    session,
                    project=project,
                    chapter=chapter,
                    spec=spec,
                )
                report["synced_arc_beats"] += await _sync_chapter_arc_beats(
                    session,
                    project=project,
                    chapter_number=chapter_number,
                    spec=spec,
                )

            for index, scene_spec in enumerate(spec["scenes"], start=1):
                scene = scenes_by_number.get(index)
                if scene is None:
                    if not apply:
                        continue
                    scene = SceneCardModel(
                        project_id=project.id,
                        chapter_id=chapter.id,
                        scene_number=index,
                        scene_type=scene_spec["type"],
                        participants=[],
                        purpose={},
                        entry_state={},
                        exit_state={},
                        key_dialogue_beats=[],
                        sensory_anchors={},
                        forbidden_actions=[],
                        target_word_count=SCENE_TARGET_WORD_COUNT,
                        status="planned",
                        metadata_json={},
                    )
                    session.add(scene)
                if apply:
                    scene.scene_type = scene_spec["type"]
                    scene.title = scene_spec["title"]
                    scene.time_label = scene_spec["time"]
                    scene.participants = scene_spec["participants"]
                    scene.purpose = {
                        "story": scene_spec["purpose"],
                        "emotion": scene_spec.get(
                            "emotion",
                            _scene_emotion_task(
                                chapter_number=chapter_number,
                                scene_number=index,
                                scene_title=scene_spec["title"],
                                scene_type=scene_spec["type"],
                                focus=scene_spec["contract"]["spotlight_character"],
                            ),
                        ),
                        "character_delta": scene_spec.get(
                            "character_delta",
                            "信任、恐惧、愧疚或隐瞒必须具体变化。",
                        ),
                        "reader_hook": scene_spec["hook"],
                        "commercial_function": "留存：物证+人物压力+退出钩子",
                    }
                    scene.entry_state = {"state": scene_spec["entry"]}
                    scene.exit_state = {"state": scene_spec["exit"]}
                    scene.key_dialogue_beats = [
                        "对白必须短而有身份差异，禁止同一冷峻腔",
                        "每场至少一句来自角色处境的自然口语，不用术语解释规则",
                    ]
                    scene.sensory_anchors = scene_spec["sensory"]
                    scene.forbidden_actions = [
                        "不得出现玩家/副本/通关/系统",
                        "不得提前说扣账人、母镜、源门、林正淳、林远山、林家辉",
                        "不得用旁白讲术语表替代动作验证",
                        *CHAPTER_EXTRA_FORBIDDEN_ACTIONS.get(chapter_number, []),
                        *scene_spec.get("forbidden_actions", []),
                    ]
                    scene.hook_requirement = scene_spec["hook"]
                    scene.target_word_count = SCENE_TARGET_WORD_COUNT
                    scene.status = "planned"
                    scene.metadata_json = {
                        **_repair_stamp(),
                        "methodology_contract": scene_spec["contract"],
                        "scene_contract": {
                            "visible_object": scene_spec["contract"]["signature_image"],
                            "character_pressure": scene_spec["contract"]["conflict_stakes"],
                            "exit_hook": scene_spec["hook"],
                        },
                        "front10_framework_regeneration": True,
                    }

            if apply and force_fresh_drafts:
                current_chapter_drafts = (
                    await session.scalars(
                        select(ChapterDraftVersionModel).where(
                            ChapterDraftVersionModel.chapter_id == chapter.id,
                            ChapterDraftVersionModel.is_current.is_(True),
                        )
                    )
                ).all()
                for draft in current_chapter_drafts:
                    snapshot["current_chapter_draft_ids"].append(str(draft.id))
                    draft.is_current = False
                    report["archived_current_drafts"]["chapters"] += 1

                scene_ids = [scene.id for scene in scenes_by_number.values()]
                purged_counts = await _purge_current_chapter_derived_context(
                    session,
                    project=project,
                    chapter=chapter,
                    scene_ids=scene_ids,
                )
                for key, value in purged_counts.items():
                    report["purged_current_chapter_context"][key] = (
                        report["purged_current_chapter_context"].get(key, 0) + value
                    )
                if scene_ids:
                    current_scene_drafts = (
                        await session.scalars(
                            select(SceneDraftVersionModel).where(
                                SceneDraftVersionModel.scene_card_id.in_(scene_ids),
                                SceneDraftVersionModel.is_current.is_(True),
                            )
                        )
                    ).all()
                    for draft in current_scene_drafts:
                        snapshot["current_scene_draft_ids"].append(str(draft.id))
                        draft.is_current = False
                        report["archived_current_drafts"]["scenes"] += 1

                pending_tasks = (
                    await session.scalars(
                        select(RewriteTaskModel).where(
                            RewriteTaskModel.project_id == project.id,
                            RewriteTaskModel.trigger_source_id == chapter.id,
                            RewriteTaskModel.status.in_(("pending", "queued")),
                        )
                    )
                ).all()
                for task in pending_tasks:
                    task.status = "superseded"
                    task.metadata_json = {
                        **(task.metadata_json or {}),
                        "superseded_by": SOURCE,
                        "superseded_at": datetime.now(UTC).isoformat(),
                    }
                    report["superseded_rewrite_tasks"] += 1

            report["chapters"].append(
                {
                    "chapter": chapter_number,
                    "before": before,
                    "after": {
                        "title": spec["title"],
                        "scene_count": 4,
                        "target_word_count": CHAPTER_TARGET_WORD_COUNT,
                    },
                }
            )

        if apply:
            project_metadata[SNAPSHOT_KEY] = snapshot
            project_metadata["front10_framework_inputs_repaired_at"] = datetime.now(UTC).isoformat()
            project_metadata["front10_framework_regeneration_required"] = True
            project.metadata_json = project_metadata

    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--force-fresh-drafts",
        action="store_true",
        help="Archive current front-10 draft flags so the chapter pipeline creates fresh drafts.",
    )
    parser.add_argument(
        "--chapters",
        help="Comma-separated front-10 chapters or ranges to repair, for example 2 or 1-3,7.",
    )
    args = parser.parse_args()
    chapters = _chapter_numbers_from_arg(args.chapters)
    print(
        json.dumps(
            asyncio.run(
                run(
                    apply=args.apply,
                    force_fresh_drafts=args.force_fresh_drafts,
                    chapters=chapters,
                )
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
