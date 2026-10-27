"""Materialize literal author ideology clauses without inventing a mandatory spine.

The existing full v1 kernel remains readable and old artifacts remain intact.
Production v2 is an effective projection of current author methodology; unknown
or omitted fields stay empty. The optional motif-library helpers remain available
as reference utilities and do not grant author authority or generate paid work.
"""

# ruff: noqa: RUF001, E501, ANN401, S112

from __future__ import annotations

from collections.abc import Mapping
import hashlib
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
    stable_seed_int,
)
from bestseller.services.story_enhancers import COST_STYLE_DEFAULT, resolve_cost_style
from bestseller.settings import AppSettings


def _cost_source_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def resolve_cached_ideology_cost_source(
    kernel: IdeologyKernel | Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve current author policy without treating a cached label as approval.

    This is an effective read projection. All cached fields, established facts,
    prior approvals and overrides remain untouched. Renderers must not fall
    back to standard when the current author source is unknown.
    """
    payload = kernel.model_dump(mode="json") if isinstance(kernel, IdeologyKernel) else dict(kernel)
    creation = (metadata or {}).get("creation_intent_contract")
    creation = dict(creation) if isinstance(creation, Mapping) else {}
    effective = resolve_cost_style(metadata)
    superseded = not effective or payload.get("cost_style") != effective
    return {
        "version": "ideology-cost-source.v1",
        "source_kernel_sha256": _cost_source_hash(payload),
        "source_creation_sha256": _cost_source_hash(creation),
        "cached_cost_style": str(payload.get("cost_style") or ""),
        "effective_cost_style": effective,
        "authority": "explicit_creation_entry" if effective else "unknown",
        "cached_policy_superseded": superseded,
        "suppressed_projection_fields": (
            ["motif_to_world_bindings", "forbidden_resolutions"] if superseded else []
        )
        + (["cost_system"] if not effective else []),
    }


def build_ideology_cost_source_supersession(
    project: Any,
    source_artifact: Any,
    *,
    expected_source_sha256: str,
    expected_creation_sha256: str,
) -> dict[str, Any]:
    """Build a bound, deterministic operator dry-run receipt; never write rows.

    The caller supplies exact project/artifact rows from its authorized read.
    An approved planning artifact is not proof of author approval of its cost
    label. This receipt supersedes only that label's authority, never its facts
    or artifact status, and does not release any quality or publication gate.
    """
    if not getattr(project, "id", None) or str(source_artifact.project_id) != str(project.id):
        raise ValueError("ideology cost source project mismatch")
    if not getattr(source_artifact, "id", None) or source_artifact.status != "approved":
        raise ValueError("ideology cost source must be an exact approved artifact")
    content = source_artifact.content
    if not isinstance(content, Mapping):
        raise ValueError("ideology cost source content missing")
    if source_artifact.artifact_type == "story_design_kernel":
        kernel = content.get("ideology_kernel")
    elif source_artifact.artifact_type == "ideology_kernel":
        kernel = content
    else:
        raise ValueError("ideology cost source artifact type mismatch")
    if not isinstance(kernel, Mapping) or not kernel:
        raise ValueError("ideology cost source kernel missing")
    receipt = resolve_cached_ideology_cost_source(kernel, project.metadata_json or {})
    if receipt["source_kernel_sha256"] != expected_source_sha256:
        raise ValueError("ideology cost source hash mismatch")
    if receipt["source_creation_sha256"] != expected_creation_sha256:
        raise ValueError("ideology cost creation hash mismatch")
    metadata = project.metadata_json or {}
    cache = metadata.get("story_design_kernel")
    current_kernel = cache.get("ideology_kernel") if isinstance(cache, Mapping) else None
    if current_kernel is None:
        current_kernel = metadata.get("ideology_kernel")
    if current_kernel is not None and (
        not isinstance(current_kernel, Mapping)
        or _cost_source_hash(dict(current_kernel)) != expected_source_sha256
    ):
        raise ValueError("ideology cost source is not the current cached kernel")
    receipt.update(
        {
            "project_id": str(project.id),
            "source_artifact_id": str(source_artifact.id),
            "source_artifact_sha256": _cost_source_hash(dict(content)),
            "dry_run": True,
            "writes": [],
            "cache_binding": "current_cache_match" if current_kernel is not None else "artifact_only",
        }
    )
    receipt["idempotency_key"] = "ideology-cost-source:" + _cost_source_hash(receipt)
    return receipt

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


# 代价维度池 —— 按本书 seed 轮换,并按 cost_style 分档。
# 旧实现是 `return [<两条写死的常量>]`,签名收了 ``formula`` 却一次都没读:全系统
# 每一本书、每个题材、每个 seed 都共用同样两条代价律("costs": "关系、寿元、记忆
# 或身份之一"),而它周围的母题/主题全部是 seed 多样化的 —— 这是跨书"代价/债务"
# 同质化的上游总根。它同时无视 cost_style,导致选了"外置/极简代价"的书,其 prompt
# 里的 schema 范例反而演示了一遍自损代价,和随后的指令自相矛盾。
_SELF_COST_DIMENSIONS: tuple[str, ...] = (
    "灵机反噬，经脉与旧伤一起发作",
    "寿元折损，肉眼可见地老下去",
    "记忆被吃掉一段，连带认得的人一起模糊",
    "感官逐项失灵（先味觉，再听觉）",
    "道心裂痕扩大，越用越难自控",
    "血脉灼烧，痕迹爬上皮肤藏不住",
    "与至亲之人的关系被一次次磨薄",
    "身份暴露的风险层层累积",
    "因果烙印显形，被更高位者察觉",
    "情绪失控的阈值一次次降低",
)
_EXTERNAL_COST_DIMENSIONS: tuple[str, ...] = (
    "代价转嫁给出手的对手，他先垮",
    "由世界／规则本身承担，秩序裂开一道缝",
    "烧掉一份稀缺资源，下一次没得用",
    "由受益者按约定分担，而非主角自损",
    "招来更高位者的注意，压力落到整个阵营头上",
)
_MINIMAL_COST_DIMENSIONS: tuple[str, ...] = (
    "一次性的资源消耗，点到为止",
    "轻微且可控的暴露风险",
)


def _cost_dimension_pool(cost_style: str) -> tuple[str, ...]:
    style = str(cost_style or COST_STYLE_DEFAULT).strip().lower()
    if style == "external":
        return _EXTERNAL_COST_DIMENSIONS
    if style == "minimal":
        return _MINIMAL_COST_DIMENSIONS
    return _SELF_COST_DIMENSIONS


def _cost_laws_from_formula(
    formula: MotifFormula,
    *,
    seed: str = "",
    cost_style: str = COST_STYLE_DEFAULT,
) -> list[dict[str, Any]]:
    """Derive this book's cost laws from its OWN motif spine + diversity seed.

    ``acquires`` names what the book's own action/suspense motifs actually grant,
    and ``costs`` is drawn from a seed-rotated pool matching the user's cost
    style — so two same-genre books no longer ship identical cost laws, and a
    纯爽 book's example stops contradicting its own directive.
    """

    pool = _cost_dimension_pool(cost_style)
    action = formula.secondary_action
    suspense = formula.secondary_suspense
    idx_a = stable_seed_int(seed, "cost-law", action.key) % len(pool)
    cost_a = pool[idx_a]
    cost_b = pool[(stable_seed_int(seed, "cost-law-reveal", suspense.key) + 1) % len(pool)]
    if len(pool) > 1 and cost_b == cost_a:
        cost_b = pool[(idx_a + 1) % len(pool)]
    return [
        {
            "acquires": f"{action.display_name}推进带来的力量／境界／资源",
            "costs": cost_a,
            "delayed": True,
            "irreversible": False,
        },
        {
            "acquires": f"{suspense.display_name}揭开的一层真相",
            "costs": cost_b,
            "delayed": False,
            "irreversible": True,
        },
    ]


def _forbidden_from_thesis(
    thesis: str, formula: MotifFormula, *, cost_style: str = COST_STYLE_DEFAULT
) -> list[str]:
    satisfaction_forward = str(cost_style or COST_STYLE_DEFAULT).strip().lower() in {
        "minimal", "external"
    }
    out = [
        f"不要让结局推翻主主题「{thesis}」(例如最后世界其实会奖励善者/天道有情)。",
        (
            "结局的胜利应兑现主角此前的行动与选择，承接已建立的世界规则。"
            if satisfaction_forward
            else "不要用'系统暗中保护主角/无偿气运搭救'来取消代价系统。"
        ),
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


def _world_bindings(
    formula: MotifFormula, *, cost_style: str = COST_STYLE_DEFAULT
) -> list[str]:
    style = str(cost_style or COST_STYLE_DEFAULT).strip().lower()
    if style == "minimal":
        pressure = (
            f"力量体系服务于{formula.secondary_action.display_name}的行动兑现；"
            "主角的优势稳定可用，压力由外部对抗和局势承载，轻量消耗点到为止。"
        )
    elif style == "external":
        pressure = (
            f"用外置代价承接{formula.secondary_action.display_name}的后果；"
            "由世界、对手或资源承担，保持主角能力与身心状态稳定。"
        )
    else:
        pressure = f"用代价系统绑定力量体系：每一次{formula.secondary_action.display_name}都要有可见的、非金融的具身代价（反噬/损耗/树敌/暴露/关系后果）。"
    return [
        f"用主母题「{formula.primary.display_name}」长出世界 invariant：{formula.primary.worldview_setting}",
        pressure,
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
    cost_style: str = COST_STYLE_DEFAULT,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return only current author clauses; absence is a valid neutral v2 kernel.

    Legacy arguments remain accepted for call compatibility. They cannot grant
    authority to a library-generated theme, cosmology, or hidden conclusion.
    """

    from bestseller.services.ideology_methodology import project_ideology_kernel

    return project_ideology_kernel(None, metadata)


# ---------------------------------------------------------------------------
# Prompt assembly (pure)
# ---------------------------------------------------------------------------


def build_ideology_system_prompt(*, language: str = "zh") -> str:
    if str(language or "").lower().startswith("en"):
        return ("Return JSON containing only explicit author methodology clauses. Unselected "
                "themes, cosmology, layers, belief transitions and endings remain empty. "
                "Cost preference does not authorize story methodology.")
    return ("仅输出作者明确选择的理念条款JSON。未选主题、宇宙前提、结构层、信念转折和结局留空。"
            "代价偏好不授权主题方法论，不从范例或资料库补造故事要求。")


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
    """纯正爽文代价风格指令 (standard → 空串, 保持 prompt 逐字节不变)。"""

    cs = str(cost_style or "standard").strip().lower()
    if cs == "external":
        if is_en:
            return (
                "\nCOST STYLE = EXTERNAL: design cost_system so every `costs` is BORNE BY THE "
                "WORLD/ENEMIES/RESOURCES (making enemies, exposure, resource drain, opportunity "
                "cost) — NEVER the protagonist's own memory/body/relationships/lifespan/status. "
                "The hero never self-harms.\n"
            )
        # Same phrasing rule as ``minimal`` below: name the direction, not the
        # motif. This branch used to enumerate 记忆/寿命/身体/关系/地位 — the
        # exact 《雾街债主》 defect, sitting in a prompt that reaches every
        # concept generation. Saying who bears the pressure is enough.
        return (
            "\n代价风格=外置：设计 cost_system 时，每条 costs 必须由【世界/对手/资源】承担"
            "（树敌、暴露、资源消耗、机会成本、招来强敌）；主角本人不承担任何折损，"
            "他的能力与身心状态不因使用而下降。主角一路爽。\n"
        )
    if cs == "minimal":
        # (2026-08-02) Rewritten from an allowlist prescription to a pacing
        # preference. The old text enumerated the ONLY permitted cost categories
        # (inventory, permits, access, orders…), which is how a cultivation world
        # ended up sounding like a logistics operation — and the matching gate
        # then killed books for using the vocabulary the genre actually needs.
        #
        # (2026-08-05) That rewrite went one step too far: it ended with "whether
        # this book has costs at all is its own design", which hands the decision
        # straight back to the model. A book created with this switch then
        # invented a 破碗 ledger as its CORE mechanic and wrote "the protagonist
        # must pay a cost" into its own writing profile — the exact inversion of
        # what the create form promises ("金手指是否强制自损代价").
        #
        # The constraint is back, but stated as a DIRECTION rather than a menu:
        # say what the cost must not BECOME, never which categories are allowed.
        # A direction cannot flatten a genre's vocabulary the way an allowlist
        # did, and it leaves the world free to be dangerous.
        # Phrasing rule for everything below: state the constraint WITHOUT
        # naming the thing it forbids. A prohibition that spells out its own
        # motif vocabulary plants that vocabulary — 2026-08-03's 《雾街债主》
        # came out of two such prohibitions, and this text tripped the guard
        # that was written to hold that line (`test_prompt_pollution_sweep`).
        # So: describe how the protagonist's advantage should *behave*, and let
        # the absence of a price be an absence, not a named prohibition.
        if is_en:
            return (
                "\nCOST STYLE = MINIMAL: the protagonist's advantage is theirs to use freely. "
                "Using it does not diminish them, and using it more does not make it more "
                "expensive. What it costs is never the engine of the book, never its selling "
                "point, and never what a chapter is mainly about. Keep the story "
                "satisfaction-forward: payoff lands quickly and momentum carries between wins. "
                "The world stays dangerous and rule-bound; the danger simply comes from what "
                "opposes the protagonist rather than from the advantage itself.\n"
            )
        return (
            "\n代价风格=极简：主角的能力可以放心使用——用它不会削弱主角，用得越多也不会越难用。"
            "它需要付出什么，永远不是本书的引擎、不是卖点、也不构成某一章的主要内容。"
            "保持爽点前置、兑现要快，主角在两次胜利之间不要长期失能或进度清零。"
            "世界依然凶险、依然有硬规则，但压力来自与主角对抗的人和局势，而不是来自他自己的能力。\n"
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
    metadata: Mapping[str, Any] | None = None,
) -> str:
    from bestseller.services.ideology_methodology import resolve_ideology_methodology_source

    source = resolve_ideology_methodology_source(None, metadata)
    request = {key: source[key] for key in ("effective_fields", "required_layers", "required_secondary_roles")}
    prefix = "Explicit author clauses only; absent fields stay empty. " if str(language).lower().startswith("en") else "仅以下作者明确条款生效；缺省字段保持空，不补造四层、宇宙前提或隐藏结局。"
    return prefix + json.dumps(request, ensure_ascii=False, sort_keys=True)


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
    """Compatibility container normalization without structural backfill."""

    # Kept for older callers: normalize containers without materializing missing
    # story clauses from any fallback. Empty is a legitimate selection.
    return dict(merged)


def parse_ideology_kernel(
    text: str,
    *,
    premise: str = "",
    book_spec: dict[str, Any] | None = None,
    volumes: int = 1,
    seed: str | None = None,
    genre: str | None = None,  # back-compat; not used for selection
    cost_style: str = COST_STYLE_DEFAULT,
    metadata: Mapping[str, Any] | None = None,
) -> IdeologyKernel:
    """Resolve current author clauses without promoting model proposals.

    Existing LLM run/artifact records retain the original output. Parsing here
    never uses a seed corpus to fill absent story requirements.
    """

    from bestseller.services.ideology_methodology import project_ideology_kernel

    # A model response is a proposal, not author selection. Explicit fields are
    # preserved exactly from the current author entry; missing fields stay empty.
    return ideology_kernel_from_dict(project_ideology_kernel(None, metadata))


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
    metadata: Mapping[str, Any] | None = None,
) -> IdeologyKernel:
    """Build the effective source projection without an unnecessary model call."""

    # The exact author contract already contains the literal clauses. A paid
    # intermediate model must not invent a mandatory ideology for every book.
    return parse_ideology_kernel("{}", metadata=metadata)


