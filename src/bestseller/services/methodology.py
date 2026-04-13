"""Writing Methodology Rule Engine.

Loads the writing_methodology.yaml rules and provides:
- Emotion pressure tracking and validation
- Conflict stakes assessment
- Hook lifecycle management rules
- Core loop cycle detection
- Visual writing / dialogue rule injection
- Climax blueprint generation
- Opening system constraints
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _methodology_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "writing_methodology.yaml"


@lru_cache(maxsize=1)
def load_methodology() -> dict[str, Any]:
    """Load the writing methodology rules from YAML config."""
    path = _methodology_config_path()
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Emotion Engineering
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmotionPressureState:
    """Tracks emotion compression state for a storyline."""

    compression_start_chapter: int
    current_chapter: int
    pressure_level: float  # 0.0 to 1.0
    ready_to_release: bool
    cycles_completed: int = 0


def assess_emotion_pressure(
    *,
    compression_start_chapter: int,
    current_chapter: int,
    max_compression: int = 5,
    min_compression: int = 2,
) -> EmotionPressureState:
    """Assess whether emotion pressure is ready for release."""
    duration = current_chapter - compression_start_chapter
    pressure_level = min(1.0, duration / max_compression)
    ready = duration >= min_compression
    return EmotionPressureState(
        compression_start_chapter=compression_start_chapter,
        current_chapter=current_chapter,
        pressure_level=pressure_level,
        ready_to_release=ready,
    )


# ---------------------------------------------------------------------------
# Conflict Stakes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConflictAssessment:
    """Assessment of a scene/chapter conflict's stakes."""

    has_stakes: bool
    stakes_description: str
    buff_count: int
    buffs: tuple[str, ...]
    assessment: str  # "weak" | "adequate" | "strong" | "climax_ready"


def assess_conflict_stakes(
    *,
    stakes: str | None,
    buffs: list[str] | None = None,
    is_climax: bool = False,
) -> ConflictAssessment:
    """Assess whether a conflict has adequate stakes and pressure buffs."""
    buff_list = buffs or []
    has_stakes = bool(stakes and stakes.strip())
    buff_count = len(buff_list)

    if is_climax:
        if buff_count >= 2 and has_stakes:
            assessment = "climax_ready"
        elif has_stakes:
            assessment = "adequate"
        else:
            assessment = "weak"
    else:
        if buff_count >= 1 and has_stakes:
            assessment = "strong"
        elif has_stakes:
            assessment = "adequate"
        else:
            assessment = "weak"

    return ConflictAssessment(
        has_stakes=has_stakes,
        stakes_description=stakes or "",
        buff_count=buff_count,
        buffs=tuple(buff_list),
        assessment=assessment,
    )


# ---------------------------------------------------------------------------
# Hook Lifecycle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookState:
    """State of a single narrative hook."""

    hook_id: str
    hook_type: str  # information_gap | deadline | mystery | desire | threat
    planted_chapter: int
    description: str
    resolved: bool = False
    resolved_chapter: int | None = None


