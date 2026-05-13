from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from bestseller.services.ranking_readiness import (
    build_listing_ip_readiness,
    build_listing_marketing_asset_pack,
)

LISTING_SCHEMA_VERSION = "1.1"
REQUIRED_TITLE_CANDIDATE_COUNT = 20


def _is_english(language: str) -> bool:
    return (language or "").lower().startswith("en")


def _get_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _get_nested(source: Any, *keys: str, default: Any = None) -> Any:
    current = source
    for key in keys:
        current = _get_value(current, key, None)
        if current is None:
            return default
    return current


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _compact_text(value: Any) -> str:
    return " ".join(_clean_text(value).split())


def _limit_chars(value: Any, max_chars: int = 500) -> str:
    text = _compact_text(value)
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def _dedupe_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in result:
            result.append(text)
    return result


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace("，", ",").split(",")]
        return _dedupe_strings(parts)
    if isinstance(value, list | tuple | set):
        return _dedupe_strings(list(value))
    return []


def _character_dict(item: Any) -> dict[str, Any]:
    return {
        "name": _clean_text(_get_value(item, "name")),
        "role": _clean_text(_get_value(item, "role")),
        "identity": _clean_text(_get_value(item, "identity") or _get_value(item, "background")),
        "appeal": _clean_text(
            _get_value(item, "appeal")
            or _get_value(item, "arc_trajectory")
            or _get_value(item, "goal")
        ),
        "goal": _clean_text(_get_value(item, "goal")),
        "arc_state": _clean_text(_get_value(item, "arc_state")),
        "is_pov_character": bool(_get_value(item, "is_pov_character", False)),
    }


def _build_shelf_intro(profile: dict[str, Any], *, max_chars: int = 500) -> str:
    """Build a reader-facing listing intro that is short enough to paste."""
    is_en = _is_english(_clean_text(profile.get("language")))
    title = _clean_text(profile.get("primary_title")) or ("Untitled" if is_en else "未命名作品")
    primary = _clean_text(profile.get("primary_category"))
    secondary = _clean_text(profile.get("secondary_category"))
    tags = _string_list(profile.get("tags"))
    tag_text = (" / ".join(tags[:4]) if is_en else "、".join(tags[:4]))

    preferred = (
        _compact_text(profile.get("short_intro"))
        or _compact_text(profile.get("long_intro"))
        or _compact_text(profile.get("logline"))
    )
    if len(preferred) >= (100 if is_en else 80):
        return _limit_chars(preferred, max_chars)

    pieces: list[str] = []
    if preferred:
        ending = "." if is_en else "。"
        pieces.append(preferred.rstrip("。.!?！？") + ending)
    else:
        if is_en:
            category = " / ".join([item for item in [primary, secondary] if item]) or "commercial fiction"
            pieces.append(f"{title} is a serialized {category} novel built around fast hooks, escalating choices, and chapter-end tension.")
        else:
            category = " / ".join([item for item in [primary, secondary] if item]) or "商业类型"
            pieces.append(f"《{title}》是一部主打{category}的长篇连载，核心看点是高压选择、持续破局和章节尾钩。")

    promo = next((_compact_text(item) for item in _string_list(profile.get("promo_copy")) if _compact_text(item)), "")
    if promo and promo not in pieces[0]:
        pieces.append(promo.rstrip("。.!?！？") + ("." if is_en else "。"))

    if tag_text:
        if is_en:
            pieces.append(
                f"For readers who want {tag_text}, sharp conflict, constant reversals, and a protagonist whose every win raises the stakes."
            )
        else:
            pieces.append(
                f"如果你喜欢{tag_text}、强冲突、持续反转和爽点升级，这本书会把你直接拉进主角的选择与代价之中。"
            )
    else:
        pieces.append(
            "Every chapter pushes a new hook, a new cost, and a stronger reason to keep reading."
            if is_en
            else "每一章都推进新的钩子、新的代价和新的追读理由。"
        )
    return _limit_chars(" ".join(piece for piece in pieces if piece), max_chars)


