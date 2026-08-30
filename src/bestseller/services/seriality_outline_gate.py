"""Validate chapter-outline execution of the current volume seriality contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class SerialityOutlineFinding:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class SerialityOutlineReport:
    passed: bool
    findings: tuple[SerialityOutlineFinding, ...]

    @property
    def blocking_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.findings)


def _text(value: object) -> str:
    return str(value or "").strip()


def _items(value: object) -> list[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return list(value)


def _track_deltas(value: object) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for item in _items(value):
        if not isinstance(item, Mapping):
            continue
        track_ref = _text(item.get("track_ref"))
        delta = _text(item.get("delta"))
        if track_ref and delta:
            rows.append((track_ref, delta))
    return rows


def canonicalize_chapter_seriality_refs(
    chapters: Sequence[Mapping[str, object]],
    volume_entry: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    """章级引用规范化——只有一个合法值的字段由卷上下文直接推导。

    2026-08-29 真机《十日补碑》第三关死因：章纲修复循环 3 轮全败于
    chapter_unit_family_mismatch / chapter_accumulation_track_mismatch。
    本门要求每章 seriality_contract 的 phase_id 与 unit_family_ref 与
    **当前卷**逐字相等——对每章它们各自只有一个合法值，要模型回声一遍
    50 字长句纯属仪式（且去种词修复后家族名更长更专属，回声更不可能）。
    与卷级 canonicalize_seriality_volume_refs 同方子：推导代替回声，
    只归一不发明。track_ref 保守匹配（相等/前缀/字符 Jaccard≥0.6）到
    本卷批准轴；匹配不上保持原样留给门。幂等。
    """

    if not isinstance(volume_entry, Mapping):
        return [dict(c) for c in chapters if isinstance(c, Mapping)]
    vol_phase = _text(volume_entry.get("seriality_phase_id"))
    vol_family = _text(volume_entry.get("unit_family_ref"))
    approved = [
        track for track, _d in _track_deltas(volume_entry.get("accumulation_track_deltas"))
    ]

    def _match(cand: str) -> str | None:
        cand = cand.strip()
        if not cand:
            return None
        for t in approved:
            # 包含也算：模型常把「已并拢的玺瓣数」缩写成「玺瓣数」。
            if cand == t or (len(cand) >= 3 and (cand in t or t in cand)):
                return t
        cs = set(cand)
        best = None
        for t in approved:
            ts = set(t)
            if not ts:
                continue
            j = len(cs & ts) / len(cs | ts)
            if j >= 0.6 and (best is None or j > best[0]):
                best = (j, t)
        if best:
            return best[1]
        # 方向性覆盖（2026-08-30 真机《十日补碑》第 7 败引文定案）：模型把
        # 整句长的批准轴缩成短名（'海眼认迹层数' vs '海眼永久认下宋潮生笔迹，
        # 封印层数与笔债笔数只升不降…'），对称 Jaccard 用并集做分母，短对长
        # 永远 6/20≈0.3 过不了线。改判据：短名字符 ≥85% 落进某条批准项、
        # 且最优与次优差 ≥0.15（歧义不归一，留给门）。
        best_cov = None
        second = 0.0
        for t in approved:
            ts = set(t)
            if not ts or not cs:
                continue
            cov = len(cs & ts) / len(cs)
            if best_cov is None or cov > best_cov[0]:
                if best_cov is not None:
                    second = max(second, best_cov[0])
                best_cov = (cov, t)
            else:
                second = max(second, cov)
        if best_cov and best_cov[0] >= 0.85 and best_cov[0] - second >= 0.15:
            return best_cov[1]
        return None

    out: list[dict[str, object]] = []
    for ch in chapters:
        if not isinstance(ch, Mapping):
            continue
        row = dict(ch)
        raw = row.get("seriality_contract")
        contract = dict(raw) if isinstance(raw, Mapping) else {}
        if vol_phase:
            contract["phase_id"] = vol_phase
        if vol_family:
            contract["unit_family_ref"] = vol_family
        deltas = contract.get("accumulation_track_deltas")
        if isinstance(deltas, Sequence) and not isinstance(deltas, (str, bytes)):
            fixed = []
            for d in deltas:
                if not isinstance(d, Mapping):
                    continue
                dd = dict(d)
                m = _match(_text(dd.get("track_ref")))
                if m is not None:
                    dd["track_ref"] = m
                fixed.append(dd)
            contract["accumulation_track_deltas"] = fixed
        row["seriality_contract"] = contract
        out.append(row)
    return out


def evaluate_seriality_outline_batch(
    chapters: Sequence[Mapping[str, object]],
    concept_contract: Mapping[str, object] | None,
    volume_entry: Mapping[str, object] | None,
) -> SerialityOutlineReport:
    """Fail only for v2 outlines; legacy projects remain unaffected."""

    if not isinstance(concept_contract, Mapping):
        return SerialityOutlineReport(passed=True, findings=())
    if not isinstance(volume_entry, Mapping):
        return SerialityOutlineReport(
            passed=False,
            findings=(
                SerialityOutlineFinding(
                    "seriality_volume_context_missing",
                    "The outline batch has no current-volume seriality context.",
                ),
            ),
        )

    # The conception contract is shared by short and long books. Short books
    # intentionally do not carry phase/unit-family/accumulation mappings, so
    # requiring a chapter-level seriality_contract here would reject a valid
    # ordinary outline after the volume-level gate has already skipped the
    # long-form mapping. Keep strict validation when target metadata is absent
    # for standalone validator callers and legacy tests.
    proof = concept_contract.get("seriality_proof")
    capacity_report = proof.get("capacity_report") if isinstance(proof, Mapping) else None
    target_chapters = 0
    capacity_tier = ""
    if isinstance(capacity_report, Mapping):
        try:
            target_chapters = int(capacity_report.get("target_chapters") or 0)
        except (TypeError, ValueError):
            target_chapters = 0
        capacity_tier = _text(capacity_report.get("capacity_tier"))
    if not target_chapters and isinstance(proof, Mapping):
        try:
            target_chapters = int(proof.get("target_chapters") or 0)
        except (TypeError, ValueError):
            target_chapters = 0
    if (target_chapters and target_chapters < 200) or capacity_tier == "short":
        return SerialityOutlineReport(passed=True, findings=())

    findings: list[SerialityOutlineFinding] = []
    contributions: list[str] = []
    phase_progressions: list[str] = []
    state_after_values: list[str] = []
    no_reset_values: list[str] = []
    unit_instance_ids: list[str] = []
    volume_phase_id = _text(volume_entry.get("seriality_phase_id"))
    volume_family = _text(volume_entry.get("unit_family_ref"))
    approved_tracks = {
        track
        for track, _delta in _track_deltas(volume_entry.get("accumulation_track_deltas"))
    }
    mapped_tracks: set[str] = set()
    for index, chapter in enumerate(chapters, start=1):
        raw = chapter.get("seriality_contract")
        contract = dict(raw) if isinstance(raw, Mapping) else {}
        missing = [
            key
            for key in (
                "phase_id",
                "unit_family_ref",
                "unit_instance_id",
                "unit_variant_contribution",
                "phase_progress",
                "prior_state_refs",
                "irreversible_state_after",
                "no_reset_evidence",
            )
            if not contract.get(key)
        ]
        if missing:
            findings.append(
                SerialityOutlineFinding(
                    "chapter_seriality_contract_missing",
                    f"Chapter {index} seriality_contract missing: {', '.join(missing)}",
                )
            )
        contribution = _text(contract.get("unit_variant_contribution"))
        if contribution:
            contributions.append(contribution)
        phase_progress = _text(contract.get("phase_progress"))
        if phase_progress:
            phase_progressions.append(phase_progress)
        state_after = _text(contract.get("irreversible_state_after"))
        if state_after:
            state_after_values.append(state_after)
        no_reset = _text(contract.get("no_reset_evidence"))
        if no_reset:
            no_reset_values.append(no_reset)
        unit_instance_id = _text(contract.get("unit_instance_id"))
        if unit_instance_id:
            unit_instance_ids.append(unit_instance_id)
        if _text(contract.get("phase_id")) != volume_phase_id:
            findings.append(
                SerialityOutlineFinding(
                    "chapter_phase_reference_mismatch",
                    f"Chapter {index} does not reference the current volume phase id exactly.",
                )
            )
        if _text(contract.get("unit_family_ref")) != volume_family:
            # 判词必须带引文（2026-08-24「重复否决却不给引文」定案）：
            # 没有「写了什么 vs 该是什么」，修复循环和排障都只能瞎猜。
            findings.append(
                SerialityOutlineFinding(
                    "chapter_unit_family_mismatch",
                    f"Chapter {index} unit_family_ref="
                    f"'{_text(contract.get('unit_family_ref'))[:40]}' != volume "
                    f"'{volume_family[:40]}'.",
                )
            )
        raw_track_deltas = contract.get("accumulation_track_deltas")
        if not isinstance(raw_track_deltas, Sequence) or isinstance(
            raw_track_deltas, (str, bytes)
        ):
            findings.append(
                SerialityOutlineFinding(
                    "chapter_accumulation_mapping_missing",
                    f"Chapter {index} must output accumulation_track_deltas (an empty array is allowed while preparing a change).",
                )
            )
        for track_ref, _delta in _track_deltas(raw_track_deltas):
            if track_ref not in approved_tracks:
                findings.append(
                    SerialityOutlineFinding(
                        "chapter_accumulation_track_mismatch",
                        f"Chapter {index} track_ref='{track_ref[:40]}' not in volume "
                        f"approved={sorted(t[:24] for t in approved_tracks)}.",
                    )
                )
            else:
                mapped_tracks.add(track_ref)

    repeated_fields = (
        ("chapter_seriality_contribution_repeated", contributions, "story-unit contribution"),
        ("chapter_phase_progress_repeated", phase_progressions, "phase progress"),
        ("chapter_irreversible_state_repeated", state_after_values, "irreversible state"),
        ("chapter_no_reset_evidence_repeated", no_reset_values, "no-reset evidence"),
    )
    if len(chapters) > 1:
        for code, values, label in repeated_fields:
            if len(values) == len(chapters) and len(set(values)) <= 1:
                findings.append(
                    SerialityOutlineFinding(
                        code,
                        f"All chapters copy the same {label} instead of progressing it.",
                    )
                )
    try:
        volume_target = int(volume_entry.get("chapter_count_target") or 0)
    except (TypeError, ValueError):
        volume_target = 0
    is_full_volume_validation = volume_target <= 0 or len(chapters) >= volume_target
    if is_full_volume_validation and approved_tracks - mapped_tracks:
        findings.append(
            SerialityOutlineFinding(
                "chapter_accumulation_coverage_incomplete",
                "The completed volume never realizes every accumulation track assigned to it.",
            )
        )
    if len(chapters) >= 6:
        minimum_instances = math.ceil(len(chapters) / 6)
        if len(set(unit_instance_ids)) < minimum_instances:
            findings.append(
                SerialityOutlineFinding(
                    "chapter_story_unit_density_too_low",
                    f"{len(chapters)} chapters need at least {minimum_instances} distinct story-unit instances.",
                )
            )
    return SerialityOutlineReport(passed=not findings, findings=tuple(findings))


__all__ = ["SerialityOutlineReport", "evaluate_seriality_outline_batch"]
