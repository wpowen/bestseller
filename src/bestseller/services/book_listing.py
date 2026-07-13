from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path
import re
from typing import Any

from bestseller.services.concept_lab import concept_lab_listing_overrides
from bestseller.services.platform_title_workflow import (
    DEFAULT_TITLE_CANDIDATE_COUNT,
    build_platform_title_workflow,
    evaluate_platform_title_candidate,
    resolve_title_style,
)
from bestseller.services.ranking_readiness import (
    build_listing_ip_readiness,
    build_listing_marketing_asset_pack,
)

LISTING_SCHEMA_VERSION = "1.3"
REQUIRED_TITLE_CANDIDATE_COUNT = DEFAULT_TITLE_CANDIDATE_COUNT


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


def _first_sentence(value: Any, *, max_chars: int) -> str:
    """Keep reader-facing listing fields concise without cutting a sentence mid-way."""
    text = _compact_text(value)
    if not text:
        return ""
    match = re.search(r"^(.+?[。！？.!?])(?:\s|$)", text)
    first = match.group(1).strip() if match else text
    return _limit_chars(first, max_chars)


def _listing_logline(value: Any, *, max_chars: int = 80) -> str:
    """Normalise a one-line hook; a full premise must never occupy this field."""
    return _first_sentence(value, max_chars=max_chars).rstrip("。.!?！？")


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


def _logline_mismatches_premise(
    logline: str,
    *,
    premise: str,
    title: str,
    genre: str,
    sub_genre: str,
    tags: list[str],
) -> bool:
    if not logline or not premise:
        return False
    from bestseller.services.hook_strength_gate import premise_anchor_groups

    groups = premise_anchor_groups(
        {
            "premise": premise,
            "title": title,
            "genre": genre,
            "sub_genre": sub_genre,
            "tags": tags,
        }
    )
    concrete_groups = {
        key: values
        for key, values in groups.items()
        if key != "genre" and any(len(value) >= 2 for value in values)
    }
    if len(concrete_groups) < 2:
        return False
    matched_group_count = sum(
        1
        for values in concrete_groups.values()
        if any(value and value in logline for value in values)
    )
    return matched_group_count < 2


def _fallback_selling_points(
    *,
    title: str,
    logline: str,
    tags: list[str],
    primary_category: str,
    is_en: bool,
) -> list[str]:
    if is_en:
        core = tags[0] if tags else primary_category or "the core hook"
        return _dedupe_strings(
            [
                f"Visible protagonist choices and payoffs around {core}.",
                "Escalating chapter hooks with a clear cost after each win.",
            ]
        )
    anchor_terms = [
        token
        for token in re.findall(r"[\u4e00-\u9fff]{2,6}", " ".join([title, logline, *tags]))
        if token not in {"主角", "读者", "故事", "小说", "都市", "修仙", "升级"}
    ]
    core = next((token for token in anchor_terms if token), tags[0] if tags else primary_category)
    return _dedupe_strings(
        [
            f"{core}开局有明确身份、压力和破局目标。",
            "每轮胜利都带来可见回报、公开误解或下一轮代价。",
        ]
    )


def _fallback_reader_promise(
    *,
    title: str,
    logline: str,
    selling_points: list[str],
    primary_category: str,
    is_en: bool,
) -> list[str]:
    if is_en:
        return _dedupe_strings(
            [
                "Readers get immediate pressure, visible choices, and staged payoffs.",
                "Each win raises the next cost instead of resetting the story.",
            ]
        )
    subject = title or "主角"
    payoff = selling_points[0] if selling_points else logline
    return _dedupe_strings(
        [
            f"读者追看{subject}如何在{primary_category or '核心赛道'}里持续破局、当众翻盘。",
            payoff or "每章都要给出新的压力、爽点兑现和追读理由。",
        ]
    )