def _safe_listing_dir(output_base_dir: str | Path | None, project_slug: str) -> Path | None:
    if output_base_dir is None:
        return None
    base_dir = Path(output_base_dir).resolve()
    listing_dir = (base_dir / project_slug / "listing").resolve()
    try:
        listing_dir.relative_to(base_dir)
    except ValueError:
        return None
    return listing_dir


def load_book_listing_file_overrides(
    *,
    output_base_dir: str | Path | None,
    project_slug: str,
) -> dict[str, Any]:
    listing_dir = _safe_listing_dir(output_base_dir, project_slug)
    if listing_dir is None or not listing_dir.exists():
        return {}

    overrides: dict[str, Any] = {"source_files": [], "load_warnings": []}
    metadata_path = listing_dir / "book-listing-metadata.json"
    if metadata_path.exists():
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                overrides.update(data)
                overrides["source_files"].append(str(metadata_path.resolve()))
            else:
                overrides["load_warnings"].append("book-listing-metadata.json is not an object")
        except json.JSONDecodeError as exc:
            overrides["load_warnings"].append(f"book-listing-metadata.json invalid: {exc}")

    title_path = listing_dir / "title-candidates.csv"
    if title_path.exists():
        try:
            with title_path.open("r", encoding="utf-8", newline="") as handle:
                rows = [
                    {
                        "id": int(row.get("id") or index)
                        if str(row.get("id") or "").isdigit()
                        else index,
                        "title": _clean_text(row.get("title")),
                        "subtitle": _clean_text(row.get("subtitle")),
                        "angle": _clean_text(row.get("angle")),
                        "recommendation": _clean_text(row.get("recommendation")),
                    }
                    for index, row in enumerate(csv.DictReader(handle), start=1)
                    if _clean_text(row.get("title"))
                ]
            if rows:
                overrides["title_candidates"] = rows
                overrides["source_files"].append(str(title_path.resolve()))
        except (OSError, csv.Error, UnicodeError) as exc:
            overrides["load_warnings"].append(f"title-candidates.csv invalid: {exc}")

    return overrides


def _category_suggestions(
    primary: str, secondary: str, platform: str, *, language: str = ""
) -> dict[str, list[str]]:
    if _is_english(language):
        general = _dedupe_strings(
            [
                primary,
                secondary,
                f"{primary} Fiction" if primary else "",
                f"{primary} Adventure" if primary else "",
                f"{secondary} Series" if secondary else "",
            ]
        )
    else:
        general = _dedupe_strings(
            [
                primary,
                secondary,
                f"{primary}脑洞" if primary else "",
                f"{primary}爽文" if primary else "",
                f"{secondary}长篇" if secondary else "",
            ]
        )
    target = _dedupe_strings(
        [
            f"{platform}/{primary}" if platform and primary else "",
            f"{platform}/{secondary}" if platform and secondary else "",
        ]
    )
    return {"general": general, "target_platform": target}


