from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

LISTING_SCHEMA_VERSION = "1.0"
REQUIRED_TITLE_CANDIDATE_COUNT = 20


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


def _category_suggestions(primary: str, secondary: str, platform: str) -> dict[str, list[str]]:
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
    title = _clean_text(profile.get("primary_title")) or "未命名作品"
    subtitle = _clean_text(profile.get("recommended_subtitle"))
    primary = _clean_text(profile.get("primary_category")) or "类型"
    secondary = _clean_text(profile.get("secondary_category")) or primary
    tags = _string_list(profile.get("tags"))
    hook = tags[0] if tags else secondary
    promise = _clean_text(profile.get("logline") or profile.get("short_intro"))
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

    while len(rows) < REQUIRED_TITLE_CANDIDATE_COUNT:
        rows.append(
            {
                "id": len(rows) + 1,
                "title": f"{title}·候选{len(rows) + 1}",
                "subtitle": short_promise,
                "angle": "自动补足候选",
                "recommendation": "备选",
            }
        )
    return rows[:REQUIRED_TITLE_CANDIDATE_COUNT]


def _derive_characters(story_bible: Any, writing_profile: Any) -> list[dict[str, Any]]:
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
            item["role"] = "主角" if item["role"] == "protagonist" else item["role"]
            normalized.append(item)
        normalized.extend(supporting)
        return normalized[:12]

    protagonist = _clean_text(_get_nested(writing_profile, "character", "protagonist_archetype"))
    drive = _clean_text(_get_nested(writing_profile, "character", "protagonist_core_drive"))
    golden_finger = _clean_text(_get_nested(writing_profile, "character", "golden_finger"))
    if not (protagonist or drive or golden_finger):
        return []
    return [
        {
            "name": "主角设定",
            "role": "主角",
            "identity": protagonist,
            "appeal": "；".join(_dedupe_strings([drive, golden_finger])),
            "goal": drive,
            "arc_state": "",
            "is_pov_character": True,
        }
    ]


def validate_book_listing_profile(profile: dict[str, Any]) -> dict[str, Any]:
    checks = [
        {
            "code": "title_candidates",
            "label": "书名候选",
            "severity": "blocker",
            "passed": len(profile.get("title_candidates") or []) >= REQUIRED_TITLE_CANDIDATE_COUNT,
            "message": f"至少需要 {REQUIRED_TITLE_CANDIDATE_COUNT} 个可测试书名/数据名。",
        },
        {
            "code": "categories",
            "label": "分类信息",
            "severity": "blocker",
            "passed": bool(profile.get("primary_category") and profile.get("secondary_category")),
            "message": "必须具备主分类和二级分类，才能稳定上架与推荐。",
        },
        {
            "code": "intro",
            "label": "简介",
            "severity": "blocker",
            "passed": len(_clean_text(profile.get("short_intro"))) >= 40,
            "message": "短简介需要足够明确地说明主角、冲突、卖点和追读钩子。",
        },
        {
            "code": "promo_copy",
            "label": "宣传文案",
            "severity": "warning",
            "passed": len(profile.get("promo_copy") or []) >= 3,
            "message": "建议至少准备 3 条用于推荐位、广告和站内露出的宣传文案。",
        },
        {
            "code": "characters",
            "label": "角色信息",
            "severity": "warning",
            "passed": len(profile.get("main_characters") or []) >= 1,
            "message": "至少需要一个主角或核心角色档案。",
        },
        {
            "code": "tags",
            "label": "标签",
            "severity": "warning",
            "passed": len(profile.get("tags") or []) >= 5,
            "message": "建议至少 5 个可供分发和推荐使用的标签。",
        },
        {
            "code": "reader_promise",
            "label": "读者承诺",
            "severity": "warning",
            "passed": len(profile.get("reader_promise") or []) >= 2,
            "message": "建议明确列出读者能持续获得什么爽感和吸引力。",
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

    project_title = _clean_text(_get_value(project, "title")) or "未命名作品"
    platform = _clean_text(_get_nested(writing_profile, "market", "platform_target")) or "全平台"
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
        "length_type": _clean_text(overrides.get("length_type")) or "长篇连载",
        "serialization_status": _clean_text(overrides.get("serialization_status"))
        or _clean_text(_get_value(project, "status")),
        "language": (
            _clean_text(overrides.get("language")) or _clean_text(_get_value(project, "language"))
        ),
        "primary_category": primary_category,
        "secondary_category": secondary_category,
        "tertiary_categories": _string_list(overrides.get("tertiary_categories")) or tags[:4],
        "platform_category_suggestions": overrides.get("platform_category_suggestions")
        or _category_suggestions(primary_category, secondary_category, platform),
        "tags": tags,
        "short_intro": short_intro,
        "long_intro": _clean_text(overrides.get("long_intro")),
        "promo_copy": promo_copy,
        "main_characters": (
            [_character_dict(item) for item in overrides.get("main_characters", [])]
            if isinstance(overrides.get("main_characters"), list)
            else _derive_characters(story_bible, writing_profile)
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

    profile["compliance"] = validate_book_listing_profile(profile)
    return profile
