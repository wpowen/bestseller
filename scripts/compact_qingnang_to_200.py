"""Compact Qingnang's legacy 500-chapter plan to a 200-chapter continuation.

The book already has prose through chapter 85 on disk. This script keeps that
prose intact, rewrites only story-bible planning surfaces, and syncs the DB
project row so the existing progressive autowrite pipeline can continue from
the compact 200-chapter target.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
import sys
from typing import Any

import yaml
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bestseller.infra.db.models import ChapterModel, ProjectModel  # noqa: E402
from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.settings import load_settings  # noqa: E402


PROJECT_SLUG = "exorcist-detective-1778051012"
TARGET_CHAPTERS = 200
WORDS_PER_CHAPTER = 2600


COMPACT_VOLUME_GOALS = {
    1: "十七栋困魂镜局：否认入账、三族旧契入口、父亲半卷线索入门",
    2: "旧城井口与清水桥义庄：半卷青囊、父亲签名伪造、义庄铜镜流转",
    3: "寿数账与张钱两家代价：借命案结算、父亲抵债真相第一层、入终局坐标",
    4: "借脸替身与三族终账：现实身份危机、真正债主落地、青囊最终边界",
}

COMPACT_VOLUME_TITLES = {
    1: "困魂镜局",
    2: "井口义庄",
    3: "寿数旧账",
    4: "三族终账",
}

COMPACT_VOLUME_THEMES = {
    1: "以十七栋镜局建立青囊核账规则和三族旧契入口。",
    2: "把旧城井口、清水桥义庄和父亲签名伪造并成一条证据链。",
    3: "用寿数账和张钱两家的代价揭开父亲抵债真相第一层。",
    4: "用替身身份危机逼出真正债主并完成主账结算。",
}


COMPACT_VOLUME_4_MILESTONES = [
    {
        "chapter_range": [151, 160],
        "milestone_label": "替身案以镜影借脸升级为现实身份风险并指向真正记账人",
        "required_evidence": ["伪监控", "身份记录异常", "借脸破绽", "第二经手人账印"],
        "reveals_unlocked": ["debt_holder_not_mirror"],
        "character_state_promises": [
            "林渊必须用现实证据自证，不得靠青囊直接洗白",
            "镜影林渊从心理威胁升级为程序化伪证源",
        ],
    },
    {
        "chapter_range": [161, 170],
        "milestone_label": "林渊被现实程序误认并反推出三族旧契的活人替认规则",
        "required_evidence": ["警方手续", "指纹差异", "账名反证", "替认回执"],
        "reveals_unlocked": ["living_substitute_rule"],
        "character_state_promises": [
            "苏婉宁必须承担程序压力并提供可复核证据链",
            "孙九斤只能用人脉补证据，不能插科打诨脱险",
        ],
    },
    {
        "chapter_range": [171, 180],
        "milestone_label": "父亲抵债真相揭开核心代价，但终局债主姓名必须用物证验真",
        "required_evidence": ["林正淳旧物", "抵债页", "镜影伪证", "声纹差异"],
        "reveals_unlocked": ["father_debt_core_cost"],
        "character_state_promises": [
            "林正淳不得反派化，真相必须保留牺牲与误账两层",
            "林渊不能把父亲牺牲当作免债理由",
        ],
    },
    {
        "chapter_range": [181, 190],
        "milestone_label": "林张钱三族各自代价互相牵制，完整旧契落地并逼出终账方案",
        "required_evidence": ["林家账印", "张家门契", "钱家改路价码", "三族旧契完整版"],
        "reveals_unlocked": ["three_clan_contract_full"],
        "character_state_promises": [
            "三族功能边界必须清楚：林家记账、张家开门、钱家守镜改路",
            "每一族都要付代价，不能变成免费盟友",
        ],
    },
    {
        "chapter_range": [191, 200],
        "milestone_label": "终局结算证明主角不能用身份逃债，只能以自选代价重定青囊边界",
        "required_evidence": ["主账结算页", "替身回执", "身份恢复文件", "青囊最终边界"],
        "reveals_unlocked": ["qingnang_final_boundary"],
        "character_state_promises": [
            "全书主账必须结算，幸存者状态要明确",
            "结尾保留青囊记因果不替人赎罪的边界，而不是开新大坑",
        ],
    },
]


REVEAL_PATCHES = [
    {
        "id": "debt_holder_not_mirror",
        "earliest_chapter": 155,
        "tokens": ["真正债主不是困魂镜", "第二经手人账印", "镜背记录"],
    },
    {
        "id": "living_substitute_rule",
        "earliest_chapter": 166,
        "tokens": ["活人替认", "替认回执", "账名反证"],
    },
    {
        "id": "father_debt_core_cost",
        "earliest_chapter": 176,
        "tokens": ["林正淳抵债页", "父亲自选代价", "三年前误账"],
    },
    {
        "id": "three_clan_contract_full",
        "earliest_chapter": 185,
        "tokens": ["三族旧契完整版", "林家账印", "张家门契", "钱家改路价码"],
    },
    {
        "id": "qingnang_final_boundary",
        "earliest_chapter": 196,
        "tokens": ["主账结算页", "青囊最终边界", "记因果不替人赎罪"],
    },
]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _write_json(path: Path, payload: dict[str, Any], *, apply: bool) -> None:
    if apply:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_yaml(path: Path, payload: dict[str, Any], *, apply: bool) -> None:
    if apply:
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _compact_volume_plan_v2(story_bible_dir: Path, *, apply: bool) -> dict[str, Any]:
    path = story_bible_dir / "volume-plan-v2.yaml"
    payload = yaml.safe_load(_read_text(path)) or {}
    volumes = payload.get("volumes") if isinstance(payload, dict) else None
    if not isinstance(volumes, list):
        raise ValueError("volume-plan-v2.yaml does not contain volumes")

    compacted = []
    for volume in volumes:
        if not isinstance(volume, dict):
            continue
        volume_no = int(volume.get("volume_no") or volume.get("volume_number") or 0)
        if volume_no < 1 or volume_no > 4:
            continue
        item = dict(volume)
        item["volume_no"] = volume_no
        item["volume_number"] = volume_no
        item["title"] = COMPACT_VOLUME_TITLES[volume_no]
        item["volume_title"] = COMPACT_VOLUME_TITLES[volume_no]
        item["chapter_range"] = [((volume_no - 1) * 50) + 1, volume_no * 50]
        item["chapter_count_target"] = 50
        item["word_count_target"] = 50 * WORDS_PER_CHAPTER
        item["volume_theme"] = COMPACT_VOLUME_THEMES[volume_no]
        item["volume_goal"] = COMPACT_VOLUME_GOALS[volume_no]
        item["volume_obstacle"] = (
            "既有正文已锁定，续写必须以现有证据和人物状态推进，不得改写前文或新增长线体系。"
        )
        item["volume_climax"] = (
            "完成本卷主证据结算并把压力准确交给下一卷。"
            if volume_no < 4
            else "主账结算页、替身回执和青囊最终边界同时落地。"
        )
        item["volume_resolution"] = {
            "goal_achieved": volume_no == 4,
            "cost_paid": "林渊每次破局都必须付出身份、寿数、证据压力或关系代价。",
            "new_threat_introduced": (
                f"第{volume_no + 1}卷压力必须来自已埋证据，不得新增随机怪谈。"
                if volume_no < 4
                else "不再引入200章之后才解决的新主线。"
            ),
        }
        if volume_no == 4:
            item["milestones"] = COMPACT_VOLUME_4_MILESTONES
        compacted.append(item)

    payload["schema_version"] = payload.get("schema_version") or "volume-plan.v2"
    payload["volumes"] = compacted
    payload["compaction_contract"] = {
        "source": "compact_qingnang_to_200",
        "updated_at": datetime.now(UTC).isoformat(),
        "target_chapters": TARGET_CHAPTERS,
        "keep_existing_prose_through_chapter": 85,
        "directive": "Use chapters 86-200 to close the existing mirror-debt story; do not extend to the old 500-chapter roadmap.",
    }
    _write_yaml(path, payload, apply=apply)
    return {"path": str(path), "volume_count": len(compacted)}


def _write_csv(path: Path, header: list[str], rows: list[list[Any]], *, apply: bool) -> None:
    if not apply:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _compact_csv_plans(story_bible_dir: Path, *, apply: bool) -> dict[str, Any]:
    volume_rows = [
        [idx, (idx - 1) * 50 + 1, idx * 50, "closed_review" if idx == 1 else "planned", COMPACT_VOLUME_GOALS[idx]]
        for idx in range(1, 5)
    ]
    batch_rows = [
        ["1A", 1, 50, "已写完；回修必须保留主镜门暂封、半数归人、父亲抵债入门三件事。", "closed_review"],
        ["2A", 51, 100, "收束井口、义庄、父亲签名伪造；禁止回到游戏化试炼词。", "written_to_85_continue_86_100"],
        ["3A", 101, 150, "借命案必须服务寿数账和父亲抵债第一层，不得新增随机怪谈。", "planned"],
        ["4A", 151, 200, "替身案、三族旧契、真正债主与青囊边界必须在本卷结算。", "finale_planned"],
    ]
    _write_csv(story_bible_dir / "volume-plan.csv", ["volume", "start_chapter", "end_chapter", "status", "goal"], volume_rows, apply=apply)
    _write_csv(story_bible_dir / "batch-queue.csv", ["batch", "start_chapter", "end_chapter", "required_callbacks", "status"], batch_rows, apply=apply)
    return {"volume_rows": len(volume_rows), "batch_rows": len(batch_rows)}


def _contract_for_chapter(chapter_no: int) -> dict[str, Any]:
    if chapter_no <= 100:
        objective = "承接义庄铜镜与城南三点定位，完成第2卷井口/义庄证据链收束。"
        evidence = "义庄登记、城南三号地块、井口铜钱、签名笔压差异"
        payoff = "确认义庄铜镜流转路线与父亲名字被冒用的证据，不揭完整父亲真相。"
        next_pressure = "把压力交给寿数账与借命案入口。"
    elif chapter_no <= 150:
        objective = "推进寿数账、张家开门与钱家改路代价，逼近父亲抵债第一层。"
        evidence = "寿数回执、钱家价码、张家门契、第二经手人签名"
        payoff = "每10章至少结算一个单元证据，长线只保留一个明确终局入口。"
        next_pressure = "把压力交给替身案和真实身份危机。"
    else:
        objective = "推进终局替身案、三族旧契和真正债主，确保第200章完成主账结算。"
        evidence = "伪监控、替认回执、三族旧契完整版、主账结算页"
        payoff = "证明林渊不能用身份逃债，只能以自选代价重定青囊边界。"
        next_pressure = "章末只交接到200章内的结算压力，不开新长线。"
    return {
        "title": f"第{chapter_no}章写前合同",
        "prewrite_anchor": f"承接已写到第85章的城南三点定位；本章必须服务200章紧凑收束，不得延回旧500章路线。",
        "chapter_objective": objective,
        "scene_beats": [
            "开章接住上一章具体人、物、地点或倒计时。",
            f"落地本章证据锚：{evidence}。",
            "让林渊通过青囊、罗盘、铜钱、账印或现实封存动作主动拆局。",
            f"完成阶段兑现：{payoff}",
            f"章末交接：{next_pressure}",
        ],
        "required_evidence": evidence,
        "required_payoff": payoff,
        "pressure_handoff": next_pressure,
        "open_question_limit": 2,
        "new_term_budget": 2,
        "hook_contract": "章末钩子必须绑定人名、物证、地点、倒计时或身份反转；不得开启200章后新大坑。",
        "forbidden_moves": [
            "不得让青囊直接替主角解题。",
            "不得新增脱离困魂镜、三族旧契、林正淳半卷青囊的随机怪谈。",
            "不得把终局债主、三族完整版、父亲完整真相提前无证据讲明。",
            "不得延用旧500章路线中的201章以后新案名。",
        ],
        "rewrite_priority": "P1" if chapter_no <= 100 else "P2",
    }


def _extend_prewrite_contract(story_bible_dir: Path, *, apply: bool) -> dict[str, Any]:
    path = story_bible_dir / "prewrite-contract.json"
    payload = json.loads(_read_text(path) or "{}")
    chapters = dict(payload.get("chapters") if isinstance(payload.get("chapters"), dict) else {})
    for chapter_no in range(86, TARGET_CHAPTERS + 1):
        existing = chapters.get(str(chapter_no))
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.update(_contract_for_chapter(chapter_no))
        chapters[str(chapter_no)] = merged
    payload["chapters"] = chapters
    payload["systemic_repair"] = {
        **(payload.get("systemic_repair") if isinstance(payload.get("systemic_repair"), dict) else {}),
        "source": "compact_qingnang_to_200",
        "updated_at": datetime.now(UTC).isoformat(),
        "coverage_chapters": [1, TARGET_CHAPTERS],
        "purpose": "Extend chapter prewrite contracts for a compact 200-chapter completion run.",
    }
    _write_json(path, payload, apply=apply)
    return {"path": str(path), "chapter_count": len(chapters)}


def _patch_reveal_schedule(story_bible_dir: Path, *, apply: bool) -> dict[str, Any]:
    path = story_bible_dir / "reveal-schedule.yaml"
    payload = yaml.safe_load(_read_text(path)) or {}
    reveals = payload.get("reveals") if isinstance(payload, dict) else None
    if not isinstance(reveals, list):
        reveals = []
    normalized = [dict(item) for item in reveals if isinstance(item, dict)]
    by_id = {str(item.get("id") or item.get("reveal_id") or ""): item for item in normalized}
    touched = []
    for patch in REVEAL_PATCHES:
        existing = by_id.get(str(patch["id"]))
        if existing is None:
            normalized.append(dict(patch))
        else:
            existing.update(patch)
        touched.append(str(patch["id"]))
    payload["schema_version"] = payload.get("schema_version") or "reveal-schedule.v1"
    payload["reveals"] = normalized
    _write_yaml(path, payload, apply=apply)
    return {"path": str(path), "patched_reveals": touched}


async def _sync_db(*, apply: bool) -> dict[str, Any]:
    settings = load_settings()
    async with session_scope(settings) as session:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == PROJECT_SLUG))
        if project is None:
            raise ValueError(f"Project not found: {PROJECT_SLUG}")
        max_chapter = int(
            await session.scalar(
                select(func.coalesce(func.max(ChapterModel.chapter_number), 0)).where(
                    ChapterModel.project_id == project.id
                )
            )
            or 0
        )
        metadata = dict(project.metadata_json or {})
        for key in (
            "self_heal_abandoned",
            "self_heal_abandoned_at",
            "self_heal_no_progress_attempts",
            "self_heal_last_chapters_total",
            "production_pause_reason",
            "requires_human_review",
            "generation_resume_blocked_until_repair_audit",
            "production_paused",
            "structural_repair_required",
        ):
            metadata.pop(key, None)
        metadata["target_chapters"] = TARGET_CHAPTERS
        metadata["outline_compaction"] = {
            "source": "compact_qingnang_to_200",
            "updated_at": datetime.now(UTC).isoformat(),
            "previous_db_target_chapters": int(project.target_chapters or 0),
            "existing_chapters_preserved": max_chapter,
            "target_chapters": TARGET_CHAPTERS,
            "directive": "Continue from existing prose; complete the current mirror-debt story by chapter 200.",
        }
        if apply:
            project.target_chapters = TARGET_CHAPTERS
            project.target_word_count = TARGET_CHAPTERS * WORDS_PER_CHAPTER
            project.current_chapter_number = max(max_chapter, int(project.current_chapter_number or 0))
            project.status = "writing"
            project.metadata_json = metadata
            chapters = list(
                await session.scalars(
                    select(ChapterModel).where(ChapterModel.project_id == project.id)
                )
            )
            for chapter in chapters:
                if int(chapter.chapter_number or 0) <= max_chapter:
                    chapter.status = "complete"
                    chapter.production_state = "ok"
            await session.commit()
        return {
            "project_slug": PROJECT_SLUG,
            "target_chapters": TARGET_CHAPTERS,
            "target_word_count": TARGET_CHAPTERS * WORDS_PER_CHAPTER,
            "existing_chapters_preserved": max_chapter,
            "applied": apply,
        }


async def _run(apply: bool) -> dict[str, Any]:
    settings = load_settings()
    package_dir = Path(settings.output.base_dir) / PROJECT_SLUG
    story_bible_dir = package_dir / "story-bible"
    if not story_bible_dir.is_dir():
        raise ValueError(f"Missing story-bible dir: {story_bible_dir}")
    result = {
        "applied": apply,
        "volume_plan_v2": _compact_volume_plan_v2(story_bible_dir, apply=apply),
        "csv_plans": _compact_csv_plans(story_bible_dir, apply=apply),
        "prewrite_contract": _extend_prewrite_contract(story_bible_dir, apply=apply),
        "reveal_schedule": _patch_reveal_schedule(story_bible_dir, apply=apply),
        "db": await _sync_db(apply=apply),
    }
    if apply:
        audit_dir = package_dir / "audits"
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / "compact-to-200-report.json"
        audit_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["audit_path"] = str(audit_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write file and DB changes.")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(apply=args.apply)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
