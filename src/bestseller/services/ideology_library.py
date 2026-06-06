"""Typed loader + diversity engine for the core-ideology layer.

Two assets, two roles:

* ``config/motif_library.yaml`` — the *deep structure* scaffold: 13 motifs in 4
  layers (cosmic-order / subject-choice / cognitive-crisis / ethical-reversal).
  Small, stable; the reasoning lenses, NOT the theme vocabulary limit.
* ``config/theme_corpus.yaml`` — the *surface theme* pool: a large, genre-agnostic
  set of concrete thematic propositions (主主题/子题). Expandable to 1000-2000.

**Selection is genre-DECOUPLED.** There is no genre→theme map. A per-book
*diversity seed* (premise + title identity) drives motif/theme selection, so two
different premises in the SAME genre land on different spines and different
themes — preventing the same-genre homogenisation the product must avoid.

Dependency-light (no LLM, no DB). Mirrors ``services/litstyle_prose.py``.
"""

# ruff: noqa: RUF001, E501

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_str,
    as_str_tuple,
    load_yaml,
)

_CONFIG_FILENAME = "motif_library.yaml"
_THEME_CORPUS_FILENAME = "theme_corpus.yaml"
_MAINSTREAM_THEMES_FILENAME = "mainstream_themes.yaml"


# ---------------------------------------------------------------------------
# Typed view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotifLayerSpec:
    """One ``layers`` entry — a structural layer of the 4-layer taxonomy."""

    key: str
    display_name: str
    function: str
    question: str
    order: int
    genre_shells: tuple[str, ...]
    motif_keys: tuple[str, ...]


@dataclass(frozen=True)
class Motif:
    """One ``motifs`` entry — a worldview thesis archetype (genre-neutral)."""

    key: str
    layer: str
    display_name: str
    one_line: str
    philosophical_origin: str
    representative_work: str
    thesis_template: str
    core_question_template: str
    conflict_type: str
    character_arc: str
    worldview_setting: str
    plot_template: str
    suspense_technique: str
    belief_initial: str
    belief_shatter: str
    belief_reconstruction: str
    common_traps: str
    trap_guard: str
    concrete_symbol_hints: tuple[str, ...]
    commercial: dict[str, str]
    reader_segment: str
    pairs_well_with: tuple[str, ...]
    # Writing scaffolding (report 母题模板表) — proven execution patterns.
    opening_hook: str = ""
    three_act: str = ""
    character_paradigm: str = ""
    key_scenes: tuple[str, ...] = ()
    extensible_subplots: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThemeEntry:
    """One genre-agnostic theme proposition.

    ``layer`` is derived from ``motif`` at load time. ``subject`` names the
    mainstream theme territory (love / power / justice / 守护 / 逆袭 …) when the
    proposition comes from ``mainstream_themes.yaml``; it is empty for the looser
    ``theme_corpus.yaml`` aphorisms. ``grounded`` (subject != "") marks the
    recognized, reader-accepted themes we prefer as the primary 主主题. No genre
    field — themes are deliberately decoupled from genre.
    """

    id: str
    motif: str
    layer: str
    proposition: str
    tone: str
    subject: str = ""

    @property
    def grounded(self) -> bool:
        return bool(self.subject)


@dataclass(frozen=True)
class MainstreamSubject:
    """One ``mainstream_themes.yaml`` subject — a recognized theme territory."""

    id: str
    name: str
    aka: str
    motif: str
    layer: str
    grounding: str
    examples: tuple[str, ...]
    statements: tuple[str, ...]


@dataclass(frozen=True)
class CombinationRecipe:
    """One ``combinations`` entry — a battle-tested motif formula (descriptive)."""

    name: str
    primary: str
    secondary: tuple[str, ...]
    hidden: str
    logic: str
    emotion: str
    selling_point: str
    fits_genres: tuple[str, ...]


@dataclass(frozen=True)
class CompositionRule:
    formula: str
    structure: str
    secondary_role_action: str
    secondary_role_suspense: str
    hidden_endgame: str
    three_binding_questions: tuple[str, ...]
    longevity_rule: str


