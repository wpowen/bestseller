from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ObligatoryScene(BaseModel, frozen=True):
    """A genre-signature scene type that the author *suggests* should appear.

    L4 de-homogenisation note
    -------------------------
    Prior to Batch 3, every same-genre book was forced to contain the
    identical scene set (``first_breakthrough`` → ``face_slap`` → ...)
    because the consistency gate flagged absence at ``medium`` severity.
    That turned genre "signature" scenes into a shared script and caused
    clone convergence across unrelated projects.

    Since Batch 3 the default is **suggested, not required**
    (``required=False``).  Missing suggested scenes surface only as ``low``
    severity advisory findings — they never block drafts.

    Authors can still opt back into a hard requirement on a per-scene
    basis by setting ``required: true`` in the facet fragment YAML.
    Prefer using ``required=False`` so different books within the same
    genre can diverge naturally.
    """

    code: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    timing: str = Field(
        default="any",
        description=(
            "When this scene should appear: "
            "'act_1', 'act_2_midpoint', 'act_3', 'final_chapter', 'any'."
        ),
    )
    check_keywords: list[str] = Field(default_factory=list)
    required: bool = Field(
        default=False,
        description=(
            "If True, missing scene surfaces as medium-severity finding "
            "(pre-L4 behaviour).  If False (default), surfaces only as "
            "low-severity advisory — genre homogenisation mitigation."
        ),
    )


class PromptPackFragments(BaseModel):
    """Methodology fragments injected into various writing stages.

    **A-class (retained)** — writing methodology; tell the LLM *how* to write,
    not *what* story to tell.  These are safe to keep.

    **B-class (removed in Batch 3)** — ``planner_book_spec``,
    ``planner_world_spec``, ``planner_cast_spec``, ``planner_volume_plan``,
    ``planner_outline`` were script-level plot injections that caused every
    same-genre project to start with the same skeleton.  They have been
    removed from all pack YAML files and from this model.  Any existing YAML
    that still contains these keys will be silently ignored by Pydantic (the
    model uses ``model_config = ConfigDict(extra="ignore")`` via BaseModel
    defaults).
    """

    global_rules: str | None = None
    scene_writer: str | None = None
    segment_writer: str | None = None
    scene_review: str | None = None
    scene_rewrite: str | None = None
    chapter_review: str | None = None
    chapter_rewrite: str | None = None
    structure_guidance: str | None = None

    # --- 方法论扩展片段 (Methodology-driven fragments) ---
    emotion_engineering: str | None = None
    conflict_stakes: str | None = None
    hook_design: str | None = None
    core_loop: str | None = None
    dialogue_rules: str | None = None
    visual_writing: str | None = None
    opening_rules: str | None = None
    climax_design: str | None = None
    pacing_guidance: str | None = None
    character_design: str | None = None
    reversal_design: str | None = None
    reaction_amplification: str | None = None


class PromptPack(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    version: str = Field(default="1.0", min_length=1, max_length=32)
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_notes: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)
    writing_profile_overrides: dict[str, Any] = Field(default_factory=dict)
    fragments: PromptPackFragments = Field(default_factory=PromptPackFragments)
    obligatory_scenes: list[ObligatoryScene] = Field(default_factory=list)


def _prompt_pack_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "prompt_packs"


