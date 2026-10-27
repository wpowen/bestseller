from __future__ import annotations

import logging
import copy
import json
import re
from typing import Any

from bestseller.domain.project import ProjectCreate, WritingProfile
from bestseller.infra.db.models import ProjectModel, StyleGuideModel
from bestseller.services.content_origin_policy import (
    ContentDefaultMigration,
    leaf_values,
    migrate_content_defaults,
    origin_record,
)
from bestseller.services.prompt_packs import (
    infer_default_prompt_pack_key,
    render_prompt_pack_fragment,
    render_prompt_pack_prompt_block,
    resolve_prompt_pack,
)
from bestseller.services.truth_version import initialize_truth_metadata
from bestseller.services.writing_presets import get_platform_preset, infer_genre_preset


def _merge_lists(base: list[Any], override: list[Any]) -> list[Any]:
    merged: list[Any] = []
    for item in [*base, *override]:
        if item not in merged:
            merged.append(item)
    return merged


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = _merge_lists(merged[key], value)
        else:
            merged[key] = value
    return merged


_logger = logging.getLogger(__name__)

# Supported language families — used for validation and logging.
_SUPPORTED_LANGUAGE_PREFIXES = ("zh", "en")

# Capped set to avoid unbounded growth in long-running processes.
# 64 unique unsupported language tags is more than enough for dedup.
_UNSUPPORTED_WARNED_MAX = 64
_unsupported_warned: set[str] = set()
_GENRE_FRAMEWORK_OVERRIDE_FIELDS: dict[str, set[str]] = {
    "market": {
        "pacing_profile",
        "prompt_pack_key",
    },
    "world": {
        "worldbuilding_density",
        "info_reveal_strategy",
        "rule_hardness",
        "mystery_density",
    },
    "style": {
        "prose_style",
        "sentence_style",
        "info_density",
        "dialogue_ratio",
    },
    "methodology": {
        "emotion_spring_min_chapters",
        "emotion_spring_max_chapters",
        "core_loop_cycle_length",
        "hook_min_active",
        "hook_max_active",
        "hook_max_age_chapters",
        "visual_writing_mode",
        "dialogue_subtext_ratio",
        "safety_net_mode",
        "reaction_amplification",
    },
}
_CREATION_TONE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "epic": ("宏大", "厚重", "史诗感"),
    "light": ("轻松", "幽默", "明快"),
    "dark": ("暗黑", "冷峻", "压抑"),
    "hot": ("热血", "燃", "爽快"),
}


_ENUM_LABEL_RE = re.compile(
    r"^\s*(?:卖点|要点|亮点|看点|point|selling\s*point|item)\s*"
    r"[0-9零一二三四五六七八九十]+\s*[：:、.．)）]\s*",
    re.IGNORECASE,
)


def strip_enumeration_label(text: str) -> str:
    """剥掉「卖点N：」这类**从 prompt 示例里抄来的**序号标签。

    2026-08-24 真机（书9）：落库的 7 条卖点里有 3 条以「卖点1：」「卖点2：」
    「卖点3：」开头。根因是 conception 的 JSON 示例值自己写成了
    `["卖点1：用故事本身的人、事、反常之处说…", "卖点2：同上"]` —— 占位符把
    标签写成了字面内容，模型照抄。示例已改，这里是确定性兜底：模型换个说法
    再抄一次时不用再等一轮真机才发现。

    只剥「标签词+序号+分隔符」这一个头，正文里的冒号不受影响。
    """

    if not isinstance(text, str):
        return text
    return _ENUM_LABEL_RE.sub("", text).strip() or text.strip()


def fold_near_duplicate_points(points: list[str] | tuple[str, ...]) -> list[str]:
    """折叠近重复的卖点/标签条目（保序，先到先留）。

    真机 prompt review（2026-08-07，custom-xianxia-1786104488）：conception 产出的
    selling_points 里同一卖点带 2-3 个措辞版本（"赚钱和升级是同一口锅里颠出来的事，
    看着就上头" / "…是同一口锅里的事，看着就上瘾"），全部灌进写手 prompt。
    虫书同病（7 条实为 4 条）。

    阈值用真实数据定（同书 6 条实测）：异义对相似度最高 0.206，同义对最低
    0.467——取 0.35，两边各有 ~2 倍余量。另收「公共连续片段 ≥12 字」：换头换尾
    的同款（共享"酒楼、地府、仙门轮着来踢馆，每一波麻烦都"20 字）整体相似度可以
    被新增内容稀释到 0.35 以下，但 12 字连续原文共享在异义对里从未出现。
    """

    from difflib import SequenceMatcher

    def _long_common_run(a: str, b: str, need: int = 12) -> bool:
        if len(a) < need or len(b) < need:
            return False
        m = SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
        return m.size >= need

    kept: list[str] = []
    kept_norm: list[str] = []
    for raw in points or []:
        p = str(raw or "").strip()
        if not p:
            continue
        norm = "".join(p.split())
        if any(
            SequenceMatcher(None, norm, k).ratio() >= 0.35
            or _long_common_run(norm, k)
            for k in kept_norm
        ):
            continue
        kept.append(p)
        kept_norm.append(norm)
    return kept


def is_english_language(language: str | None) -> bool:
    normalized = (language or "").strip().lower()
    return normalized.startswith("en")


