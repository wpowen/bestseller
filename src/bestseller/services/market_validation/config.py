"""Market validation config loading (``config/market_validation.yaml``).

The config is the single place that knows platform category registries,
taxonomy-to-platform mappings, title shell rules and thresholds, so the code
stays free of hardcoded platform vocabulary.
"""


from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
import yaml

logger = logging.getLogger(__name__)

_CONFIG_CACHE: dict[str, MarketValidationConfig] = {}


class SourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    enabled: bool = False
    base_url: str = ""
    timeout_seconds: float = Field(default=20.0, gt=0.0)
    rank_pages: int = Field(default=3, ge=1, le=10)


class WebSearchSourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    enabled: bool = True
    site_filters: tuple[str, ...] = ()


class SourcesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    fanqiehub: SourceConfig = SourceConfig(
        enabled=True, base_url="https://www.fanqiehub.com", timeout_seconds=25.0
    )
    qimao: SourceConfig = SourceConfig(
        enabled=True, base_url="https://www.qimao.com", timeout_seconds=20.0
    )
    weread: SourceConfig = SourceConfig(
        enabled=False, base_url="https://weread.qq.com", timeout_seconds=20.0
    )
    websearch: WebSearchSourceConfig = WebSearchSourceConfig()


class GenreHeatConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    min_sample: int = Field(default=10, ge=1)
    top_books: int = Field(default=5, ge=1, le=20)


class CompetitorScanConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    max_candidates: int = Field(default=24, ge=1, le=100)


class TitleShellRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str
    pattern: str
    # advisory_only shells inform the report but never drive a caution/fail
    # verdict (e.g. the colon subtitle form is a healthy mainstream shape).
    advisory_only: bool = False


class TitleCheckConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    near_distance_max: int = Field(default=1, ge=0, le=3)
    core_token_min_len: int = Field(default=3, ge=2)
    shell_crowd_min_books: int = Field(default=3, ge=2)
    shell_weak_heat_median: int = Field(default=100_000, ge=0)
    shells: tuple[TitleShellRule, ...] = ()


class VerdictConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    llm_enabled: bool = True


class BlurbBenchmarkConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    min_board_samples: int = Field(default=8, ge=1)


class FanqieCategoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    label: str
    cat_id: str = ""


class PlatformCategoryMapping(BaseModel):
    """One platform's category list for a taxonomy genre (ordered by relevance)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    channel: str = ""
    categories: tuple[str, ...] = ()


class GenreMapEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    fanqie: PlatformCategoryMapping | None = None
    sub_overrides: dict[str, GenreMapEntry] = Field(default_factory=dict)


class MarketValidationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    version: int = 1
    enabled: bool = False
    sources: SourcesConfig = SourcesConfig()
    genre_heat: GenreHeatConfig = GenreHeatConfig()
    competitor_scan: CompetitorScanConfig = CompetitorScanConfig()
    title_check: TitleCheckConfig = TitleCheckConfig()
    verdict: VerdictConfig = VerdictConfig()
    blurb_benchmark: BlurbBenchmarkConfig = BlurbBenchmarkConfig()
    fanqie_categories: dict[str, tuple[FanqieCategoryEntry, ...]] = Field(
        default_factory=dict
    )
    genre_map: dict[str, GenreMapEntry] = Field(default_factory=dict)
    qimao_label_aliases: dict[str, tuple[str, ...]] = Field(default_factory=dict)


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[4] / "config" / "market_validation.yaml"


def load_market_validation_config(
    path: Path | str | None = None,
) -> MarketValidationConfig:
    """Load and validate the market validation config (cached per path).

    A missing or broken config yields a disabled config instead of raising, so
    callers can treat "config unusable" and "capability off" identically.
    """

    resolved = Path(path) if path is not None else _default_config_path()
    cache_key = str(resolved)
    cached = _CONFIG_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        config = MarketValidationConfig.model_validate(raw)
    except FileNotFoundError:
        logger.warning("market_validation.yaml not found at %s; disabled", resolved)
        config = MarketValidationConfig(enabled=False)
    except Exception:
        logger.exception("Invalid market_validation.yaml at %s; disabled", resolved)
        config = MarketValidationConfig(enabled=False)
    _CONFIG_CACHE[cache_key] = config
    return config


def reset_market_validation_config_cache() -> None:
    _CONFIG_CACHE.clear()