def _fallback_target_audiences(
    *,
    primary_category: str,
    secondary_category: str,
    tags: list[str],
    is_en: bool,
) -> list[str]:
    if is_en:
        return _dedupe_strings(
            [
                (
                    "Readers who want fast serialized "
                    f"{primary_category or 'commercial fiction'} hooks."
                ),
                "Readers who prefer visible protagonist progress and escalating stakes.",
            ]
        )
    tag_text = "、".join(tags[:3]) if tags else secondary_category or primary_category
    return _dedupe_strings(
        [
            f"喜欢{primary_category or '商业类型'}强冲突和快速爽点兑现的读者。",
            f"偏好{tag_text}、身份逆袭和持续升级的连载读者。",
        ]
    )


def _supplement_listing_tags(
    tags: list[str],
    *,
    title: str,
    premise: str,
    logline: str,
) -> list[str]:
    if len(tags) >= 5:
        return tags
    preferred_markers = (
        "灵务局",
        "考编",
        "岗位权限",
        "公务工单",
        "临聘巡检",
        "审批黑箱",
        "灵石配额",
        "转正资格",
        "强制复检",
        "合规台账",
        "死亡名单",
        "双穿门",
    )
    supplements: list[str] = []
    source = " ".join([title, premise, logline])
    for marker in preferred_markers:
        if marker in source:
            supplements.append(marker)
    for token in re.findall(r"[\u4e00-\u9fff]{2,6}", source):
        if token not in {"主角", "读者", "故事", "小说", "一部", "都市", "修仙"}:
            supplements.append(token)
        if len(tags) + len(supplements) >= 8:
            break
    return _dedupe_strings([*tags, *supplements])[:8]


def _character_dict(item: Any) -> dict[str, Any]:
    return {
        "name": _clean_text(_get_value(item, "name")),
        "role": _clean_text(_get_value(item, "role")),
        "identity": _first_sentence(
            _get_value(item, "identity") or _get_value(item, "background"), max_chars=80
        ),
        "appeal": _first_sentence(
            _get_value(item, "appeal")
            or _get_value(item, "arc_trajectory")
            or _get_value(item, "goal"),
            max_chars=140,
        ),
        "goal": _first_sentence(_get_value(item, "goal"), max_chars=100),
        "arc_state": _first_sentence(_get_value(item, "arc_state"), max_chars=100),
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


def _title_label_fields(platform: str) -> dict[str, str]:
    style = resolve_title_style(platform)
    if style.key == "general":
        return {
            "platform": style.key,
            "platform_label": style.label,
            "platform_scope": "all_platform",
            "scope_label": "全平台",
            "platform_tag": "全平台",
            "display_label": "全平台",
        }
    return {
        "platform": style.key,
        "platform_label": style.label,
        "platform_scope": "target_platform",
        "scope_label": "目标平台",
        "platform_tag": style.label,
        "display_label": f"{style.label} · 目标平台",
    }


def _fallback_title_candidates(profile: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        workflow = build_platform_title_workflow(
            profile,
            target_platform=_clean_text(profile.get("target_platform")),
            candidate_count=REQUIRED_TITLE_CANDIDATE_COUNT,
        )
        candidates = workflow.get("candidates")
        if isinstance(candidates, list) and len(candidates) >= REQUIRED_TITLE_CANDIDATE_COUNT:
            return candidates[:REQUIRED_TITLE_CANDIDATE_COUNT]
    except Exception:
        # Keep the older generic fallback as a last-resort safety net.
        candidates = []

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
            (f"{hook}手记", short_promise, "悬念与因果感", "备选"),
            (f"{primary}第一案", subtitle, "单元案入口", "卷名"),
            (f"{secondary}生死局", short_promise, "危机感强", "广告测试"),
            (f"开局撞见{hook}", subtitle, "开篇事件感", "广告测试"),
            (f"{hook}规则", short_promise, "规则钩子", "备选"),
            (f"{primary}秘卷", subtitle, "神秘感强", "备选"),
            (f"{secondary}追凶", short_promise, "行动目标明确", "备选"),
            (f"{title}录", subtitle, "传统系列感", "备选"),
            (f"{hook}不眠夜", short_promise, "氛围与危机", "卷名"),
            (f"{primary}回响", subtitle, "结果与代价感", "备选"),
            (f"{secondary}开局", short_promise, "强开场", "广告测试"),
            (f"{hook}名单", subtitle, "名单悬念", "备选"),
            (f"{title}：终局前夜", short_promise, "阶段高潮", "卷名"),
        ]

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    label_fields = _title_label_fields(_clean_text(profile.get("target_platform")))
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
                **label_fields,
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
                **label_fields,
            }
        )
    return rows[:REQUIRED_TITLE_CANDIDATE_COUNT]