def normalize_language(language: str | None) -> str:
    """Return a normalised language tag, defaulting to ``zh-CN``.

    Logs a one-time warning if the language is not in the supported set
    (currently Chinese and English).  Unsupported languages fall back to
    English when the tag looks Latin-script (fr, de, es, pt, it …) and
    to Chinese otherwise, so the prompts at least use a familiar script.
    """
    raw = (language or "").strip()
    if not raw:
        return "zh-CN"
    lower = raw.lower()
    if any(lower.startswith(p) for p in _SUPPORTED_LANGUAGE_PREFIXES):
        return raw
    # Unsupported — warn once per tag and choose the best fallback.
    if lower not in _unsupported_warned:
        if len(_unsupported_warned) >= _UNSUPPORTED_WARNED_MAX:
            _unsupported_warned.clear()
        _unsupported_warned.add(lower)
        _logger.warning(
            "Language '%s' is not fully supported (supported: zh-*, en-*). "
            "Prompts will use the closest supported language.",
            raw,
        )
    # Latin-script languages → English prompts are closer than Chinese.
    _LATIN_PREFIXES = ("fr", "de", "es", "pt", "it", "nl", "pl", "ro", "sv", "da", "no", "fi", "cs", "hu", "tr", "vi", "id", "ms", "tl")
    if any(lower.startswith(p) for p in _LATIN_PREFIXES):
        return "en-US"
    # CJK or other → Chinese prompts are closer.
    return "zh-CN"