@lru_cache(maxsize=1)
def load_prompt_pack_registry() -> dict[str, PromptPack]:
    registry: dict[str, PromptPack] = {}
    pack_dir = _prompt_pack_dir()
    if not pack_dir.exists():
        return registry
    for path in sorted(pack_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        pack = PromptPack.model_validate(raw)
        registry[pack.key] = pack
    return registry


def list_prompt_packs() -> list[PromptPack]:
    return list(load_prompt_pack_registry().values())


def get_prompt_pack(key: str | None) -> PromptPack | None:
    if not key:
        return None
    return load_prompt_pack_registry().get(key)


def infer_default_prompt_pack_key(genre: str, sub_genre: str | None = None) -> str | None:
    label = f"{genre} {sub_genre or ''}".lower()
    # Cultivation-leveling spine: 诡异修仙 / 高武 / 极道 etc. When present, a
    # 规则怪谈 / 怪谈 token is HORROR FLAVOR on a 升级流 cultivation book, not a
    # rule-survival/detective engine. Without this guard those tokens short-
    # circuit to ``suspense-mystery`` below (before the xianxia catch-all is ever
    # reached), which then overrides the correct ``xianxia-upgrade-core`` pack
    # via the contamination guard in writing_profile → the writer gets detective
    # prompts and every cultivation scene churns. Strong cultivation tokens only
    # (NOT bare 升级, which also appears in urban) so pure rule-survival stays put.
    _cultivation_spine = any(
        token in label
        for token in (
            "修仙", "修真", "仙侠", "玄幻", "高武", "武道", "极道",
            "炼体", "灵根", "金丹", "元婴", "境界", "洪荒", "渡劫",
        )
    )
    _horror_flavor = any(
        token in label
        for token in ("诡异", "规则怪谈", "规则生存", "克苏鲁", "scp", "怪谈", "诡秘")
    )
    # ── Framework convergence: cultivation-horror hybrid → canonical taxonomy ──
    # A 诡异修仙 / 高武 + 规则怪谈 / 诡异 book is cultivation with horror FLAVOR. The
    # cascade below has several secondary-tag routes (宗门经营→game-esport,
    # 规则怪谈→suspense, 系统流/副本→game-esport) that hijack such hybrids onto the
    # wrong methodology — the recurring "secondary tag beats the spine" class we
    # kept patching one route at a time. Route these through the SINGLE canonical
    # source of truth (genre_taxonomy, whose canonicalize() now applies spine
    # precedence) so the writer pack AGREES with the review profile. Pure
    # sub-modes (宗门经营 sim without a 修仙 spine, 复仇仙侠, normal books) are
    # untouched — they don't trip both flags.
    if _cultivation_spine and _horror_flavor:
        try:
            from bestseller.services.genre_taxonomy import canonicalize, get_genre

            _node = get_genre(canonicalize(genre, sub_genre))
            if _node is not None and _node.pack_default:
                return _node.pack_default
        except Exception:
            pass
        return "xianxia-upgrade-core"
    # ── 2026 trending subgenre routes (check FIRST so they win over generic tokens) ──
    # 都市修仙 / 修仙 2.0 — must beat generic 都市 and 仙 routes
    if any(token in label for token in ("都市修仙", "修仙2.0", "修仙二.0", "灵气复苏", "系统修仙", "urban-cultivation", "urban cultivation")):
        return "urban-cultivation-2.0"
    # 社畜摆烂 / 沙雕种田
    if any(token in label for token in ("社畜", "摆烂", "沙雕", "躺平", "slacker")):
        return "shezhu-bailan-comedy"
    # 娱乐圈甜宠
    if any(token in label for token in ("娱乐圈", "选秀", "剧组", "直播带货", "entertainment-circle", "entertainment")):
        return "entertainment-sweet"
    # Cozy LitRPG (Eng cozy + litrpg combo)
    if any(token in label for token in ("cozy-litrpg", "cozy litrpg", "magical inn", "treasured bakery", "cozy crafting")):
        return "cozy-litrpg"
    # System Apocalypse — Healer / Support
    if any(token in label for token in ("system apocalypse", "system-apocalypse", "healer mc", "support class", "healer-mc")):
        return "system-apocalypse-healer"
    # Villainess / Novel-Extra reincarnation
    if any(token in label for token in ("villainess", "otome isekai", "novel extra", "novel-extra", "反派千金", "穿成反派", "穿书反派")):
        return "villainess-reincarnation"

    # ── Pre-existing routes ──
    # Rule-survival/meta games are suspense engines even when the label also
    # contains apocalypse/sci-fi terms; route them before generic apocalypse.
    # Skipped when a cultivation spine is present (then it is horror flavor on a
    # 升级流 book — see ``_cultivation_spine`` above).
    if not _cultivation_spine and any(
        token in label
        for token in (
            "规则生存",
            "规则怪谈",
            "规则类",
            "副本规则",
            "meta博弈",
            "元叙事",
            "剧本杀",
            "rule survival",
            "rule horror",
            "infinite flow",
        )
    ):
        return "suspense-mystery"
    # Apocalypse / survival (check before sci-fi to catch "末日科幻" correctly)
    if any(token in label for token in ("末日", "囤货", "废土")):
        return "apocalypse-supply-chain"
    # Suspense & mystery (cultivation-spine books treat 怪谈/恐怖 as flavor and
    # fall through to the xianxia routes below).
    if not _cultivation_spine and any(
        token in label for token in ("推理", "探案", "怪谈", "诡事", "民俗", "悬疑", "恐怖", "惊悚")
    ):
        return "suspense-mystery"
    # Female-lead palace drama (check before history to catch "宫斗权谋")
    if any(token in label for token in ("宫斗", "大女主", "后宫", "心理暗战", "女帝")):
        return "female-palace"
    # Historical & strategy
    if any(token in label for token in ("历史", "争霸", "经商", "穿越", "考据", "权谋", "三国", "战国")):
        return "history-strategy"
    # Sci-fi & space
    if any(token in label for token in ("星海", "星际", "黑科技", "机甲", "太空", "科幻")):
        return "scifi-starwar"
    # Game & esport
    if any(token in label for token in ("游戏", "电竞", "无限流", "怪物猎人", "副本", "系统流")):
        return "game-esport"
    # Eastern aesthetic fantasy
    if any(token in label for token in ("东方美学", "国风", "水墨", "诗词", "古典仙侠")):
        return "eastern-aesthetic"
    # ── Xianxia sub-genre fan-out (L1 de-homogenisation) ──
    # Problem: every plain "仙侠" / "玄幻" book routed to the same
    # ``xianxia-upgrade-core`` pack, causing L3 script-injection collisions
    # (废灵根 → 宗门压迫 → 反派方域) across unrelated projects. These
    # sub-routes give users (or auto-categorisation) a way to pull a xianxia
    # book onto an adjacent methodology pack with different beats/tropes.
    # Each route is an intentional semantic bridge, not a misfit.
    # -- revenge-driven / blood-feud xianxia → history-strategy
    #    (scheming, power-games, long-arc plotting; fits revenge DNA)
    if any(
        token in label
        for token in (
            "复仇仙侠", "血仇修仙", "灭门修仙", "灭门仙侠",
            "血海深仇", "复仇修仙",
        )
    ):
        return "history-strategy"
    # -- sect-management / dojo-builder xianxia → game-esport
    #    (simulation/management loop; progression driven by roster+infra)
    if any(
        token in label
        for token in (
            "宗门经营", "宗门养成", "门派养成", "掌门仙侠", "建宗立派",
            "宗门建造", "仙门模拟",
        )
    ):
        return "game-esport"
    # -- antihero / demonic-path / villain-pov xianxia → psychological-thriller
    #    (dark-protagonist psychology, paranoia, moral compromise)
    if any(
        token in label
        for token in (
            "魔修", "魔头", "反派仙侠", "黑化仙侠", "魔道仙侠",
            "反英雄仙侠", "反派修仙",
        )
    ):
        return "psychological-thriller"
    # -- crafting-focused xianxia (alchemy/forging/talisman) → litrpg-progression
    #    (skill-tree / recipe / progression loops map cleanly to crafting)
    if any(
        token in label
        for token in (
            "炼丹仙侠", "炼器仙侠", "丹师修仙", "符修", "器修",
            "炼丹修仙", "炼器修仙",
        )
    ):
        return "litrpg-progression"
    # -- slice-of-life / sect-farming xianxia → cozy-fantasy
    #    (magical-inn / cozy-crafting vibe fits 种田/养成 pacing)
    if any(
        token in label
        for token in (
            "种田仙侠", "修仙种田", "仙门种田", "修仙养成",
            "田园仙侠",
        )
    ):
        return "cozy-fantasy"
    # Urban power — MUST run before the generic xianxia catch-all below.
    # The catch-all matches the over-broad token "升级", which appears in
    # nearly every 爽文 (including urban). A 都市/异能 book whose generated
    # sub-genre or tone mentions 升级 (e.g. "迪化升级", "系统升级流") would
    # otherwise be hijacked to xianxia-upgrade-core. Regression: 误读成神.
    if any(token in label for token in ("都市", "异能", "现实")):
        return "urban-power-reversal"
    # Romance / female-frequency (general) — also runs before the xianxia
    # catch-all, since "成长" frequently pairs with "升级" in these labels.
    if any(token in label for token in ("女频", "言情", "成长", "恋爱")):
        return "romance-tension-growth"
    # Generic Xianxia / xuanhuan (catch-all) — intentionally runs LAST so the
    # specific sub-routes above and the urban/romance routes just above win
    # first. A pure 升级流 / 仙侠 / 玄幻 label with no urban or romance token
    # still lands here.
    if any(token in label for token in ("仙", "玄幻", "奇幻", "升级", "修真")):
        return "xianxia-upgrade-core"
    # ── Canonical fallback (convergence) ──
    # The keyword cascade above misses some valid taxonomy genres whose label
    # uses none of its tokens (e.g. "高武世界" → no 仙/玄幻/升级 token → previously
    # returned None → writer got a generic pack). Defer to the canonical
    # taxonomy's pack_default before giving up, so any genre the taxonomy knows
    # gets a genre-consistent pack and agrees with the review profile.
    try:
        from bestseller.services.genre_taxonomy import canonicalize, get_genre

        _node = get_genre(canonicalize(genre, sub_genre))
        if _node is not None and _node.pack_default:
            return _node.pack_default
    except Exception:
        pass
    return None


def resolve_prompt_pack(key: str | None, *, genre: str, sub_genre: str | None = None) -> PromptPack | None:
    explicit = get_prompt_pack(key)
    if explicit is not None:
        return explicit
    return get_prompt_pack(infer_default_prompt_pack_key(genre, sub_genre))


def render_prompt_pack_prompt_block(pack: PromptPack | None) -> str:
    if pack is None:
        return ""
    lines = [
        f"Prompt Pack：{pack.name}（{pack.key} v{pack.version}）",
        f"- 定位：{pack.description}",
    ]
    if pack.tags:
        lines.append(f"- 关键词：{'、'.join(pack.tags)}")
    if pack.source_notes:
        lines.append(f"- 设计说明：{'；'.join(pack.source_notes)}")
    if pack.anti_patterns:
        lines.append(f"- 明确避免：{'；'.join(pack.anti_patterns)}")
    if pack.fragments.global_rules:
        lines.append(f"- Pack 级写法规则：{pack.fragments.global_rules}")
    return "\n".join(lines)


def render_prompt_pack_fragment(pack: PromptPack | None, fragment_name: str) -> str:
    if pack is None:
        return ""
    value = getattr(pack.fragments, fragment_name, None)
    return value.strip() if isinstance(value, str) and value.strip() else ""


# Methodology fragment keys that should be injected into scene writing prompts.
_METHODOLOGY_SCENE_FRAGMENTS = (
    "emotion_engineering",
    "conflict_stakes",
    "hook_design",
    "core_loop",
    "dialogue_rules",
    "visual_writing",
    "pacing_guidance",
    "reaction_amplification",
)

# Methodology fragment keys for chapter review prompts.
_METHODOLOGY_REVIEW_FRAGMENTS = (
    "emotion_engineering",
    "conflict_stakes",
    "hook_design",
    "core_loop",
    "pacing_guidance",
)

# Methodology fragment keys for planner/outline prompts.
_METHODOLOGY_PLANNER_FRAGMENTS = (
    "opening_rules",
    "climax_design",
    "character_design",
    "reversal_design",
    "core_loop",
)


def render_methodology_block(
    pack: PromptPack | None,
    *,
    phase: str = "scene",
) -> str:
    """Render combined methodology guidance for a phase.

    Delegates to ``methodology_bridge.render_phase_block`` so sparse prompt
    packs still receive ``writing_methodology.yaml`` fallbacks.
    """
    from bestseller.services.methodology_bridge import render_phase_block

    phase_map = {
        "scene": "scene",
        "review": "review",
        "planner": "planner",
        "prewrite": "prewrite",
        "judge": "judge",
    }
    bridge_phase = phase_map.get(phase, "scene")
    return render_phase_block(
        pack,
        phase=bridge_phase,  # type: ignore[arg-type]
        heading="写法方法论指导",
    )