@dataclass(frozen=True)
class MotifLibrary:
    """Typed view over ``motif_library.yaml`` + ``theme_corpus.yaml``."""

    version: str
    layers: tuple[MotifLayerSpec, ...]
    motifs: tuple[Motif, ...]
    combinations: tuple[CombinationRecipe, ...]
    composition_rule: CompositionRule
    themes: tuple[ThemeEntry, ...]
    subjects: tuple[MainstreamSubject, ...] = ()

    def by_key(self, key: str) -> Motif | None:
        for motif in self.motifs:
            if motif.key == key:
                return motif
        return None

    def by_layer(self, layer_key: str) -> tuple[Motif, ...]:
        return tuple(m for m in self.motifs if m.layer == layer_key)

    def themes_for_motif(self, motif_key: str, *, grounded_only: bool = False) -> tuple[ThemeEntry, ...]:
        return tuple(
            t for t in self.themes
            if t.motif == motif_key and (t.grounded or not grounded_only)
        )

    @property
    def grounded_themes(self) -> tuple[ThemeEntry, ...]:
        return tuple(t for t in self.themes if t.grounded)

    @property
    def motif_keys(self) -> tuple[str, ...]:
        return tuple(m.key for m in self.motifs)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _parse_layers(raw: object) -> tuple[MotifLayerSpec, ...]:
    out: list[MotifLayerSpec] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            key = as_str(data.get("key"))
            if not key:
                continue
            out.append(
                MotifLayerSpec(
                    key=key,
                    display_name=as_str(data.get("display_name"), default=key),
                    function=as_str(data.get("function")),
                    question=as_str(data.get("question")),
                    order=_as_int(data.get("order"), default=0),
                    genre_shells=as_str_tuple(data.get("genre_shells")),
                    motif_keys=as_str_tuple(data.get("motif_keys")),
                )
            )
    return tuple(sorted(out, key=lambda lyr: lyr.order))


def _parse_belief(data: dict) -> tuple[str, str, str]:
    belief = as_dict(data.get("belief_arc"))
    return (
        as_str(belief.get("initial")),
        as_str(belief.get("shatter")),
        as_str(belief.get("reconstruction")),
    )


def _parse_motif_templates(raw: object) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            motif = as_str(data.get("motif"))
            if motif:
                out[motif] = data
    return out


def _parse_motifs(
    raw: object, templates: dict[str, dict[str, object]] | None = None
) -> tuple[Motif, ...]:
    templates = templates or {}
    out: list[Motif] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            key = as_str(data.get("key"))
            if not key:
                continue
            initial, shatter, reconstruction = _parse_belief(data)
            commercial_raw = as_dict(data.get("commercial"))
            commercial = {str(k): str(v) for k, v in commercial_raw.items()}
            tpl = templates.get(key, {})
            out.append(
                Motif(
                    key=key,
                    layer=as_str(data.get("layer")),
                    display_name=as_str(data.get("display_name"), default=key),
                    one_line=as_str(data.get("one_line")),
                    philosophical_origin=as_str(data.get("philosophical_origin")),
                    representative_work=as_str(data.get("representative_work")),
                    thesis_template=as_str(data.get("thesis_template")),
                    core_question_template=as_str(data.get("core_question_template")),
                    conflict_type=as_str(data.get("conflict_type")),
                    character_arc=as_str(data.get("character_arc")),
                    worldview_setting=as_str(data.get("worldview_setting")),
                    plot_template=as_str(data.get("plot_template")),
                    suspense_technique=as_str(data.get("suspense_technique")),
                    belief_initial=initial,
                    belief_shatter=shatter,
                    belief_reconstruction=reconstruction,
                    common_traps=as_str(data.get("common_traps")),
                    trap_guard=as_str(data.get("trap_guard")),
                    concrete_symbol_hints=as_str_tuple(data.get("concrete_symbol_hints")),
                    commercial=commercial,
                    reader_segment=as_str(data.get("reader_segment")),
                    pairs_well_with=as_str_tuple(data.get("pairs_well_with")),
                    opening_hook=as_str(tpl.get("opening_hook")),
                    three_act=as_str(tpl.get("three_act")),
                    character_paradigm=as_str(tpl.get("character_paradigm")),
                    key_scenes=as_str_tuple(tpl.get("key_scenes")),
                    extensible_subplots=as_str_tuple(tpl.get("extensible_subplots")),
                )
            )
    return tuple(out)


