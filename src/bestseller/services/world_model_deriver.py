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
import hashlib
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.world_model import (
    ContentSetting,
    FaultLine,
    WorldLaw,
    WorldModel,
    world_model_from_dict,
)
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
        "baseline_layers": [],
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


def compile_world_model_from_world_spec(
    *,
    premise: str,
    world_spec: dict[str, Any],
) -> WorldModel:
    """Compile the only injectable WorldModel from an approved WorldSpec.

    This is deliberately deterministic.  The previous production path asked a
    second LLM to invent a broad differential world from the premise and then
    promoted it above WorldSpec as a "constitution".  Its fixed dimension menu
    made unrelated finance, mortality, transport, and kinship systems appear in
    every genre.  Compilation keeps every emitted fact traceable to the already
    validated WorldSpec and cannot add a new story domain.
    """

    canonical = json.dumps(world_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    world_name = str(world_spec.get("world_name") or "").strip()
    world_premise = str(world_spec.get("world_premise") or premise or "").strip()
    axioms = [text for text in (str(premise or "").strip(), world_premise) if text]
    axioms = list(dict.fromkeys(axioms))[:2]
    laws: list[dict[str, Any]] = []
    raw_rules = world_spec.get("rules")
    if isinstance(raw_rules, list):
        for index, raw_rule in enumerate(raw_rules, start=1):
            if not isinstance(raw_rule, dict):
                continue
            name = str(raw_rule.get("rule_name") or raw_rule.get("name") or f"规则{index}").strip()
            description = str(raw_rule.get("description") or raw_rule.get("rule") or "").strip()
            consequence = str(
                raw_rule.get("story_consequence")
                or raw_rule.get("consequence")
                or description
            ).strip()
            if not description or not consequence:
                continue
            dimension = f"approved_world_rule_{index}"
            laws.append(
                {
                    "dimension": dimension,
                    "baseline": "",
                    "delta": f"{name}：{description}",
                    "order": 1,
                    "derived_from": [name],
                    "depends_on": [],
                    "enforcement": consequence,
                    "tiers": [],
                    "story_use": consequence,
                    "specificity": 1.0,
                }
            )

    power = world_spec.get("power_system")
    if isinstance(power, dict):
        power_name = str(power.get("name") or "成长体系").strip()
        acquisition = str(power.get("acquisition_method") or "").strip()
        hard_limits = str(power.get("hard_limits") or "").strip()
        power_delta = "；".join(part for part in (power_name, acquisition) if part)
        if power_delta and hard_limits:
            tier_steps: list[dict[str, str]] = []
            progression = power.get("tier_progression")
            if isinstance(progression, list):
                for raw_step in progression:
                    if not isinstance(raw_step, dict):
                        continue
                    tier = str(raw_step.get("tier") or "").strip()
                    value = str(
                        raw_step.get("breakthrough_cost")
                        or raw_step.get("bottleneck")
                        or ""
                    ).strip()
                    if tier and value:
                        tier_steps.append({"tier": tier, "value": value})
            laws.append(
                {
                    "dimension": "approved_power_system",
                    "baseline": "",
                    "delta": power_delta,
                    "order": 1,
                    "derived_from": [power_name],
                    "depends_on": [],
                    "enforcement": hard_limits,
                    "tiers": tier_steps,
                    "story_use": hard_limits,
                    "specificity": 1.0,
                }
            )

    if not laws:
        # A schema-valid but non-injectable empty model is safer than inventing
        # generic laws.  The caller's planning readiness gate decides whether an
        # empty WorldSpec is acceptable.
        return WorldModel(
            source_artifact_type="world_spec",
            source_artifact_hash=source_hash,
            axioms=axioms,
            baseline=world_name,
            uniqueness_principle="只允许复用已批准 WorldSpec 中存在的事实。",
        )

    fault_lines = [
        {
            "name": str(law["derived_from"][0]),
            "tension": str(law["story_use"]),
            "world_law_refs": [str(law["dimension"])],
            "used_by_protagonist": index == 0,
        }
        for index, law in enumerate(laws[:3])
    ]
    return WorldModel.model_validate(
        {
            "version": 2,
            "source_artifact_type": "world_spec",
            "source_artifact_hash": source_hash,
            "axioms": axioms,
            "baseline": world_name,
            "baseline_layers": [],
            "baseline_rationale": "从已批准 WorldSpec 确定性编译，不做第二次世界设定生成。",
            "uniqueness_principle": "所有规律逐条来自已批准 WorldSpec，禁止增加新世界维度。",
            "world_laws": laws,
            "fault_lines": fault_lines,
            "content_settings": [],
        }
    )


# ---------------------------------------------------------------------------
# Prompt assembly (pure)
# ---------------------------------------------------------------------------


def build_world_model_system_prompt(*, language: str = "zh") -> str:
    is_en = str(language or "").lower().startswith("en")
    if is_en:
        return (
            "You are a world-model architect. Build a book's world by DIFFERENTIAL "
            "derivation: take a reality baseline, inject the premise's 1-3 axioms as a "
            "perturbation, and derive how each social dimension is FORCED to change and the "
            "fault lines the story stands on. Output ONE valid JSON WorldModel object only — "
            "no prose, no markdown. HARD RULES: (1) every world_law MUST be derived_from an "
            "axiom — no free-floating rules; (2) NEVER default to the genre's stock world; "
            "(3) each law needs a behavioural, checkable 'enforcement' assertion; "
            "(4) for LADDER-like dimensions (lifespan/power/value) list key rungs in "
            "tiers:[{tier,value}]; (5) use depends_on to point to other law dimension keys "
            "(law->law chain); (6) if societies COEXIST (e.g. mundane + hidden), list them in "
            "top-level baseline_layers. COMPACTNESS (CRITICAL — the JSON MUST close completely; "
            "prefer terse over truncated): at most ONE law per dimension; delta <= 45 chars, "
            "enforcement <= 55 chars, baseline <= 30 chars, tiers <= 6 rungs; encode ripple "
            "order ONLY in the integer 'order' field — NEVER narrate ripples in any field; "
            "content_settings <= 6; whole JSON <= 5000 chars."
        )
    return (
        "你是『世界模型』架构师。用**差分推演**造世:以一个现实基线为底座,把本书前提的 "
        "1-3 条公理当作扰动注入,推导每个社会维度相对基线被**迫**改变了什么、以及故事所站的断层线。"
        "只输出一个合法的 WorldModel JSON 对象——不要解释、不要 markdown、不要任何叙述。"
        "【硬性规则】(1) 每条 world_law 必须 derived_from 某条公理——禁止凭空规则;"
        "(2) 严禁套用题材的默认世界,同题材两本书必须得到不同世界;"
        "(3) 每条规律必须带一句行为级、可校验的 enforcement 断言(正文必须/禁止怎样);"
        "(4) 寿命/实力/价值等**阶梯式**维度,用 tiers:[{tier,value}] 列关键档位(如{tier:炼气,value:百岁});"
        "(5) 用 depends_on 标注本规律依赖的其它维度key(law→law链);"
        "(6) 若世界有共存的多层社会(如世俗界/修仙界),在顶层用 baseline_layers 列出。"
        "【紧凑硬约束·最重要·JSON 必须完整闭合,宁可精炼也不可被截断】"
        "每个维度至多 1 条 law;delta≤45字、enforcement≤55字、baseline≤30字;tiers≤6档,每档≤12字;"
        "涟漪阶数只用整数 order 字段表示,**严禁在任何字段里写'一阶/二阶/三阶涟漪'之类叙述**;"
        "content_settings≤6 条;整个 JSON≤5000 字符。"
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
            "Steps: (1) extract 1-3 axioms; (2) confirm the baseline substrate; (3) diff the "
            "dimensions the premise touches — a speculative/genre premise usually changes MOST "
            "dimensions, so BE THOROUGH and cover them all; only omit a dimension that is genuinely "
            "identical to the baseline (write a law per covered dimension with a checkable "
            "enforcement + integer 'order'); (4) extract fault_lines and mark the one the "
            "protagonist stands on; (5) derive content_settings, each derived_from_law. Keep every "
            "field terse and ensure the JSON closes completely.\n"
            f"Schema-valid MINIMUM (make it premise-specific, do NOT copy verbatim):\n{schema_hint}"
        )
    return (
        f"前提:\n{premise}\n\n"
        f"{genre_ctx}"
        f"{dimensions_block}\n\n"
        "步骤:(1) 提取 1-3 条公理;(2) 确认/选择基线底座;(3) 对**本书前提涉及或改变**的维度逐个差分——"
        "架空/超自然/科幻题材通常会改变其中大多数维度,**务必覆盖全面**,每维写一条 baseline→delta + "
        "可校验 enforcement + 整数 order;仅当某维度与基线**完全一致**时才略过;"
        "(4) 提取 fault_lines 并标出主角所站的那条(used_by_protagonist=true);(5) 派生 "
        "content_settings,每项 derived_from_law 可追溯。各字段务必精炼,确保 JSON 完整闭合。\n"
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


def _match_brace_forward(text: str, open_idx: int) -> int:
    """Return the index of the ``}`` matching the ``{`` at ``open_idx`` (string-aware).

    Returns -1 when the object never closes (truncated tail).
    """

    depth = 0
    in_str = esc = False
    for j in range(open_idx, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _balanced_objects_with_key(text: str, key: str) -> list[str]:
    """Return every COMPLETE object that DIRECTLY contains ``"key"``.

    Anchored on each occurrence of the key (so a truncated *outer* object does
    not hide the complete inner objects written before the cut): walk left to the
    enclosing ``{``, then forward to its matching ``}``. Crucial for truncated
    LLM output (``finish_reason="length"``) — every law written before the cut is
    recovered; the trailing incomplete one is skipped.
    """

    out: list[str] = []
    seen: set[int] = set()
    for m in re.finditer(re.escape(f'"{key}"'), text):
        depth = 0
        start = -1
        k = m.start() - 1
        while k >= 0:
            ch = text[k]
            if ch == "}":
                depth += 1
            elif ch == "{":
                if depth == 0:
                    start = k
                    break
                depth -= 1
            k -= 1
        if start == -1 or start in seen:
            continue
        close = _match_brace_forward(text, start)
        if close == -1:
            continue  # this object is the truncated tail
        seen.add(start)
        out.append(text[start : close + 1])
    return out


def _loads_object(blob: str) -> dict[str, Any] | None:
    try:
        value = json.loads(blob)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json

            value = repair_json(blob, return_objects=True)
        except Exception:
            return None
    return value if isinstance(value, dict) else None


def _salvage_payload(text: str) -> dict[str, Any]:
    """Recover axioms/baseline/world_laws from a possibly-truncated response."""

    salvaged: dict[str, Any] = {}
    laws = [obj for blob in _balanced_objects_with_key(text, "dimension") if (obj := _loads_object(blob))]
    if laws:
        salvaged["world_laws"] = laws
    fault_lines = [
        obj for blob in _balanced_objects_with_key(text, "tension") if (obj := _loads_object(blob))
    ]
    if fault_lines:
        salvaged["fault_lines"] = fault_lines
    # axioms / baseline live in the head, before any truncation.
    ax = re.search(r'"axioms"\s*:\s*\[(.*?)\]', text, flags=re.S)
    if ax:
        salvaged["axioms"] = re.findall(r'"((?:[^"\\]|\\.)+)"', ax.group(1))
    base = re.search(r'"baseline"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if base:
        salvaged["baseline"] = base.group(1)
    return salvaged


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


def _keep_valid(items: Any, model_cls: type) -> list[Any]:
    """Return only the list elements that validate against ``model_cls``.

    Drops malformed entries instead of letting one bad element raise on the whole
    container. Order-preserving.
    """

    if not isinstance(items, list):
        return []
    kept: list[Any] = []
    for item in items:
        try:
            model_cls.model_validate(item)
        except Exception:
            continue
        kept.append(item)
    return kept


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
    # Truncation-resilient: if the whole-object parse yielded no laws (the LLM
    # blew the token budget and the JSON never closed), salvage every complete
    # law object written before the cut instead of silently using the fallback.
    if not (isinstance(payload.get("world_laws"), list) and payload.get("world_laws")):
        salvaged = _salvage_payload(text)
        if salvaged.get("world_laws"):
            for key, value in salvaged.items():
                if key == "world_laws" or not payload.get(key):
                    payload[key] = value
    if not (isinstance(payload.get("world_laws"), list) and payload.get("world_laws")):
        _score_laws_in_place(fallback)
        return world_model_from_dict(fallback)
    merged = {**fallback, **{k: v for k, v in payload.items() if v not in (None, "", [], {})}}
    merged["world_laws"] = payload["world_laws"]  # LLM/salvaged laws win wholesale
    if not merged.get("axioms"):
        merged["axioms"] = fallback.get("axioms")
    _score_laws_in_place(merged)
    # Sanitise every optional sub-list element-wise: one malformed item (e.g. a
    # content_setting the LLM emitted without a 'name') must NEVER nuke the whole
    # derivation back to the generic scaffold. Drop only the offending element.
    merged["world_laws"] = _keep_valid(merged.get("world_laws"), WorldLaw)
    merged["fault_lines"] = _keep_valid(merged.get("fault_lines"), FaultLine)
    merged["content_settings"] = _keep_valid(merged.get("content_settings"), ContentSetting)
    if not merged["world_laws"]:
        _score_laws_in_place(fallback)
        return world_model_from_dict(fallback)
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
            max_tokens_override=6000,
        ),
    )
    return parse_world_model(
        completion.content, premise=premise, genre=genre, baseline_hint=baseline_hint
    )


__all__ = [
    "build_world_model_system_prompt",
    "build_world_model_user_prompt",
    "compile_world_model_from_world_spec",
    "derive_world_model",
    "extract_axioms",
    "fallback_world_model",
    "parse_world_model",
]
