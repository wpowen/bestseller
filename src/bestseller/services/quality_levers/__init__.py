"""Quality Levers · Loader services for the post-2026-05 framework upgrade.

Each module here loads one YAML data contract from ``config/`` and exposes:

* a frozen dataclass model
* a ``load_<name>()`` cached reader returning the typed model
* a ``render_<name>_block()`` returning a prompt-ready fragment

The package follows the pattern established in
:mod:`bestseller.services.methodology` for the legacy
``qimao_signing_gate`` config so that downstream services
(``drafts.py``, ``reviews.py``, ``planner.py``) can compose the
rendered fragments alongside existing methodology blocks.
"""

from __future__ import annotations

from bestseller.services.quality_levers.chapter_position_profiles import (
    ChapterPositionProfile,
    ChapterPositionProfilesConfig,
    SensitiveWindowAntiPatterns,
    detect_chapter_positions,
    load_chapter_position_profiles,
    render_chapter_position_block,
)
from bestseller.services.quality_levers.chapter_signature_audit import (
    ChapterSignatureConfig,
    SignatureType,
    load_chapter_signature_audit,
    render_chapter_signature_block,
)
from bestseller.services.quality_levers.character_engine import (
    CharacterEngineConfig,
    CharacterProfile,
    collect_forbidden_words,
    collect_forbidden_words_from_profiles,
    collect_signature_words,
    collect_signature_words_from_profiles,
    get_character_profile,
    load_character_engine,
    render_character_engine_profile_block,
    render_character_profile_block,
    synthesize_character_engine_profile,
)
from bestseller.services.quality_levers.critic_personas import (
    AggregatedCritique,
    AggregationPolicy,
    ConsensusIssue,
    CriticPersona,
    CriticPersonasConfig,
    PersonaIssue,
    PersonaResult,
    aggregate_persona_results,
    get_persona,
    load_critic_personas,
    render_all_persona_briefs,
    render_persona_system_prompt,
)
from bestseller.services.quality_levers.detectors import (
    AbstractSensoryResult,
    BannedPatternsResult,
    DumpingResult,
    ForbiddenVoiceResult,
    PulseDensityResult,
    QuantitativeChapterAudit,
    SensoryCoverageResult,
    SignatureDensityResult,
    WordCountGateResult,
    audit_chapter,
    compute_pulse_density,
    compute_sensory_coverage,
    count_cjk_chars,
    detect_psychological_dumping,
    evaluate_word_count,
    measure_signature_density,
    scan_abstract_sensory_terms,
    scan_banned_patterns,
    scan_forbidden_voice_words,
)
from bestseller.services.quality_levers.emotion_choreography import (
    EmotionChoreographyConfig,
    EmotionLabelAuditResult,
    ExpressionLayer,
    audit_emotion_labels,
    load_emotion_choreography,
    render_emotion_choreography_block,
)
from bestseller.services.quality_levers.information_choreography import (
    HardIndicator,
    InformationChoreographyConfig,
    InformationFlowState,
    InformationFlowVerdict,
    InformationMode,
    ReaderBelief,
    evaluate_information_state,
    load_information_choreography,
    render_information_choreography_block,
)
from bestseller.services.quality_levers.integrator import (
    CriticLeverContext,
    WriterLeverContext,
    build_critic_quality_levers_block,
    build_writer_quality_levers_block,
)
from bestseller.services.quality_levers.multi_persona_executor import (
    MultiPersonaExecution,
    PersonaInvocation,
    PersonaRunner,
    decode_runner_result,
    run_multi_persona_critique,
)
from bestseller.services.quality_levers.dashboard_runner import (
    DashboardRunResult,
    DashboardSink,
    FilesystemDashboardSink,
    build_dashboard_for_chapters,
    should_run_dashboard,
)
from bestseller.services.quality_levers.multi_persona_runner import (
    MultiPersonaCallContext,
    run_async_multi_persona_critique,
)
from bestseller.services.quality_levers.platform_profiles import (
    OpeningHook,
    OpeningSigningGate,
    PacingPreference,
    PlatformProfile,
    PlatformProfilesConfig,
    PulseWords,
    VoicePreference,
    load_platform_profiles,
    parse_rejection_reason,
    render_platform_profile_block,
    resolve_platform_id,
)
from bestseller.services.quality_levers.project_meta import (
    QualityLeversProjectMeta,
    RejectionHistoryEntry,
    extract_quality_levers_meta,
)
from bestseller.services.quality_levers.prose_style_anchors import (
    BannedPattern,
    ProseStyleAnchorsConfig,
    StyleAnchor,
    get_anti_ai_banned_patterns,
    get_style_anchor,
    load_prose_style_anchors,
    render_style_anchor_block,
)
from bestseller.services.quality_levers.quality_trend_dashboard import (
    AlertRule,
    ChapterScoreSnapshot,
    DashboardAlert,
    DashboardWindow,
    MetricDefinition,
    QualityTrendDashboardConfig,
    evaluate_dashboard_window,
    load_quality_trend_dashboard,
    render_dashboard_summary,
)
from bestseller.services.quality_levers.rejection_repair_playbook import (
    RejectionCause,
    RejectionRepairPlaybookConfig,
    RepairAction,
    load_rejection_repair_playbook,
    render_repair_actions_block,
)
from bestseller.services.quality_levers.rhythm_engineering import (
    RhythmAnchorType,
    RhythmAuditResult,
    RhythmEngineeringConfig,
    audit_rhythm,
    load_rhythm_engineering,
    render_rhythm_block,
)
from bestseller.services.quality_levers.sensory_inventory import (
    SceneTypeRequirement,
    SensoryAxis,
    SensoryInventoryConfig,
    get_scene_requirement,
    load_sensory_inventory,
    render_sensory_requirement_block,
)