def _parse_combinations(raw: object) -> tuple[CombinationRecipe, ...]:
    out: list[CombinationRecipe] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            name = as_str(data.get("name"))
            if not name:
                continue
            out.append(
                CombinationRecipe(
                    name=name,
                    primary=as_str(data.get("primary")),
                    secondary=as_str_tuple(data.get("secondary")),
                    hidden=as_str(data.get("hidden")),
                    logic=as_str(data.get("logic")),
                    emotion=as_str(data.get("emotion")),
                    selling_point=as_str(data.get("selling_point")),
                    fits_genres=as_str_tuple(data.get("fits_genres")),
                )
            )
    return tuple(out)


def _parse_composition_rule(raw: object) -> CompositionRule:
    data = as_dict(raw)
    roles = as_dict(data.get("secondary_roles"))
    return CompositionRule(
        formula=as_str(data.get("formula")),
        structure=as_str(data.get("structure")),
        secondary_role_action=as_str(roles.get("action")),
        secondary_role_suspense=as_str(roles.get("suspense")),
        hidden_endgame=as_str(data.get("hidden_endgame")),
        three_binding_questions=as_str_tuple(data.get("three_binding_questions")),
        longevity_rule=as_str(data.get("longevity_rule")),
    )


def _parse_themes(raw: object, motif_layer: dict[str, str]) -> tuple[ThemeEntry, ...]:
    out: list[ThemeEntry] = []
    seen_ids: set[str] = set()
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            theme_id = as_str(data.get("id"))
            proposition = as_str(data.get("proposition"))
            motif = as_str(data.get("motif"))
            if not proposition or theme_id in seen_ids:
                continue
            seen_ids.add(theme_id or proposition[:12])
            out.append(
                ThemeEntry(
                    id=theme_id or proposition[:16],
                    motif=motif,
                    layer=motif_layer.get(motif, ""),
                    proposition=proposition,
                    tone=as_str(data.get("tone")),
                    subject=as_str(data.get("subject")),
                )
            )
    return tuple(out)


def _parse_mainstream(
    raw: object, motif_layer: dict[str, str]
) -> tuple[tuple[MainstreamSubject, ...], tuple[ThemeEntry, ...]]:
    """Parse ``mainstream_themes.yaml`` into subjects + flattened (grounded) themes.

    Each subject's ``statements`` become :class:`ThemeEntry` rows tagged with the
    subject id (``grounded=True``), mapped to the subject's motif/layer. These are
    the recognized, reader-accepted themes we prefer as the primary 主主题.
    """

    subjects: list[MainstreamSubject] = []
    themes: list[ThemeEntry] = []
    if not isinstance(raw, (list, tuple)):
        return (), ()
    for entry in raw:
        data = as_dict(entry)
        sid = as_str(data.get("id"))
        motif = as_str(data.get("motif"))
        if not sid:
            continue
        layer = motif_layer.get(motif, "")
        statements = as_str_tuple(data.get("statements"))
        subjects.append(
            MainstreamSubject(
                id=sid,
                name=as_str(data.get("name"), default=sid),
                aka=as_str(data.get("aka")),
                motif=motif,
                layer=layer,
                grounding=as_str(data.get("grounding")),
                examples=as_str_tuple(data.get("examples")),
                statements=statements,
            )
        )
        for i, prop in enumerate(statements):
            themes.append(
                ThemeEntry(
                    id=f"{sid}_{i:02d}",
                    motif=motif,
                    layer=layer,
                    proposition=prop,
                    tone="",
                    subject=sid,
                )
            )
    return tuple(subjects), tuple(themes)


