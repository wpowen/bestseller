"""Verify that a VolumePlan implements the approved SerialityProof."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SerialityVolumeFinding:
    code: str
    message: str


# 2026-08-29 真机《十日补碑》定案：phase_reference_invalid 墙拆掉后，这本书
# 死在了单独一条 unit_family_repeated（相邻两卷同冲突家族）。那是**审美
# 重复度**问题，不是容量证明失败——容量证明问的是阶段有没有映射完、家族
# 有没有覆盖、积累是否不可逆；相邻卷体验重复归卷间差异化约束管。它在修复
# 循环里已经挣到过一次重生（重生成整份卷计划），重生没修好不该赔上整本书。
# 按仓库规矩（审美/软缺陷只挣重生和留痕，不发杀权）降为 advisory：照常
# 检出、照常进修复反馈与落库回执，但不再单独否决。
ADVISORY_CODES: frozenset[str] = frozenset({"unit_family_repeated"})


@dataclass(frozen=True, slots=True)
class SerialityVolumeReport:
    passed: bool
    findings: tuple[SerialityVolumeFinding, ...]

    @property
    def blocking_codes(self) -> tuple[str, ...]:
        return tuple(
            item.code for item in self.findings if item.code not in ADVISORY_CODES
        )

    @property
    def advisory_codes(self) -> tuple[str, ...]:
        return tuple(
            item.code for item in self.findings if item.code in ADVISORY_CODES
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "blocking_codes": list(self.blocking_codes),
            "advisory_codes": list(self.advisory_codes),
            "findings": [
                {"code": item.code, "message": item.message} for item in self.findings
            ],
            "schema_version": "seriality-volume-mapping.v1",
        }


def _text(value: object) -> str:
    return str(value or "").strip()


def _items(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, Sequence) or isinstance(value, bytes):
        return []
    return [text for item in value if (text := _text(item))]


def _phase_id(index: int) -> str:
    return f"phase-{index:02d}"


def _track_deltas(value: object) -> list[tuple[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    rows: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        track_ref = _text(item.get("track_ref"))
        delta = _text(item.get("delta"))
        if track_ref and delta:
            rows.append((track_ref, delta))
    return rows


def _is_concrete_delta(track_ref: str, delta: str) -> bool:
    generic = {
        "变化",
        "推进",
        "升级",
        "增加",
        "提升",
        "发生变化",
        "progress",
        "increase",
        "upgrade",
        "change",
    }
    remainder = delta.replace(track_ref, "").strip(" ：:，,。.;；-—")
    return len(remainder) >= 4 and remainder.casefold() not in generic


def _best_match(candidate: str, approved: Sequence[str]) -> str | None:
    """把模型写的近似引用规范化成批准原文；对不上就返回 None。

    LLM 逐字复现 80-120 字长句的成功率接近零——这不是它该干的活。
    匹配只做保守归一：完全相等 / 一方是另一方前缀 / 字符集 Jaccard ≥ 0.6。
    """

    cand = candidate.strip()
    if not cand:
        return None
    for text in approved:
        if cand == text:
            return text
    for text in approved:
        if text.startswith(cand) or cand.startswith(text):
            return text
    cset = set(cand)
    best: tuple[float, str] | None = None
    for text in approved:
        tset = set(text)
        if not tset:
            continue
        j = len(cset & tset) / len(cset | tset)
        if j >= 0.6 and (best is None or j > best[0]):
            best = (j, text)
    return best[1] if best else None


def _phase_chapter_range(phase_text: str) -> tuple[int, int] | None:
    import re as _re

    m = _re.search(r"第\s*(\d+)\s*[至到\-—~]\s*(\d+)\s*章", phase_text)
    if not m:
        return None
    lo, hi = int(m.group(1)), int(m.group(2))
    return (lo, hi) if lo <= hi else None


def canonicalize_seriality_volume_refs(
    volume_plan: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    concept_contract: Mapping[str, Any] | None,
) -> Any:
    """在验收前做确定性规范化——引用由键推导，不指望 LLM 抄准长句。

    2026-08-29 真机《破庙里我把玉玺摔成四瓣》死因：卷计划 prompt 命令模型
    「逐字引用批准的 seriality_phase_ref」，但整条 prompt 链（含修复循环）
    从没把批准列表渲染给模型看，10/10 卷 phase_reference_invalid，建书死在
    foundation。这是「验收端验模型看不见的契约」（2026-08-04 写手欠产案、
    2026-08-24 契约表整张没实现案的同族）。

    规范化规则（全部确定性、幂等，只归一不发明）：
      · phase：有合法 phase_id → ref 直接由 id 查表覆盖（id 是键，ref 是
        推导值）；没有 id 但 ref 能保守匹配上批准原文 → 反推 id；两者都
        没有但卷的章数累计区间落在某阶段的「第X至Y章」内 → 按区间中点推断
        （阶段原文自带章号区间，这是比让模型抄句子可靠得多的信号源）。
      · unit_family_ref / track_ref：保守匹配（相等/前缀/Jaccard≥0.6）到
        批准原文则替换为原文；匹配不上保持原样，留给门与修复循环。
    """

    if not isinstance(concept_contract, Mapping):
        return volume_plan
    proof = concept_contract.get("seriality_proof")
    if not isinstance(proof, Mapping):
        return volume_plan
    raw = (
        volume_plan.get("volumes")
        if isinstance(volume_plan, Mapping)
        else volume_plan
    )
    if not isinstance(raw, Sequence):
        return volume_plan
    phases = _items(proof.get("phase_transitions"))
    families = _items(proof.get("unit_families"))
    tracks = _items(proof.get("accumulation_tracks"))
    id_to_ref = {_phase_id(i): text for i, text in enumerate(phases, start=1)}
    ranges = {pid: _phase_chapter_range(text) for pid, text in id_to_ref.items()}

    out: list[dict[str, Any]] = []
    cursor = 0
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        try:
            n_ch = int(row.get("chapter_count_target") or 0)
        except (TypeError, ValueError):
            n_ch = 0
        mid = cursor + max(n_ch, 1) / 2
        cursor += max(n_ch, 0)

        pid = _text(row.get("seriality_phase_id"))
        if pid in id_to_ref:
            row["seriality_phase_ref"] = id_to_ref[pid]
        else:
            matched = _best_match(_text(row.get("seriality_phase_ref")), phases)
            if matched is None and ranges:
                for cand_id, rng in ranges.items():
                    if rng and rng[0] <= mid <= rng[1]:
                        matched = id_to_ref[cand_id]
                        break
            if matched is not None:
                row["seriality_phase_ref"] = matched
                row["seriality_phase_id"] = next(
                    k for k, v in id_to_ref.items() if v == matched
                )

        fam = _best_match(_text(row.get("unit_family_ref")), families)
        if fam is not None:
            row["unit_family_ref"] = fam
        deltas = row.get("accumulation_track_deltas")
        if isinstance(deltas, Sequence) and not isinstance(deltas, (str, bytes)):
            fixed = []
            for d in deltas:
                if not isinstance(d, Mapping):
                    continue
                dd = dict(d)
                t = _best_match(_text(dd.get("track_ref")), tracks)
                if t is not None:
                    dd["track_ref"] = t
                fixed.append(dd)
            row["accumulation_track_deltas"] = fixed
        out.append(row)
    if isinstance(volume_plan, Mapping):
        merged = dict(volume_plan)
        merged["volumes"] = out
        return merged
    return out


def render_seriality_volume_contract_block(
    concept_contract: Mapping[str, Any] | None,
) -> str:
    """把批准列表渲染成 prompt 块——验收端要求逐字引用的文本必须先给模型看。"""

    if not isinstance(concept_contract, Mapping):
        return ""
    proof = concept_contract.get("seriality_proof")
    if not isinstance(proof, Mapping):
        return ""
    phases = _items(proof.get("phase_transitions"))
    families = _items(proof.get("unit_families"))
    tracks = _items(proof.get("accumulation_tracks"))
    if not phases and not families and not tracks:
        return ""
    lines = ["【已批准的长篇容量证明——引用必须逐字取自本清单】"]
    if phases:
        lines.append("阶段（每卷引用一个 phase_id，ref 为该行原文）：")
        lines += [f"  {_phase_id(i)}: {t}" for i, t in enumerate(phases, start=1)]
    if families:
        lines.append("故事单元家族（unit_family_ref 逐字取自下列）：")
        lines += [f"  - {t}" for t in families]
    if tracks:
        lines.append("永久积累轴（track_ref 逐字取自下列）：")
        lines += [f"  - {t}" for t in tracks]
    return "\n".join(lines) + "\n"


def evaluate_seriality_volume_mapping(
    volume_plan: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    concept_contract: Mapping[str, Any] | None,
) -> SerialityVolumeReport:
    if not isinstance(concept_contract, Mapping):
        return SerialityVolumeReport(passed=True, findings=())
    proof = concept_contract.get("seriality_proof")
    if not isinstance(proof, Mapping):
        return SerialityVolumeReport(
            passed=False,
            findings=(
                SerialityVolumeFinding(
                    "seriality_proof_missing",
                    "ConceptContract has no SerialityProof.",
                ),
            ),
        )

    # SerialityProof is persisted for short books as well so the conception
    # contract keeps one stable shape. Short books intentionally have no
    # phase/unit-family/accumulation mapping; applying this long-form gate to
    # them creates false phase_reference_invalid failures on ordinary plans.
    # Only enforce mapping for true long-form targets. When target metadata is
    # absent, retain strict standalone-validator behavior for existing tests.
    capacity_report = proof.get("capacity_report")
    target_chapters = 0
    capacity_tier = ""
    if isinstance(capacity_report, Mapping):
        try:
            target_chapters = int(capacity_report.get("target_chapters") or 0)
        except (TypeError, ValueError):
            target_chapters = 0
        capacity_tier = _text(capacity_report.get("capacity_tier"))
    if not target_chapters:
        try:
            target_chapters = int(proof.get("target_chapters") or 0)
        except (TypeError, ValueError):
            target_chapters = 0
    if (target_chapters and target_chapters < 200) or capacity_tier == "short":
        return SerialityVolumeReport(passed=True, findings=())

    raw_volumes = (
        volume_plan.get("volumes")
        if isinstance(volume_plan, Mapping)
        else volume_plan
    )
    volumes = (
        [dict(item) for item in raw_volumes if isinstance(item, Mapping)]
        if isinstance(raw_volumes, Sequence)
        else []
    )
    findings: list[SerialityVolumeFinding] = []
    if not volumes:
        return SerialityVolumeReport(
            passed=False,
            findings=(
                SerialityVolumeFinding(
                    "volume_plan_empty",
                    "No volumes exist to implement SerialityProof.",
                ),
            ),
        )

    phases = _items(proof.get("phase_transitions"))
    phase_ids = {_phase_id(index): phase for index, phase in enumerate(phases, start=1)}
    mapped_phase_ids: list[str] = []
    for index, item in enumerate(volumes, start=1):
        phase_id = _text(item.get("seriality_phase_id"))
        phase_ref = _text(item.get("seriality_phase_ref"))
        expected_ref = phase_ids.get(phase_id)
        if expected_ref is None or phase_ref != expected_ref:
            findings.append(
                SerialityVolumeFinding(
                    "phase_reference_invalid",
                    f"Volume {index} must use one exact approved phase id/ref pair.",
                )
            )
            continue
        mapped_phase_ids.append(phase_id)
    missing_phases = [
        phase for phase_id, phase in phase_ids.items() if phase_id not in mapped_phase_ids
    ]
    if missing_phases:
        findings.append(
            SerialityVolumeFinding(
                "phase_mapping_incomplete",
                "Unmapped phase transformations: " + " / ".join(missing_phases),
            )
        )
    mapped_phase_indexes = [int(item.split("-")[-1]) for item in mapped_phase_ids]
    if mapped_phase_indexes != sorted(mapped_phase_indexes):
        findings.append(
            SerialityVolumeFinding(
                "phase_order_invalid",
                "Volume phases move backwards instead of following the approved order.",
            )
        )

    tracks = _items(proof.get("accumulation_tracks"))
    all_track_deltas = [row for item in volumes for row in _track_deltas(item.get("accumulation_track_deltas"))]
    mapped_tracks = [track for track, _delta in all_track_deltas]
    missing_tracks = [
        track for track in tracks if track not in mapped_tracks
    ]
    if missing_tracks:
        findings.append(
            SerialityVolumeFinding(
                "accumulation_mapping_incomplete",
                "Unmapped permanent accumulation tracks: " + " / ".join(missing_tracks),
            )
        )
    for index, item in enumerate(volumes, start=1):
        missing = [
            key
            for key in (
                "seriality_phase_id",
                "seriality_phase_ref",
                "unit_family_ref",
                "renewable_unit_variant",
                "accumulation_track_deltas",
            )
            if not item.get(key)
        ]
        if missing:
            findings.append(
                SerialityVolumeFinding(
                    "volume_seriality_fields_missing",
                    f"Volume {index} is missing: {', '.join(missing)}",
                )
            )
        family = _text(item.get("unit_family_ref"))
        families = _items(proof.get("unit_families"))
        if family and family not in families:
            findings.append(
                SerialityVolumeFinding(
                    "unit_family_reference_invalid",
                    f"Volume {index} unit_family_ref is not an exact approved family.",
                )
            )
        for track_ref, delta in _track_deltas(item.get("accumulation_track_deltas")):
            if track_ref not in tracks:
                findings.append(
                    SerialityVolumeFinding(
                        "accumulation_track_reference_invalid",
                        f"Volume {index} references an unapproved accumulation track.",
                    )
                )
            elif not _is_concrete_delta(track_ref, delta):
                findings.append(
                    SerialityVolumeFinding(
                        "accumulation_delta_generic",
                        f"Volume {index} gives no concrete irreversible state change for {track_ref}.",
                    )
                )
    variants = [_text(item.get("renewable_unit_variant")) for item in volumes]
    if any(
        variants[index] and variants[index] == variants[index - 1]
        for index in range(1, len(variants))
    ):
        findings.append(
            SerialityVolumeFinding(
                "renewable_unit_repeated",
                "Consecutive volumes reuse the same renewable unit variant.",
            )
        )
    families = [_text(item.get("unit_family_ref")) for item in volumes]
    if any(
        families[index] and families[index] == families[index - 1]
        for index in range(1, len(families))
    ):
        findings.append(
            SerialityVolumeFinding(
                "unit_family_repeated",
                "Consecutive volumes reuse the same story-unit family.",
            )
        )
    required_family_coverage = min(len(volumes), len(_items(proof.get("unit_families"))))
    if len(set(families) - {""}) < required_family_coverage:
        findings.append(
            SerialityVolumeFinding(
                "unit_family_coverage_incomplete",
                "The volume plan does not exercise enough approved story-unit families.",
            )
        )
    delta_signatures = [(track, delta.casefold()) for track, delta in all_track_deltas]
    if len(delta_signatures) != len(set(delta_signatures)):
        findings.append(
            SerialityVolumeFinding(
                "accumulation_delta_repeated",
                "The same permanent state delta is copied across volumes.",
            )
        )
    # passed 只看阻断级；advisory 照常留在 findings 里进回执与修复反馈。
    blocking = [f for f in findings if f.code not in ADVISORY_CODES]
    return SerialityVolumeReport(passed=not blocking, findings=tuple(findings))


__all__ = ["SerialityVolumeReport", "evaluate_seriality_volume_mapping"]