@dataclass
class HookLedger:
    """Manages the lifecycle of all active hooks."""

    hooks: list[HookState] = field(default_factory=list)

    @property
    def active_hooks(self) -> list[HookState]:
        return [h for h in self.hooks if not h.resolved]

    @property
    def active_count(self) -> int:
        return len(self.active_hooks)

    def validate_chapter(
        self,
        chapter_number: int,
        *,
        hooks_planted: int = 0,
        hooks_resolved: int = 0,
        language: str | None = None,
    ) -> list[str]:
        """Validate hook lifecycle rules for a chapter. Returns warnings."""
        warnings: list[str] = []
        active = self.active_count
        _is_en = (language or "").lower().startswith("en")

        if hooks_planted == 0:
            warnings.append(
                f"Chapter {chapter_number}: no new hooks planted. Rule: at least 1 new hook per chapter."
                if _is_en else
                f"第{chapter_number}章未植入新钩子。规则：每章≥1个新钩子。"
            )
        if hooks_resolved == 0 and active > 0:
            warnings.append(
                f"Chapter {chapter_number}: no old hooks resolved. Rule: at least 1 resolution per chapter."
                if _is_en else
                f"第{chapter_number}章未消解任何旧钩子。规则：每章≥1个消解。"
            )
        if active > 7:
            warnings.append(
                f"Active hooks = {active}, exceeding the limit of 7. Readers may lose track."
                if _is_en else
                f"活跃钩子数={active}，超过上限7。读者可能记不住。"
            )
        if active < 3 and chapter_number > 3:
            warnings.append(
                f"Active hooks = {active}, below the minimum of 3. Suspense is insufficient."
                if _is_en else
                f"活跃钩子数={active}，低于下限3。悬念不足。"
            )

        # Check for stale hooks (> 15 chapters)
        for h in self.active_hooks:
            age = chapter_number - h.planted_chapter
            if age > 15:
                warnings.append(
                    f"Hook \"{h.description}\" has gone {age} chapters unresolved, exceeding the 15-chapter limit. Readers may have forgotten it."
                    if _is_en else
                    f"钩子「{h.description}」已{age}章未消解，超过15章上限，可能已被读者遗忘。"
                )

        return warnings


# ---------------------------------------------------------------------------
# Core Loop Tracking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoopCycleStatus:
    """Status of the core satisfaction loop."""

    cycles_completed: int
    chapters_since_last_cycle: int
    needs_breathing_room: bool  # After 3-4 cycles, insert a rest chapter
    cycle_fatigue_warning: bool


def assess_loop_status(
    *,
    cycles_completed: int,
    chapters_since_last_cycle: int,
    cycle_length: int = 3,
) -> LoopCycleStatus:
    """Assess whether the core loop needs attention."""
    needs_rest = cycles_completed > 0 and cycles_completed % 4 == 0
    fatigue = chapters_since_last_cycle > cycle_length + 2
    return LoopCycleStatus(
        cycles_completed=cycles_completed,
        chapters_since_last_cycle=chapters_since_last_cycle,
        needs_breathing_room=needs_rest,
        cycle_fatigue_warning=fatigue,
    )


# ---------------------------------------------------------------------------
# Opening System Constraints
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpeningConstraints:
    """Constraints for the first N chapters."""

    protagonist_appears_in: str  # "first_paragraph"
    core_conflict_by_words: int  # e.g., 500
    world_building_mode: str  # "embedded_in_action"
    hook_intensity: str  # "maximum"
    golden_finger_reveal_by_chapter: int  # 3


def get_opening_constraints() -> OpeningConstraints:
    """Return standard opening system constraints."""
    return OpeningConstraints(
        protagonist_appears_in="first_paragraph",
        core_conflict_by_words=500,
        world_building_mode="embedded_in_action",
        hook_intensity="maximum",
        golden_finger_reveal_by_chapter=3,
    )


# ---------------------------------------------------------------------------
# Climax Blueprint
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClimaxBlueprint:
    """Blueprint for designing a climax sequence."""

    converging_arcs: tuple[str, ...]
    pressure_buffs: tuple[str, ...]
    reversal_fuse: str
    reaction_characters: tuple[str, ...]
    aftermath_tone: str


def build_climax_blueprint(
    *,
    converging_arcs: list[str],
    pressure_buffs: list[str] | None = None,
    reversal_fuse: str = "",
    reaction_characters: list[str] | None = None,
    aftermath_tone: str = "地位反转 + 温情余韵",
) -> ClimaxBlueprint:
    """Build a climax blueprint for a volume/arc climax."""
    return ClimaxBlueprint(
        converging_arcs=tuple(converging_arcs),
        pressure_buffs=tuple(pressure_buffs or ["时间限制", "自身限制"]),
        reversal_fuse=reversal_fuse,
        reaction_characters=tuple(reaction_characters or []),
        aftermath_tone=aftermath_tone,
    )


# ---------------------------------------------------------------------------
# Writer's Block Breaker
# ---------------------------------------------------------------------------


BLOCK_BREAKER_STRATEGIES = (
    "variable_invasion",
    "extreme_pressure",
    "endpoint_reverse",
    "foreshadow_recall",
)