@lru_cache(maxsize=1)
def load_motif_library() -> MotifLibrary:
    """Return the cached, typed view over the motif library + theme pools.

    Theme pool = grounded mainstream themes (``mainstream_themes.yaml``, preferred
    for the primary 主主题) FIRST, then the looser ``theme_corpus.yaml`` aphorisms.
    """

    raw = load_yaml(_CONFIG_FILENAME)
    templates = _parse_motif_templates(raw.get("motif_templates"))
    motifs = _parse_motifs(raw.get("motifs"), templates)
    motif_layer = {m.key: m.layer for m in motifs}

    mainstream_raw = load_yaml(_MAINSTREAM_THEMES_FILENAME)
    subjects, grounded = _parse_mainstream(mainstream_raw.get("subjects"), motif_layer)

    corpus_raw = load_yaml(_THEME_CORPUS_FILENAME)
    aphorisms = _parse_themes(corpus_raw.get("themes"), motif_layer)

    # Grounded themes lead; dedupe by proposition so a statement can't double up.
    seen_props: set[str] = set()
    themes: list[ThemeEntry] = []
    for t in (*grounded, *aphorisms):
        if t.proposition in seen_props:
            continue
        seen_props.add(t.proposition)
        themes.append(t)

    return MotifLibrary(
        version=as_str(raw.get("version")),
        layers=_parse_layers(raw.get("layers")),
        motifs=motifs,
        combinations=_parse_combinations(raw.get("combinations")),
        composition_rule=_parse_composition_rule(raw.get("composition_rule")),
        themes=tuple(themes),
        subjects=subjects,
    )


@lru_cache(maxsize=1)
def load_theme_corpus() -> tuple[ThemeEntry, ...]:
    """The genre-agnostic theme pool (also reachable via ``load_motif_library().themes``)."""

    return load_motif_library().themes


# ---------------------------------------------------------------------------
# Exemplars + creation principles (report's worked premises + 实操原则)
# ---------------------------------------------------------------------------

_EXEMPLARS_FILENAME = "ideology_exemplars.yaml"


@dataclass(frozen=True)
class IdeologyExemplar:
    """One worked premise from the report — title + motif recipe + synopsis."""

    title: str
    recipe: tuple[str, ...]
    synopsis: str


@lru_cache(maxsize=1)
def load_ideology_exemplars() -> tuple[tuple[IdeologyExemplar, ...], dict[str, str]]:
    """Return the report's worked-premise exemplars + creation principles."""

    raw = load_yaml(_EXEMPLARS_FILENAME)
    exemplars: list[IdeologyExemplar] = []
    for entry in raw.get("exemplars") or []:
        data = as_dict(entry)
        title = as_str(data.get("title"))
        if not title:
            continue
        exemplars.append(
            IdeologyExemplar(
                title=title,
                recipe=as_str_tuple(data.get("recipe")),
                synopsis=as_str(data.get("synopsis")),
            )
        )
    principles = {str(k): str(v) for k, v in as_dict(raw.get("principles")).items()}
    return tuple(exemplars), principles


def render_exemplars_block(*, seed: str = "", count: int = 2) -> str:
    """Render a few seed-selected worked exemplars + the creation principles.

    The exemplars are the report's own mainstream worked premises — used as
    few-shot so the model differentiates from proven patterns rather than
    inventing a contrived theme.
    """

    exemplars, principles = load_ideology_exemplars()
    if not exemplars and not principles:
        return ""
    lines: list[str] = []
    if exemplars:
        ordered = sorted(exemplars, key=lambda e: stable_seed_int(seed, "exemplar", e.title))
        chosen = ordered[: max(1, count)]
        lines.append("# 母题配方范例（报告自带的成熟样例 — 照着『分化/具体化』, 不要凭空硬造）")
        for ex in chosen:
            lines.append(f"- 《{ex.title}》[{' + '.join(ex.recipe)}]：{ex.synopsis}")
    if principles:
        lines.append("# 创作原则（必须遵循）")
        order = ["motif_weighting", "worldview_from_cost", "pacing_dual_track", "ip_preembed", "closing_rule"]
        for key in order:
            if key in principles:
                lines.append(f"- {principles[key]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Diversity seed — genre-free, premise/identity-driven, deterministic-per-book
# ---------------------------------------------------------------------------


def stable_seed_int(*parts: str) -> int:
    """Stable (non-randomised) hash of the seed parts → big int.

    Uses sha256 (NOT Python's salted ``hash``) so the same book identity always
    maps to the same theme — reproducible — while different identities diverge.
    """

    seed = "::".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
    if not seed:
        seed = "default-ideology-seed"
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)


def book_diversity_seed(
    *, premise: str = "", title: str = "", extra: str = ""
) -> str:
    """Compose a per-book diversity seed from its identity — NEVER from genre.

    Genre is intentionally excluded so the genre cannot steer theme selection.
    """

    return "::".join(p.strip() for p in (title, premise, extra) if p and p.strip())