def _normalize_legacy_title_candidates(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"))
        if not title:
            continue
        rows.append(
            {
                "id": int(item.get("id") or index),
                "title": title,
                "subtitle": _clean_text(item.get("subtitle")),
                "angle": _clean_text(item.get("angle")),
                "recommendation": _clean_text(item.get("recommendation")),
            }
        )
    return rows[:REQUIRED_TITLE_CANDIDATE_COUNT]


def _merge_preferred_title_candidates(
    preferred: list[dict[str, Any]],
    generated: list[dict[str, Any]],
    *,
    platform: str,
) -> list[dict[str, Any]]:
    if not preferred:
        return generated
    label_fields = _title_label_fields(platform)
    label_quotas = Counter(
        _clean_text(item.get("display_label"))
        for item in generated
        if isinstance(item, dict) and _clean_text(item.get("display_label"))
    )
    default_label = _clean_text(label_fields.get("display_label"))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    label_counts: Counter[str] = Counter()
    for raw in [*preferred, *generated]:
        if not isinstance(raw, dict):
            continue
        title = _clean_text(raw.get("title"))
        title_key = title.casefold()
        if not title or title_key in seen:
            continue
        row = dict(raw)
        row["id"] = len(rows) + 1
        row["title"] = title
        for key, value in label_fields.items():
            row.setdefault(key, value)
        label = _clean_text(row.get("display_label")) or default_label
        if label and label_quotas and label_counts[label] >= label_quotas.get(label, 0):
            continue
        seen.add(title_key)
        if label:
            label_counts[label] += 1
        rows.append(row)
        if len(rows) >= REQUIRED_TITLE_CANDIDATE_COUNT:
            break
    return rows


def _attach_title_candidate_evaluations(
    candidates: list[dict[str, Any]],
    workflow: dict[str, Any],
    *,
    profile: dict[str, Any],
    platform: str,
) -> None:
    evaluations = (
        workflow.get("candidate_evaluations")
        if isinstance(workflow.get("candidate_evaluations"), dict)
        else {}
    )
    for candidate in candidates:
        if not isinstance(candidate, dict) or isinstance(candidate.get("title_evaluation"), dict):
            continue
        title = _clean_text(candidate.get("title"))
        evaluation = evaluations.get(title)
        if not isinstance(evaluation, dict):
            evaluation = evaluate_platform_title_candidate(
                profile,
                title,
                target_platform=platform,
            )
        candidate["title_evaluation"] = evaluation
        checks = evaluation.get("checks")
        if isinstance(checks, dict):
            candidate["reader_review"] = checks


def _drop_blocked_title_candidates(
    candidates: list[dict[str, Any]],
    blocked_titles: list[str],
    fill_pool: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blocked = {_clean_text(title).casefold() for title in blocked_titles if _clean_text(title)}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in [*candidates, *fill_pool]:
        if not isinstance(candidate, dict):
            continue
        title = _clean_text(candidate.get("title"))
        key = title.casefold()
        if not title or key in blocked or key in seen:
            continue
        row = dict(candidate)
        row["id"] = len(rows) + 1
        rows.append(row)
        seen.add(key)
        if len(rows) >= REQUIRED_TITLE_CANDIDATE_COUNT:
            break
    return rows


def _title_candidate_csv_cell(value: object) -> str:
    text = _compact_text(value).replace('"', '""')
    return f'"{text}"'


def write_platform_title_workflow_artifacts(
    profile: dict[str, Any],
    listing_dir: str | Path,
) -> dict[str, Path]:
    target_dir = Path(listing_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = target_dir / "title-workflow.json"
    candidates_path = target_dir / "title-candidates.csv"
    workflow = (
        profile.get("title_workflow")
        if isinstance(profile.get("title_workflow"), dict)
        else {}
    )
    candidates = (
        profile.get("title_candidates")
        if isinstance(profile.get("title_candidates"), list)
        else []
    )
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    headers = [
        "id",
        "title",
        "subtitle",
        "platform_label",
        "scope_label",
        "display_label",
        "pattern",
        "score",
        "score_breakdown",
        "evaluation_decision",
        "reader_attraction",
        "story_transmission",
        "platform_fit",
        "revision_prompt",
        "angle",
        "recommendation",
    ]
    rows = [",".join(_title_candidate_csv_cell(item) for item in headers)]
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        values = [
            candidate_evaluation := (
                candidate.get("title_evaluation")
                if isinstance(candidate.get("title_evaluation"), dict)
                else {}
            ),
        ]
        checks = (
            candidate_evaluation.get("checks")
            if isinstance(candidate_evaluation.get("checks"), dict)
            else {}
        )
        values = [
            candidate.get("id") or index,
            candidate.get("title"),
            candidate.get("subtitle"),
            candidate.get("platform_label"),
            candidate.get("scope_label"),
            candidate.get("display_label"),
            candidate.get("pattern"),
            candidate.get("score"),
            json.dumps(candidate.get("score_breakdown") or {}, ensure_ascii=False, sort_keys=True),
            candidate_evaluation.get("decision"),
            json.dumps(checks.get("reader_attraction") or {}, ensure_ascii=False, sort_keys=True),
            json.dumps(checks.get("story_transmission") or {}, ensure_ascii=False, sort_keys=True),
            json.dumps(checks.get("platform_fit") or {}, ensure_ascii=False, sort_keys=True),
            (
                candidate_evaluation.get("feedback", {}).get("revision_prompt")
                if isinstance(candidate_evaluation.get("feedback"), dict)
                else ""
            ),
            candidate.get("angle"),
            candidate.get("recommendation"),
        ]
        rows.append(",".join(_title_candidate_csv_cell(item) for item in values))
    candidates_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return {"workflow": workflow_path, "candidates": candidates_path}


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
    # This fallback is reader-facing when no structured character cards exist.
    # Prefer one concrete ability over concatenating the planning dossier.
    return [
        {
            "name": "Protagonist Profile / 主角设定" if is_en else "主角设定",
            "role": "Protagonist / 主角" if is_en else "主角",
            "identity": protagonist,
            "appeal": _first_sentence(golden_finger or drive, max_chars=140),
            "goal": _first_sentence(drive, max_chars=100),
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
    concept_overrides = concept_lab_listing_overrides(metadata)
    file_overrides = load_book_listing_file_overrides(
        output_base_dir=output_base_dir,
        project_slug=_clean_text(_get_value(project, "slug")),
    )
    metadata_overrides = (
        metadata.get("listing_profile")
        if isinstance(metadata.get("listing_profile"), dict)
        else {}
    )
    # Explicit file overrides are written by the listing editor/regeneration UI
    # and therefore must win over older conception/database snapshots.
    overrides = {**concept_overrides, **metadata_overrides, **file_overrides}
    concept_title_candidates = _normalize_legacy_title_candidates(
        concept_overrides.get("title_candidates")
    )

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
    premise_text = _clean_text(metadata.get("premise"))
    # Preserve an intentional concept/editor one-liner verbatim. Only inferred
    # fallbacks need sentence extraction so a full premise cannot leak here.
    override_logline = _clean_text(overrides.get("logline"))
    metadata_logline = _clean_text(metadata.get("logline"))
    market_logline = _listing_logline(_get_nested(writing_profile, "market", "logline"))
    hook_logline = _listing_logline(_get_nested(metadata, "hook_spec", "one_liner"))
    concept_hook_logline = _listing_logline(
        _get_nested(metadata, "concept_contract", "hook_card", "one_liner")
    ) or _listing_logline(_get_nested(metadata, "hook_card", "one_liner"))
    if (
        override_logline
        and premise_text
        and _logline_mismatches_premise(
            override_logline,
            premise=premise_text,
            title=project_title,
            genre=_clean_text(_get_value(project, "genre")),
            sub_genre=_clean_text(_get_value(project, "sub_genre")),
            tags=tags,
        )
    ):
        override_logline = ""
    if (
        metadata_logline
        and premise_text
        and _logline_mismatches_premise(
            metadata_logline,
            premise=premise_text,
            title=project_title,
            genre=_clean_text(_get_value(project, "genre")),
            sub_genre=_clean_text(_get_value(project, "sub_genre")),
            tags=tags,
        )
    ):
        metadata_logline = ""
    logline_candidates = (
        ("listing_override", override_logline),
        ("metadata_logline", metadata_logline),
        ("hook_card", concept_hook_logline),
        ("market_logline", market_logline),
        ("legacy_hook_spec", hook_logline),
        (
            "reader_promise",
            _listing_logline(_get_nested(writing_profile, "market", "reader_promise")),
        ),
        ("premise", _listing_logline(premise_text)),
    )
    logline_source, logline = next(
        ((source, value) for source, value in logline_candidates if value),
        ("empty", ""),
    )
    tags = _supplement_listing_tags(
        tags,
        title=project_title,
        premise=premise_text,
        logline=logline,
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
        spine = metadata.get("story_spine") if isinstance(metadata.get("story_spine"), dict) else {}
        why_now = _first_sentence(spine.get("why_now"), max_chars=140)
        question = _first_sentence(spine.get("question"), max_chars=140)
        if is_en:
            promo_copy = _dedupe_strings(
                [
                    logline,
                    why_now,
                    question,
                ]
            )
        else:
            promo_copy = _dedupe_strings(
                [
                    logline,
                    why_now,
                    question,
                ]
            )
    promo_copy = [
        _first_sentence(item, max_chars=140)
        for item in promo_copy
        if _first_sentence(item, max_chars=140)
    ]

    reader_promise = _string_list(overrides.get("reader_promise"))
    if not reader_promise:
        reader_promise = _dedupe_strings(
            _string_list(_get_nested(writing_profile, "market", "selling_points"))
            + [_clean_text(_get_nested(writing_profile, "market", "reader_promise"))]
        )
    selling_points = _string_list(overrides.get("selling_points"))
    if not selling_points:
        selling_points = _dedupe_strings(
            _string_list(metadata.get("selling_points"))
            + _string_list(_get_nested(writing_profile, "market", "selling_points"))
        )
    if not selling_points:
        selling_points = _fallback_selling_points(
            title=project_title,
            logline=logline,
            tags=tags,
            primary_category=primary_category,
            is_en=is_en,
        )
    if len(reader_promise) < 2:
        reader_promise = _dedupe_strings(
            reader_promise
            + _fallback_reader_promise(
                title=project_title,
                logline=logline,
                selling_points=selling_points,
                primary_category=primary_category,
                is_en=is_en,
            )
        )
    target_audiences = _string_list(overrides.get("target_audiences"))
    if not target_audiences:
        target_audiences = _dedupe_strings(
            _string_list(metadata.get("target_audiences"))
            + _string_list(_get_nested(writing_profile, "market", "target_audiences"))
        )
    if not target_audiences:
        target_audiences = _fallback_target_audiences(
            primary_category=primary_category,
            secondary_category=secondary_category,
            tags=tags,
            is_en=is_en,
        )

    profile: dict[str, Any] = {
        "schema_version": LISTING_SCHEMA_VERSION,
        "book_id": (
            _clean_text(overrides.get("book_id")) or _clean_text(_get_value(project, "slug"))
        ),
        "target_platform": platform,
        "primary_title": _clean_text(overrides.get("primary_title")) or project_title,
        "recommended_subtitle": _clean_text(overrides.get("recommended_subtitle")),
        "logline": logline,
        "logline_source": logline_source,
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
        "selling_points": selling_points,
        "target_audiences": target_audiences,
        "public_emotion_kernel": (
            dict(overrides.get("public_emotion_kernel"))
            if isinstance(overrides.get("public_emotion_kernel"), dict)
            else (
                dict(metadata.get("public_emotion_kernel"))
                if isinstance(metadata.get("public_emotion_kernel"), dict)
                else {}
            )
        ),
        "compliance_boundary_kernel": (
            dict(overrides.get("compliance_boundary_kernel"))
            if isinstance(overrides.get("compliance_boundary_kernel"), dict)
            else (
                dict(metadata.get("compliance_boundary_kernel"))
                if isinstance(metadata.get("compliance_boundary_kernel"), dict)
                else {}
            )
        ),
        "not_recommended_categories": _string_list(overrides.get("not_recommended_categories")),
        "title_candidates": [],
        "legacy_title_candidates": _normalize_legacy_title_candidates(
            overrides.get("title_candidates")
        ),
        "previous_title": _clean_text(metadata.get("previous_title")),
        "previous_titles": _string_list(metadata.get("previous_titles")),
        "source_files": _string_list(file_overrides.get("source_files")),
        "load_warnings": _string_list(file_overrides.get("load_warnings")),
    }

    title_workflow = build_platform_title_workflow(
        profile,
        target_platform=platform,
        candidate_count=REQUIRED_TITLE_CANDIDATE_COUNT,
    )
    profile["title_candidates"] = title_workflow["candidates"]
    title_candidate_source = "platform_title_workflow"
    if concept_title_candidates:
        profile["title_candidates"] = _merge_preferred_title_candidates(
            concept_title_candidates,
            profile["title_candidates"],
            platform=platform,
        )
        title_candidate_source = "concept_lab+platform_title_workflow"
    if len(profile["title_candidates"]) < REQUIRED_TITLE_CANDIDATE_COUNT:
        profile["title_candidates"] = (
            profile["title_candidates"] + _fallback_title_candidates(profile)
        )[:REQUIRED_TITLE_CANDIDATE_COUNT]
        title_candidate_source = f"{title_candidate_source}+fallback"
    _attach_title_candidate_evaluations(
        profile["title_candidates"],
        title_workflow,
        profile=profile,
        platform=platform,
    )
    blocked_titles = _string_list(metadata.get("previous_titles")) + [
        _clean_text(metadata.get("previous_title"))
    ]
    if blocked_titles:
        profile["title_candidates"] = _drop_blocked_title_candidates(
            profile["title_candidates"],
            blocked_titles,
            (
                title_workflow.get("candidates", [])
                if isinstance(title_workflow.get("candidates"), list)
                else []
            )
            + _fallback_title_candidates(profile),
        )
        _attach_title_candidate_evaluations(
            profile["title_candidates"],
            title_workflow,
            profile=profile,
            platform=platform,
        )

    profile["title_workflow"] = {
        **title_workflow,
        "candidate_source": title_candidate_source,
        "legacy_candidate_count": len(profile.get("legacy_title_candidates") or []),
        "candidates": profile["title_candidates"],
        "candidate_count": len(profile["title_candidates"]),
    }

    profile["shelf_intro"] = (
        _limit_chars(overrides.get("shelf_intro"), max_chars=500)
        or _build_shelf_intro(profile, max_chars=500)
    )
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
