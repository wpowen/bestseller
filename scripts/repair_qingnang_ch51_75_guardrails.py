"""Install ch51-75 recovery guardrails and rewrite tasks for 《青囊不语问阴阳》."""

from __future__ import annotations

# ruff: noqa: E501
import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select, update

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bestseller.infra.db.models import ChapterModel, ProjectModel, RewriteTaskModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402

PROJECT_SLUG = "exorcist-detective-1778051012"
REPAIR_SOURCE = "qingnang_ch51_75_recovery_20260523"

FORBIDDEN_TERM_ADDITIONS: list[dict[str, str]] = [
    {
        "term": "玩家",
        "reason": "死亡游戏/副本框架词，当前正典应保持民俗探案与镜账语义，不能把读者拉到游戏系统。",
        "suggestion": "入局者、欠账人、参与者、认账人",
    },
    {
        "term": "源代码",
        "reason": "游戏/系统漂移词，破坏青囊、镜纹、账页这些既有物件体系。",
        "suggestion": "账页底纹、镜纹底账、术法纹路、旧印纹",
    },
    {
        "term": "试炼通关",
        "reason": "游戏化结算词，不能作为三族契约或镜局规则的正典表达。",
        "suggestion": "过门槛、认账通过、暂获资格、过账",
    },
    {
        "term": "镜主候选",
        "reason": "不得作为稳定身份提前正典化；ch51-75 只可作为镜影伪造称谓或误导线索。",
        "suggestion": "镜影伪称、伪执镜人、假账名、被镜影点名的人",
    },
]

STATE_RULE_ADDITIONS: list[dict[str, Any]] = [
    {
        "subject": "沈家旧卷",
        "status": "ch62 起只能作为镜侧伪证/待核旧卷，不是稳定第四族，也不能凌驾三族契约。",
        "applies_after_chapter": 51,
        "forbidden_patterns": [
            "沈家.{0,30}(第四族|三族之上|真正主族|凌驾三族)",
            "沈家旧卷.{0,30}(确认|证明|坐实).{0,20}(第四族|镜主)",
            "沈家.{0,20}(取代|覆盖).{0,20}三族契约",
        ],
        "reason": "ch51-75 的沈家线必须被重框为镜影制造的身份战材料，不能追认为新世界观中枢。",
        "allowed_next": "只能通过物证疑点、镜面伪造痕迹、钱婆婆或张家后人的否认来推进。",
    },
    {
        "subject": "镜主/试炼",
        "status": "ch69-71 只能作为镜影伪造身份审查或认账门槛，不是游戏系统，也不是主角稳定升级线。",
        "applies_after_chapter": 51,
        "forbidden_patterns": [
            "镜主候选",
            "试炼通关",
            "源代码",
            "通关奖励",
            "系统提示",
            "权限解锁",
        ],
        "reason": "镜局规则应落在青囊、罗盘、铜钱、账页、契约代价上，不能漂移成系统流。",
        "allowed_next": "可写为镜影伪称、认账门槛、镜债临时转移、伪执卷人的诱导。",
    },
]

RECOVERY_CONTRACT_MD = """# ch51-75 Recovery Contract

适用书目：《青囊不语问阴阳》

适用范围：第 51-75 章，以及任何承接这段内容的新章大纲、重写任务、门禁审查。

## 恢复决策

采用 Option C：ch51-75 不整体作废，也不把“城南旧事馆 / 镜中城 / 沈家旧卷 / 镜主试炼”完整追认为稳定正典。它们被吸纳为“镜影伪造的身份战支线”。

执行含义：

- ch51-75 的表层事件可以保留为林渊经历过的镜侧迷局。
- 这段内容的世界观解释权必须回收给既有三族契约、青囊秘卷、罗盘、铜钱、镜账体系。
- “沈家”只能作为镜侧伪证、待核旧卷或诱导林渊误认身份的材料，不得成为凌驾三族的新主族。
- “镜主候选 / 试炼通关 / 源代码 / 玩家”等游戏化词必须从正典叙述中剥离，改为镜影伪称、账页底纹、认账门槛、入局者等民俗探案语义。

## 必须修复的桥

1. ch50 -> ch51 必须补“审讯室到自己房间”的释放/转移/镜侧过门桥，不能从被铐状态直接切新案。
2. ch51 必须回钩 README 和第二卷启动要求中的“林家老宅井口 / 父亲半卷青囊 / 真执卷人身份战”，城南旧事馆不能单独开新书。
3. ch62 的“玩家”与沈家旧卷必须改写为镜影诱导语或外来伪证，现场人物要质疑这个词的来源。
4. ch64 的身世揭露必须降级为“镜侧血痕/账印误导”，不能写成稳定血脉确认。
5. ch69-71 的试炼、镜主、源代码表达必须改写成青囊、罗盘、铜钱、账页可验证的物件动作和代价选择。

## 后续写作硬约束

- 新章 prompt 必须读取 `story-bible/kernels/`、`continuity-ledger.md`、`event-state-ledger.md` 最近 5 章 delta 和本文件。
- 第二卷恢复期的章节必须显式标注：哪些是“林渊确认过的现实物证”，哪些是“镜影给出的说法”。
- 任何新增家族、组织、称谓、地点，都必须先登记 bible/ledger，再进入章节正文。
"""

