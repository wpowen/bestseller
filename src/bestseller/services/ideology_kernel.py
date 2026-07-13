"""Derivation of the per-book :class:`IdeologyKernel`.

Turns ``(premise, BookSpec)`` into a structured core-ideology kernel: one primary
theme (主主题) + woven sub-themes (子题) drawn from a large, genre-DECOUPLED theme
corpus, organised by a 4-layer motif scaffold.

**Genre never drives theme selection.** Selection is driven by a per-book
*diversity seed* (premise + title identity), so two same-genre books get
different themes. ``genre`` is passed only as soft prompt context with an explicit
"avoid the genre cliché" instruction.

Split into pure, unit-testable pieces (reused by the pilot's standalone runner):
* :func:`build_ideology_system_prompt` / :func:`build_ideology_user_prompt`
* :func:`fallback_ideology_kernel` — deterministic, always-valid, seed-diverse.
* :func:`parse_ideology_kernel` — JSON → validated kernel (fallback-safe).
* :func:`derive_ideology_kernel` — production entry via ``complete_text``.
"""

# ruff: noqa: RUF001, E501, ANN401, S112

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.ideology import (
    LAYER_DISPLAY,
    IdeologyKernel,
    ideology_kernel_from_dict,
)
from bestseller.services.ideology_library import (
    Motif,
    MotifFormula,
    MotifLibrary,
    ThemeEntry,
    book_diversity_seed,
    load_motif_library,
    render_motif_library_prompt_block,
    select_themes,
    stable_seed_int,
    suggest_motif_formula,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings

# ---------------------------------------------------------------------------
# Diversity seed — premise/title identity, NEVER genre
# ---------------------------------------------------------------------------


def _seed_for(premise: str, book_spec: dict[str, Any] | None, title: str = "") -> str:
    logline = ""
    if isinstance(book_spec, dict):
        title = title or str(book_spec.get("title") or "")
        logline = str(book_spec.get("logline") or book_spec.get("dramatic_question") or "")
    return book_diversity_seed(premise=f"{premise} {logline}".strip(), title=title)


def _pick_motif_theme(
    library: MotifLibrary, motif_key: str, seed: str, salt: str
) -> ThemeEntry | None:
    pool = library.themes_for_motif(motif_key)
    if not pool:
        return None
    return pool[stable_seed_int(seed, salt, motif_key) % len(pool)]


# ---------------------------------------------------------------------------
# Deterministic fallback — a valid, coherent, SEED-DIVERSE kernel (no LLM)
# ---------------------------------------------------------------------------


def _binding_from_motif(
    motif: Motif,
    library: MotifLibrary,
    seed: str,
    *,
    role: str | None = None,
    reveal_after: int | None = None,
) -> dict[str, Any]:
    """Build a motif binding; its book_thesis is a seed-picked corpus theme (diverse)."""

    theme = _pick_motif_theme(library, motif.key, seed, "binding")
    book_thesis = theme.proposition if theme else motif.thesis_template
    return {
        "motif_key": motif.key,
        "display_name": motif.display_name,
        "layer": motif.layer,
        "book_thesis": book_thesis,
        "book_core_question": motif.core_question_template,
        "concrete_symbols": list(motif.concrete_symbol_hints[:4]),
        **({"role": role} if role else {}),
        **({"reveal_after_volume": reveal_after} if reveal_after else {}),
    }


def _cost_laws_from_formula(formula: MotifFormula) -> list[dict[str, Any]]:
    return [
        {
            "acquires": "力量 / 境界 / 资源",
            "costs": "关系、寿元、记忆或身份之一",
            "delayed": True,
            "irreversible": False,
        },
        {
            "acquires": "真相 / 揭秘",
            "costs": "既有信念崩塌、与亲近者的关系受伤",
            "delayed": False,
            "irreversible": True,
        },
    ]


def _forbidden_from_thesis(thesis: str, formula: MotifFormula) -> list[str]:
    out = [
        f"不要让结局推翻主主题「{thesis}」(例如最后世界其实会奖励善者/天道有情)。",
        "不要用'系统暗中保护主角/无偿气运搭救'来取消代价系统。",
    ]
    if formula.primary.trap_guard:
        out.append(f"避免陷阱：{formula.primary.common_traps}；做法：{formula.primary.trap_guard}")
    return out


def _per_volume_pressure(formula: MotifFormula, thesis: str, *, volumes: int) -> list[str]:
    volumes = max(int(volumes or 1), 1)
    p = formula.primary
    out: list[str] = [
        f"第1卷：建立信念「{p.belief_initial}」, 主主题「{thesis}」先以希望/秩序面目出现。",
    ]
    if volumes >= 3:
        mid = (volumes // 2) + 1
        out.append(
            f"第{mid}卷(中段)：打碎信念「{p.belief_shatter}」, 副母题"
            f"{formula.secondary_suspense.display_name}揭开第一层真相, "
            f"{formula.secondary_action.display_name}逼主角付出第一笔大代价。"
        )
    if volumes >= 2:
        out.append(
            f"第{volumes}卷(终局)：重建「{p.belief_reconstruction}」, "
            f"隐藏母题{formula.hidden.display_name}完成价值反转。"
        )
    return out


def _world_bindings(formula: MotifFormula) -> list[str]:
    return [
        f"用主母题「{formula.primary.display_name}」长出世界 invariant：{formula.primary.worldview_setting}",
        f"用代价系统绑定力量体系：每一次{formula.secondary_action.display_name}都要有可见账单。",
        f"用副母题「{formula.secondary_suspense.display_name}」组织揭秘阶梯：每解一层, 前一层改义。",
    ]


def fallback_ideology_kernel(
    *,
    premise: str = "",
    book_spec: dict[str, Any] | None = None,
    volumes: int = 1,
    seed: str | None = None,
    title: str = "",
    genre: str | None = None,  # accepted for back-compat; NOT used for selection
) -> dict[str, Any]:
    """Build a deterministic, schema-valid, SEED-DIVERSE IdeologyKernel payload.

    Never raises. Genre is ignored for selection — the per-book seed (premise +
    title) drives the motif spine and the 主主题/子题 so same-genre books differ.
    """

    library = load_motif_library()
    seed = seed or _seed_for(premise, book_spec, title)
    formula = suggest_motif_formula(seed=seed, library=library)
    selection = select_themes(library, formula=formula, seed=seed)

    p = formula.primary
    emphasis_motif = (
        library.by_key(selection.primary_theme.motif) if selection.primary_theme else None
    ) or p
    thesis = selection.primary_theme.proposition if selection.primary_theme else p.thesis_template
    core_question = emphasis_motif.core_question_template or p.core_question_template
    cosmic_premise = (
        p.worldview_setting.strip()
        or f"这个世界不会因为主角善良就奖励他（{p.display_name}）。"
    )

    sub_themes = [
        {"proposition": t.proposition, "motif_key": t.motif, "layer": t.layer}
        for t in selection.sub_themes
    ]

    payload: dict[str, Any] = {
        "version": 1,
        "cosmic_premise": cosmic_premise,
        "thesis_statement": thesis,
        "core_question": core_question,
        "sub_themes": sub_themes,
        "primary_motif": _binding_from_motif(p, library, seed),
        "secondary_motifs": [
            _binding_from_motif(formula.secondary_action, library, seed, role="action"),
            _binding_from_motif(formula.secondary_suspense, library, seed, role="suspense"),
        ],
        "hidden_endgame_motif": _binding_from_motif(
            formula.hidden,
            library,
            seed,
            reveal_after=max(1, int(volumes * 0.6)) if volumes and volumes > 1 else None,
        ),
        "belief_arc": {
            "initial_belief": p.belief_initial,
            "midpoint_shatter": p.belief_shatter,
            "final_reconstruction": p.belief_reconstruction,
        },
        "cost_system": _cost_laws_from_formula(formula),
        "layer_coverage": {m.layer: m.display_name for m in formula.all_motifs()},
        "motif_to_world_bindings": _world_bindings(formula),
        "per_volume_thesis_pressure": _per_volume_pressure(formula, thesis, volumes=volumes),
        "forbidden_resolutions": _forbidden_from_thesis(thesis, formula),
    }
    return payload


# ---------------------------------------------------------------------------
# Prompt assembly (pure)
# ---------------------------------------------------------------------------


def build_ideology_system_prompt(*, language: str = "zh") -> str:
    is_en = str(language or "").lower().startswith("en")
    if is_en:
        return (
            "You are a literary architect specialising in the THESIS (core ideology) of a long "
            "novel — one primary theme (主主题) plus woven sub-themes (子题) that run through the "
            "whole book and grow its world & arc. Output ONE valid JSON IdeologyKernel object only. "
            "HARD RULE: the theme MUST be driven by THIS premise — never default to the genre's "
            "stock theme. Two same-genre books must get different themes. Bind every motif to this "
            "book's concrete material; never leave a label abstract. Keep strings ≤ 60 chars, JSON ≤ 6500 chars."
        )
    return (
        "你是长篇小说『核心理念』架构师——负责给一本书定 1 个主主题 + 若干穿插子题, "
        "贯穿全书并长出世界观与走向。只输出一个合法的 IdeologyKernel JSON 对象, 不要解释。"
        "【硬性规则·主题必须主流且接地】主主题必须取自公认、读者耳熟能详的主流主题"
        "(如爱与牺牲/成长/守护/复仇/权力与腐化/正义/救赎/命运与选择/真相与欺骗…), 再据本书前提"
        "做分化与具体化, 写成一句本书专属的论断; 严禁为标新立异硬造一个扭曲、不符合常识的理念"
        "(否则读者会觉得『理念不对』而弃读)。"
        "【硬性多样性规则】主题由『本书前提』决定, 同题材两本书必须得到不同主题, 严禁套用题材默认主题。"
        "把每个母题『具体化』到本书, 绝不停留在空泛标签。所有字符串 ≤ 60 字, 整个 JSON ≤ 6500 字符。"
    )


def _book_spec_digest(book_spec: dict[str, Any] | None, *, limit: int = 1200) -> str:
    if not isinstance(book_spec, dict):
        return ""
    keep = {}
    for key in (
        "title", "logline", "themes", "theme_statement", "dramatic_question",
        "protagonist", "stakes", "reader_promise", "tone",
    ):
        if book_spec.get(key) not in (None, "", [], {}):
            keep[key] = book_spec[key]
    return json.dumps(keep, ensure_ascii=False, default=str)[:limit]


def _cost_style_directive(cost_style: str, *, is_en: bool) -> str:
    """纯正爽文代价风格指令（standard → 空串，保持 prompt 逐字节不变）。"""

    cs = str(cost_style or "standard").strip().lower()
    if cs == "external":
        if is_en:
            return (
                "\nCOST STYLE = EXTERNAL: design cost_system so every `costs` is BORNE BY THE "
                "WORLD/ENEMIES/RESOURCES (making enemies, exposure, resource drain, opportunity "
                "cost) — NEVER the protagonist's own memory/body/relationships/lifespan/status. "
                "The hero never self-harms.\n"
            )
        return (
            "\n代价风格=外置：设计 cost_system 时，每条 costs 必须由【世界/对手/资源】承担"
            "（树敌、暴露、资源消耗、机会成本、招来强敌），绝不能削减主角自己的记忆/身体/关系/"
            "寿命/地位。主角一路爽，不自损。\n"
        )
    if cs == "minimal":
        if is_en:
            return (
                "\nCOST STYLE = MINIMAL: keep cost_system light (opportunity cost / time / making "
                "enemies), never weakening the hero; costs must not interrupt payoff.\n"
            )
        return (
            "\n代价风格=极简：cost_system 从简（机会成本/时间/树敌为主），不削弱主角，代价不得"
            "打断爽点兑现。\n"
        )
    return ""


def build_ideology_user_prompt(
    *,
    premise: str,
    genre: str | None = None,
    book_spec: dict[str, Any] | None = None,
    volumes: int = 1,
    fallback_payload: dict[str, Any] | None = None,
    seed: str | None = None,
    language: str = "zh",
    cost_style: str = "standard",
) -> str:
    is_en = str(language or "").lower().startswith("en")
    seed = seed or _seed_for(premise, book_spec)
    library_block = render_motif_library_prompt_block(seed=seed)
    fallback = fallback_payload or fallback_ideology_kernel(
        premise=premise, book_spec=book_spec, volumes=volumes, seed=seed
    )
    schema_hint = json.dumps(fallback, ensure_ascii=False, indent=2)
    genre_ctx = (
        f"题材(仅作背景, 不得据此套用默认主题)：{genre}\n" if genre else ""
    )
    if is_en:
        return (
            f"Premise:\n{premise}\n\n"
            f"{('Genre (context only — do NOT default to its stock theme): ' + genre + chr(10)) if genre else ''}"
            f"Planned volumes: {volumes}\nBookSpec digest: {_book_spec_digest(book_spec)}\n\n"
            f"{library_block}\n\n"
            "Produce an IdeologyKernel JSON with: cosmic_premise, thesis_statement (the ONE 主主题, "
            "premise-driven, NOT a genre cliché), core_question, sub_themes (3-6 woven 子题, each "
            "{proposition, motif_key, layer}), primary_motif, secondary_motifs (exactly two — one "
            "role=action, one role=suspense), hidden_endgame_motif (with reveal_after_volume), "
            "belief_arc, cost_system (>=2 laws), motif_to_world_bindings, per_volume_thesis_pressure, "
            "forbidden_resolutions. Cover all four layers.\n"
            f"Schema-valid MINIMUM (make it premise-specific, do not copy verbatim):\n{schema_hint}"
            + _cost_style_directive(cost_style, is_en=is_en)
        )
    return (
        f"前提：\n{premise}\n\n"
        f"{genre_ctx}"
        f"规划卷数：{volumes}\nBookSpec 摘要：{_book_spec_digest(book_spec)}\n\n"
        f"{library_block}\n\n"
        "请产出 IdeologyKernel JSON, 必须包含：cosmic_premise(宇宙前提)、thesis_statement(唯一的主主题, "
        "由前提决定, 绝不能是题材套话)、core_question(贯穿全书的核心问题)、sub_themes(3-6 个穿插子题, "
        "每个 {proposition, motif_key, layer})、primary_motif、secondary_motifs(恰好两个: 一个 "
        "role=action 管行动, 一个 role=suspense 管悬念)、hidden_endgame_motif(含 reveal_after_volume)、"
        "belief_arc、cost_system(≥2 条)、motif_to_world_bindings、per_volume_thesis_pressure、"
        "forbidden_resolutions。必须覆盖四层。\n"
        f"以下为 schema 最低结构(请据前提做出本书独有的具体化, 不要照抄)：\n{schema_hint}"
        + _cost_style_directive(cost_style, is_en=is_en)
    )


# ---------------------------------------------------------------------------
# Parsing
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


def _role_of(binding: Any) -> str:
    role = str((binding or {}).get("role", "")).strip().lower() if isinstance(binding, dict) else ""
    if role in {"action", "行动", "act", "drive"}:
        return "action"
    if role in {"suspense", "悬念", "mystery", "reveal"}:
        return "suspense"
    return ""


def _motif_key_of(binding: Any) -> str:
    if not isinstance(binding, dict):
        return ""
    return str(binding.get("motif_key") or binding.get("key") or binding.get("motif") or "").strip()


def _ensure_structure(merged: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    """Guarantee the required spine even when the LLM under-delivers.

    The LLM sometimes returns 1 secondary / no hidden / a layer-thin spine. Rather
    than let its partial output override the complete fallback pair, backfill the
    missing action/suspense role + hidden from the fallback (deduped by motif), so
    the kernel always carries the 2-secondary (action+suspense) + hidden structure.
    """

    used: set[str] = set()
    primary_key = _motif_key_of(merged.get("primary_motif"))
    if primary_key:
        used.add(primary_key)

    sec_raw = merged.get("secondary_motifs")
    sec_in = [s for s in sec_raw if isinstance(s, dict)] if isinstance(sec_raw, list) else []
    sec: list[Any] = []
    for s in sec_in:
        k = _motif_key_of(s)
        if k and k in {_motif_key_of(x) for x in sec}:
            continue  # drop duplicate motif
        sec.append(s)
        if k:
            used.add(k)

    roles_present = {_role_of(s) for s in sec if _role_of(s)}
    fb_sec = [s for s in fallback.get("secondary_motifs", []) if isinstance(s, dict)]
    for needed in ("action", "suspense"):
        if needed in roles_present:
            continue
        for f in fb_sec:
            if _role_of(f) == needed and _motif_key_of(f) not in used:
                sec.append(f)
                used.add(_motif_key_of(f))
                break
    merged["secondary_motifs"] = sec

    if not merged.get("hidden_endgame_motif"):
        merged["hidden_endgame_motif"] = fallback.get("hidden_endgame_motif")
    # Ensure a non-trivial cost system + at least one sub-theme survive.
    if not merged.get("cost_system"):
        merged["cost_system"] = fallback.get("cost_system")
    if not merged.get("sub_themes"):
        merged["sub_themes"] = fallback.get("sub_themes")
    return merged


def parse_ideology_kernel(
    text: str,
    *,
    premise: str = "",
    book_spec: dict[str, Any] | None = None,
    volumes: int = 1,
    seed: str | None = None,
    genre: str | None = None,  # back-compat; not used for selection
) -> IdeologyKernel:
    """Parse LLM JSON into a validated kernel, falling back deterministically.

    Keeps the LLM's rich theme content (thesis, sub_themes, motif theses) but
    backfills any missing structural piece (2 secondaries with both roles, hidden,
    cost system) from the deterministic fallback so the spine is always complete.
    """

    payload = _parse_json_object(text)
    fallback = fallback_ideology_kernel(
        premise=premise, book_spec=book_spec, volumes=volumes, seed=seed
    )
    if not payload:
        return ideology_kernel_from_dict(fallback)
    merged = {**fallback, **{k: v for k, v in payload.items() if v not in (None, "", [], {})}}
    merged = _ensure_structure(merged, fallback)
    try:
        return ideology_kernel_from_dict(merged)
    except Exception:
        return ideology_kernel_from_dict(fallback)


# ---------------------------------------------------------------------------
# Production entry point
# ---------------------------------------------------------------------------


async def derive_ideology_kernel(
    session: AsyncSession,
    settings: AppSettings,
    *,
    premise: str,
    genre: str | None = None,
    book_spec: dict[str, Any] | None = None,
    volumes: int = 1,
    title: str = "",
    language: str = "zh",
    cost_style: str = "standard",
    project_id: Any | None = None,
    workflow_run_id: Any | None = None,
) -> IdeologyKernel:
    """Derive the book's IdeologyKernel via the planner LLM (fallback-safe).

    ``cost_style`` (纯正爽文三档 standard|external|minimal) shapes the cost-system
    derivation prompt and is stamped onto the returned kernel so downstream render
    picks the matching 代价系统 variant. Default standard → prompt/render byte-identical.
    """

    seed = _seed_for(premise, book_spec, title)
    fallback = fallback_ideology_kernel(
        premise=premise, book_spec=book_spec, volumes=volumes, seed=seed
    )
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="planner",
            system_prompt=build_ideology_system_prompt(language=language),
            user_prompt=build_ideology_user_prompt(
                premise=premise, genre=genre, book_spec=book_spec,
                volumes=volumes, fallback_payload=fallback, seed=seed, language=language,
                cost_style=cost_style,
            ),
            fallback_response=json.dumps(fallback, ensure_ascii=False),
            prompt_template="ideology_kernel",
            prompt_version="v2",
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            metadata={"artifact": "ideology_kernel", "genre": genre or "", "diversity_seed": seed[:64]},
            max_tokens_override=4200,
        ),
    )
    kernel = parse_ideology_kernel(
        completion.content, premise=premise, book_spec=book_spec, volumes=volumes, seed=seed
    )
    cs = str(cost_style or "standard").strip().lower()
    if cs != "standard" and cs != kernel.cost_style:
        kernel = kernel.model_copy(update={"cost_style": cs})
    return kernel


def ideology_kernel_health_summary(kernel: IdeologyKernel) -> dict[str, Any]:
    """Cheap observability snapshot (used by the pilot + gate reporting)."""

    return {
        "thesis_statement": kernel.thesis_statement,
        "sub_theme_count": len(kernel.sub_themes),
        "sub_themes": [t.proposition for t in kernel.sub_themes[:6]],
        "primary_motif": kernel.primary_motif.display_name,
        "secondary_motifs": [b.display_name for b in kernel.secondary_motifs],
        "secondary_roles": sorted(kernel.secondary_roles()),
        "hidden_motif": (
            kernel.hidden_endgame_motif.display_name if kernel.hidden_endgame_motif else None
        ),
        "covered_layers": sorted(
            LAYER_DISPLAY.get(layer, layer) for layer in kernel.covered_layers()
        ),
        "layer_count": len(kernel.covered_layers()),
        "cost_laws": len(kernel.cost_system),
        "forbidden_count": len(kernel.forbidden_resolutions),
    }


__all__ = [
    "build_ideology_system_prompt",
    "build_ideology_user_prompt",
    "derive_ideology_kernel",
    "fallback_ideology_kernel",
    "ideology_kernel_health_summary",
    "parse_ideology_kernel",
]