def suggest_block_breaker(
    *,
    unresolved_clue_count: int = 0,
    chapters_since_last_climax: int = 0,
) -> str:
    """Suggest a block-breaking strategy based on current state."""
    if unresolved_clue_count > 3:
        return "foreshadow_recall"
    if chapters_since_last_climax > 8:
        return "extreme_pressure"
    return "variable_invasion"


# ---------------------------------------------------------------------------
# Methodology Prompt Rendering
# ---------------------------------------------------------------------------


def render_methodology_scene_rules(
    *,
    chapter_number: int,
    is_opening: bool = False,
    is_climax: bool = False,
    pacing_mode: str = "build",
) -> str:
    """Render methodology rules applicable to the current scene context.

    Returns a prompt-ready string to inject into scene writer instructions.
    """
    rules: list[str] = []

    # Always-on visual writing rules
    rules.append(
        "【画面感规则】\n"
        "- 展示不讲述：用动作代替形容词，用后果代替本体描写\n"
        '- 情绪隐藏：不写\u201c愤怒/伤心\u201d等情绪词，通过动作和环境传达\n'
        "- 环境交互：人物必须与环境产生物理连接，不做背景板\n"
        "- 五感叠加：每个重要场景至少调用2个感官通道\n"
        "- 一笔多用：单段文字同时承载环境+人设+伏笔+情感"
    )

    # Always-on dialogue rules
    rules.append(
        "【对话规则】\n"
        "- 身份化语言：每个角色说话带有独特的身份烙印\n"
        "- 潜台词：表面表达≠内心意图，张力来自反差\n"
        "- 打破问答：不要有问必答，用打岔/反问/情绪切断\n"
        "- 动作卡位：对话间穿插微动作细节反映真实内心"
    )

    # Opening-specific rules
    if is_opening or chapter_number <= 3:
        rules.append(
            "【开篇规则（黄金三章）】\n"
            "- 主角第一时间出场，聚光灯法则\n"
            "- 第一章必须制造冲突，不允许纯设定铺陈\n"
            "- 前500字必须包含：人物+困境+行动\n"
            "- 金手指/核心卖点在前3章清晰展示\n"
            "- 不允许大段环境描写和背景介绍"
        )

    # Climax-specific rules
    if is_climax:
        rules.append(
            "【高潮规则】\n"
            "- 多线交汇：让3+条线索在同一时刻汇合\n"
            "- 极限压抑：封死所有正常出口\n"
            "- BUFF叠加：时间限制+自身限制+社会压力\n"
            "- 信息差反转：用前文伏笔而非天降神兵\n"
            "- 视角切换：主角出招后立即写旁观者反应\n"
            "- 情绪收运：高潮后展示地位变化+温情余韵"
        )

    # Pacing-specific rules
    if pacing_mode == "build":
        rules.append(
            "【节奏：蓄力期】\n"
            "- 本场景处于情绪压缩阶段，增加信息密度\n"
            "- 多植入伏笔、发展关系、铺设世界观\n"
            "- 制造不公/压迫/对比，为后续释放蓄能"
        )
    elif pacing_mode == "accelerate":
        rules.append(
            "【节奏：加速期】\n"
            "- 减少描写，加快对话和动作节奏\n"
            "- 钩子密度加大，每段结尾留悬念\n"
            "- 筹码升级，极限施压开始"
        )
    elif pacing_mode == "breathe":
        rules.append(
            "【节奏：呼吸口】\n"
            "- 紧张节奏后的喘息口，允许日常/温馨/搞笑\n"
            "- 但不是废话：利用此机会植入伏笔和感情线\n"
            "- 保持1个低强度钩子维持续读欲"
        )

    # Reaction amplification (always relevant)
    rules.append(
        "【反应放大法】\n"
        "- 主角的高光时刻后必须切换视角写旁观者反应\n"
        "- 反应层次：震惊→不可置信→后悔→崇拜/恐惧\n"
        "- 至少2个角色提供反应视角"
    )

    return "\n\n".join(rules)