def ideology_kernel_health_summary(kernel: IdeologyKernel) -> dict[str, Any]:
    """Cheap observability snapshot (used by the pilot + gate reporting)."""

    return {
        "thesis_statement": kernel.thesis_statement,
        "sub_theme_count": len(kernel.sub_themes),
        "sub_themes": [t.proposition for t in kernel.sub_themes[:6]],
        "primary_motif": kernel.primary_motif.display_name if kernel.primary_motif else None,
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


def cost_style_directive(cost_style: str, *, is_en: bool = False) -> str:
    """Public alias: translate a cost_style enum into prompt instructions.

    Exported for CONCEPTION-time use. This module runs at planning time, which
    is too late for the create-form 纯爽 switch: a book that dies at the
    conception gates never reaches planning, so the switch must already be a
    readable directive inside conception and concept-tournament prompts
    (2026-07-24: a minimal-cost book was hard-killed by the logline gate over
    a 随机系统收税 that conception invented while seeing only the bare token
    ``"cost_style": "minimal"``).
    """

    return _cost_style_directive(cost_style, is_en=is_en)


__all__ = [
    "build_ideology_cost_source_supersession",
    "build_ideology_system_prompt",
    "build_ideology_user_prompt",
    "cost_style_directive",
    "derive_ideology_kernel",
    "fallback_ideology_kernel",
    "ideology_kernel_health_summary",
    "parse_ideology_kernel",
    "resolve_cached_ideology_cost_source",
]