def _fallback_title_candidates(profile: dict[str, Any]) -> list[dict[str, Any]]:
    language = _clean_text(profile.get("language"))
    is_en = _is_english(language)

    title = _clean_text(profile.get("primary_title")) or ("Untitled" if is_en else "未命名作品")
    subtitle = _clean_text(profile.get("recommended_subtitle"))
    primary = _clean_text(profile.get("primary_category")) or ("Genre" if is_en else "类型")
    secondary = _clean_text(profile.get("secondary_category")) or primary
    tags = _string_list(profile.get("tags"))
    hook = tags[0] if tags else secondary
    promise = _clean_text(profile.get("logline") or profile.get("short_intro"))

    if is_en:
        short_promise = promise[:40] if promise else f"{hook} — high-conflict opening"
        specs = [
            (title, subtitle, "Current main title / 当前主书名", "Primary / 主推"),
            (f"{title}: {hook}", short_promise, "Genre-hook title / 强化类型入口", "Platform test / 平台测试"),
            (f"The {hook} Files", subtitle, "Core appeal highlight / 突出核心卖点", "Alt / 备选"),
            (f"{primary} Chronicles", short_promise, "Clear genre signal / 类型识别明确", "Alt / 备选"),
            (f"{secondary} Diaries", subtitle, "Series potential / 系列化空间强", "Alt / 备选"),
            (f"Breaking {primary}", short_promise, "Power-fantasy direct / 爽文表达直接", "Ad test / 广告测试"),
            (f"{title}: Origins", subtitle, "Prequel/arc name / 适合番外/卷名", "Arc name / 卷名"),
            (f"The {hook} Ledger", short_promise, "Suspense & consequence / 悬念与因果感", "Alt / 备选"),
            (f"{primary}: Case One", subtitle, "Case entry / 单元案入口", "Arc name / 卷名"),
            (f"{secondary} Endgame", short_promise, "High stakes / 危机感强", "Ad test / 广告测试"),
            (f"When I Met the {hook}", subtitle, "Opening incident / 开篇事件感", "Ad test / 广告测试"),
            (f"Rules of {hook}", short_promise, "Rule hook / 规则钩子", "Alt / 备选"),
            (f"The {primary} Codex", subtitle, "Mystery appeal / 神秘感强", "Alt / 备选"),
            (f"{secondary}: The Hunt", short_promise, "Clear objective / 行动目标明确", "Alt / 备选"),
            (f"The {title} Records", subtitle, "Classic series / 传统系列感", "Alt / 备选"),
            (f"{hook}: Sleepless Night", short_promise, "Atmosphere & crisis / 氛围与危机", "Arc name / 卷名"),
            (f"The {primary} Receipt", subtitle, "Cost & consequence / 结果与代价感", "Alt / 备选"),
            (f"{secondary}: Day One", short_promise, "Strong opener / 强开场", "Ad test / 广告测试"),
            (f"The {hook} List", subtitle, "List suspense / 名单悬念", "Alt / 备选"),
            (f"{title}: Eve of the Finale", short_promise, "Climax arc / 阶段高潮", "Arc name / 卷名"),
        ]
    else:
        short_promise = promise[:26] if promise else f"{hook}开局，强冲突推进"
        specs = [
            (title, subtitle, "当前主书名", "主推"),
            (f"{title}：{hook}", short_promise, "强化类型入口", "平台测试"),
            (f"{hook}之书", subtitle, "突出核心卖点", "备选"),
            (f"{primary}档案", short_promise, "类型识别明确", "备选"),
            (f"{secondary}异闻录", subtitle, "系列化空间强", "备选"),
            (f"我在{primary}里破局", short_promise, "爽文表达直接", "下沉测试"),
            (f"{title}前传", subtitle, "适合番外/卷名", "卷名"),
            (f"{hook}账本", short_promise, "悬念与因果感", "备选"),
            (f"{primary}第一案", subtitle, "单元案入口", "卷名"),
            (f"{secondary}生死局", short_promise, "危机感强", "广告测试"),
            (f"开局撞见{hook}", subtitle, "开篇事件感", "广告测试"),
            (f"{hook}规则", short_promise, "规则钩子", "备选"),
            (f"{primary}秘卷", subtitle, "神秘感强", "备选"),
            (f"{secondary}追凶", short_promise, "行动目标明确", "备选"),
            (f"{title}录", subtitle, "传统系列感", "备选"),
            (f"{hook}不眠夜", short_promise, "氛围与危机", "卷名"),
            (f"{primary}回执", subtitle, "结果与代价感", "备选"),
            (f"{secondary}开局", short_promise, "强开场", "广告测试"),
            (f"{hook}名单", subtitle, "名单悬念", "备选"),
            (f"{title}：终局前夜", short_promise, "阶段高潮", "卷名"),
        ]

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for candidate_title, candidate_subtitle, angle, recommendation in specs:
        normalized = _clean_text(candidate_title)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        rows.append(
            {
                "id": len(rows) + 1,
                "title": normalized,
                "subtitle": _clean_text(candidate_subtitle),
                "angle": angle,
                "recommendation": recommendation,
            }
        )

    filler_label = "Auto-fill candidate / 自动补足候选" if is_en else "自动补足候选"
    filler_rec = "Alt / 备选" if is_en else "备选"
    while len(rows) < REQUIRED_TITLE_CANDIDATE_COUNT:
        suffix = f"Candidate {len(rows) + 1}" if is_en else f"候选{len(rows) + 1}"
        rows.append(
            {
                "id": len(rows) + 1,
                "title": f"{title} · {suffix}",
                "subtitle": short_promise,
                "angle": filler_label,
                "recommendation": filler_rec,
            }
        )
    return rows[:REQUIRED_TITLE_CANDIDATE_COUNT]


