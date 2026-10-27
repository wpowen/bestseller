from __future__ import annotations

# ruff: noqa: ANN401
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from bestseller.domain.chapter_generation_input import ChapterGenerationInputBundle
from bestseller.services.methodology_overlay import resolve_methodology_contract_mode


_BLOCKING_SEVERITIES = {"critical", "major"}


@dataclass(frozen=True)
class ChapterPreDraftQualityFinding:
    code: str
    severity: str
    message: str
    repair_hint: str
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "repair_hint": self.repair_hint,
            "path": self.path,
        }


@dataclass(frozen=True)
class ChapterPreDraftQualityReport:
    passed: bool
    findings: tuple[ChapterPreDraftQualityFinding, ...]
    coverage: float
    gate_name: str = "chapter_predraft_quality_gate"

    @property
    def blocked(self) -> bool:
        return any(finding.severity in _BLOCKING_SEVERITIES for finding in self.findings)

    @property
    def blocking_issues(self) -> tuple[ChapterPreDraftQualityFinding, ...]:
        return tuple(
            finding for finding in self.findings if finding.severity in _BLOCKING_SEVERITIES
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_name": self.gate_name,
            "passed": self.passed,
            "blocked": self.blocked,
            "coverage": self.coverage,
            "findings": [finding.to_dict() for finding in self.findings],
            "blocking_codes": [finding.code for finding in self.blocking_issues],
        }


def evaluate_chapter_predraft_quality(
    bundle: ChapterGenerationInputBundle,
) -> ChapterPreDraftQualityReport:
    """Check explicit input constraints; never manufacture prose obligations."""

    findings: list[ChapterPreDraftQualityFinding] = []
    _append_structural_findings(findings, bundle)
    _append_missing_context_findings(findings, bundle.missing_context_keys)
    _append_methodology_findings(findings, bundle)
    _append_source_constraint_findings(findings, bundle)
    passed = not any(finding.severity in _BLOCKING_SEVERITIES for finding in findings)
    return ChapterPreDraftQualityReport(
        passed=passed,
        findings=tuple(findings),
        coverage=bundle.coverage,
    )


def _append_structural_findings(
    findings: list[ChapterPreDraftQualityFinding], bundle: ChapterGenerationInputBundle,
) -> None:
    """Typed task prerequisites are distinct from optional creative recipes."""
    chapter_number = bundle.chapter.get("chapter_number")
    target = bundle.quality_targets.get("target_word_count")
    if type(chapter_number) is not int or chapter_number < 1:
        findings.append(ChapterPreDraftQualityFinding(
            code="PREDRAFT_CHAPTER_NUMBER_INVALID", severity="critical",
            message="章号必须是正整数。", repair_hint="修正当前任务的章号，不补写剧情。",
            path="chapter.chapter_number",
        ))
    if type(target) is not int or target < 1:
        findings.append(ChapterPreDraftQualityFinding(
            code="PREDRAFT_TARGET_WORD_COUNT_INVALID", severity="critical",
            message="目标字数必须是正整数。", repair_hint="提供合法的本次生成目标字数，不套用默认配额。",
            path="quality_targets.target_word_count",
        ))
    if "scenes" in bundle.required_context_keys and not bundle.scenes:
        findings.append(ChapterPreDraftQualityFinding(
            code="PREDRAFT_REQUIRED_SCENES_MISSING", severity="critical",
            message="当前任务声明需要场景，但未提供场景输入。",
            repair_hint="提供当前任务的真实场景或修正任务声明，不编造场景职责。", path="scenes",
        ))
    acceptance = _mapping(bundle.acceptance_contract)
    proposals = _mapping(_mapping(acceptance.get("source_authority_channels")).get("ai_proposals"))
    planning = _mapping(bundle.chapter_contract.get("planning_intent")) or bundle.chapter_contract
    candidates = [bundle.chapter.get(key) for key in ("goal", "opening_situation", "main_conflict", "hook_description")]
    candidates.extend(item.get("value") for item in _sequence_of_mappings(acceptance.get("planning_intent")))
    candidates.extend(item.get("value") for item in _sequence_of_mappings(acceptance.get("must_deliver")))
    candidates.extend(scene.get("purpose") for scene in bundle.scenes)
    candidates.extend([planning, proposals.get("chapter_contract"), proposals.get("chapter_metadata")])
    # Structural metadata/authority labels alone are not a writable plan.
    def has_content(value: Any) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, Mapping):
            return any(has_content(item) for key, item in value.items()
                if key not in {"authority", "schema_version", "source", "source_id", "source_hash"})
        if isinstance(value, (list, tuple)):
            return any(has_content(item) for item in value)
        return False

    if not any(has_content(value) for value in candidates):
        findings.append(ChapterPreDraftQualityFinding(
            code="PREDRAFT_WRITABLE_PLAN_MISSING", severity="critical",
            message="没有章节目标，也没有可写的实际规划内容。",
            repair_hint="提供真实目标或场景规划，不用默认开篇或尾钩填空。", path="chapter.goal",
        ))


