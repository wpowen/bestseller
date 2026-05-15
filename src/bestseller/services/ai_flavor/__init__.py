"""AI-flavor span-level detector and patcher.

Public surface kept thin so call sites import from one place::

    from bestseller.services.ai_flavor import (
        AiFlavorReport,
        AiFlavorSpan,
        detect,
    )

The Gate service (``bestseller.services.ai_flavor_gate``) wraps these
primitives with project/chapter context and emits the unified
``CheckerReport`` envelope; everything in this package is pure-string
and DB-agnostic so it can be unit-tested and reused from CLI tools.
"""

from bestseller.services.ai_flavor.detector import detect
from bestseller.services.ai_flavor.patcher import PatchResult, apply_patches
from bestseller.services.ai_flavor.types import (
    AiFlavorReport,
    AiFlavorSpan,
    Severity,
)


__all__ = [
    "AiFlavorReport",
    "AiFlavorSpan",
    "PatchResult",
    "Severity",
    "apply_patches",
    "detect",
]