def _derive_characters(
    story_bible: Any, writing_profile: Any, *, language: str = ""
) -> list[dict[str, Any]]:
    is_en = _is_english(language)
    characters = [_character_dict(item) for item in _get_value(story_bible, "characters", []) or []]
    characters = [item for item in characters if item["name"]]
    if characters:
        protagonists = [
            item
            for item in characters
            if item["role"] == "protagonist" or item["is_pov_character"]
        ]
        supporting = [
            item
            for item in characters
            if item not in protagonists
        ]
        normalized: list[dict[str, Any]] = []
        for item in protagonists:
            item = dict(item)
            if item["role"] == "protagonist":
                item["role"] = "Protagonist / 主角" if is_en else "主角"
            normalized.append(item)
        normalized.extend(supporting)
        return normalized[:12]

    protagonist = _clean_text(_get_nested(writing_profile, "character", "protagonist_archetype"))
    drive = _clean_text(_get_nested(writing_profile, "character", "protagonist_core_drive"))
    golden_finger = _clean_text(_get_nested(writing_profile, "character", "golden_finger"))
    if not (protagonist or drive or golden_finger):
        return []
    sep = "; " if is_en else "；"
    return [
        {
            "name": "Protagonist Profile / 主角设定" if is_en else "主角设定",
            "role": "Protagonist / 主角" if is_en else "主角",
            "identity": protagonist,
            "appeal": sep.join(_dedupe_strings([drive, golden_finger])),
            "goal": drive,
            "arc_state": "",
            "is_pov_character": True,
        }
    ]