def _append_missing_context_findings(
    findings: list[ChapterPreDraftQualityFinding],
    missing_context_keys: Sequence[str],
) -> None:
    for key in missing_context_keys:
        if key == "scenes.methodology_contract":
            continue  # Optional/diagnostic/required is resolved from actual provenance below.
        findings.append(ChapterPreDraftQualityFinding(
            code="PREDRAFT_CONTEXT_UNKNOWN", severity="minor",
            message=f"输入未提供该上下文，适用性未知：{key}",
            repair_hint="核对真实来源；不要自动补写职责、技巧或剧情配方。", path=key,
        ))


def _append_methodology_findings(
    findings: list[ChapterPreDraftQualityFinding], bundle: ChapterGenerationInputBundle,
) -> None:
    acceptance = _mapping(bundle.acceptance_contract)
    channels = _mapping(acceptance.get("source_authority_channels"))
    if (bundle.schema_version != "chapter-generation-input.v2"
        or acceptance.get("schema_version") != "chapter-acceptance-contract.v2"):
        return
    # The builder retains the original per-field source receipts here. A raw
    # mode/required key in an old bundle is not proof of author selection.
    mode = resolve_methodology_contract_mode(SimpleNamespace(
        metadata_json=_mapping(channels.get("explicit_creator_requirements")),
    ))
    if mode == "off":
        return
    scenes = bundle.scenes if mode == "strict" else _sequence_of_mappings(
        _mapping(channels.get("ai_proposals")).get("scene_methodology_contracts")
    )
    for scene in scenes:
        if scene.get("methodology_contract"):
            continue
        findings.append(ChapterPreDraftQualityFinding(
            code="PREDRAFT_METHODOLOGY_CONTRACT_MISSING",
            severity="major" if mode == "strict" else "minor",
            message="显式选定的方法论未提供场景合同。",
            repair_hint=(
                "按已授权的方法论补齐合同，不改已发生事实。"  # noqa: RUF001
                if mode == "strict"
                else "仅记录诊断；不补写或强制落实方法论。"  # noqa: RUF001
            ),
            path=f"scenes.{scene.get('scene_number')}.methodology_contract",
        ))


