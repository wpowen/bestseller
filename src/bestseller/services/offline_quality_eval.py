"""Deterministic, offline quality evaluation for generated novel bundles.

This module deliberately does not call an LLM, the network, a database, or a
human review service.  It evaluates a manifest of chapter files and emits
evidence that can be reproduced on a developer machine or in CI.  It is a
static preflight, not a claim that a manuscript is commercial or榜单级.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict
import json
from pathlib import Path
import re
from statistics import fmean
from typing import Any

from bestseller.services.ai_flavor import detect as detect_ai_flavor
from bestseller.services.benchmark_structure import (
    aggregate_profiles,
    compare_to_baseline,
    load_structure_baseline,
    profile_chapter,
)

SCHEMA_VERSION = "offline-quality-eval/v1"
COMMERCIAL_NOT_TESTED = "not_tested"
DEFAULT_MIN_CHARS = 1800
DEFAULT_MAX_CHARS = 3500


def evaluate_manifest(
    manifest: Mapping[str, Any],
    *,
    base_dir: Path | None = None,
    baseline_path: Path | None = None,
) -> dict[str, Any]:
    """Evaluate every configured arm and return a JSON-serialisable report."""

    root = Path(base_dir or ".").resolve()
    arms = manifest.get("arms") or {}
    if not isinstance(arms, Mapping):
        raise ValueError("manifest.arms must be an object")
    reports = {
        str(name): _evaluate_arm(
            str(name), entries, manifest=manifest, root=root, baseline_path=baseline_path
        )
        for name, entries in arms.items()
    }
    comparison = _compare_arms(reports)
    statuses = [str(item.get("status")) for item in reports.values()]
    static_status = "pass"
    if not reports or any(status == "inconclusive" for status in statuses):
        static_status = "inconclusive"
    elif any(status == "fail" for status in statuses):
        static_status = "fail"
    elif any(status == "warn" for status in statuses):
        static_status = "warn"
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_version": str(manifest.get("evaluation_version") or "offline-v1"),
        "book": dict(manifest.get("book") or {}),
        "static_status": static_status,
        "commercial_validation": {
            "status": COMMERCIAL_NOT_TESTED,
            "reason": "离线静态评测不包含真实读者、编辑盲读、留存、付费或榜单数据。",
        },
        "arms": reports,
        "comparison": comparison,
    }


def write_report(report: Mapping[str, Any], output_dir: Path) -> dict[str, Path]:
    """Write ``report.json`` and a compact human-readable ``report.md``."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    md_path = output_dir / "report.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render a stable report without hiding unknown or unverified evidence."""

    lines = [
        "# 离线质量评测报告",
        "",
        f"- 静态状态: `{report.get('static_status', 'inconclusive')}`",
        "- 商业/榜单验证: `not_tested` (本报告不作商业水平结论)",
        "",
        "## 维度结果",
        "",
        "| 版本 | 状态 | 综合分 | AI 味分(越低越好) | 结构 | 事实 | 黄金三章 | "
        "角色辨识 | 读者承诺 |",
        "|---|---|---:|---:|---|---|---|---|---|",
    ]
    for name, arm in sorted((report.get("arms") or {}).items()):
        dims = arm.get("dimensions") or {}
        score = arm.get("score")
        lines.append(
            (
                "| {name} | {status} | {score} | {ai} | {structure} | {facts} | "
                "{golden} | {chars} | {promise} |"
            ).format(
                name=name,
                status=arm.get("status", "inconclusive"),
                score="—" if score is None else score,
                ai=_metric_value(dims.get("ai_flavor"), "ai_score"),
                structure=_metric_status(dims.get("structure")),
                facts=_metric_status(dims.get("fact_consistency")),
                golden=_metric_status(dims.get("golden_three")),
                chars=_metric_status(dims.get("character_distinguishability")),
                promise=_metric_status(dims.get("reader_promise")),
            )
        )
    comparison = report.get("comparison") or {}
    lines.extend(
        ["", "## A/B 对比", "", f"结论: `{comparison.get('recommendation', 'inconclusive')}`"]
    )
    for note in comparison.get("notes") or ():
        lines.append(f"- {note}")
    lines.extend(
        [
            "",
            "## 证据边界",
            "",
            "本报告只使用本地文件和确定性规则; 缺失文件/事实清单会标记为 `inconclusive`, "
            "不会被当作通过。",
            "",
        ]
    )
    return "\n".join(lines)


def _evaluate_arm(
    name: str,
    entries: object,
    *,
    manifest: Mapping[str, Any],
    root: Path,
    baseline_path: Path | None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    chapter_entries = entries
    if isinstance(entries, Mapping) and "chapters" in entries:
        chapter_entries = entries.get("chapters")
        metadata = {
            str(key): value
            for key, value in entries.items()
            if key != "chapters" and key in {"prompt_variant", "prompt_hash", "model", "notes"}
        }
    chapters = _load_chapters(chapter_entries, root)
    texts = [item for item in chapters if item["text"] is not None]
    dimensions = {
        "structure": _structure_metric(texts, manifest, baseline_path),
        "ai_flavor": _ai_metric(texts),
        "fact_consistency": _fact_metric(texts, manifest),
        "golden_three": _golden_three_metric(texts, manifest),
        "character_distinguishability": _character_metric(texts, manifest),
        "reader_promise": _promise_metric(texts, manifest),
    }
    known_scores = [
        float(item["score"]) for item in dimensions.values() if item.get("score") is not None
    ]
    score = round(fmean(known_scores), 2) if known_scores else None
    statuses = [str(item.get("status")) for item in dimensions.values()]
    status = "pass"
    if (
        not texts
        or any(item["error"] for item in chapters)
        or any(item == "inconclusive" for item in statuses)
    ):
        status = "inconclusive"
    elif any(item == "fail" for item in statuses):
        status = "fail"
    elif any(item == "warn" for item in statuses):
        status = "warn"
    return {
        "name": name,
        "metadata": metadata,
        "status": status,
        "score": score,
        # Keep reports compact and avoid copying the manuscript into JSON.
        "chapters": [{key: item[key] for key in ("chapter", "path", "error")} for item in chapters],
        "dimensions": dimensions,
    }


def _load_chapters(entries: object, root: Path) -> list[dict[str, Any]]:
    if isinstance(entries, Mapping):
        if "chapters" in entries:
            entries = entries.get("chapters")
        else:
            entries = [{"chapter": number, "path": path} for number, path in entries.items()]
    if isinstance(entries, Mapping):
        entries = [{"chapter": number, "path": path} for number, path in entries.items()]
    if not isinstance(entries, list):
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(entries, start=1):
        if isinstance(item, str):
            item = {"chapter": index, "path": item}
        if not isinstance(item, Mapping):
            continue
        number = int(item.get("chapter", item.get("number", index)))
        path_value = item.get("path")
        path = (root / str(path_value)).resolve() if path_value else None
        text: str | None = None
        error: str | None = None
        if path is None:
            error = "missing_path"
        else:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                error = f"read_error:{exc.__class__.__name__}"
        result.append(
            {"chapter": number, "path": str(path) if path else None, "text": text, "error": error}
        )
    return sorted(result, key=lambda row: row["chapter"])


def _structure_metric(
    texts: list[dict[str, Any]], manifest: Mapping[str, Any], baseline_path: Path | None
) -> dict[str, Any]:
    profiles = []
    chapter_rows = []
    target = manifest.get("quality") or {}
    min_chars = int(target.get("min_chars", DEFAULT_MIN_CHARS))
    max_chars = int(target.get("max_chars", DEFAULT_MAX_CHARS))
    for item in texts:
        profile = profile_chapter(item["text"] or "")
        chapter_rows.append(
            {"chapter": item["chapter"], "profile": asdict(profile) if profile else None}
        )
        if profile:
            profiles.append(profile)
    if not profiles:
        return _metric("inconclusive", None, {"reason": "no_chapter_profile"})
    aggregate = aggregate_profiles(profiles)
    in_range = sum(min_chars <= profile.chars <= max_chars for profile in profiles)
    ratio = in_range / len(profiles)
    status = "pass" if ratio >= 0.8 else "warn" if ratio >= 0.5 else "fail"
    findings = compare_to_baseline(profiles, load_structure_baseline(baseline_path), tier="t2")
    return _metric(
        status,
        round(ratio * 100, 2),
        {
            "target_chars": [min_chars, max_chars],
            "in_range_ratio": round(ratio, 4),
            "aggregate": aggregate,
            "baseline_findings": findings,
            "chapters": chapter_rows,
        },
    )


def _ai_metric(texts: list[dict[str, Any]]) -> dict[str, Any]:
    if not texts:
        return _metric("inconclusive", None, {"reason": "no_chapters"})
    reports = [
        detect_ai_flavor(item["text"] or "", language="zh", chapter_number=item["chapter"])
        for item in texts
    ]
    scores = [float(report.overall_score) for report in reports]
    categories = Counter(span.category for report in reports for span in report.spans)
    ai_score = round(fmean(scores), 2)
    status = "pass" if ai_score <= 25 else "warn" if ai_score < 50 else "fail"
    return _metric(
        status,
        round(100 - min(ai_score, 100), 2),
        {
            "ai_score": ai_score,
            "chapter_scores": {
                str(item["chapter"]): report.overall_score
                for item, report in zip(texts, reports, strict=True)
            },
            "pattern_counts": dict(sorted(categories.items())),
        },
    )


def _fact_metric(texts: list[dict[str, Any]], manifest: Mapping[str, Any]) -> dict[str, Any]:
    facts = manifest.get("facts") or {}
    if not facts:
        return _metric("inconclusive", None, {"reason": "facts_not_supplied"})
    body = "\n".join(item["text"] or "" for item in texts)
    violations: list[str] = []
    missing: list[str] = []
    protagonist = facts.get("protagonist") or {}
    name = str(protagonist.get("name") or "")
    if name and name not in body:
        missing.append(f"protagonist:{name}")
    age = protagonist.get("age")
    age_mentions = [int(value) for value in re.findall(r"(?<!\d)(\d{1,3})\s*[岁嵗]", body)]
    if age is not None and age_mentions and int(age) not in age_mentions:
        violations.append(f"protagonist_age_expected:{age},observed:{sorted(set(age_mentions))}")
    for term in facts.get("forbidden_terms") or ():
        if str(term) and str(term) in body:
            violations.append(f"forbidden:{term}")
    for label, terms in (facts.get("required_terms") or {}).items():
        values = terms if isinstance(terms, list) else [terms]
        if values and not any(str(term) in body for term in values):
            missing.append(str(label))
    status = "fail" if violations else "warn" if missing else "pass"
    score = 100 if status == "pass" else 70 if status == "warn" else 0
    return _metric(
        status,
        score,
        {"violations": violations, "missing": missing, "age_mentions": sorted(set(age_mentions))},
    )


def _golden_three_metric(
    texts: list[dict[str, Any]], manifest: Mapping[str, Any]
) -> dict[str, Any]:
    spec = manifest.get("golden_three") or {}
    if not spec:
        return _metric("inconclusive", None, {"reason": "golden_three_spec_not_supplied"})
    first = {item["chapter"]: item["text"] or "" for item in texts if item["chapter"] <= 3}
    if len(first) < int(spec.get("min_chapters", 3)):
        return _metric(
            "inconclusive", None, {"reason": "fewer_than_three_chapters", "found": sorted(first)}
        )
    hook_chapters = {number: text for number, text in first.items() if number <= 2}
    hook = _coverage(
        hook_chapters, spec.get("hook_keywords") or spec.get("required_keywords") or ()
    )
    payoff = (
        _coverage({3: first.get(3, "")}, spec.get("payoff_keywords") or ())
        if spec.get("payoff_keywords")
        else 1.0
    )
    score = round((hook + payoff) / 2 * 100, 2)
    status = "pass" if score >= 75 else "warn" if score >= 50 else "fail"
    return _metric(status, score, {"hook_coverage": hook, "payoff_coverage": payoff})


def _character_metric(texts: list[dict[str, Any]], manifest: Mapping[str, Any]) -> dict[str, Any]:
    characters = (manifest.get("facts") or {}).get("characters") or {}
    if not characters:
        return _metric("inconclusive", None, {"reason": "character_markers_not_supplied"})
    body = "\n".join(item["text"] or "" for item in texts)
    coverage: dict[str, float] = {}
    marker_owners: dict[str, list[str]] = {}
    for name, markers in characters.items():
        values = [
            str(marker)
            for marker in (markers if isinstance(markers, list) else [markers])
            if str(marker)
        ]
        coverage[str(name)] = (
            round(sum(marker in body for marker in values) / len(values), 4) if values else 0.0
        )
        for marker in values:
            if marker in body:
                marker_owners.setdefault(marker, []).append(str(name))
    overlap = {marker: owners for marker, owners in marker_owners.items() if len(owners) > 1}
    mean_coverage = fmean(coverage.values()) if coverage else 0.0
    score = round(mean_coverage * 100, 2)
    status = "pass" if score >= 75 and not overlap else "warn" if score >= 50 else "fail"
    return _metric(status, score, {"marker_coverage": coverage, "overlapping_markers": overlap})


def _promise_metric(texts: list[dict[str, Any]], manifest: Mapping[str, Any]) -> dict[str, Any]:
    spec = manifest.get("reader_promise") or {}
    keywords = spec.get("keywords") or ()
    if not keywords:
        return _metric("inconclusive", None, {"reason": "reader_promise_keywords_not_supplied"})
    stages = spec.get("stages") or {"opening": [1, 2, 3]}
    by_chapter = {item["chapter"]: item["text"] or "" for item in texts}
    stage_results: dict[str, float] = {}
    for stage, chapter_numbers in stages.items():
        body = "\n".join(by_chapter.get(int(number), "") for number in chapter_numbers)
        stage_results[str(stage)] = round(
            sum(str(keyword) in body for keyword in keywords) / len(keywords), 4
        )
    score = round(fmean(stage_results.values()) * 100, 2) if stage_results else None
    status = (
        "pass"
        if score is not None and score >= 75
        else "warn"
        if score is not None and score >= 50
        else "fail"
        if score is not None
        else "inconclusive"
    )
    return _metric(status, score, {"stage_coverage": stage_results, "keywords": list(keywords)})


def _compare_arms(arms: Mapping[str, Any]) -> dict[str, Any]:
    names = sorted(arms)
    if len(names) < 2:
        return {"recommendation": "inconclusive", "notes": ["至少需要两个版本才能进行 A/B 对比。"]}
    left, right = names[0], names[1]
    if arms[left].get("status") == "inconclusive" or arms[right].get("status") == "inconclusive":
        return {
            "arms": [left, right],
            "recommendation": "inconclusive",
            "notes": ["至少一个版本的静态证据不完整, 不能下 A/B 结论。"],
        }
    left_score, right_score = arms[left].get("score"), arms[right].get("score")
    if left_score is None or right_score is None:
        return {"recommendation": "inconclusive", "notes": ["至少一个版本没有可比较的静态分数。"]}
    delta = round(float(right_score) - float(left_score), 2)
    recommendation = right if delta >= 5 else left if delta <= -5 else "inconclusive"
    return {
        "arms": [left, right],
        "score_delta_right_minus_left": delta,
        "recommendation": recommendation,
        "notes": ["A/B 仅比较离线静态指标; 不代表真实读者或商业表现。"],
    }


def _coverage(chapters: Mapping[int, str], keywords: Sequence[object]) -> float:
    values = [str(keyword) for keyword in keywords if str(keyword)]
    if not values:
        return 1.0
    body = "\n".join(chapters.values())
    return sum(value in body for value in values) / len(values)


def _metric(status: str, score: float | None, evidence: Mapping[str, Any]) -> dict[str, Any]:
    payload = {"status": status, "score": score}
    payload.update(dict(evidence))
    return payload


def _metric_status(metric: Mapping[str, Any] | None) -> str:
    return str((metric or {}).get("status", "inconclusive"))


def _metric_value(metric: Mapping[str, Any] | None, key: str) -> str:
    value = (metric or {}).get(key)
    return "—" if value is None else str(value)


__all__ = [
    "COMMERCIAL_NOT_TESTED",
    "SCHEMA_VERSION",
    "evaluate_manifest",
    "render_markdown",
    "write_report",
]
