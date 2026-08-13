"""Market validation subsystem (advisory-only market intelligence).

Validates a book concept against live platform data: genre heat, competitor
scan, title dedup / shell crowding, blurb benchmark and an advisory verdict.
Independent lane — nothing in the generation pipeline depends on it unless the
operator opts in. See docs/dev-plans/2026-08-08-market-validation-capability.md.
"""

from bestseller.domain.market_validation import (
    MarketValidationReport,
    MarketValidationRequest,
)
from bestseller.services.market_validation.config import (
    load_market_validation_config,
    reset_market_validation_config_cache,
)

__all__ = [
    "MarketValidationReport",
    "MarketValidationRequest",
    "load_market_validation_config",
    "reset_market_validation_config_cache",
]
