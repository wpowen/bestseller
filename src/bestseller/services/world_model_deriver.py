"""Derivation of the per-book :class:`WorldModel` — the genre-neutral engine.

Turns ``(premise, genre, baseline)`` into a structured world model by *differential
mapping*: take the reality baseline, inject the premise's axioms as a perturbation,
and derive how every social dimension is forced to change, plus the ripples and the
fault lines the story stands on.

**Genre never drives world content.** Genre is passed only as soft context with an
explicit "do not default to the genre's stock world" instruction; the actual laws
are derived from THIS book's axioms. This is the structural cure for cross-book
homogenisation — different premises diff their baseline differently, so they
cannot produce the same world.

Split into pure, unit-testable pieces (mirrors ``services/ideology_kernel.py``):
* :func:`build_world_model_system_prompt` / :func:`build_world_model_user_prompt`
* :func:`fallback_world_model` — deterministic, schema-valid, premise-seeded.
* :func:`parse_world_model` — JSON → validated model (fallback-safe), scored.
* :func:`derive_world_model` — production entry via ``complete_text``.
"""

# ruff: noqa: RUF001, E501, ANN401, S112

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.world_model import WorldModel, world_model_from_dict
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.world_dimensions import (
    law_specificity,
    load_world_dimensions,
    render_dimensions_prompt_block,
    select_baseline,
)
from bestseller.settings import AppSettings

_MAX_AXIOMS = 3


# ---------------------------------------------------------------------------
# Axiom extraction (deterministic, premise-only)
# ---------------------------------------------------------------------------


def extract_axioms(premise: str, *, limit: int = _MAX_AXIOMS) -> list[str]:
    """Split the premise into 1-``limit`` atomic what-if clauses (heuristic).

    The LLM does the real extraction in production; this gives the fallback a
    premise-anchored axiom set so derived laws can reference it.
    """

    text = (premise or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"[。.;；\n!?！?]+", text) if p.strip()]
    if not parts:
        parts = [text]
    # Prefer the clauses that carry the speculative core (longest, distinct).
    parts = sorted(dict.fromkeys(parts), key=len, reverse=True)[:limit]
    return [p[:80] for p in parts]


# ---------------------------------------------------------------------------
# Deterministic fallback — valid, premise-anchored, low-specificity by nature
# ---------------------------------------------------------------------------

# A small, high-leverage subset of dimensions the fallback always populates so
# the scaffold is never empty. The LLM is expected to cover the full table.
_FALLBACK_DIMENSIONS = (
    "value_and_currency",
    "power_and_institutions",
    "violence_and_security",
    "class_and_stratification",
    "life_death_and_time",
)