REWRITE_TARGETS: list[dict[str, Any]] = [
    {
        "chapter_number": 51,
        "priority": 1,
        "title": "补 ch50->ch51 过桥，并把城南旧事馆纳入老宅井口线",
        "reasons": ["ch50_to_ch51_state_gap", "volume_2_opening_drift"],
        "instructions": "重写第51章开头 600-900 字：交代林渊如何从审讯室被释放、转移或被镜侧过门带回房间；电话/新案必须带出林家老宅井口、父亲半卷青囊、真执卷人身份战，城南旧事馆只能作为这条线的支证入口。",
    },
    {
        "chapter_number": 62,
        "priority": 1,
        "title": "移除玩家词，并把沈家旧卷降级为镜侧伪证",
        "reasons": ["forbidden_game_vocabulary", "unregistered_shen_family"],
        "instructions": "全章检索并替换“玩家”等游戏化词；沈家旧卷不能证明第四族或新主族，只能写为镜影抛出的伪证/待核旧卷。必须让林渊、苏婉宁或钱婆婆之一指出这个词不属于三族账法。",
    },
    {
        "chapter_number": 63,
        "priority": 1,
        "title": "镜主候选改成镜影伪称，不能坐实身份升级",
        "reasons": ["mirror_lord_candidate_drift", "canon_state_regression"],
        "instructions": "把“镜主候选人/镜主候选”等称谓改写为镜影伪称、假执卷名或旧账诱导。可以保留父亲替林渊抵债的危机，但必须明确这是待核说法，不能确认父亲或林渊拥有稳定镜主身份。",
    },
    {
        "chapter_number": 64,
        "priority": 2,
        "title": "身世揭露降级为镜侧误导，不做血脉确认",
        "reasons": ["premature_identity_confirmation", "lineage_kernel_violation"],
        "instructions": "把血脉确认、镜主身份等确定性表达改成镜纹、旧账、血痕、铜钱反应造成的误导；结尾保留身份危机，但答案必须推迟到老宅井口与半卷青囊验证。",
    },
    {
        "chapter_number": 69,
        "priority": 2,
        "title": "试炼语义改为认账门槛和镜债代价",
        "reasons": ["trial_game_semantics", "ethical_dilemma_not_landed"],
        "instructions": "把试炼、通关、权限等词改为认账门槛、过账、镜债临时转移；必须落到青囊秘卷、罗盘、铜钱、账页的物件动作上，并写清短期救人和长期镜债之间的两难。",
    },
    {
        "chapter_number": 71,
        "priority": 1,
        "title": "清除镜主候选/源代码，并封存为镜影伪称",
        "reasons": ["mirror_lord_candidate_drift", "source_code_vocabulary"],
        "instructions": "删除或改写“镜主候选”“源代码”“试炼通关”等表达；保留镜影诱导林渊认领身份的危机，但必须由物证反驳或留下待核状态。结尾要回钩老宅井口与父亲半卷青囊。",
    },
]


def _entry_term(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        return str(entry.get("term") or "").strip()
    return ""


def merge_forbidden_terms(raw_terms: list[Any], additions: list[dict[str, str]]) -> list[Any]:
    merged = list(raw_terms)
    index_by_term = {_entry_term(entry): idx for idx, entry in enumerate(merged) if _entry_term(entry)}
    for addition in additions:
        term = addition["term"]
        if term in index_by_term:
            existing = merged[index_by_term[term]]
            merged[index_by_term[term]] = {**(existing if isinstance(existing, dict) else {"term": term}), **addition}
        else:
            index_by_term[term] = len(merged)
            merged.append(dict(addition))
    return merged


def merge_state_rules(raw_rules: list[Any], additions: list[dict[str, Any]]) -> list[Any]:
    merged = list(raw_rules)
    index_by_subject = {
        str(entry.get("subject") or "").strip(): idx
        for idx, entry in enumerate(merged)
        if isinstance(entry, dict) and str(entry.get("subject") or "").strip()
    }
    for addition in additions:
        subject = addition["subject"]
        if subject in index_by_subject:
            existing = merged[index_by_subject[subject]]
            merged[index_by_subject[subject]] = {**(existing if isinstance(existing, dict) else {}), **addition}
        else:
            index_by_subject[subject] = len(merged)
            merged.append(dict(addition))
    return merged


def merge_guardrails(raw_guardrails: dict[str, Any]) -> dict[str, Any]:
    merged = dict(raw_guardrails)
    merged["forbidden_terms"] = merge_forbidden_terms(
        list(merged.get("forbidden_terms") or []),
        FORBIDDEN_TERM_ADDITIONS,
    )
    merged["state_rules"] = merge_state_rules(
        list(merged.get("state_rules") or []),
        STATE_RULE_ADDITIONS,
    )
    return merged


def build_recovery_contract_text() -> str:
    return RECOVERY_CONTRACT_MD.rstrip() + "\n"


async def _load_project_and_chapters() -> tuple[ProjectModel, dict[int, ChapterModel]]:
    settings = load_settings()
    async with session_scope(settings) as session:
        project = (await session.scalars(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))).one()
        chapters = (
            await session.scalars(
                select(ChapterModel)
                .where(ChapterModel.project_id == project.id)
                .where(ChapterModel.chapter_number.in_([target["chapter_number"] for target in REWRITE_TARGETS]))
            )
        ).all()
        return project, {int(chapter.chapter_number): chapter for chapter in chapters}


