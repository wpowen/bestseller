"""Story-idea attractiveness judge (LLM 9-dim, genre-aware, fail-open).

Answers: *is this premise/setting a good, compelling story worth writing — one a
reader would be drawn into?*  Scores the 9-dimension rubric in
``config/story_appeal.yaml`` → ``premise_rubric``.

Design (mirrors ``outline_llm_judge.py``):
  1. Deterministic pre-pass extracts the 11 psychological triggers + per-dim
     proxy scores from premise/synopsis (genre lexicons).  These become both
     *evidence* injected into the prompt and the ``fallback_response`` — so when
     the LLM is unavailable/stubbed/failed, the judge still returns a meaningful
     deterministic verdict (never raises).
  2. The LLM enriches the scores and writes rationale + suggestions.
  3. One-vote-veto gating (via :mod:`bestseller.services.story_appeal`) caps the
     grade when a critical dimension is below its floor.
  4. Verdict cache (reused from ``outline_llm_judge``) avoids non-deterministic
     pass/fail flips for byte-identical inputs.
"""

from __future__ import annotations

# ruff: noqa: ANN401, RUF001 — Chinese labels + Any session/settings.
import json
import logging
import re
from typing import Any

from bestseller.domain.appeal import AppealDimension, PremiseAppealVerdict
from bestseller.services.story_appeal import (
    apply_premise_gating,
    grade_from_total,
    resolve_genre_lexicon,
)

logger = logging.getLogger(__name__)

PREMISE_APPEAL_JUDGE_TYPE = "premise_appeal"
_SHORT_FORM_MAX_CHAPTERS = 24

_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")
_DIGIT_RE = re.compile(r"[0-9一二三四五六七八九十百千万亿]")
_FIRST_PERSON_RE = re.compile(r"我(?!们)|吾")  # exclude 我们 (narrator filler)
_ANOMALY_RE = re.compile(r"反常|诡异|居然|竟然|离奇|莫名|不该|本不该|没有理由|异常")
_SUSTAIN_RE = re.compile(r"升级|系统|循环|任务|关系网|阵营|势力|境界|副本|世界观|签到|进化|图鉴")


def _cjk_len(text: str) -> int:
    return len(_CJK_RE.findall(text or ""))


def _hits(text: str, terms: Any) -> int:
    if not text or not isinstance(terms, (list, tuple)):
        return 0
    return sum(1 for t in terms if t and str(t) in text)