# ---------------------------------------------------------------------------
# Motif spine selection (seed-driven, genre-free)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotifFormula:
    """A resolved primary + secondary(action/suspense) + hidden motif spine."""

    primary: Motif
    secondary_action: Motif
    secondary_suspense: Motif
    hidden: Motif
    recipe_name: str = ""
    logic: str = ""
    selling_point: str = ""

    def all_motifs(self) -> tuple[Motif, ...]:
        return (self.primary, self.secondary_action, self.secondary_suspense, self.hidden)

    def covered_layers(self) -> frozenset[str]:
        return frozenset(m.layer for m in self.all_motifs())


_PRIMARY_LAYER = "cosmic_order"
_ACTION_LAYER = "subject_choice"
_SUSPENSE_LAYER = "cognitive_crisis"
_HIDDEN_LAYER = "ethical_reversal"


def _pick_in_layer(
    library: MotifLibrary,
    layer: str,
    seed: str,
    *,
    exclude: frozenset[str] = frozenset(),
) -> Motif:
    """Seed-rotate a motif WITHIN a layer (genre plays no part)."""

    cands = [m for m in library.by_layer(layer) if m.key not in exclude]
    if not cands:
        cands = [m for m in library.motifs if m.key not in exclude] or list(library.motifs)
    idx = stable_seed_int(seed, "motif-layer", layer) % len(cands)
    return cands[idx]


def _enrich_from_recipe(library: MotifLibrary, primary: Motif, seed: str) -> CombinationRecipe | None:
    """Pick a recipe with matching primary for display flavour (genre-free, seed-broken ties)."""

    matches = [r for r in library.combinations if r.primary == primary.key]
    if not matches:
        return None
    return matches[stable_seed_int(seed, "recipe") % len(matches)]


def suggest_motif_formula(
    *,
    seed: str = "",
    library: MotifLibrary | None = None,
) -> MotifFormula:
    """Build the canonical 4-layer motif spine, seed-rotated WITHIN each layer.

    Structure (depth scaffold; the surface 主主题/子题 come from the theme corpus):
        primary            = cosmic-order   (宇宙前提 base)
        secondary_action   = subject-choice (drives "how the hero acts")
        secondary_suspense = cognitive-crisis (drives "what gets revealed")
        hidden             = ethical-reversal (late value re-estimation)

    Each slot's specific motif is chosen by the per-book diversity ``seed`` — NOT
    by genre — so two same-genre books get different spines. Guarantees all four
    layers covered + distinct action/suspense roles. The LLM derivation may
    deviate; the coherence gate audits coverage softly.
    """

    library = library or load_motif_library()

    primary = _pick_in_layer(library, _PRIMARY_LAYER, seed)
    used = {primary.key}
    action = _pick_in_layer(library, _ACTION_LAYER, seed, exclude=frozenset(used))
    used.add(action.key)
    suspense = _pick_in_layer(library, _SUSPENSE_LAYER, seed, exclude=frozenset(used))
    used.add(suspense.key)
    hidden = _pick_in_layer(library, _HIDDEN_LAYER, seed, exclude=frozenset(used))

    recipe = _enrich_from_recipe(library, primary, seed)
    return MotifFormula(
        primary=primary,
        secondary_action=action,
        secondary_suspense=suspense,
        hidden=hidden,
        recipe_name=recipe.name if recipe else "",
        logic=recipe.logic if recipe else library.composition_rule.structure,
        selling_point=recipe.selling_point if recipe else "",
    )


# ---------------------------------------------------------------------------
# Theme selection — the large, diverse, genre-free surface (主主题 + 子题)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThemeSelection:
    """A book's resolved surface themes: one 主主题 + woven 子题."""

    primary_theme: ThemeEntry | None
    sub_themes: tuple[ThemeEntry, ...]


def _theme_in_motif(
    library: MotifLibrary,
    motif_key: str,
    seed: str,
    salt: str,
    *,
    grounded_only: bool = False,
) -> ThemeEntry | None:
    pool = library.themes_for_motif(motif_key, grounded_only=grounded_only)
    if not pool and grounded_only:
        pool = library.themes_for_motif(motif_key)  # fall back to full pool
    if not pool:
        return None
    return pool[stable_seed_int(seed, "theme", salt, motif_key) % len(pool)]