def validate_book_listing_profile(profile: dict[str, Any]) -> dict[str, Any]:
    is_en = _is_english(_clean_text(profile.get("language")))
    checks = [
        {
            "code": "title_candidates",
            "label": "Title Candidates / 书名候选" if is_en else "书名候选",
            "severity": "blocker",
            "passed": len(profile.get("title_candidates") or []) >= REQUIRED_TITLE_CANDIDATE_COUNT,
            "message": (
                f"At least {REQUIRED_TITLE_CANDIDATE_COUNT} testable title candidates required.\n"
                f"至少需要 {REQUIRED_TITLE_CANDIDATE_COUNT} 个可测试书名/数据名。"
                if is_en
                else f"至少需要 {REQUIRED_TITLE_CANDIDATE_COUNT} 个可测试书名/数据名。"
            ),
        },
        {
            "code": "categories",
            "label": "Categories / 分类信息" if is_en else "分类信息",
            "severity": "blocker",
            "passed": bool(profile.get("primary_category") and profile.get("secondary_category")),
            "message": (
                "Primary and secondary categories are required for stable shelving and recommendations.\n"
                "必须具备主分类和二级分类，才能稳定上架与推荐。"
                if is_en
                else "必须具备主分类和二级分类，才能稳定上架与推荐。"
            ),
        },
        {
            "code": "intro",
            "label": "Synopsis / 简介" if is_en else "简介",
            "severity": "blocker",
            "passed": (
                len(_clean_text(profile.get("shelf_intro") or profile.get("short_intro"))) >= 40
                and len(_clean_text(profile.get("shelf_intro") or profile.get("short_intro"))) <= 500
            ),
            "message": (
                "The short intro must clearly convey protagonist, conflict, appeal, and reading hook.\n"
                "短简介需要在 500 字以内，并明确说明主角、冲突、卖点和追读钩子。"
                if is_en
                else "短简介需要在 500 字以内，并明确说明主角、冲突、卖点和追读钩子。"
            ),
        },
        {
            "code": "promo_copy",
            "label": "Promo Copy / 宣传文案" if is_en else "宣传文案",
            "severity": "warning",
            "passed": len(profile.get("promo_copy") or []) >= 3,
            "message": (
                "Recommend at least 3 promo copies for recommendation slots, ads, and on-site exposure.\n"
                "建议至少准备 3 条用于推荐位、广告和站内露出的宣传文案。"
                if is_en
                else "建议至少准备 3 条用于推荐位、广告和站内露出的宣传文案。"
            ),
        },
        {
            "code": "characters",
            "label": "Characters / 角色信息" if is_en else "角色信息",
            "severity": "warning",
            "passed": len(profile.get("main_characters") or []) >= 1,
            "message": (
                "At least one protagonist or core character profile is needed.\n"
                "至少需要一个主角或核心角色档案。"
                if is_en
                else "至少需要一个主角或核心角色档案。"
            ),
        },
        {
            "code": "tags",
            "label": "Tags / 标签" if is_en else "标签",
            "severity": "warning",
            "passed": len(profile.get("tags") or []) >= 5,
            "message": (
                "Recommend at least 5 tags for distribution and recommendations.\n"
                "建议至少 5 个可供分发和推荐使用的标签。"
                if is_en
                else "建议至少 5 个可供分发和推荐使用的标签。"
            ),
        },
        {
            "code": "reader_promise",
            "label": "Reader Promise / 读者承诺" if is_en else "读者承诺",
            "severity": "warning",
            "passed": len(profile.get("reader_promise") or []) >= 2,
            "message": (
                "List what readers consistently gain — what thrill and appeal keeps them reading.\n"
                "建议明确列出读者能持续获得什么爽感和吸引力。"
                if is_en
                else "建议明确列出读者能持续获得什么爽感和吸引力。"
            ),
        },
        {
            "code": "short_video_scripts",
            "label": "Short Video Scripts / 短视频脚本" if is_en else "短视频脚本",
            "severity": "warning",
            "passed": (
                len(
                    (
                        profile.get("marketing_assets")
                        if isinstance(profile.get("marketing_assets"), dict)
                        else {}
                    ).get("short_video_scripts", [])
                )
                >= 3
            ),
            "message": (
                "Prepare 15s / 45s / 90s scripts for discovery, character conflict, "
                "and world hooks.\n"
                "建议准备 15 秒概念钩子、45 秒角色关系冲突、90 秒世界观/议题亮点脚本。"
                if is_en
                else "建议准备 15 秒概念钩子、45 秒角色关系冲突、90 秒世界观/议题亮点脚本。"
            ),
        },
    ]
    blocker_count = sum(
        1 for item in checks if item["severity"] == "blocker" and not item["passed"]
    )
    warning_count = sum(
        1 for item in checks if item["severity"] == "warning" and not item["passed"]
    )
    passed_count = sum(1 for item in checks if item["passed"])
    score = round(passed_count / len(checks) * 100)
    if blocker_count:
        status = "blocked"
    elif warning_count:
        status = "needs_attention"
    else:
        status = "ready"
    return {
        "status": status,
        "score": score,
        "blocker_count": blocker_count,
        "warning_count": warning_count,
        "checks": checks,
    }