def _lex(lexicon: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = lexicon.get(key) if isinstance(lexicon, dict) else None
    return tuple(str(x) for x in raw) if isinstance(raw, (list, tuple)) else ()


def _clamp(v: float, lo: float = 0.0, hi: float = 5.0) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Deterministic pre-pass: psychological triggers + per-dim proxy scores
# ---------------------------------------------------------------------------

_DIM_KEYS: tuple[str, ...] = (
    "concept_strength",
    "novelty",
    "conflict_stakes",
    "emotional_value",
    "hook_suspense",
    "immersion",
    "sustainability",
    "audience_fit",
    "structure_pace",
)


def _detect_triggers(text: str, head: str, lex: dict[str, Any]) -> list[str]:
    fired: list[str] = []
    curiosity = _hits(text, _lex(lex, "curiosity_markers")) + len(re.findall(r"[？?]", text))
    has_concrete = bool(_DIGIT_RE.search(text)) or bool(re.search(r"[“”\"]", text))
    spoiled = _hits(text, _lex(lex, "spoiler_markers")) > 0
    if curiosity > 0 and not spoiled:
        fired.append("T1_curiosity_gap")
    if has_concrete and curiosity > 0:
        fired.append("T2_calibrated_gap")
    if _hits(text, _lex(lex, "high_arousal_emotion")) > 0:
        fired.append("T3_high_arousal")
    if _hits(text, _lex(lex, "reader_anchors")) > 0 or _hits(
        text, _lex(lex, "identity_markers")
    ) > 0:
        fired.append("T4_immersion_anchor")
    if _hits(text, _lex(lex, "reversal_markers")) > 0:
        fired.append("T5_wish_preview")
    if _hits(text, _lex(lex, "cost_markers")) > 0:
        fired.append("T6_loss_tension")
    if _hits(head, _lex(lex, "conflict_verbs")) > 0:
        fired.append("T7_in_medias_res")
    if 2 <= (curiosity) <= 5:
        fired.append("T8_suspense_stack")
    if bool(_ANOMALY_RE.search(text)):
        fired.append("T10_novelty_anomaly")
    if has_concrete and not (curiosity == 0):
        fired.append("T11_payoff_credible")
    return fired


def _deterministic_scores(
    premise: str, synopsis: str, lex: dict[str, Any]
) -> dict[str, float]:
    text = f"{premise} {synopsis}"

    identity = _hits(text, _lex(lex, "identity_markers"))
    conflict = _hits(text, _lex(lex, "conflict_verbs"))
    arousal = _hits(text, _lex(lex, "high_arousal_emotion"))
    reversal = _hits(text, _lex(lex, "reversal_markers"))
    cost = _hits(text, _lex(lex, "cost_markers"))
    curiosity = _hits(text, _lex(lex, "curiosity_markers")) + len(re.findall(r"[？?]", text))
    anchors = _hits(text, _lex(lex, "reader_anchors"))
    red = _hits(text, _lex(lex, "red_ocean_tropes"))
    palette = _hits(text, _lex(lex, "emotion_palette"))
    sustain = len(_SUSTAIN_RE.findall(text)) + _hits(text, _lex(lex, "golden_finger_forms"))
    premise_len = _cjk_len(premise)

    # Neutral baseline ≈ 2.6 ("unknown, lean-pass") so the deterministic
    # FALLBACK (LLM down/stubbed) never spuriously fails a genuinely strong
    # premise and triggers regeneration — only clear negatives (red-ocean
    # tropes, no conflict/emotion) pull a dimension down. The LLM provides the
    # ceiling (recommend). See [[scene-richness-gate-self-harm]] for why a
    # harsh deterministic floor is an anti-pattern.
    base = 2.6
    scores = {
        "concept_strength": _clamp(
            1.4
            + (1.3 if identity else 0)
            + (1.3 if conflict or arousal else 0)
            + (1.0 if reversal else 0)
            + (0.6 if 0 < premise_len <= 70 else 0)
        ),
        "novelty": _clamp(3.6 - red * 1.0 + (0.6 if reversal else 0) + (0.4 if anchors else 0)),
        "conflict_stakes": _clamp(
            base - 1.0 + min(conflict, 2) * 1.0 + (1.3 if cost else 0) + min(arousal, 2) * 0.5
        ),
        "emotional_value": _clamp(base - 0.6 + min(arousal, 3) * 0.7 + min(palette, 3) * 0.5),
        "hook_suspense": _clamp(base - 0.4 + min(curiosity, 4) * 0.6 + (0.8 if conflict else 0)),
        "immersion": _clamp(
            base - 0.2 + (1.2 if anchors else 0) + (0.8 if identity else 0)
            + (1.0 if _FIRST_PERSON_RE.search(synopsis) else 0)
        ),
        "sustainability": _clamp(base - 0.1 + min(sustain, 4) * 0.7),
        "audience_fit": _clamp(
            base + min(palette + anchors, 4) * 0.45 - (0.6 if red >= 3 else 0)
        ),
        "structure_pace": _clamp(
            base + (0.9 if reversal and cost else 0) + (0.5 if conflict else 0)
        ),
    }
    return scores


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------


def _build_system_prompt(rubric: dict[str, Any], genre_context: Any, language: str) -> str:
    is_en = str(language or "").lower().startswith("en")
    lines: list[str] = []
    for key in _DIM_KEYS:
        spec = rubric.get(key, {}) if isinstance(rubric, dict) else {}
        if isinstance(spec, dict):
            lines.append(
                f"- {key}（{spec.get('label', key)}）：高分=「{spec.get('high', '')}」；"
                f"低分=「{spec.get('low', '')}」"
            )
    rubric_block = "\n".join(lines)
    genre_block = ""
    try:
        if genre_context is not None:
            genre_block = genre_context.render_story_logic_block("zh")
    except Exception:
        genre_block = ""
    if is_en:
        head = (
            "You are a top web-novel acquisitions editor. Judge whether this "
            "STORY IDEA is compelling enough that readers click and keep reading. "
            "Score each dimension 0-5 (decimals ok). Output STRICT JSON only."
        )
    else:
        head = (
            "你是顶级网文签约主编。判断这个【故事创意/设定】是否足够吸引人——"
            "读者会不会忍不住点进来并追读。务必按榜单级标准、且按本题材的爽点逻辑评判，"
            "不要因题材不同而误判冲突类型。每个维度打 0-5 分（可小数）。只输出严格 JSON。"
        )
    return (
        f"{head}\n\n# 评分维度（rubric）\n{rubric_block}\n\n{genre_block}\n\n"
        "# 输出格式（严格 JSON）\n"
        '{"dimension_scores": {"concept_strength": 0-5, ...九个维度全给},'
        ' "rationale": {"<dim>": "一句话依据"},'
        ' "suggestions": ["针对最弱项的具体改进，3-5 条"],'
        ' "overall_comment": "一句话总评"}'
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    s = (text or "").strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(s[start : end + 1])
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def evaluate_premise_appeal(
    session: Any,
    settings: Any,
    *,
    premise: str,
    synopsis: str,
    title: str,
    tags: list[str] | None = None,
    writing_profile: dict[str, Any] | None = None,
    genre: str | None,
    sub_genre: str | None = None,
    chapter_count: int = 0,
    project_slug: str | None = None,
    judge_model_key: str | None = None,
    config: dict[str, Any] | None = None,
    lexicon: dict[str, Any] | None = None,
    workflow_run_id: Any | None = None,
) -> PremiseAppealVerdict:
    """Score story-idea attractiveness. Never raises (fail-open to det. scores)."""

    from bestseller.services.story_appeal import load_story_appeal_config

    cfg = config if config is not None else load_story_appeal_config()
    lex = lexicon if lexicon is not None else resolve_genre_lexicon(genre, sub_genre)
    rubric = cfg.get("premise_rubric", {}) if isinstance(cfg, dict) else {}
    is_long = chapter_count <= 0 or chapter_count > _SHORT_FORM_MAX_CHAPTERS

    det_scores = _deterministic_scores(premise, synopsis, lex)
    triggers = _detect_triggers(
        f"{premise} {synopsis}", synopsis[:40] + premise[:40], lex
    )

    # Fallback JSON = deterministic verdict. Used verbatim if the LLM is
    # unavailable/stubbed/fails, so the judge always yields a real verdict.
    fallback_payload = {
        "dimension_scores": {k: round(v, 2) for k, v in det_scores.items()},
        "rationale": {},
        "suggestions": [],
        "overall_comment": "deterministic-fallback",
    }
    fallback_json = json.dumps(fallback_payload, ensure_ascii=False)

    parsed: dict[str, Any] = {}
    llm_run_id: str | None = None
    llm_used = False
    try:
        parsed, llm_run_id = await _run_llm_judge(
            session,
            settings,
            premise=premise,
            synopsis=synopsis,
            title=title,
            tags=tags or [],
            genre=genre,
            sub_genre=sub_genre,
            det_scores=det_scores,
            triggers=triggers,
            rubric=rubric,
            fallback_json=fallback_json,
            project_slug=project_slug,
            judge_model_key=judge_model_key,
            workflow_run_id=workflow_run_id,
            cfg=cfg,
        )
        llm_used = bool(llm_run_id) and parsed.get("overall_comment") != "deterministic-fallback"
    except Exception:
        logger.warning("premise appeal LLM judge failed; deterministic only", exc_info=True)
        parsed = fallback_payload

    return _assemble_verdict(
        parsed=parsed,
        det_scores=det_scores,
        triggers=triggers,
        rubric=rubric,
        cfg=cfg,
        is_long=is_long,
        llm_used=llm_used,
        llm_run_id=llm_run_id,
    )


async def _run_llm_judge(
    session: Any,
    settings: Any,
    *,
    premise: str,
    synopsis: str,
    title: str,
    tags: list[str],
    genre: str | None,
    sub_genre: str | None,
    det_scores: dict[str, float],
    triggers: list[str],
    rubric: dict[str, Any],
    fallback_json: str,
    project_slug: str | None,
    judge_model_key: str | None,
    workflow_run_id: Any | None,
    cfg: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    from bestseller.services.judge_genre_context import (
        resolve_judge_genre_context,
    )
    from bestseller.services.llm import (
        LLMCompletionRequest,
        complete_text,
    )

    try:
        genre_context = resolve_judge_genre_context(genre=genre, sub_genre=sub_genre)
        language = "zh-CN"
    except Exception:
        genre_context = None
        language = "zh-CN"

    # Verdict cache — reuse the outline judge's hashing/store helpers.
    input_hash = None
    if project_slug:
        try:
            from bestseller.services.outline_llm_judge import (
                compute_judge_input_hash,
                load_cached_judge_verdict,
            )

            input_hash = compute_judge_input_hash(
                {"premise": premise, "synopsis": synopsis, "title": title, "tags": tags}
            )
            cached = await load_cached_judge_verdict(
                session,
                project_slug=project_slug,
                judge_type=PREMISE_APPEAL_JUDGE_TYPE,
                input_hash=input_hash,
            )
            if cached:
                return cached, cached.get("_llm_run_id")
        except Exception:
            input_hash = None

    payload = {
        "title": title,
        "premise": premise[:1200],
        "synopsis": synopsis[:1200],
        "tags": tags[:10],
        "genre": genre,
        "sub_genre": sub_genre,
        "deterministic_signals": {
            "scores": {k: round(v, 2) for k, v in det_scores.items()},
            "triggers_fired": triggers,
        },
    }
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=_build_system_prompt(rubric, genre_context, language),
            user_prompt=(
                "## 任务\n判断下面这个故事创意的吸引力，按 system 的 rubric 打分。\n"
                "确定性信号仅供参考（triggers_fired 是已检测到的心理钩子），"
                "最终以创意实质质量为准。\n\n"
                f"## 待评创意\n```json\n{json.dumps(payload, ensure_ascii=False)[:5000]}\n```\n\n"
                "## 立即输出严格 JSON（九个维度都要给分）。"
            ),
            fallback_response=fallback_json,
            prompt_template="premise_appeal_judge",
            prompt_version="v1",
            model_catalog_key=judge_model_key
            or _resolve_judge_model_key(settings),
            workflow_run_id=workflow_run_id,
            metadata={"judge_scope": "premise_appeal", "genre": str(genre or "")},
            max_tokens_override=2048,
        ),
    )
    parsed = _parse_json_object(completion.content)
    run_id = str(completion.llm_run_id) if completion.llm_run_id else None

    # Store verdict for byte-identical reruns (best-effort).
    if project_slug and input_hash and parsed:
        try:
            from bestseller.services.outline_llm_judge import (
                store_judge_verdict,
            )

            to_cache = dict(parsed)
            to_cache["_llm_run_id"] = run_id
            await store_judge_verdict(
                session,
                project_slug=project_slug,
                judge_type=PREMISE_APPEAL_JUDGE_TYPE,
                input_hash=input_hash,
                verdict=to_cache,
            )
        except Exception:
            logger.debug("premise appeal verdict cache store failed", exc_info=True)

    return parsed, run_id


def _resolve_judge_model_key(settings: Any) -> str | None:
    try:
        from bestseller.services.chapter_llm_quality_judge import (
            resolve_commercial_judge_model_key,
        )

        return resolve_commercial_judge_model_key(settings)
    except Exception:
        return None


def _assemble_verdict(
    *,
    parsed: dict[str, Any],
    det_scores: dict[str, float],
    triggers: list[str],
    rubric: dict[str, Any],
    cfg: dict[str, Any],
    is_long: bool,
    llm_used: bool,
    llm_run_id: str | None,
) -> PremiseAppealVerdict:
    llm_scores = parsed.get("dimension_scores", {}) if isinstance(parsed, dict) else {}
    rationale_map = parsed.get("rationale", {}) if isinstance(parsed, dict) else {}
    weight_key = "weight_long" if is_long else "weight_short"

    dims: list[AppealDimension] = []
    weighted = 0.0
    total_weight = 0.0
    for key in _DIM_KEYS:
        spec = rubric.get(key, {}) if isinstance(rubric, dict) else {}
        weight = (
            float(spec.get(weight_key, spec.get("weight_long", 0)))
            if isinstance(spec, dict)
            else 0.0
        )
        label = str(spec.get("label", key)) if isinstance(spec, dict) else key
        score = _coerce_score(llm_scores.get(key), det_scores.get(key, 0.0))
        rationale = str(rationale_map.get(key, "")) if isinstance(rationale_map, dict) else ""
        dims.append(
            AppealDimension(key=key, label=label, score=score, weight=weight, rationale=rationale)
        )
        weighted += (score / 5.0) * weight
        total_weight += weight

    total = (weighted / total_weight * 100.0) if total_weight else 0.0
    base_grade = grade_from_total(total, cfg)
    gated_grade, caps = apply_premise_gating(
        tuple(dims), base_grade, config=cfg, is_long=is_long
    )

    suggestions = _coerce_suggestions(parsed.get("suggestions"))
    if not suggestions:
        suggestions = _fallback_suggestions(dims)
    findings = tuple(
        f"[{d.label}] {d.rationale or '评分偏低'}" for d in dims if d.score < 3.0
    )

    return PremiseAppealVerdict(
        total=total,
        grade=base_grade,
        gated_grade=gated_grade,
        dimensions=tuple(dims),
        triggers_fired=tuple(triggers),
        findings=findings,
        suggestions=tuple(suggestions),
        gating_caps=caps,
        llm_used=llm_used,
        llm_run_id=llm_run_id,
    )


def _coerce_score(llm_value: Any, det_value: float) -> float:
    try:
        if llm_value is None:
            return _clamp(det_value)
        v = float(llm_value)
        # accept 0-1 or 0-5 scales from the LLM
        if 0.0 <= v <= 1.0 and det_value > 1.0:
            v *= 5.0
        return _clamp(v)
    except (TypeError, ValueError):
        return _clamp(det_value)


def _coerce_suggestions(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()][:5]
    return []


def _fallback_suggestions(dims: list[AppealDimension]) -> list[str]:
    weak = sorted(dims, key=lambda d: d.score)[:3]
    out = []
    for d in weak:
        if d.score < 3.5:
            out.append(f"强化「{d.label}」：当前 {d.score:.1f}/5")
    return out


__all__ = ["PREMISE_APPEAL_JUDGE_TYPE", "evaluate_premise_appeal"]