def sanitize_genre_story_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Strip genre presets down to framework hints instead of story content."""
    if not isinstance(overrides, dict):
        return {}

    sanitized: dict[str, Any] = {}
    for section, allowed_keys in _GENRE_FRAMEWORK_OVERRIDE_FIELDS.items():
        raw_section = overrides.get(section)
        if not isinstance(raw_section, dict):
            continue
        kept = {
            key: value
            for key, value in raw_section.items()
            if key in allowed_keys and value not in (None, "", [], {})
        }
        if kept:
            sanitized[section] = kept
    return sanitized


def _default_writing_profile_payload(language: str | None = None) -> dict[str, Any]:
    profile = WritingProfile().model_dump(mode="json")
    if is_english_language(language):
        # Localise absence; do not replace it with an English commercial recipe.
        for section in ("market", "character", "serialization"):
            for key, value in profile[section].items():
                if value == "未指定":
                    profile[section][key] = "unspecified"
        profile["market"].update(
            platform_target="unspecified", content_mode="fiction",
            update_strategy="Follow the project release plan",
        )
    return profile


def _genre_preset(genre: str, sub_genre: str | None = None) -> dict[str, Any]:
    preset = infer_genre_preset(genre, sub_genre)
    if preset is not None:
        return sanitize_genre_story_overrides(dict(preset.writing_profile_overrides))

    label = f"{genre} {sub_genre or ''}".lower()
    if any(token in label for token in ("末日", "科幻", "星际", "生存")):
        return {
            "market": {
                "pacing_profile": "fast",
            },
            "character": {
            },
            "world": {
                "worldbuilding_density": "medium",
                "mystery_density": "high",
            },
            "style": {
                "prose_style": "commercial-web-serial",
                "sentence_style": "short-punchy",
                "info_density": "lean",
                "dialogue_ratio": 0.42,
            },
        }
    # Urban / suspense — MUST precede the generic xianxia "升级" branch below,
    # so a 都市/异能 book whose label mentions 升级 keeps light, realistic
    # style defaults instead of xianxia world density. Mirrors the ordering
    # fix in prompt_packs.infer_default_prompt_pack_key. Regression: 误读成神.
    if any(token in label for token in ("都市", "异能", "悬疑", "现实")):
        return {
            "market": {
                "pacing_profile": "fast",
            },
            "character": {
            },
            "world": {
                "worldbuilding_density": "light",
                "info_reveal_strategy": "背景设定必须紧贴现实场景和事件推进。",
                "mystery_density": "medium",
            },
            "style": {
                "dialogue_ratio": 0.45,
            },
        }
    if any(token in label for token in ("女频", "成长", "言情", "宫斗")):
        return {
            "market": {
                "pacing_profile": "medium-fast",
            },
            "character": {
            },
            "world": {
                "worldbuilding_density": "light-medium",
                "mystery_density": "medium",
            },
            "style": {
                "dialogue_ratio": 0.48,
            },
        }
    # Generic xianxia / xuanhuan — runs after urban/romance so a 都市/异能 or
    # 女频 label that also contains 升级 wins above; a pure 仙/玄幻/升级 label
    # with no urban or romance token lands here.
    if any(token in label for token in ("仙", "玄幻", "奇幻", "升级")):
        return {
            "market": {
                "pacing_profile": "fast",
            },
            "character": {
            },
            "world": {
                "worldbuilding_density": "medium",
                "rule_hardness": "hard",
                "mystery_density": "medium",
            },
            "style": {
                "dialogue_ratio": 0.32,
            },
        }
    # ── English genre fallbacks ──
    if any(token in label for token in ("apocalypse", "sci-fi", "scifi", "space", "survival", "post-apocal")):
        return {
            "market": {"pacing_profile": "fast"},
            "world": {"worldbuilding_density": "medium", "mystery_density": "high"},
            "style": {"prose_style": "commercial-web-serial", "sentence_style": "short-punchy", "info_density": "lean", "dialogue_ratio": 0.42},
        }
    if any(token in label for token in ("fantasy", "progression", "litrpg", "xianxia", "cultivation", "magic")):
        return {
            "market": {"pacing_profile": "fast"},
            "world": {"worldbuilding_density": "medium", "rule_hardness": "hard", "mystery_density": "medium"},
            "style": {"dialogue_ratio": 0.32},
        }
    if any(token in label for token in ("urban", "thriller", "mystery", "suspense", "detective", "crime")):
        return {
            "market": {"pacing_profile": "fast"},
            "world": {"worldbuilding_density": "light", "mystery_density": "medium"},
            "style": {"dialogue_ratio": 0.45},
        }
    if any(token in label for token in ("romance", "love", "women", "palace", "regency", "contemporary")):
        return {
            "market": {"pacing_profile": "medium-fast"},
            "world": {"worldbuilding_density": "light-medium", "mystery_density": "medium"},
            "style": {"dialogue_ratio": 0.48},
        }
    return {}


def resolve_writing_profile(
    explicit_profile: WritingProfile | dict[str, Any] | None,
    *,
    genre: str,
    sub_genre: str | None = None,
    audience: str | None = None,
    language: str | None = None,
    forced_prompt_pack_key: str | None = None,
) -> WritingProfile:
    inferred_genre_preset = infer_genre_preset(genre, sub_genre)
    resolved_language = (
        language
        or (inferred_genre_preset.language if inferred_genre_preset is not None else None)
        or "zh-CN"
    )
    base = _default_writing_profile_payload(resolved_language)
    origin_kinds = dict.fromkeys(leaf_values(base), "framework_default")

    def record_origin(overlay: dict[str, Any], kind: str) -> None:
        origin_kinds.update(dict.fromkeys(leaf_values(overlay), kind))

    preset = _genre_preset(genre, sub_genre)
    record_origin(preset, "genre_preset")
    merged = _deep_merge(base, preset)
    if audience:
        merged["market"]["content_mode"] = audience
        origin_kinds["market.content_mode"] = "explicit"
    explicit_payload: dict[str, Any] | None = None
    if explicit_profile is not None:
        explicit_payload = (
            explicit_profile.model_dump(mode="json", exclude_unset=True)
            if isinstance(explicit_profile, WritingProfile)
            else dict(explicit_profile)
        )
        pack_key = explicit_payload.get("market", {}).get("prompt_pack_key")
    else:
        pack_key = None
    platform_name = (
        explicit_payload.get("market", {}).get("platform_target")
        if explicit_payload is not None
        else merged.get("market", {}).get("platform_target")
    )
    platform_preset = get_platform_preset(str(platform_name) if platform_name else None)
    if platform_preset is not None:
        merged = _deep_merge(merged, platform_preset.writing_profile_overrides)
        record_origin(platform_preset.writing_profile_overrides, "platform_preset")
    auto_prompt_pack_key = infer_default_prompt_pack_key(genre, sub_genre)
    preset_prompt_pack_key = (
        inferred_genre_preset.prompt_pack_key
        if inferred_genre_preset is not None
        else None
    )
    # Genre-derived route for the book's *own* genre. Mirrors the original
    # precedence (token inference first, curated preset as fallback) so this
    # guard never reorders the non-explicit resolution path.
    genre_route_key = auto_prompt_pack_key or preset_prompt_pack_key
    # Contamination guard (cross-genre prompt-pack leak):
    # ``pack_key`` is an explicit pack carried on the incoming profile — in the
    # conception flow it is produced by an LLM and can drift to whatever
    # methodology the framework was historically dominated by (e.g. a 都市修真
    # 2.0 / urban-cultivation comedy inheriting ``suspense-mystery`` from prior
    # detective projects). When that explicit pack contradicts the book's own
    # genre route, the genre is authoritative — the explicit pack only acts as a
    # fallback for genres with no recognised route. This keeps deliberate,
    # genre-consistent preset choices intact while refusing cross-genre leakage.
    # A persisted GenreIntentContract is stronger than model-produced profile
    # fields and heuristic token inference.  This is the final profile boundary:
    # once the user selected a taxonomy pack, no later LLM output may reroute it.
    effective_pack_key = forced_prompt_pack_key or pack_key or genre_route_key
    if forced_prompt_pack_key:
        genre_route_key = forced_prompt_pack_key
        pack_key = forced_prompt_pack_key
    if not forced_prompt_pack_key and genre_route_key and pack_key and pack_key != genre_route_key:
        # Only a CROSS-FAMILY explicit pack is contamination. Two packs in the
        # same review category (e.g. xianxia-upgrade-core vs xuanhuan-power-
        # fantasy — both action-progression) are sibling cultivation packs: the
        # explicit one (chosen at conception, baked into the planning artifacts)
        # is kept so planning and writing stay on the SAME methodology. Without
        # this, a 玄幻/诡异修仙 book whose sub_genre normalises to 玄幻 would warn
        # and flip the writer from the xianxia pack the outline used to the
        # xuanhuan sibling — a needless planning↔writing seam.
        from bestseller.services.genre_taxonomy import pack_category

        explicit_cat = pack_category(pack_key)
        route_cat = pack_category(genre_route_key)
        same_family = explicit_cat is not None and explicit_cat == route_cat
        # Review categories describe story motion, not world ontology.  The
        # urban-cultivation pack intentionally injects APP/职场/现代生活, while
        # xianxia/xuanhuan packs assume a genre-native cultivation world.  They
        # are therefore incompatible even though all three are reviewed as
        # ``action-progression``.  Treat crossing that boundary as contamination
        # so an LLM-supplied pack cannot silently modernise plain 仙侠.
        ontology_sensitive_packs = {"urban-cultivation-2.0"}
        if (
            pack_key in ontology_sensitive_packs
            or genre_route_key in ontology_sensitive_packs
        ) and pack_key != genre_route_key:
            same_family = False
        if not same_family:
            _logger.warning(
                "prompt_pack contamination guard: explicit pack %r contradicts genre "
                "route %r (genre=%r sub_genre=%r); using genre route.",
                pack_key,
                genre_route_key,
                genre,
                sub_genre,
            )
            effective_pack_key = genre_route_key
    prompt_pack = resolve_prompt_pack(
        effective_pack_key,
        genre=genre,
        sub_genre=sub_genre,
    )
    if prompt_pack is not None:
        technique_overrides = sanitize_genre_story_overrides(prompt_pack.writing_profile_overrides)
        merged = _deep_merge(merged, technique_overrides)
        record_origin(technique_overrides, "prompt_pack")
    if explicit_payload is not None:
        merged = _deep_merge(merged, explicit_payload)
        record_origin(explicit_payload, "explicit")
    profile = WritingProfile.model_validate(merged)
    if prompt_pack is not None:
        profile.market.prompt_pack_key = prompt_pack.key
    if not profile.style.tone_keywords:
        profile.style.tone_keywords = [genre] + ([sub_genre] if sub_genre else [])
    # Interactive fiction mode overrides — applied last so they always win
    if profile.interactive_fiction.enabled:
        profile.market.platform_target = "LifeScript"
        profile.market.content_mode = "Interactive Fiction" if is_english_language(resolved_language) else "交互式小说"
        profile.market.update_strategy = "Complete Release" if is_english_language(resolved_language) else "全本发布"
        profile.style.pov_type = "second"
    profile._content_origins = {
        path: origin_record(value, kind=origin_kinds.get(path, "derived"),
                            producer="resolve_writing_profile")
        for path, value in leaf_values(profile.model_dump(mode="json")).items()
    }
    if isinstance(explicit_profile, WritingProfile) and explicit_profile._content_origins:
        # Re-resolving a code-produced model is not a fresh author submission.
        for path, value in leaf_values(explicit_payload or {}).items():
            old_origin = explicit_profile._content_origins.get(path)
            current = profile._content_origins.get(path, {})
            profile._content_origins[path] = (
                dict(old_origin)
                if isinstance(old_origin, dict)
                and old_origin.get("content_hash") == current.get("content_hash")
                else origin_record(value, kind="unknown", producer="resolve_writing_profile")
            )
    return profile


def resolve_project_create_writing_profile(payload: ProjectCreate) -> WritingProfile:
    forced_pack: str | None = None
    metadata = payload.metadata if isinstance(payload.metadata, dict) else {}
    contract = metadata.get("genre_intent_contract")
    if isinstance(contract, dict):
        raw_pack = contract.get("prompt_pack_key")
        if isinstance(raw_pack, str) and raw_pack.strip():
            forced_pack = raw_pack.strip()
    profile = resolve_writing_profile(
        payload.writing_profile,
        genre=payload.genre,
        sub_genre=payload.sub_genre,
        audience=payload.audience,
        language=payload.language,
        forced_prompt_pack_key=forced_pack,
    )
    creation_contract = metadata.get("creation_intent_contract")
    if isinstance(creation_contract, dict):
        pov = str(creation_contract.get("pov") or "").strip()
        if pov:
            profile.style.pov_type = pov
        preference = str(
            creation_contract.get("tone_preference")
            or (
                contract.get("tone_preference")
                if isinstance(contract, dict)
                else ""
            )
            or ""
        ).strip().lower()
        lead = _CREATION_TONE_KEYWORDS.get(preference, ())
        if lead:
            profile.style.tone_keywords = _merge_lists(
                list(lead),
                list(profile.style.tone_keywords or ()),
            )
    return profile


#: A one-line hook is a one-line hook. Anything past this is a premise wearing
#: the logline's name tag (live 2026-08-09: 258 chars in this slot).
_LOGLINE_MAX_CHARS = 120


def _apply_logline_to_metadata(
    metadata: dict[str, Any], writing_profile: WritingProfile
) -> None:
    """Surface the derived logline at ``metadata["logline"]``.

    The T6 stage (``conception._derive_logline_from_champion``) distils a short,
    market-calibrated hook from the finalized blurb and writes it to
    ``writing_profile.market.logline``. That slot was added on 2026-07-09 for
    exactly this reason — the value used to be dropped by Pydantic — but the
    metadata flattener next to it copies ``platform_target / reader_promise /
    selling_points / trope_keywords / opening_strategy / chapter_hook_strategy /
    prompt_pack_key`` and never copied this one.

    So the distillation ran on every book and no consumer ever saw it: exports,
    ``commercial_novel_gate``, ``imagery_system_design``, ``narrative``,
    ``narrative_tree``, ``book_listing`` and the dashboard's 一句话钩子 all read
    ``metadata["logline"]``. Live 2026-08-09 《废脉炉子天天骂我》: the derived
    hook was 80 chars, ``metadata["logline"]`` was the 258-char premise verbatim.

    Precedence: the derived hook wins. An existing value is kept only when it is
    actually hook-shaped — a premise-length string, or a verbatim copy of the
    premise, is not a logline no matter who put it there.
    """

    derived = str(writing_profile.market.logline or "").strip()
    existing = str(metadata.get("logline") or "").strip()
    premise = str(metadata.get("premise") or "").strip()
    existing_is_hook_shaped = bool(
        existing and len(existing) <= _LOGLINE_MAX_CHARS and existing != premise
    )
    if derived and not existing_is_hook_shaped:
        metadata["logline"] = derived
        return
    if existing and not existing_is_hook_shaped and not derived:
        # No derived hook to promote, but a premise must not keep masquerading
        # as one — downstream readers treat this field as short copy.
        metadata.pop("logline", None)


def build_project_metadata(payload: ProjectCreate, writing_profile: WritingProfile) -> dict[str, Any]:
    from bestseller.services.brainhole_engine import (
        BRAINHOLE_PROFILE_METADATA_KEY,
        resolve_brainhole_profile,
    )
    from bestseller.services.genre_skill_profiles import (
        GENRE_SKILL_PROFILE_METADATA_KEY,
        resolve_genre_skill_profile,
    )
    from bestseller.services.prewrite_quality_profile import (
        apply_default_prewrite_quality_profile,
    )
    from bestseller.services.story_effect_skills import (
        STORY_EFFECT_SKILL_CATALOG_METADATA_KEY,
        resolve_story_effect_skill_catalog,
    )

    metadata = apply_default_prewrite_quality_profile(initialize_truth_metadata(payload.metadata))
    # Every newly created long-form project must pass conception before planning.
    # Existing persisted projects are untouched; explicit legacy imports retain
    # their compatibility path.
    if payload.target_chapters >= 200 and not metadata.get("legacy_import"):
        metadata["concept_contract_required"] = True
    metadata["writing_profile"] = writing_profile.model_dump(mode="json")
    # A reconstructed/legacy model has no trusted resolution receipt. Do not
    # turn all of its fields into purported user choices just by serialising it.
    metadata["writing_profile_origins"] = {
        path: (
            dict(writing_profile._content_origins[path])
            if path in writing_profile._content_origins
            and writing_profile._content_origins[path].get("content_hash")
                == origin_record(value, kind="unknown")["content_hash"]
            else origin_record(value, kind="unknown", producer="build_project_metadata")
        )
        for path, value in leaf_values(metadata["writing_profile"]).items()
    }
    metadata.setdefault("platform_target", writing_profile.market.platform_target)
    metadata.setdefault("reader_promise", writing_profile.market.reader_promise)
    _apply_logline_to_metadata(metadata, writing_profile)
    metadata.setdefault("selling_points", writing_profile.market.selling_points)
    metadata.setdefault("trope_keywords", writing_profile.market.trope_keywords)
    metadata.setdefault("opening_strategy", writing_profile.market.opening_strategy)
    metadata.setdefault("chapter_hook_strategy", writing_profile.market.chapter_hook_strategy)
    metadata.setdefault("prompt_pack_key", writing_profile.market.prompt_pack_key)
    metadata.setdefault(
        GENRE_SKILL_PROFILE_METADATA_KEY,
        resolve_genre_skill_profile(
            payload.genre,
            payload.sub_genre,
            prompt_pack_key=writing_profile.market.prompt_pack_key,
        ).to_metadata(),
    )
    metadata.setdefault(
        BRAINHOLE_PROFILE_METADATA_KEY,
        resolve_brainhole_profile(
            payload.genre,
            payload.sub_genre,
            prompt_pack_key=writing_profile.market.prompt_pack_key,
        ).to_metadata(),
    )
    # (2026-08-03) The 18-skill effect catalog is only baked in when the creator
    # actually ticked skills on the creation page. It used to be unconditional,
    # and it rendered into EVERY chapter-outline prompt at ~8,300 characters —
    # the single largest block, a quarter of the whole prompt, and a menu of 18
    # options for a book that had asked for none. Live evidence 2026-08-03:
    # 《雾街债主》 died with "required hard core exceeds combined writer prompt
    # budget" (15,336 required vs 14,400 usable) while carrying this catalog.
    from bestseller.services.story_enhancers import resolve_story_enhancers

    if resolve_story_enhancers(metadata).effect_skills:
        metadata.setdefault(
            STORY_EFFECT_SKILL_CATALOG_METADATA_KEY,
            resolve_story_effect_skill_catalog(
                payload.genre,
                payload.sub_genre,
                prompt_pack_key=writing_profile.market.prompt_pack_key,
            ).to_metadata(),
        )
    metadata.setdefault("golden_finger", writing_profile.character.golden_finger)
    metadata.setdefault("protagonist_archetype", writing_profile.character.protagonist_archetype)
    metadata.setdefault("growth_curve", writing_profile.character.growth_curve)
    if payload.publishing is not None:
        metadata["publishing"] = _deep_merge(
            metadata.get("publishing", {}) if isinstance(metadata.get("publishing"), dict) else {},
            payload.publishing.model_dump(mode="json", exclude_none=True),
        )
    return metadata


def migrate_writing_profile_defaults(
    raw: dict[str, Any],
    *,
    origins: dict[str, Any] | None = None,
    language: str | None = None,
) -> ContentDefaultMigration:
    """Plan old-default supersession; callers persist receipt + invalidate caches.

    No content/value matching: the source receipt and current hash must agree.
    Unresolved history stays intact and is surfaced in the migration receipt.
    """
    defaults = leaf_values(_default_writing_profile_payload(language))
    paths = {
        "character.growth_curve", "character.romance_mode", "character.relationship_tension",
        "character.antagonist_mode", "character.ensemble_mode", "market.opening_strategy",
        "market.chapter_hook_strategy", "market.payoff_rhythm",
        "world.info_reveal_strategy", "world.rule_hardness",
        *(path for path in defaults if path.startswith("serialization.")),
    }
    # Genre prose technique remains available. The retired universal commercial
    # style is only a source-default migration, never a genre-style deletion.
    style_origin = (origins or {}).get("style.prose_style")
    if not isinstance(style_origin, dict) or style_origin.get("kind") == "framework_default":
        paths.add("style.prose_style")
    return migrate_content_defaults(
        raw, origins=origins, replacements={path: defaults[path] for path in paths},
    )


def get_project_writing_profile(
    project: ProjectModel,
    style_guide: StyleGuideModel | None = None,
) -> WritingProfile:
    from bestseller.services.explicit_author_pov import apply_explicit_author_pov

    metadata = getattr(project, "metadata_json", {}) or {}
    intent = metadata.get("genre_intent_contract") or {}
    forced_pack = intent.get("prompt_pack_key") if isinstance(intent, dict) else None
    raw = metadata.get("writing_profile")
    if isinstance(raw, dict):
        migration = migrate_writing_profile_defaults(
            raw, origins=project.metadata_json.get("writing_profile_origins"),
            language=getattr(project, "language", None),
        )
        # Unknown legacy optional craft preferences are preserved in storage,
        # but cannot silently become current author instructions on a read.
        effective_payload = copy.deepcopy(migration.payload)
        neutral = leaf_values(_default_writing_profile_payload(getattr(project, "language", None)))
        for path in migration.receipt["unresolved_paths"]:
            parent = effective_payload
            parts = path.split(".")
            for part in parts[:-1]:
                parent = parent[part]
            parent[parts[-1]] = neutral[path]
        profile = resolve_writing_profile(
            effective_payload,
            genre=getattr(project, "genre", None),
            sub_genre=getattr(project, "sub_genre", None),
            audience=getattr(project, "audience", None),
            language=getattr(project, "language", None),
            forced_prompt_pack_key=forced_pack,
        )
        # A read is not a new author choice. Keep historical unknowns unknown
        # even if this model is later passed back through metadata construction.
        for path, value in leaf_values(migration.payload).items():
            evidence = migration.origins.get(path)
            profile._content_origins[path] = (
                dict(evidence)
                if isinstance(evidence, dict)
                and evidence.get("content_hash") == origin_record(value, kind="unknown")["content_hash"]
                else origin_record(value, kind="unknown", producer="get_project_writing_profile")
            )
        return apply_explicit_author_pov(project, profile)
    fallback = {}
    if style_guide is not None:
        fallback = {
            "style": {
                "pov_type": getattr(style_guide, 'pov_type', 'third-limited'),
                "tense": getattr(style_guide, 'tense', 'present'),
                "tone_keywords": list(getattr(style_guide, 'tone_keywords', [])),
                "prose_style": getattr(style_guide, 'prose_style', 'commercial-web-serial'),
                "sentence_style": getattr(style_guide, 'sentence_style', 'mixed'),
                "info_density": getattr(style_guide, 'info_density', 'medium'),
                "dialogue_ratio": float(getattr(style_guide, 'dialogue_ratio', 0.4)),
                "taboo_topics": list(getattr(style_guide, 'taboo_topics', [])),
                "taboo_words": list(getattr(style_guide, 'taboo_words', [])),
                "reference_works": list(getattr(style_guide, 'reference_works', [])),
                "custom_rules": list(getattr(style_guide, 'custom_rules', [])),
            }
        }
    profile = resolve_writing_profile(
        fallback,
        genre=getattr(project, "genre", None),
        sub_genre=getattr(project, "sub_genre", None),
        audience=getattr(project, "audience", None),
        language=getattr(project, "language", None),
        forced_prompt_pack_key=forced_pack,
    )
    return apply_explicit_author_pov(project, profile)


def get_project_runtime_writing_profile(project, style_guide=None) -> WritingProfile:
    """Keep prose preferences; do not reuse unbound old plot instructions.

    Boundary-supplied ``explicit`` is not author adoption. Once a foundation
    cohort is selected, its facts/task channels replace unbound profile plot
    caches. Storage, routing identity and genuinely adopted choices survive.
    """
    profile = get_project_writing_profile(project, style_guide)
    metadata = getattr(project, "metadata_json", {}) or {}
    foundation = metadata.get("active_foundation_selection")
    if not isinstance(foundation, dict):
        return profile
    defaults = leaf_values(_default_writing_profile_payload(getattr(project, "language", None)))
    paths = {
        "character.protagonist_core_drive", "character.golden_finger",
        "character.growth_curve", "character.romance_mode", "character.relationship_tension",
        "character.antagonist_mode", "character.ensemble_mode",
        "world.power_system_style", "world.setting_tags",
        "market.opening_contract", "market.opening_strategy", "market.chapter_hook_strategy",
        "market.reader_promise", "market.selling_points", "market.payoff_rhythm",
        *(path for path in defaults if path.startswith("serialization.")),
    }
    values = leaf_values(profile.model_dump(mode="json"))
    retired = []
    for path in sorted(paths):
        if path not in values or path not in defaults:
            continue
        value = values[path]
        origin = profile._content_origins.get(path, {})
        if origin.get("adopted") is True and origin.get("content_hash") == origin_record(value, kind="unknown")["content_hash"]:
            continue
        target = profile
        parts = path.split(".")
        for part in parts[:-1]:
            target = getattr(target, part)
        setattr(target, parts[-1], copy.deepcopy(defaults[path]))
        if value != defaults[path]:
            retired.append({"path": path, "stored_value_sha256": origin_record(value, kind="unknown")["content_hash"],
                            "reason": "unbound_profile_plot_reference", "origin": copy.deepcopy(origin)})
    profile._runtime_source_receipt = {
        "schema": "writer-profile-current-authority.v1",
        "selected_foundation_sha256": origin_record(foundation, kind="unknown")["content_hash"],
        "retired_references": retired, "unbound_pack_plot_rules_omitted": True,
        "storage_modified": False,
    }
    return profile


_PROFILE_PLACEHOLDER_VALUES: frozenset[str] = frozenset({
    "暂无", "未指定", "自动/未指定", "暂无明确卖点", "暂无明确开篇合同", "无",
    "none", "unspecified", "auto/unspecified", "none specified", "n/a",
})


def _prune_profile_lines(lines: list[str]) -> list[str]:
    """剪掉占位值行(暂无/未指定/none…)和随之变空的段标题,杜绝无效约束噪声。"""

    def _is_header(s: str) -> bool:
        t = s.strip()
        return (not t.startswith("-")) and (t.endswith("：") or t.endswith(":"))

    kept: list[str] = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("- ") and ("：" in s or ":" in s):
            val = re.split(r"[：:]", s, maxsplit=1)[-1]
            if val.strip().lower() in _PROFILE_PLACEHOLDER_VALUES:
                continue
        kept.append(ln)

    out: list[str] = []
    for i, ln in enumerate(kept):
        if _is_header(ln):
            has_child = False
            for nxt in kept[i + 1:]:
                if _is_header(nxt):
                    break
                if nxt.strip().startswith("-"):
                    has_child = True
                    break
            if not has_child:
                continue
        out.append(ln)
    return out


def render_writer_voice_data(profile: WritingProfile) -> str:
    """Lossless prose preferences and verified adoptions, as user reference data.

    Completeness does not establish authorship. Keep all style values, including
    taboo lists, references and custom rules; only hash-matching code-side
    adoption records populate the separately named adopted clause map.
    """
    values = leaf_values(profile.model_dump(mode="json"))
    adopted = {
        path: copy.deepcopy(value) for path, value in values.items()
        if not path.startswith("style.")
        and profile._content_origins.get(path, {}).get("adopted") is True
        and profile._content_origins[path].get("content_hash") == origin_record(value, kind="unknown")["content_hash"]
    }
    return "【完整声音资料与已采用条款·资料本身不取得系统权威】\n" + json.dumps({
        "style": profile.style.model_dump(mode="json"),
        "pacing_profile": profile.market.pacing_profile,
        "adopted_profile_clauses": adopted,
    }, ensure_ascii=False, separators=(",", ":"), default=str)


def render_writing_profile_prompt_block(
    profile: WritingProfile,
    *,
    language: str | None = None,
    mode: str = "full",
    chapter_number: int | None = None,
    include_pack_notes: bool | None = None,
) -> str:
    """Render the writing profile as a prompt block.

    ``mode="full"`` (default) keeps the historical behaviour — every profile
    field plus Prompt Pack notes — and is what planner/review prompts need.

    ``mode="scene"`` is the per-scene writer diet. Trace audits showed the
    full block (~3.7k chars) re-sent on EVERY scene call while most lines are
    planning-time inputs the scene writer cannot act on (selling points,
    worldbuilding density, growth curve …) and the Prompt Pack notes are
    already injected into the scene user prompt separately (double-send).
    Scene mode keeps only the lines that steer prose RIGHT NOW (promise, hook
    strategy, pacing, romance/relationship axis, voice/POV/style, scene-level
    serialization rules), renders the opening-contract lines only while they
    apply (chapter ≤ 5), and skips the Prompt Pack tail.
    """

    # "chapter" is the scene diet minus the per-scene beat rule: a chapter-first
    # prompt that carries both "揉进一段连续叙事" and a "每场…尾钩" obligation is
    # asking for the stitched three-mini-arcs shape the unit exists to remove.
    # The obligation is not dropped — render_serial_fiction_guardrails(scope=
    # "chapter") restates it against the whole chapter.
    if profile._runtime_source_receipt.get("unbound_pack_plot_rules_omitted"):
        include_pack_notes = False
    chapter_mode = mode == "chapter"
    scene_mode = mode == "scene" or chapter_mode
    opening_phase = chapter_number is None or chapter_number <= 5
    # Pack notes default: full mode keeps them (planner/review need the pack
    # design); scene mode drops them because the scene user prompt injects the
    # same pack block separately (double-send). Callers whose user prompt does
    # NOT carry the pack (e.g. chapter-first draft) opt back in explicitly.
    if include_pack_notes is None:
        include_pack_notes = not scene_mode
    prompt_pack = resolve_prompt_pack(
        profile.market.prompt_pack_key,
        genre=" ".join(profile.style.tone_keywords) or "通用",
        sub_genre=None,
    )
    if is_english_language(language):
        lines = [
            "Platform and Reader Promise:",
            f"- Platform target: {profile.market.platform_target}",
        ]
        if not scene_mode:
            lines += [
                f"- Prompt pack: {profile.market.prompt_pack_key or 'auto/unspecified'}",
                f"- Content mode: {profile.market.content_mode}",
            ]
        lines += [
            f"- Reader promise: {profile.market.reader_promise or 'unspecified'}",
        ]
        # Opening chapters must deliver the selling points (golden-three
        # contract), so they survive scene-mode trimming there.
        if not scene_mode or opening_phase:
            lines += [
                f"- Selling points: {', '.join(profile.market.selling_points) or 'none specified'}",
                f"- Trope tags: {', '.join(profile.market.trope_keywords) or 'none'}",
            ]
        lines += [
            f"- Hook tags: {', '.join(profile.market.hook_keywords) or 'none'}",
        ]
        if not scene_mode or opening_phase:
            lines += [
                f"- Opening contract: {profile.market.opening_contract or 'none specified'}",
                f"- Opening strategy: {profile.market.opening_strategy}",
            ]
        lines += [
            f"- Chapter hook strategy: {profile.market.chapter_hook_strategy}",
            f"- Pace: {profile.market.pacing_profile} / Payoff rhythm: {profile.market.payoff_rhythm}",
            "Character and Story Engine:",
        ]
        if not scene_mode:
            lines += [
                f"- Protagonist archetype: {profile.character.protagonist_archetype or 'unspecified'}",
            ]
        lines += [
            f"- Protagonist core drive: {profile.character.protagonist_core_drive or 'unspecified'}",
            f"- Unique edge: {profile.character.golden_finger or 'unspecified'}",
        ]
        if not scene_mode:
            lines += [
                f"- Growth curve: {profile.character.growth_curve}",
            ]
        lines += [
            f"- Romance mode: {profile.character.romance_mode}",
            f"- Relationship tension: {profile.character.relationship_tension}",
        ]
        if not scene_mode:
            lines += [
                f"- Antagonist mode: {profile.character.antagonist_mode}",
                "World and Information Release:",
                f"- Worldbuilding density: {profile.world.worldbuilding_density}",
                f"- Reveal strategy: {profile.world.info_reveal_strategy}",
                f"- Rule hardness: {profile.world.rule_hardness}",
                f"- Power system: {profile.world.power_system_style or 'unspecified'}",
                f"- Mystery density: {profile.world.mystery_density}",
                f"- Setting tags: {', '.join(profile.world.setting_tags) or 'none'}",
            ]
        lines += [
            "Style and Serialization Rules:",
            f"- POV: {profile.style.pov_type} / Tense: {profile.style.tense}",
            f"- Tone keywords: {', '.join(profile.style.tone_keywords) or 'unspecified'}",
            f"- Prose style: {profile.style.prose_style}",
            f"- Sentence style: {profile.style.sentence_style} / Info density: {profile.style.info_density} / Dialogue ratio: {profile.style.dialogue_ratio:.2f}",
        ]
        if not scene_mode:
            lines += [
                f"- Reference works: {', '.join(profile.style.reference_works) or 'none'}",
            ]
        lines += [
            f"- Extra rules: {'; '.join(profile.style.custom_rules) or 'none'}",
            "Serialization Guardrails:",
        ]
        if not scene_mode or opening_phase:
            lines += [
                f"- {profile.serialization.opening_mandate}",
                f"- {profile.serialization.first_three_chapter_goal}",
            ]
        if not chapter_mode:
            lines.append(f"- {profile.serialization.scene_drive_rule}")
        lines += [
            f"- {profile.serialization.exposition_rule}",
            f"- {profile.serialization.chapter_ending_rule}",
        ]
        if not scene_mode or opening_phase:
            lines += [
                f"- {profile.serialization.free_chapter_strategy}",
            ]
        lines = _prune_profile_lines(lines)
        if include_pack_notes:
            pack_block = render_prompt_pack_prompt_block(prompt_pack)
            if pack_block:
                lines.extend(["Prompt Pack Notes:", pack_block])
        return "\n".join(lines)
    lines = [
        "平台与读者承诺：",
        f"- 平台目标：{profile.market.platform_target}",
    ]
    if not scene_mode:
        lines += [
            f"- Prompt Pack：{profile.market.prompt_pack_key or '自动/未指定'}",
            f"- 内容模式：{profile.market.content_mode}",
        ]
    lines += [
        f"- 读者承诺：{profile.market.reader_promise or '未指定'}",
    ]
    # 开篇章节（黄金三章窗口）必须兑现核心卖点，scene 模式下保留。
    # 卖点逐条一行：顿号拼接会把 5-7 条卖点糊成 500 字长句（真机 prompt review
    # 2026-08-07），近重复条目先折叠（部分重叠的treat为不同条，交给上游约束）。
    if not scene_mode or opening_phase:
        _points = fold_near_duplicate_points(profile.market.selling_points)
        if _points:
            lines.append("- 核心卖点：")
            lines.extend(f"  · {p}" for p in _points[:6])
        else:
            lines.append("- 核心卖点：暂无明确卖点")
        lines.append(f"- 套路标签：{'、'.join(profile.market.trope_keywords) or '暂无'}")
    lines += [
        f"- 钩子标签：{'、'.join(profile.market.hook_keywords) or '暂无'}",
    ]
    if not scene_mode or opening_phase:
        lines += [
            f"- 开篇合同：{profile.market.opening_contract or '暂无明确开篇合同'}",
            f"- 开篇策略：{profile.market.opening_strategy}",
        ]
    lines += [
        f"- 章节钩子策略：{profile.market.chapter_hook_strategy}",
        f"- 节奏：{profile.market.pacing_profile} / 回报节奏：{profile.market.payoff_rhythm}",
        "人物与故事引擎：",
    ]
    if not scene_mode:
        lines += [
            f"- 主角原型：{profile.character.protagonist_archetype or '未指定'}",
        ]
    lines += [
        f"- 主角核心驱动力：{profile.character.protagonist_core_drive or '未指定'}",
        f"- 外挂/差异化优势：{profile.character.golden_finger or '未指定'}",
    ]
    if not scene_mode:
        lines += [
            f"- 成长曲线：{profile.character.growth_curve}",
        ]
    lines += [
        f"- 感情线模式：{profile.character.romance_mode}",
        f"- 关系张力：{profile.character.relationship_tension}",
    ]
    if not scene_mode:
        lines += [
            f"- 反派机制：{profile.character.antagonist_mode}",
            "世界与信息释放：",
            f"- 世界观密度：{profile.world.worldbuilding_density}",
            f"- 设定揭示方式：{profile.world.info_reveal_strategy}",
            f"- 规则硬度：{profile.world.rule_hardness}",
            f"- 力量体系：{profile.world.power_system_style or '未指定'}",
            f"- 悬念密度：{profile.world.mystery_density}",
            f"- 设定标签：{'、'.join(profile.world.setting_tags) or '暂无'}",
        ]
    lines += [
        "文风与连载规则：",
        f"- 视角：{profile.style.pov_type} / 时态：{profile.style.tense}",
        f"- 语气关键词：{'、'.join(profile.style.tone_keywords) or '未指定'}",
        f"- prose style：{profile.style.prose_style}",
        f"- 句式：{profile.style.sentence_style} / 信息密度：{profile.style.info_density} / 对话占比：{profile.style.dialogue_ratio:.2f}",
    ]
    if not scene_mode:
        lines += [
            f"- 参考作品：{'、'.join(profile.style.reference_works) or '暂无'}",
        ]
    lines += [
        f"- 额外规则：{'；'.join(profile.style.custom_rules) or '暂无'}",
        "连载硬约束：",
    ]
    if not scene_mode or opening_phase:
        lines += [
            f"- {profile.serialization.opening_mandate}",
            f"- {profile.serialization.first_three_chapter_goal}",
        ]
    if not chapter_mode:
        lines.append(f"- {profile.serialization.scene_drive_rule}")
    lines += [
        f"- {profile.serialization.exposition_rule}",
        f"- {profile.serialization.chapter_ending_rule}",
    ]
    if not scene_mode or opening_phase:
        lines += [
            f"- {profile.serialization.free_chapter_strategy}",
        ]
    lines = _prune_profile_lines(lines)
    if include_pack_notes:
        pack_block = render_prompt_pack_prompt_block(prompt_pack)
        if pack_block:
            lines.extend(["Prompt Pack 设计：", pack_block])
    return "\n".join(lines)


def render_serial_fiction_guardrails(
    profile: WritingProfile,
    *,
    language: str | None = None,
    scope: str = "scene",
) -> str:
    """Render project-selected rules without inventing a commercial contract.

    The profile renderer already exposes these to writer/reviewer alike. This
    shared guardrail repeats only actual rules plus continuity, never a default
    danger, hook quota, or mandatory scene beat sequence.
    """
    english = is_english_language(language)
    lines = [
        "Follow the current creative contract and adopted continuity."
        if english else "遵循当前创作合同与已采用事实，保持叙事连续性。"
    ]
    for value in (
        profile.market.opening_contract, profile.market.opening_strategy,
        profile.market.chapter_hook_strategy, profile.market.reader_promise,
        profile.serialization.opening_mandate,
        profile.serialization.first_three_chapter_goal,
        profile.serialization.scene_drive_rule if scope != "chapter" else None,
        profile.serialization.exposition_rule,
        profile.serialization.chapter_ending_rule,
        profile.serialization.free_chapter_strategy,
    ):
        if value and value.strip().lower() not in _PROFILE_PLACEHOLDER_VALUES:
            if value not in lines:
                lines.append(value)
    prompt_pack = resolve_prompt_pack(
        profile.market.prompt_pack_key,
        genre=" ".join(profile.style.tone_keywords) or "general",
        sub_genre=None,
    )
    pack_rules = render_prompt_pack_fragment(prompt_pack, "global_rules")
    if pack_rules and not profile._runtime_source_receipt.get("unbound_pack_plot_rules_omitted"):
        lines.append(("Genre technique guidance: " if english else "题材技法建议：") + pack_rules)
    return "\n".join(lines)