async def create_rewrite_tasks(*, replace_existing: bool) -> list[str]:
    settings = load_settings()
    created: list[str] = []
    async with session_scope(settings) as session:
        project = (await session.scalars(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))).one()
        if replace_existing:
            await session.execute(
                update(RewriteTaskModel)
                .where(
                    RewriteTaskModel.project_id == project.id,
                    RewriteTaskModel.status.in_(["pending", "queued"]),
                    RewriteTaskModel.metadata_json["repair_source"].as_string() == REPAIR_SOURCE,
                )
                .values(status="superseded")
            )

        chapters = (
            await session.scalars(
                select(ChapterModel)
                .where(ChapterModel.project_id == project.id)
                .where(ChapterModel.chapter_number.in_([target["chapter_number"] for target in REWRITE_TARGETS]))
            )
        ).all()
        chapter_by_no = {int(chapter.chapter_number): chapter for chapter in chapters}
        for target in REWRITE_TARGETS:
            chapter_no = int(target["chapter_number"])
            chapter = chapter_by_no.get(chapter_no)
            if chapter is None:
                continue
            task = RewriteTaskModel(
                project_id=project.id,
                trigger_type="ch51_75_recovery",
                trigger_source_id=chapter.id,
                rewrite_strategy="targeted_edit",
                priority=int(target["priority"]),
                status="pending",
                instructions=(
                    "【ch51-75 恢复契约】\n"
                    "本任务按 story-bible/ch51-75-recovery-contract.md 执行：将 ch51-75 吸纳为镜影伪造的身份战支线，"
                    "不要把游戏化词、沈家第四族、镜主候选或源代码追认为稳定正典。\n\n"
                    f"【任务】{target['title']}\n"
                    f"{target['instructions']}"
                ),
                context_required=[
                    "current_chapter",
                    "prior_chapter_tail",
                    "next_chapter_head",
                    "story_bible",
                    "story_bible/ch51-75-recovery-contract.md",
                    "story_bible/kernels",
                    "continuity_ledger_recent_5",
                    "event_state_ledger_recent_5",
                    "canon_guardrails",
                ],
                metadata_json={
                    "repair_source": REPAIR_SOURCE,
                    "chapter_number": chapter_no,
                    "title": target["title"],
                    "reasons": target["reasons"],
                },
            )
            session.add(task)
            await session.flush()
            created.append(str(task.id))
    return created


async def run(*, apply: bool, replace_existing: bool) -> dict[str, Any]:
    settings = load_settings()
    story_bible_dir = Path(settings.output.base_dir) / PROJECT_SLUG / "story-bible"
    guardrails_path = story_bible_dir / "canon-guardrails.json"
    raw_guardrails = json.loads(guardrails_path.read_text(encoding="utf-8")) if guardrails_path.exists() else {}
    merged_guardrails = merge_guardrails(raw_guardrails)
    contract_path = story_bible_dir / "ch51-75-recovery-contract.md"
    created_tasks: list[str] = []

    if apply:
        story_bible_dir.mkdir(parents=True, exist_ok=True)
        guardrails_path.write_text(json.dumps(merged_guardrails, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        contract_path.write_text(build_recovery_contract_text(), encoding="utf-8")
        created_tasks = await create_rewrite_tasks(replace_existing=replace_existing)

    return {
        "project_slug": PROJECT_SLUG,
        "applied": apply,
        "repair_source": REPAIR_SOURCE,
        "guardrails_path": str(guardrails_path),
        "recovery_contract_path": str(contract_path),
        "forbidden_terms_added": [entry["term"] for entry in FORBIDDEN_TERM_ADDITIONS],
        "state_rules_added": [entry["subject"] for entry in STATE_RULE_ADDITIONS],
        "planned_rewrite_chapters": [target["chapter_number"] for target in REWRITE_TARGETS],
        "created_tasks": created_tasks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--keep-existing", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(apply=args.apply, replace_existing=not args.keep_existing)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