def fallback_world_model(
    *,
    premise: str = "",
    genre: str | None = None,
    baseline_hint: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic, schema-valid, premise-anchored WorldModel payload.

    Never raises. Genre is used only to pick the baseline substrate (era), never
    to inject story content. Laws are generic-but-anchored placeholders; the LLM
    replaces them with truly derived rules.
    """

    table = load_world_dimensions()
    axioms = extract_axioms(premise) or ["(待补:本书核心设定公理)"]
    anchor = axioms[0]
    baseline_key, rationale = select_baseline(genre=genre, premise=premise, table=table)
    if baseline_hint:
        baseline_key = baseline_hint
    base = table.baseline(baseline_key)
    baseline_label = base.label if base else baseline_key

    dim_by_key = {d.key: d for d in table.dimensions}
    laws: list[dict[str, Any]] = []
    for dim_key in _FALLBACK_DIMENSIONS:
        dim = dim_by_key.get(dim_key)
        if dim is None:
            continue
        laws.append(
            {
                "dimension": dim_key,
                "baseline": f"在『{baseline_label}』底座下,{dim.question}",
                "delta": f"受公理「{anchor}」影响,本维度相对基线被迫改变(待具体推演)。",
                "order": dim.order,
                "derived_from": [anchor],
                "enforcement": f"正文涉及[{dim_key}]时须与公理「{anchor}」的后果一致,不得回退到基线常态而无理由。",
                "story_use": dim.ripple_hint,
            }
        )

    payload: dict[str, Any] = {
        "version": 1,
        "axioms": axioms,
        "baseline": baseline_label,
        "baseline_rationale": rationale,
        "uniqueness_principle": "每条世界规律必须可追溯到本书公理;具体设定必须从规律生长,不得凭空补充。",
        "world_laws": laws,
        "fault_lines": [
            {
                "name": "新规律 × 残留旧秩序",
                "tension": f"公理「{anchor}」带来的新规律与基线『{baseline_label}』的旧秩序相互挤压。",
                "world_law_refs": list(_FALLBACK_DIMENSIONS),
                "used_by_protagonist": True,
            }
        ],
        "content_settings": [],
    }
    return payload


# ---------------------------------------------------------------------------
# Prompt assembly (pure)
# ---------------------------------------------------------------------------


def build_world_model_system_prompt(*, language: str = "zh") -> str:
    is_en = str(language or "").lower().startswith("en")
    if is_en:
        return (
            "You are a world-model architect. Build a book's world by DIFFERENTIAL "
            "derivation: take a reality baseline, inject the premise's 1-3 axioms as a "
            "perturbation, and derive how each social dimension is FORCED to change, the "
            "ripples (2nd/3rd order), and the fault lines the story stands on. Output ONE "
            "valid JSON WorldModel object only. HARD RULES: (1) every world_law MUST be "
            "derived_from an axiom — no free-floating rules; (2) NEVER default to the genre's "
            "stock world; two same-genre books must yield different worlds; (3) each law needs "
            "a behavioural, checkable 'enforcement' assertion (what the prose must/must-not do); "
            "(4) content_settings must each trace to a law. Keep JSON ≤ 7000 chars."
        )
    return (
        "你是『世界模型』架构师。用**差分推演**造世:以一个现实基线为底座,把本书前提的 "
        "1-3 条公理当作扰动注入,推导每个社会维度相对基线被**迫**改变了什么、二阶三阶涟漪、"
        "以及故事所站的断层线。只输出一个合法的 WorldModel JSON 对象,不要解释。"
        "【硬性规则】(1) 每条 world_law 必须 derived_from 某条公理——禁止凭空规则;"
        "(2) 严禁套用题材的默认世界,同题材两本书必须得到不同世界;"
        "(3) 每条规律必须带一句行为级、可校验的 enforcement 断言(正文必须/禁止怎样,例如"
        "『人人可飞:出现地面车辆通勤须显式给理由』);"
        "(4) content_settings 每项必须 derived_from_law 可追溯。整个 JSON ≤ 7000 字符。"
    )


def build_world_model_user_prompt(
    *,
    premise: str,
    genre: str | None = None,
    baseline_hint: str | None = None,
    fallback_payload: dict[str, Any] | None = None,
    language: str = "zh",
) -> str:
    is_en = str(language or "").lower().startswith("en")
    dimensions_block = render_dimensions_prompt_block()
    fallback = fallback_payload or fallback_world_model(
        premise=premise, genre=genre, baseline_hint=baseline_hint
    )
    schema_hint = json.dumps(fallback, ensure_ascii=False, indent=2)
    genre_ctx = (
        f"题材(仅作背景,不得据此套用默认世界):{genre}\n" if genre else ""
    )
    if is_en:
        return (
            f"Premise:\n{premise}\n\n"
            f"{('Genre (context only — do NOT default to its stock world): ' + genre + chr(10)) if genre else ''}"
            f"{dimensions_block}\n\n"
            "Steps: (1) extract 1-3 axioms; (2) confirm the baseline substrate; (3) for EACH "
            "dimension diff baseline→delta with a checkable enforcement; (4) push ripples to the "
            "stated order; (5) extract fault_lines and mark the one the protagonist stands on; "
            "(6) derive content_settings, each derived_from_law.\n"
            f"Schema-valid MINIMUM (make it premise-specific, do NOT copy verbatim):\n{schema_hint}"
        )
    return (
        f"前提:\n{premise}\n\n"
        f"{genre_ctx}"
        f"{dimensions_block}\n\n"
        "步骤:(1) 提取 1-3 条公理;(2) 确认/选择基线底座;(3) 对**每个维度**做差分,写出 "
        "baseline→delta 并给可校验的 enforcement;(4) 把涟漪推到该维度标注的阶数;(5) 提取 "
        "fault_lines 并标出主角所站的那条(used_by_protagonist=true);(6) 派生 content_settings,"
        "每项 derived_from_law 可追溯。\n"
        f"以下为 schema 最低结构(请据前提做出本书独有的具体推演,不要照抄):\n{schema_hint}"
    )


# ---------------------------------------------------------------------------
# Parsing + scoring
# ---------------------------------------------------------------------------


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    unfenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I | re.S).strip()
    candidates = [stripped, unfenced]
    match = re.search(r"\{.*\}", unfenced, flags=re.S)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    for candidate in candidates:
        try:
            from json_repair import repair_json

            repaired = repair_json(candidate, return_objects=True)
        except Exception:
            continue
        if isinstance(repaired, dict):
            return repaired
    return {}


def _score_laws_in_place(payload: dict[str, Any]) -> None:
    """Fill each law's ``specificity`` = anchoring to the model's axioms."""

    axioms = payload.get("axioms") or []
    if isinstance(axioms, str):
        axioms = [axioms]
    laws = payload.get("world_laws")
    if not isinstance(laws, list):
        return
    for law in laws:
        if not isinstance(law, dict):
            continue
        law_text = " ".join(
            str(law.get(k, "")) for k in ("delta", "enforcement", "story_use")
        )
        law["specificity"] = law_specificity(law_text, [str(a) for a in axioms])


def parse_world_model(
    text: str,
    *,
    premise: str = "",
    genre: str | None = None,
    baseline_hint: str | None = None,
) -> WorldModel:
    """Parse LLM JSON into a validated, scored WorldModel (fallback-safe).

    Keeps the LLM's derived content but backfills missing structural pieces from
    the deterministic fallback, then scores each law's specificity (anchoring).
    """

    fallback = fallback_world_model(premise=premise, genre=genre, baseline_hint=baseline_hint)
    payload = _parse_json_object(text)
    if not payload:
        _score_laws_in_place(fallback)
        return world_model_from_dict(fallback)
    merged = {**fallback, **{k: v for k, v in payload.items() if v not in (None, "", [], {})}}
    # The world_laws / fault_lines from the LLM win wholesale when present.
    if not merged.get("world_laws"):
        merged["world_laws"] = fallback.get("world_laws")
    if not merged.get("axioms"):
        merged["axioms"] = fallback.get("axioms")
    _score_laws_in_place(merged)
    try:
        return world_model_from_dict(merged)
    except Exception:
        _score_laws_in_place(fallback)
        return world_model_from_dict(fallback)


# ---------------------------------------------------------------------------
# Production entry point
# ---------------------------------------------------------------------------


async def derive_world_model(
    session: AsyncSession,
    settings: AppSettings,
    *,
    premise: str,
    genre: str | None = None,
    baseline_hint: str | None = None,
    language: str = "zh",
    project_id: Any | None = None,
    workflow_run_id: Any | None = None,
) -> WorldModel:
    """Derive the book's WorldModel via the planner LLM (fallback-safe)."""

    fallback = fallback_world_model(premise=premise, genre=genre, baseline_hint=baseline_hint)
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="planner",
            system_prompt=build_world_model_system_prompt(language=language),
            user_prompt=build_world_model_user_prompt(
                premise=premise,
                genre=genre,
                baseline_hint=baseline_hint,
                fallback_payload=fallback,
                language=language,
            ),
            fallback_response=json.dumps(fallback, ensure_ascii=False),
            prompt_template="world_model",
            prompt_version="v1",
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            metadata={"artifact": "world_model", "genre": genre or ""},
            max_tokens_override=4600,
        ),
    )
    return parse_world_model(
        completion.content, premise=premise, genre=genre, baseline_hint=baseline_hint
    )


__all__ = [
    "build_world_model_system_prompt",
    "build_world_model_user_prompt",
    "derive_world_model",
    "extract_axioms",
    "fallback_world_model",
    "parse_world_model",
]