def _append_source_constraint_findings(
    findings: list[ChapterPreDraftQualityFinding], bundle: ChapterGenerationInputBundle,
) -> None:
    from bestseller.services.chapter_generation_input_builder import _has_guarded_adopted_source

    channels = _mapping(_mapping(bundle.acceptance_contract).get("source_authority_channels"))
    if (not channels or bundle.schema_version != "chapter-generation-input.v2"
        or bundle.acceptance_contract.get("schema_version") != "chapter-acceptance-contract.v2"):
        findings.append(ChapterPreDraftQualityFinding(
            code="PREDRAFT_SOURCE_AUTHORITY_UNKNOWN", severity="minor",
            message="旧输入没有来源分层，不能把 must_deliver 或默认门禁升为作者硬要求。",
            repair_hint="重建来源分层输入；未知保持未知，不补开篇或收尾配方。",
            path="acceptance_contract.source_authority_channels",
        ))
        return
    document = bundle.model_dump(mode="python")
    chapter_number = bundle.chapter.get("chapter_number")
    explicit = _mapping(channels.get("explicit_creator_requirements"))
    for name, value in explicit.items():
        contract = _mapping(value)
        field_sources = _mapping(contract.get("field_sources"))
        # These are existing creation-intent fields, not inferred prose duties.
        if name == "creation_intent_contract":
            if field_sources.get("language") in {"explicit", "user_explicit"}:
                expected, actual = contract.get("language"), bundle.project.get("language")
                if expected and actual:
                    from bestseller.services.writing_profile import normalize_language

                    if normalize_language(str(expected)) != normalize_language(str(actual)):
                        findings.append(ChapterPreDraftQualityFinding(
                            code="PREDRAFT_EXPLICIT_REQUIREMENT_CONFLICT", severity="critical",
                            message="输入语言与创作者明确选择的语言冲突。",
                            repair_hint="按明确作者合同修正输入，不改变作者选择。", path="project.language",
                        ))
            if (field_sources.get("chapter_count") in {"explicit", "user_explicit"}
                and type(contract.get("chapter_count")) is int and type(chapter_number) is int
                and chapter_number > contract["chapter_count"]):
                findings.append(ChapterPreDraftQualityFinding(
                    code="PREDRAFT_EXPLICIT_REQUIREMENT_CONFLICT", severity="critical",
                    message="当前章节超出创作者明确限定的章节范围。",
                    repair_hint="核对作者范围或取得新的明确合同，不自动延长。", path="chapter.chapter_number",
                ))
        clauses = [(clause, False) for clause in
                   _sequence_of_mappings(contract.get("hard_constraints"))]
        if field_sources.get("hook_spec") == "explicit":
            clauses.extend((clause, True) for clause in _sequence_of_mappings(
                _mapping(contract.get("hook_spec")).get("hard_constraints")
            ))
        for index, (clause, explicit_hook) in enumerate(clauses):
            # A default contract or a clause merely named 'hard' is not consent.
            if (clause.get("source") not in {"user", "explicit", "user_explicit"}
                and not (explicit_hook and clause.get("source") is None)
                and field_sources.get("hard_constraints") not in {"explicit", "user_explicit"}):
                continue
            if clause.get("hard") is not True:
                continue
            scope = _mapping(clause.get("scope"))
            if "chapter_numbers" in scope:
                chapters = scope["chapter_numbers"]
                if (not isinstance(chapters, (list, tuple))
                    or any(type(number) is not int for number in chapters)):
                    _append_unknown_constraint(findings, f"{name}.hard_constraints.{index}")
                    continue
                if chapter_number not in chapters:
                    continue
            _check_declared_constraint(findings, document, clause,
                source_path=f"explicit_creator_requirements.{name}.hard_constraints.{index}")
    # Only upstream-guarded adopted assertions may constrain current facts.
    # Natural-language facts remain visible to the actual identity/continuity
    # validators; this pre-draft check never guesses a semantic contradiction.
    for category, records in _mapping(channels.get("established_facts")).items():
        for index, record in enumerate(_sequence_of_mappings(records)):
            if not _has_guarded_adopted_source(record, project_id=bundle.project.get("id")):
                continue
            for assertion in _sequence_of_mappings(record.get("input_assertions")):
                _check_declared_constraint(findings, document, assertion,
                    source_path=f"established_facts.{category}.{index}", fact=True)


def _append_unknown_constraint(findings: list[ChapterPreDraftQualityFinding], path: str) -> None:
    findings.append(ChapterPreDraftQualityFinding(
        code="PREDRAFT_CONSTRAINT_UNKNOWN", severity="minor",
        message="该来源条款无法在当前结构化输入中确定核验。",
        repair_hint="保留原条款供来源审查；不得猜测通过、违规或新增替代配方。", path=path,
    ))


def _check_declared_constraint(
    findings: list[ChapterPreDraftQualityFinding], document: Mapping[str, Any],
    clause: Mapping[str, Any], *, source_path: str, fact: bool = False,
) -> None:
    """Evaluate only a source-declared comparison, never infer one from prose."""
    path = clause.get("path")
    operation = clause.get("operator")
    if (not isinstance(path, str) or not path or len(path) > 256
        or operation not in {"equals", "contains", "present"}
        or (operation != "present" and "expected" not in clause)):
        _append_unknown_constraint(findings, source_path)
        return
    actual: Any = document
    found = True
    for component in path.split("."):
        if isinstance(actual, Mapping) and component in actual:
            actual = actual[component]
        elif isinstance(actual, (list, tuple)) and component.isdigit() and int(component) < len(actual):
            actual = actual[int(component)]
        else:
            found = False
            break
    if operation == "present":
        matched = found and actual not in (None, "", [], {})
    elif not found:
        _append_unknown_constraint(findings, source_path)
        return
    elif operation == "equals":
        matched = actual == clause["expected"]
    elif isinstance(actual, (list, tuple, str, Mapping)):
        expected = clause["expected"]
        if isinstance(actual, (str, Mapping)) and not isinstance(expected, str):
            _append_unknown_constraint(findings, source_path)
            return
        matched = expected in actual
    else:
        _append_unknown_constraint(findings, source_path)
        return
    if not matched:
        findings.append(ChapterPreDraftQualityFinding(
            code="PREDRAFT_ADOPTED_FACT_CONFLICT" if fact else "PREDRAFT_EXPLICIT_REQUIREMENT_CONFLICT",
            severity="critical", message=f"输入与明确来源条款冲突：{source_path}",
            repair_hint="核对原来源并修正冲突；保留身份与连续性，不用默认配方替代。", path=path,
        ))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))