def select_themes(
    library: MotifLibrary,
    *,
    formula: MotifFormula,
    seed: str,
    n_sub: int = 4,
) -> ThemeSelection:
    """Pick one primary theme (主主题) + woven sub-themes (子题) from the corpus.

    The PRIMARY theme's emphasis motif is chosen by ``seed`` among the spine's four
    motifs — so the headline theme may foreground ANY layer (cosmic / subject /
    cognitive / ethical), not just the structural cosmic primary. The 主主题 is
    drawn from the GROUNDED (mainstream, reader-recognized) pool so it never reads
    as a contrived/idiosyncratic theme; sub-themes may use the wider pool for
    texture. Seed-picked, deduped, entirely genre-free.
    """

    spine = list(formula.all_motifs())
    # Which spine motif does the 主主题 foreground? (seed → any of the four)
    emphasis = spine[stable_seed_int(seed, "emphasis") % len(spine)]
    # Prefer a grounded/mainstream theme for the headline 主主题.
    primary_theme = _theme_in_motif(library, emphasis.key, seed, "primary", grounded_only=True)
    chosen_ids: set[str] = {primary_theme.id} if primary_theme else set()

    sub: list[ThemeEntry] = []
    # One sub-theme per remaining spine motif (woven 子题, one per layer for spread).
    for motif in spine:
        if motif.key == emphasis.key:
            continue
        t = _theme_in_motif(library, motif.key, seed, "sub")
        if t and t.id not in chosen_ids:
            sub.append(t)
            chosen_ids.add(t.id)

    # Top up from the whole corpus (still seed-diverse, deduped) if asked for more.
    if len(sub) < n_sub and library.themes:
        ordered = sorted(
            library.themes,
            key=lambda t: stable_seed_int(seed, "topup", t.id),
        )
        for t in ordered:
            if len(sub) >= n_sub:
                break
            if t.id not in chosen_ids:
                sub.append(t)
                chosen_ids.add(t.id)

    return ThemeSelection(primary_theme=primary_theme, sub_themes=tuple(sub[:n_sub]))


def sample_corpus_for_prompt(
    library: MotifLibrary, *, seed: str, count: int = 14
) -> tuple[ThemeEntry, ...]:
    """A seed-shuffled spread of themes to show the LLM the corpus's breadth."""

    if not library.themes:
        return ()
    ordered = sorted(library.themes, key=lambda t: stable_seed_int(seed, "sample", t.id))
    return tuple(ordered[:count])


# ---------------------------------------------------------------------------
# Prompt-block renderers (the derivation "menu")
# ---------------------------------------------------------------------------