def build_book_listing_profile(
    *,
    project: Any,
    writing_profile: Any | None = None,
    story_bible: Any | None = None,
    output_base_dir: str | Path | None = None,
) -> dict[str, Any]:
    metadata = _get_value(project, "metadata_json", {}) or {}
    file_overrides = load_book_listing_file_overrides(
        output_base_dir=output_base_dir,
        project_slug=_clean_text(_get_value(project, "slug")),
    )
    metadata_overrides = (
        metadata.get("listing_profile")
        if isinstance(metadata.get("listing_profile"), dict)
        else {}
    )
    overrides = {**file_overrides, **metadata_overrides}

    project_language = (
        _clean_text(_get_value(project, "language")) or "zh-CN"
    )
    is_en = _is_english(project_language)
    project_title = _clean_text(_get_value(project, "title")) or ("Untitled" if is_en else "未命名作品")
    platform = _clean_text(_get_nested(writing_profile, "market", "platform_target")) or (
        "All Platforms" if is_en else "全平台"
    )
    override_tags = _string_list(overrides.get("tags"))
    if override_tags:
        tags = override_tags
    else:
        tags = _dedupe_strings(
            _string_list(metadata.get("tags"))
            + _string_list(_get_nested(writing_profile, "market", "trope_keywords"))
            + _string_list(_get_nested(writing_profile, "market", "hook_keywords"))
            + _string_list(_get_nested(writing_profile, "world", "setting_tags"))
        )
    primary_category = (
        _clean_text(overrides.get("primary_category"))
        or _clean_text(_get_value(project, "genre"))
        or "未分类"
    )
    secondary_category = (
        _clean_text(overrides.get("secondary_category"))
        or _clean_text(_get_value(project, "sub_genre"))
        or (tags[0] if tags else "")
    )
    tags = _dedupe_strings([primary_category, secondary_category] + tags)
    logline = (
        _clean_text(overrides.get("logline"))
        or _clean_text(metadata.get("logline"))
        or _clean_text(metadata.get("premise"))
        or _clean_text(_get_nested(writing_profile, "market", "reader_promise"))
    )
    if is_en:
        short_intro = (
            _clean_text(overrides.get("short_intro"))
            or _clean_text(metadata.get("synopsis"))
            or (
                f'"{project_title}" is a serialized {primary_category} novel. {logline} '
                f"The protagonist must keep breaking through before the crisis escalates, "
                f"driving every chapter toward new conflict and page-turning hooks.\n"
                f"《{project_title}》是一部{primary_category}长篇连载。{logline}"
                f"主角必须在危机升级前持续破局，把每一章都推向新的冲突和追读钩子。"
                if logline
                else ""
            )
        )
    else:
        short_intro = (
            _clean_text(overrides.get("short_intro"))
            or _clean_text(metadata.get("synopsis"))
            or (
                f"《{project_title}》是一部{primary_category}长篇连载。{logline}"
                f"主角必须在危机升级前持续破局，把每一章都推向新的冲突和追读钩子。"
                if logline
                else ""
            )
        )
    promo_copy = _string_list(overrides.get("promo_copy"))
    if not promo_copy:
        core_hook = tags[0] if tags else primary_category
        if is_en:
            promo_copy = _dedupe_strings(
                [
                    logline,
                    (
                        f"Not every {primary_category} novel wins on worldbuilding alone — "
                        f"this one hooks you through protagonist choices and relentless pressure.\n"
                        f"不是所有{primary_category}都靠设定取胜，这本书靠主角选择和危机反压追着读者往下看。"
                    ),
                    (
                        f"Conflict from the first page, cliffhangers at every chapter end, "
                        f"core appeal escalating around {core_hook}.\n"
                        f"开局就给冲突，章节尾持续留钩，核心爽点围绕{core_hook}不断升级。"
                    ),
                ]
            )
        else:
            promo_copy = _dedupe_strings(
                [
                    logline,
                    f"不是所有{primary_category}都靠设定取胜，这本书靠主角选择和危机反压追着读者往下看。",
                    f"开局就给冲突，章节尾持续留钩，核心爽点围绕{core_hook}不断升级。",
                ]
            )

    reader_promise = _string_list(overrides.get("reader_promise"))
    if not reader_promise:
        reader_promise = _dedupe_strings(
            _string_list(_get_nested(writing_profile, "market", "selling_points"))
            + [_clean_text(_get_nested(writing_profile, "market", "reader_promise"))]
        )

    profile: dict[str, Any] = {
        "schema_version": LISTING_SCHEMA_VERSION,
        "book_id": (
            _clean_text(overrides.get("book_id")) or _clean_text(_get_value(project, "slug"))
        ),
        "primary_title": _clean_text(overrides.get("primary_title")) or project_title,
        "recommended_subtitle": _clean_text(overrides.get("recommended_subtitle")),
        "logline": logline,
        "channel": (
            _clean_text(overrides.get("channel")) or _clean_text(_get_value(project, "audience"))
        ),
        "length_type": _clean_text(overrides.get("length_type")) or (
            "Serialized Novel / 长篇连载" if is_en else "长篇连载"
        ),
        "serialization_status": _clean_text(overrides.get("serialization_status"))
        or _clean_text(_get_value(project, "status")),
        "language": (
            _clean_text(overrides.get("language")) or _clean_text(_get_value(project, "language"))
        ),
        "primary_category": primary_category,
        "secondary_category": secondary_category,
        "tertiary_categories": _string_list(overrides.get("tertiary_categories")) or tags[:4],
        "platform_category_suggestions": overrides.get("platform_category_suggestions")
        or _category_suggestions(primary_category, secondary_category, platform, language=project_language),
        "tags": tags,
        "short_intro": short_intro,
        "long_intro": _clean_text(overrides.get("long_intro")),
        "promo_copy": promo_copy,
        "main_characters": (
            [_character_dict(item) for item in overrides.get("main_characters", [])]
            if isinstance(overrides.get("main_characters"), list)
            else _derive_characters(story_bible, writing_profile, language=project_language)
        ),
        "reader_promise": reader_promise,
        "not_recommended_categories": _string_list(overrides.get("not_recommended_categories")),
        "title_candidates": (
            overrides.get("title_candidates")
            if isinstance(overrides.get("title_candidates"), list)
            else []
        ),
        "source_files": _string_list(file_overrides.get("source_files")),
        "load_warnings": _string_list(file_overrides.get("load_warnings")),
    }

    if not profile["title_candidates"]:
        profile["title_candidates"] = _fallback_title_candidates(profile)
    else:
        profile["title_candidates"] = [
            {
                "id": int(item.get("id") or index),
                "title": _clean_text(item.get("title")),
                "subtitle": _clean_text(item.get("subtitle")),
                "angle": _clean_text(item.get("angle")),
                "recommendation": _clean_text(item.get("recommendation")),
            }
            for index, item in enumerate(profile["title_candidates"], start=1)
            if isinstance(item, dict) and _clean_text(item.get("title"))
        ][:REQUIRED_TITLE_CANDIDATE_COUNT]
    if len(profile["title_candidates"]) < REQUIRED_TITLE_CANDIDATE_COUNT:
        profile["title_candidates"] = (
            profile["title_candidates"] + _fallback_title_candidates(profile)
        )[:REQUIRED_TITLE_CANDIDATE_COUNT]

    profile["shelf_intro"] = _build_shelf_intro(profile, max_chars=500)
    character_names = _dedupe_strings(
        [
            item.get("name")
            for item in profile.get("main_characters", [])
            if isinstance(item, dict)
        ]
    )
    profile["character_names"] = character_names
    profile["copy_pack"] = {
        "title": profile["primary_title"],
        "subtitle": profile["recommended_subtitle"],
        "book_id": profile["book_id"],
        "category": " / ".join(
            item for item in [profile["primary_category"], profile["secondary_category"]] if item
        ),
        "tags": "、".join(profile["tags"]),
        "character_names": "、".join(character_names),
        "shelf_intro": profile["shelf_intro"],
    }
    profile["marketing_assets"] = build_listing_marketing_asset_pack(
        profile,
        story_bible=story_bible,
    )
    profile["ip_readiness"] = build_listing_ip_readiness(
        profile,
        story_bible=story_bible,
    )
    profile["compliance"] = validate_book_listing_profile(profile)
    return profile