__all__ = [
    "AbstractSensoryResult",
    "AggregatedCritique",
    "AggregationPolicy",
    "BannedPattern",
    "BannedPatternsResult",
    "ChapterPositionProfile",
    "ChapterPositionProfilesConfig",
    "CharacterEngineConfig",
    "CharacterProfile",
    "ConsensusIssue",
    "CriticPersona",
    "CriticPersonasConfig",
    "DumpingResult",
    "ForbiddenVoiceResult",
    "OpeningHook",
    "OpeningSigningGate",
    "PacingPreference",
    "PersonaIssue",
    "PersonaResult",
    "PlatformProfile",
    "PlatformProfilesConfig",
    "ProseStyleAnchorsConfig",
    "PulseDensityResult",
    "PulseWords",
    "QuantitativeChapterAudit",
    "RejectionCause",
    "RejectionRepairPlaybookConfig",
    "RepairAction",
    "SceneTypeRequirement",
    "SensitiveWindowAntiPatterns",
    "SensoryAxis",
    "SensoryCoverageResult",
    "SensoryInventoryConfig",
    "SignatureDensityResult",
    "StyleAnchor",
    "VoicePreference",
    "WordCountGateResult",
    "aggregate_persona_results",
    "audit_chapter",
    "collect_forbidden_words",
    "collect_forbidden_words_from_profiles",
    "collect_signature_words",
    "collect_signature_words_from_profiles",
    "compute_pulse_density",
    "compute_sensory_coverage",
    "count_cjk_chars",
    "detect_chapter_positions",
    "detect_psychological_dumping",
    "evaluate_word_count",
    "get_anti_ai_banned_patterns",
    "get_character_profile",
    "get_persona",
    "get_scene_requirement",
    "get_style_anchor",
    "load_chapter_position_profiles",
    "load_character_engine",
    "load_critic_personas",
    "load_platform_profiles",
    "load_prose_style_anchors",
    "load_rejection_repair_playbook",
    "load_sensory_inventory",
    "measure_signature_density",
    "parse_rejection_reason",
    "render_all_persona_briefs",
    "render_chapter_position_block",
    "render_character_engine_profile_block",
    "render_character_profile_block",
    "render_persona_system_prompt",
    "render_platform_profile_block",
    "render_repair_actions_block",
    "render_sensory_requirement_block",
    "render_style_anchor_block",
    "resolve_platform_id",
    "synthesize_character_engine_profile",
    "scan_abstract_sensory_terms",
    "scan_banned_patterns",
    "scan_forbidden_voice_words",
]
