from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from bestseller.domain.cultural_texture import CulturalTextureModule, MaterialPaletteItem
from bestseller.domain.religious_organization import ReligiousOrganization

GateMode = Literal["strict", "warn"]

STRICT_CULTURAL_CATEGORIES = {
    "历史",
    "历史架空",
    "古典",
    "古典权谋",
    "武侠",
    "武侠群像",
    "民俗悬疑",
    "惊悚灵异",
    "驱魔探案综合",
    "wuxia-jianghu",
    "suspense-mystery",
    "history-strategy",
    "western_fantasy",
    "西式奇幻",
}


@dataclass(frozen=True)
class CulturalTextureFinding:
    code: str
    severity: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CulturalTextureReport:
    chapter_no: int | None
    landed_palette_items: tuple[str, ...]
    findings: tuple[CulturalTextureFinding, ...]

    @property
    def is_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)


def cultural_module_mode_for_category(category: str | None) -> GateMode:
    normalized = str(category or "").strip()
    return "strict" if normalized in STRICT_CULTURAL_CATEGORIES else "warn"


def pick_palette_items_for_chapter(
    module: CulturalTextureModule | dict,
    *,
    chapter_no: int,
    count: int = 3,
) -> list[MaterialPaletteItem]:
    if isinstance(module, dict):
        module = CulturalTextureModule.model_validate(module)
    palette = list(module.palette)
    if not palette:
        return []
    start = (max(chapter_no, 1) - 1) % len(palette)
    return [palette[(start + offset) % len(palette)] for offset in range(min(count, len(palette)))]


def scan_cultural_texture_density(
    module: CulturalTextureModule | dict,
    *,
    chapter_text: str,
    chapter_no: int | None = None,
    recent_missing_chapters: list[int] | None = None,
    category: str | None = None,
    is_palette_subversion: bool = False,
) -> CulturalTextureReport:
    if isinstance(module, dict):
        module = CulturalTextureModule.model_validate(module)
    if is_palette_subversion:
        return CulturalTextureReport(chapter_no=chapter_no, landed_palette_items=(), findings=())

    text = chapter_text or ""
    landed = tuple(item.name for item in module.palette if item.sensory_hook in text)
    severity = "critical" if cultural_module_mode_for_category(category) == "strict" else "warning"
    findings: list[CulturalTextureFinding] = []
    if not landed:
        findings.append(
            CulturalTextureFinding(
                code="missing_palette_landing",
                severity=severity,
                message="Chapter does not land any cultural palette sensory_hook.",
                payload={"chapter_no": chapter_no},
            )
        )
        recent = set(recent_missing_chapters or [])
        if chapter_no is not None and {chapter_no - 1, chapter_no - 2}.issubset(recent):
            findings.append(
                CulturalTextureFinding(
                    code="palette_gap_streak",
                    severity=severity,
                    message="Three consecutive chapters missed cultural palette landing.",
                    payload={"chapters": [chapter_no - 2, chapter_no - 1, chapter_no]},
                )
            )
    for forbidden in module.taboo_behaviors:
        if forbidden and forbidden in text:
            findings.append(
                CulturalTextureFinding(
                    code="forbidden_address_or_taboo",
                    severity=severity,
                    message=f"Forbidden cultural behavior/address appears: {forbidden}",
                    payload={"term": forbidden},
                )
            )
    return CulturalTextureReport(
        chapter_no=chapter_no,
        landed_palette_items=landed,
        findings=tuple(findings),
    )


def validate_religious_sacred_sites(
    organization: ReligiousOrganization | dict,
    *,
    region_names: set[str],
) -> list[str]:
    if isinstance(organization, dict):
        organization = ReligiousOrganization.model_validate(organization)
    return [site for site in organization.sacred_sites if site not in region_names]
