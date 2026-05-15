"""Character intelligence services.

This package translates distilled fiction strategy into project-local
character design contracts, then lets downstream story bible and draft
systems consume those contracts without coupling to raw aggregate files.
"""

from bestseller.services.character_intelligence.optimizer import (
    CHARACTER_INTELLIGENCE_PROFILE_VERSION,
    build_optimized_character_profile,
    optimize_project_character_profiles,
)
from bestseller.services.character_intelligence.strategy import (
    build_character_strategy_from_distillation,
    character_strategy_from_project_metadata,
    normalize_character_strategy,
)

__all__ = [
    "CHARACTER_INTELLIGENCE_PROFILE_VERSION",
    "build_optimized_character_profile",
    "build_character_strategy_from_distillation",
    "character_strategy_from_project_metadata",
    "normalize_character_strategy",
    "optimize_project_character_profiles",
]