def render_motif_library_prompt_block(
    *,
    seed: str = "",
    library: MotifLibrary | None = None,
    max_per_layer: int = 4,
    sample_themes: int = 14,
) -> str:
    """Render the motif scaffold + a diverse theme-corpus sample for derivation.

    Critically: shows NO genre-specific suggestion, and instructs the model to
    pick by PREMISE and to AVOID the genre's clichéd default theme.
    """

    library = library or load_motif_library()
    rule = library.composition_rule
    lines: list[str] = [
        "# 核心理念脚手架（13 母题 × 4 层 — 母题是『深层结构/思考透镜』, 不是题材, 也不是主题上限）",
        f"组合公式：{rule.formula}",
        f"结构：{rule.structure}",
        f"- 副母题·管行动：{rule.secondary_role_action}",
        f"- 副母题·管悬念：{rule.secondary_role_suspense}",
        f"- 隐藏终局母题：{rule.hidden_endgame}",
        "三个绑定问题（必须回答）：" + "；".join(rule.three_binding_questions),
        "",
        "【硬性要求·主题必须主流且接地】主主题必须取自下方『主流主题库』里公认、读者耳熟能详的"
        "主题(爱与牺牲/成长/守护/复仇/权力与腐化/正义/救赎/命运…), 再据『本书前提』做分化与具体化; "
        "严禁为了标新立异硬造一个扭曲、不符合常识的核心理念(那样读者会觉得『理念不对』而弃读)。",
        "【硬性多样性要求】同题材的不同书必须给出不同的主主题与子题, 不要因为是仙侠就写天地不仁、"
        "是历史就写大道无情——靠前提分化, 不靠题材套路。",
        "",
    ]

    # Mainstream subject menu — the recognized, reader-accepted theme territories.
    if library.subjects:
        lines.append("# 主流主题库（公认选题 — 主主题从这里选, 然后据前提具体化成一句本书的论断）")
        for layer in library.layers:
            subs = [s for s in library.subjects if s.layer == layer.key]
            if not subs:
                continue
            names = "、".join(f"{s.name}" for s in subs)
            lines.append(f"- [{layer.display_name}] {names}")
        lines.append("")

    for layer in library.layers:
        lines.append(f"## {layer.display_name}（{layer.function}）：{layer.question}")
        for motif in library.by_layer(layer.key)[:max_per_layer]:
            lines.append(
                f"- [{motif.key}] {motif.display_name}：{motif.one_line}"
            )
        lines.append("")

    # Prefer grounded (mainstream) statements in the sample so the model anchors on
    # recognized theme arguments rather than the looser aphorisms.
    grounded = library.grounded_themes
    pool = grounded or library.themes
    sample = (
        tuple(sorted(pool, key=lambda t: stable_seed_int(seed, "sample", t.id))[:sample_themes])
        if pool else ()
    )
    if sample:
        lines.append(
            f"# 主流主题样本（{len(grounded)} 条公认主题中随机 {len(sample)} 条 — 直接选其一做主主题, 再据前提改写成本书专属论断）"
        )
        for t in sample:
            subj = next((s for s in library.subjects if s.id == t.subject), None)
            tag = subj.name if subj else (library.by_key(t.motif).display_name if library.by_key(t.motif) else t.motif)
            lines.append(f"- 「{t.proposition}」（{tag}）")
        lines.append("")

    formula = suggest_motif_formula(seed=seed, library=library)
    selection = select_themes(library, formula=formula, seed=seed)
    lines.append("# 按本书前提的多样性建议（可改 — 它由前提 seed 生成, 与题材无关）")
    lines.append(
        f"- 结构母题脊柱：宇宙[{formula.primary.display_name}] · "
        f"行动[{formula.secondary_action.display_name}] · "
        f"悬念[{formula.secondary_suspense.display_name}] · "
        f"隐藏[{formula.hidden.display_name}]（4 层全覆盖）"
    )
    if selection.primary_theme is not None:
        lines.append(f"- 候选主主题：「{selection.primary_theme.proposition}」")
    if selection.sub_themes:
        lines.append("- 候选子题：" + "；".join(f"「{t.proposition}」" for t in selection.sub_themes))

    # Per-motif writing scaffolding for the chosen spine (report 母题模板表).
    scaffold = [m for m in formula.all_motifs() if m.opening_hook or m.character_paradigm]
    if scaffold:
        lines.append("# 脊柱母题的写作脚手架（开篇钩子/三幕/人物范式/关键场景 — 直接据前提改写）")
        for m in scaffold:
            bits = []
            if m.opening_hook:
                bits.append(f"钩子「{m.opening_hook}」")
            if m.three_act:
                bits.append(f"三幕「{m.three_act}」")
            if m.character_paradigm:
                bits.append(f"人物范式「{m.character_paradigm}」")
            if m.key_scenes:
                bits.append(f"关键场景[{', '.join(m.key_scenes[:4])}]")
            lines.append(f"- {m.display_name}：{'；'.join(bits)}")

    exemplars_block = render_exemplars_block(seed=seed)
    if exemplars_block:
        lines.append(exemplars_block)
    return "\n".join(lines)


__all__ = [
    "CombinationRecipe",
    "CompositionRule",
    "IdeologyExemplar",
    "MainstreamSubject",
    "Motif",
    "MotifFormula",
    "MotifLayerSpec",
    "MotifLibrary",
    "ThemeEntry",
    "ThemeSelection",
    "book_diversity_seed",
    "load_ideology_exemplars",
    "load_motif_library",
    "load_theme_corpus",
    "render_exemplars_block",
    "render_motif_library_prompt_block",
    "sample_corpus_for_prompt",
    "select_themes",
    "stable_seed_int",
    "suggest_motif_formula",
]
