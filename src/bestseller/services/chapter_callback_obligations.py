from __future__ import annotations

# ruff: noqa: RUF001, ANN401
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
import yaml

from bestseller.infra.db.models import ProjectModel


@dataclass(frozen=True)
class CallbackObligation:
    clue_id: str
    clue_surface: str
    set_in_chapter: int
    payoff_window: tuple[int, int]
    obligation_kind: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def collect_callback_obligations(
    session: AsyncSession,
    project: ProjectModel,
    chapter_number: int,
) -> tuple[CallbackObligation, ...]:
    _ = session
    project_dir = Path("output") / str(project.slug or "")
    schedule = _load_yaml(project_dir / "story-bible" / "reveal-schedule.yaml")
    ledger_surfaces = _load_clue_surfaces(project_dir / "story-bible" / "clue-ledger.md")
    obligations: list[CallbackObligation] = []
    for item in _iter_schedule_items(schedule):
        clue_id = str(item.get("id") or item.get("clue_id") or "").strip()
        if not clue_id:
            continue
        set_chapter = _int(item.get("set_in_chapter") or item.get("setup_chapter"))
        payoff = (
            item.get("payoff_window")
            or item.get("payoff_chapters")
            or item.get("payoff_chapter")
        )
        lo, hi = _window(payoff)
        if lo <= chapter_number <= hi:
            kind = "must_payoff" if chapter_number == hi or lo == hi else "must_reference"
        elif set_chapter and 0 < chapter_number - set_chapter <= 5:
            kind = "should_recall"
        else:
            continue
        surface = str(
            item.get("surface")
            or item.get("clue_surface")
            or ledger_surfaces.get(clue_id)
            or clue_id
        ).strip()
        obligations.append(
            CallbackObligation(
                clue_id=clue_id,
                clue_surface=surface,
                set_in_chapter=set_chapter,
                payoff_window=(lo, hi),
                obligation_kind=kind,
            )
        )
    return tuple(obligations)


def render_callback_block(
    obligations: tuple[CallbackObligation, ...],
    *,
    language: str = "zh-CN",
) -> str:
    if not obligations:
        return ""
    if str(language or "").lower().startswith("en"):
        lines = ["[Callback obligations]", "This chapter must reference/pay off:"]
        for item in obligations[:12]:
            lines.append(
                f"- {item.obligation_kind}: {item.clue_id} \"{item.clue_surface}\" "
                f"(set ch{item.set_in_chapter}, "
                f"payoff {item.payoff_window[0]}-{item.payoff_window[1]})"
            )
        return "\n".join(lines)
    lines = [
        "【本章伏笔回收硬约束】",
        "本章必须明确 reference 或 payoff 以下前面埋的线索（跳过会被确定性审计拦截）：",
    ]
    for item in obligations[:12]:
        label = {
            "must_payoff": "必须 payoff",
            "must_reference": "必须 reference",
            "should_recall": "建议回响",
        }.get(item.obligation_kind, item.obligation_kind)
        lines.append(
            f"  - {label}: {item.clue_id}「{item.clue_surface}」"
            f"（第 {item.set_in_chapter} 章埋下，"
            f"兑现窗口 {item.payoff_window[0]}-{item.payoff_window[1]}）"
        )
    return "\n".join(lines)


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _iter_schedule_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("reveals", "items", "schedule", "clues"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
    return []


def _load_clue_surfaces(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    surfaces: dict[str, str] = {}
    for line in text.splitlines():
        if "C-" not in line and "CLUE" not in line:
            continue
        parts = [part.strip(" -*|") for part in line.split("|") if part.strip(" -*|")]
        if not parts:
            continue
        clue_id = next((part for part in parts if part.startswith(("C-", "CLUE"))), "")
        if clue_id:
            surfaces[clue_id] = next((part for part in parts if part != clue_id), clue_id)
    return surfaces


def _window(value: Any) -> tuple[int, int]:
    if isinstance(value, int):
        return value, value
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value)
        return number, number
    if isinstance(value, list | tuple) and value:
        lo = _int(value[0])
        hi = _int(value[-1])
        return (lo, hi) if lo and hi else (999999, 999999)
    return 999999, 999999


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = ["CallbackObligation", "collect_callback_obligations", "render_callback_block"]
