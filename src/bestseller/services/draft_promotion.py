"""Transactional quality-gated promotion for exact draft versions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.enums import DraftPromotionState
from bestseller.domain.promotion import (
    PromotionCandidate,
    PromotionEvidence,
    is_promotion_eligible,
    promotion_rank_key,
    validate_promotion_transition,
)
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    DraftPromotionDecisionModel,
    QualityScoreModel,
    SceneCardModel,
    SceneDraftVersionModel,
)

logger = logging.getLogger(__name__)

DraftKind = Literal["scene", "chapter"]
DraftModel = type[SceneDraftVersionModel] | type[ChapterDraftVersionModel]
DraftInstance = SceneDraftVersionModel | ChapterDraftVersionModel


@dataclass(frozen=True)
class PromotionOutcome:
    changed: bool
    reason: str
    promoted_draft_id: UUID | None = None
    incumbent_draft_id: UUID | None = None


@dataclass(frozen=True)
class _EligibleDraft:
    draft: DraftInstance
    score: QualityScoreModel
    candidate: PromotionCandidate


def draft_supersession_codes(
    *,
    origin: str,
    took_current: bool,
    chars: int = 0,  # 已废弃：见下方说明，保留仅为兼容既有调用点
    supersedes_version: int | None = None,
    hold_reason: str | None = None,
) -> list[str]:
    """一条新稿为什么产生、有没有接管 current —— 写进 promotion_reason_codes。

    2026-08-20 真机《罚我守坟》定罪：全库 518 个 chapter_draft_versions 的
    promotion_state 100% 停在 candidate，promotion_score /
    promotion_reason_codes / promoted_at 一个都没写过，
    draft_promotion_decisions 表 0 行——本模块这套按分选优在生产里从未跑过。
    真正决定「哪一稿上线」的是四处构造点各自的
    「先把当前那版翻 False，再插一条新的」，其中两处带条件
    （not _keeps_prior_draft / not quality_gate_rejected_current_promotion），
    但条件的结果没有任何地方记录。于是 ch20 五个版本 AI 味 84/88/32/68/96，
    上线的是 68 那版而手上有 32 的，**没有一行记录说明为什么**。

    本函数只补记账，不参与任何选优判定。返回的是纯字符串码，因为
    promotion_reason_codes 的既有消费方按字符串码读。
    """

    # ⚠️ 记的是**写入时的意图**，不是最终是否上线。2026-08-22 真机《书院笔仙》
    # ch15 v2 回执写着 no 而它就是当前稿——该版后来通过别的路径成了 current。
    # 最终状态由 `chapter_draft_versions.is_current` 表达；用一个字段冒充两件事
    # 会让以后的归因读错，所以字段名必须自带「写入时」这层含义。
    # ⚠️ 刻意**不记 chars**：章级稿在创建之后有 6 条路径原地改写 content_md
    # （micro-trim / 钩子桥接 / deslop / AI味补丁 / 复检补丁 / 终局补丁），
    # 回执是创建那一刻算的，真机上 24/111 个版本与行里的实际长度对不上。
    # 行的真实长度永远可以 `length(content_md)` 查到——一份会过期的副本
    # 比没有更糟（「同一事实住两地，后写的赢」）。
    codes = [
        f"origin:{origin}",
        f"wrote_as_current:{'yes' if took_current else 'no'}",
    ]
    if supersedes_version is not None:
        codes.append(f"supersedes:v{int(supersedes_version)}")
    if not took_current and hold_reason:
        codes.append(f"hold:{hold_reason}")
    return codes


def _models_for_kind(draft_kind: DraftKind) -> tuple[type, type, object, object]:
    if draft_kind == "scene":
        return (
            SceneCardModel,
            SceneDraftVersionModel,
            SceneDraftVersionModel.scene_card_id,
            QualityScoreModel.scene_draft_version_id,
        )
    if draft_kind == "chapter":
        return (
            ChapterModel,
            ChapterDraftVersionModel,
            ChapterDraftVersionModel.chapter_id,
            QualityScoreModel.chapter_draft_version_id,
        )
    raise ValueError(f"Unsupported draft_kind: {draft_kind}")


#: 诚实轴：只有这四个维度是对正文本身的判断。
#: ``score_dialogue``(=continuity) 与 ``score_hook`` 是**关键词回声公式**
#: （2026-08-22 定案），2026-08-23 已让它们不能否决 chapter verdict——但提升
#: 路径当时没跟着改，于是同一批回声分继续从这里毙掉每一次上架。
#: 真机 169 份质量分：含回声时最弱维 ≥0.75 的有 **0 份**；只用诚实轴则
#: 141 份（83%），诚实轴均分 0.858、106 份（63%）≥0.85。
def _core_scores(score: QualityScoreModel) -> tuple[float, ...]:
    values = (
        score.score_goal,
        score.score_conflict,
        score.score_emotion,
        score.score_style,
    )
    return tuple(float(value) for value in values if value is not None)


def _core_overall(core_scores: tuple[float, ...]) -> float | None:
    """诚实轴合成分。空则 None（调用方退回旧口径，不静默放行）。"""

    return sum(core_scores) / len(core_scores) if core_scores else None


def _blocking_codes(evidence: dict[str, object]) -> tuple[str, ...]:
    raw = evidence.get("blocking_codes", evidence.get("blockers", ()))
    if not isinstance(raw, (list, tuple, set)):
        return (str(raw),) if raw else ()
    return tuple(str(item) for item in raw if str(item).strip())


def _eligible_row(
    draft: DraftInstance,
    score: QualityScoreModel,
    *,
    min_overall: float,
    min_core: float,
) -> _EligibleDraft | None:
    if not score.judge_key or not score.judge_key.strip():
        return None
    exact_score_draft_id = (
        score.scene_draft_version_id
        if isinstance(draft, SceneDraftVersionModel)
        else score.chapter_draft_version_id
    )
    evidence_json = dict(score.evidence_summary or {})
    core_scores = _core_scores(score)
    evidence = PromotionEvidence(
        draft_id=draft.id,
        score_draft_id=exact_score_draft_id,
        score_overall=float(score.score_overall),
        core_scores=core_scores,
        hard_gates_passed=evidence_json.get("hard_gates_passed") is True,
        blocking_codes=_blocking_codes(evidence_json),
        core_overall=_core_overall(core_scores),
    )
    # ⚠️ 2026-08-23 我曾把资格判据改成认 16 维商业判官（理由「真尺子掌权」），
    # 2026-08-24 用数据推翻了那个前提并撤回：
    #   * 该判官对本书 149 份判决 **0 通过**；
    #   * 拿它去跑 **10 本真实出版小说的章节，10/10 全判 fail**
    #     （0.42–0.72，与我们自己的 0.538 均值完全重叠）。
    # 在它的通过线上零区分力 —— 它是优秀的**批评者**（意见带引文，
    # rewrite_plan 已接进重写反馈），但不是合格的**验收尺**，不该握否决权。
    # 达标改看诚实轴（见 domain/promotion.py 的 core_overall）。
    if not is_promotion_eligible(
        evidence,
        min_overall=min_overall,
        min_core=min_core,
    ):
        return None
    return _EligibleDraft(
        draft=draft,
        score=score,
        candidate=PromotionCandidate(
            draft_id=draft.id,
            version_no=draft.version_no,
            score_overall=float(score.score_overall),
            core_scores=core_scores,
        ),
    )


def select_best_eligible_draft(candidates: list[_EligibleDraft]) -> _EligibleDraft | None:
    """Select overall, then weakest core score, then earlier draft version."""
    if not candidates:
        return None
    return max(candidates, key=lambda item: promotion_rank_key(item.candidate))


def _score_recency_key(score: QualityScoreModel) -> tuple[int, datetime, str]:
    return (
        int(score.evaluation_round or 1),
        score.created_at or datetime.min.replace(tzinfo=UTC),
        str(score.id),
    )


async def transition_draft_state(
    session: AsyncSession,
    *,
    project_id: UUID,
    draft_kind: DraftKind,
    draft_id: UUID,
    to_state: DraftPromotionState,
    decision_source: str,
    actor: str | None = None,
    reason: str | None = None,
    evidence: dict[str, object] | None = None,
    reason_codes: list[str] | None = None,
    quality_score_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
    promotion_score: float | None = None,
    metadata: dict[str, object] | None = None,
) -> bool:
    """Transition one exact draft and append one idempotent audit decision."""
    _, draft_model, _, _ = _models_for_kind(draft_kind)
    draft = (
        await session.execute(
            select(draft_model).where(draft_model.id == draft_id).with_for_update()
        )
    ).scalar_one_or_none()
    if draft is None or draft.project_id != project_id:
        raise ValueError("Draft not found in project")
    from_state = DraftPromotionState(draft.promotion_state)
    if from_state is to_state:
        return False
    validate_promotion_transition(
        from_state,
        to_state,
        decision_source=decision_source,
        actor=actor,
        reason=reason,
        evidence=evidence,
    )
    async with session.begin_nested():
        draft.promotion_state = to_state.value
        draft.promotion_reason_codes = list(reason_codes or [])
        if promotion_score is not None:
            draft.promotion_score = promotion_score
        now = datetime.now(UTC)
        if to_state is DraftPromotionState.PROMOTED:
            draft.promoted_at = now
        if to_state is DraftPromotionState.QUARANTINED:
            draft.quarantined_at = now
        promotion_metadata = dict(draft.promotion_metadata or {})
        promotion_metadata.update(metadata or {})
        draft.promotion_metadata = promotion_metadata
        session.add(
            _decision(
                project_id=project_id,
                draft_kind=draft_kind,
                draft_id=draft_id,
                quality_score_id=quality_score_id,
                workflow_run_id=workflow_run_id,
                from_state=from_state,
                to_state=to_state,
                decision_source=decision_source,
                reason_codes=list(reason_codes or []),
                promotion_score=promotion_score,
                actor=actor,
                reason=reason,
                evidence=dict(evidence or {}),
                metadata=dict(metadata or {}),
            )
        )
        await session.flush()
    return True


async def mark_candidate_under_review(
    session: AsyncSession,
    *,
    project_id: UUID,
    draft_kind: DraftKind,
    draft_id: UUID,
    workflow_run_id: UUID | None = None,
) -> bool:
    return await transition_draft_state(
        session,
        project_id=project_id,
        draft_kind=draft_kind,
        draft_id=draft_id,
        to_state=DraftPromotionState.UNDER_REVIEW,
        decision_source="quality_gate",
        evidence={"action": "begin_quality_review"},
        reason_codes=["quality_review_started"],
        workflow_run_id=workflow_run_id,
    )


async def mark_draft_eligible(
    session: AsyncSession,
    *,
    project_id: UUID,
    draft_kind: DraftKind,
    draft_id: UUID,
    quality_score_id: UUID,
    min_overall: float = 0.85,
    min_core: float = 0.80,
    workflow_run_id: UUID | None = None,
) -> bool:
    _, draft_model, _, score_draft_column = _models_for_kind(draft_kind)
    row = (
        await session.execute(
            select(draft_model, QualityScoreModel)
            .join(QualityScoreModel, score_draft_column == draft_model.id)
            .where(draft_model.id == draft_id, QualityScoreModel.id == quality_score_id)
        )
    ).one_or_none()
    if row is None:
        raise ValueError("Exact draft quality score not found")
    draft, score = row
    eligible = _eligible_row(
        draft,
        score,
        min_overall=min_overall,
        min_core=min_core,
    )
    if eligible is None:
        raise ValueError("Draft quality evidence is not promotion eligible")
    return await transition_draft_state(
        session,
        project_id=project_id,
        draft_kind=draft_kind,
        draft_id=draft_id,
        to_state=DraftPromotionState.ELIGIBLE,
        decision_source="quality_gate",
        evidence=dict(score.evidence_summary or {}),
        reason_codes=["quality_eligible"],
        quality_score_id=score.id,
        workflow_run_id=workflow_run_id,
        promotion_score=float(score.score_overall),
        metadata={"evaluation_round": score.evaluation_round, "judge_key": score.judge_key},
    )


async def quarantine_draft(
    session: AsyncSession,
    *,
    project_id: UUID,
    draft_kind: DraftKind,
    draft_id: UUID,
    reason_codes: list[str],
    evidence: dict[str, object],
    workflow_run_id: UUID | None = None,
) -> bool:
    if not reason_codes or not evidence:
        raise ValueError("Quarantine requires reason codes and evidence")
    return await transition_draft_state(
        session,
        project_id=project_id,
        draft_kind=draft_kind,
        draft_id=draft_id,
        to_state=DraftPromotionState.QUARANTINED,
        decision_source="quality_gate",
        evidence=evidence,
        reason_codes=reason_codes,
        workflow_run_id=workflow_run_id,
    )


def _decision(
    *,
    project_id: UUID,
    draft_kind: DraftKind,
    draft_id: UUID,
    quality_score_id: UUID | None,
    workflow_run_id: UUID | None,
    from_state: DraftPromotionState,
    to_state: DraftPromotionState,
    decision_source: str,
    reason_codes: list[str],
    promotion_score: float | None,
    actor: str | None,
    reason: str | None,
    evidence: dict[str, object],
    metadata: dict[str, object],
) -> DraftPromotionDecisionModel:
    return DraftPromotionDecisionModel(
        project_id=project_id,
        scene_draft_version_id=draft_id if draft_kind == "scene" else None,
        chapter_draft_version_id=draft_id if draft_kind == "chapter" else None,
        quality_score_id=quality_score_id,
        workflow_run_id=workflow_run_id,
        from_state=from_state.value,
        to_state=to_state.value,
        decision_source=decision_source,
        reason_codes=reason_codes,
        promotion_score=promotion_score,
        actor=actor,
        reason=reason,
        evidence_json=evidence,
        metadata_json=metadata,
    )


async def _advance_to_promoted(
    session: AsyncSession,
    *,
    draft_kind: DraftKind,
    draft: DraftInstance,
    score: QualityScoreModel,
    workflow_run_id: UUID | None,
) -> None:
    current = DraftPromotionState(draft.promotion_state)
    path: list[DraftPromotionState]
    if current in {DraftPromotionState.LEGACY_UNVERIFIED, DraftPromotionState.CANDIDATE}:
        path = [DraftPromotionState.UNDER_REVIEW, DraftPromotionState.ELIGIBLE]
    elif current is DraftPromotionState.UNDER_REVIEW:
        path = [DraftPromotionState.ELIGIBLE]
    elif current is DraftPromotionState.ELIGIBLE:
        path = []
    else:
        raise ValueError(f"Draft in state {current.value} cannot be auto-promoted")
    path.append(DraftPromotionState.PROMOTED)

    evidence = dict(score.evidence_summary or {})
    for target in path:
        validate_promotion_transition(
            current,
            target,
            decision_source="quality_gate",
            evidence=evidence,
        )
        session.add(
            _decision(
                project_id=draft.project_id,
                draft_kind=draft_kind,
                draft_id=draft.id,
                quality_score_id=score.id,
                workflow_run_id=workflow_run_id,
                from_state=current,
                to_state=target,
                decision_source="quality_gate",
                reason_codes=["quality_eligible"],
                promotion_score=float(score.score_overall),
                actor=None,
                reason=None,
                evidence=evidence,
                metadata={"evaluation_round": score.evaluation_round, "judge_key": score.judge_key},
            )
        )
        draft.promotion_state = target.value
        current = target
    draft.promotion_score = float(score.score_overall)
    draft.promotion_reason_codes = ["quality_eligible"]
    draft.promoted_at = datetime.now(UTC)
    metadata = dict(draft.promotion_metadata or {})
    metadata["quality_score_id"] = str(score.id)
    metadata["evaluation_round"] = score.evaluation_round
    metadata["judge_key"] = score.judge_key
    draft.promotion_metadata = metadata


async def promote_best_draft(
    session: AsyncSession,
    *,
    project_id: UUID,
    draft_kind: DraftKind,
    parent_id: UUID,
    judge_key: str,
    min_overall: float = 0.85,
    min_core: float = 0.80,
    workflow_run_id: UUID | None = None,
) -> PromotionOutcome:
    """Promote the best exact-version eligible draft under a parent row lock.

    The caller owns the outer transaction. This service flushes but never
    commits; the savepoint makes a partial-index race safe for that transaction.
    """
    parent_model, draft_model, parent_column, score_draft_column = _models_for_kind(
        draft_kind
    )
    if not judge_key.strip():
        raise ValueError("judge_key is required for deterministic promotion")
    parent = (
        await session.execute(
            select(parent_model).where(parent_model.id == parent_id).with_for_update()
        )
    ).scalar_one_or_none()
    if parent is None or parent.project_id != project_id:
        raise ValueError("Promotion parent not found in project")

    incumbent = (
        await session.execute(
            select(draft_model).where(
                parent_column == parent_id,
                draft_model.promotion_state == DraftPromotionState.PROMOTED.value,
            )
        )
    ).scalar_one_or_none()

    rows = (
        await session.execute(
            select(draft_model, QualityScoreModel)
            .join(QualityScoreModel, score_draft_column == draft_model.id)
            .where(
                parent_column == parent_id,
                QualityScoreModel.judge_key == judge_key,
            )
        )
    ).all()

    latest_score_by_draft: dict[UUID, tuple[DraftInstance, QualityScoreModel]] = {}
    for draft, score in rows:
        prior = latest_score_by_draft.get(draft.id)
        if prior is None or _score_recency_key(score) > _score_recency_key(prior[1]):
            latest_score_by_draft[draft.id] = (draft, score)

    eligible_by_draft: dict[UUID, _EligibleDraft] = {}
    incumbent_ranked: _EligibleDraft | None = None
    for draft, score in latest_score_by_draft.values():
        ranked = _eligible_row(
            draft,
            score,
            min_overall=min_overall,
            min_core=min_core,
        )
        if ranked is None:
            continue
        eligible_by_draft[draft.id] = ranked
        if draft.promotion_state == DraftPromotionState.PROMOTED.value:
            if incumbent_ranked is None or promotion_rank_key(
                ranked.candidate
            ) > promotion_rank_key(incumbent_ranked.candidate):
                incumbent_ranked = ranked

    candidates = [
        ranked
        for ranked in eligible_by_draft.values()
        if ranked.draft.promotion_state
        in {
            DraftPromotionState.LEGACY_UNVERIFIED.value,
            DraftPromotionState.CANDIDATE.value,
            DraftPromotionState.UNDER_REVIEW.value,
            DraftPromotionState.ELIGIBLE.value,
        }
    ]
    if not candidates:
        return PromotionOutcome(
            changed=False,
            reason="no_eligible_candidate",
            promoted_draft_id=incumbent.id if incumbent else None,
            incumbent_draft_id=incumbent.id if incumbent else None,
        )
    selected = select_best_eligible_draft(candidates)
    assert selected is not None
    if incumbent is not None:
        if incumbent_ranked is None or promotion_rank_key(
            selected.candidate
        ) <= promotion_rank_key(incumbent_ranked.candidate):
            return PromotionOutcome(
                changed=False,
                reason="incumbent_is_better_or_unscored",
                promoted_draft_id=incumbent.id,
                incumbent_draft_id=incumbent.id,
            )

    try:
        async with session.begin_nested():
            if incumbent is not None:
                replacement_evidence = {"replacement_draft_id": str(selected.draft.id)}
                validate_promotion_transition(
                    DraftPromotionState.PROMOTED,
                    DraftPromotionState.SUPERSEDED,
                    decision_source="replacement",
                    evidence=replacement_evidence,
                )
                incumbent.promotion_state = DraftPromotionState.SUPERSEDED.value
                session.add(
                    _decision(
                        project_id=project_id,
                        draft_kind=draft_kind,
                        draft_id=incumbent.id,
                        quality_score_id=(
                            incumbent_ranked.score.id if incumbent_ranked else None
                        ),
                        workflow_run_id=workflow_run_id,
                        from_state=DraftPromotionState.PROMOTED,
                        to_state=DraftPromotionState.SUPERSEDED,
                        decision_source="replacement",
                        reason_codes=["better_candidate"],
                        promotion_score=(
                            incumbent_ranked.candidate.score_overall
                            if incumbent_ranked
                            else incumbent.promotion_score
                        ),
                        actor=None,
                        reason=None,
                        evidence=replacement_evidence,
                        metadata={},
                    )
                )
                await session.flush()
            await _advance_to_promoted(
                session,
                draft_kind=draft_kind,
                draft=selected.draft,
                score=selected.score,
                workflow_run_id=workflow_run_id,
            )
            await session.flush()
    except IntegrityError:
        conflict_incumbent = (
            await session.execute(
                select(draft_model).where(
                    parent_column == parent_id,
                    draft_model.promotion_state == DraftPromotionState.PROMOTED.value,
                )
            )
        ).scalar_one_or_none()
        return PromotionOutcome(
            changed=False,
            reason="unique_conflict_preserved_incumbent",
            promoted_draft_id=(conflict_incumbent.id if conflict_incumbent else None),
            incumbent_draft_id=(conflict_incumbent.id if conflict_incumbent else None),
        )

    return PromotionOutcome(
        changed=True,
        reason="promoted",
        promoted_draft_id=selected.draft.id,
        incumbent_draft_id=incumbent.id if incumbent else None,
    )


async def repromote_stranded_chapters(
    session: AsyncSession,
    *,
    project_id: UUID,
    workflow_run_id: UUID | None = None,
    min_overall: float = 0.85,
    min_core: float = 0.80,
) -> tuple[UUID, ...]:
    """把「当时被拒、现在够格」的章节稿重新送去提升，返回真的提升了的章 id。

    资格判据会随框架演进改变。改判据的那一刻，此前按旧口径判过的稿就永远停在
    ``under_review``——提升只在章节评审的那一轮尝试一次，没有任何路径会回头
    重评。真机（书 9，2026-08-24）：第 7 章在架稿诚实轴 0.860、第 11 章 0.863，
    都过了 0.85 的线，却因为评分发生在改判之前而卡死；同一本书里改判之后评
    的第 13、15 章一路走到 promoted。

    这个清扫**只会提升**：它复用 ``promote_best_draft``（自带资格校验、父行锁
    与幂等审计），不降级、不拦截、不改任何门的结论。单章失败只跳过该章。
    """

    chapter_ids = list(
        await session.scalars(
            select(ChapterModel.id)
            .where(ChapterModel.project_id == project_id)
            .order_by(ChapterModel.chapter_number.asc())
        )
    )
    promoted: list[UUID] = []
    for chapter_id in chapter_ids:
        has_promoted = await session.scalar(
            select(ChapterDraftVersionModel.id).where(
                ChapterDraftVersionModel.chapter_id == chapter_id,
                ChapterDraftVersionModel.promotion_state
                == DraftPromotionState.PROMOTED.value,
            )
        )
        if has_promoted is not None:
            continue
        scores = list(
            await session.scalars(
                select(QualityScoreModel)
                .join(
                    ChapterDraftVersionModel,
                    QualityScoreModel.chapter_draft_version_id
                    == ChapterDraftVersionModel.id,
                )
                .where(ChapterDraftVersionModel.chapter_id == chapter_id)
            )
        )
        judged = [s for s in scores if str(s.judge_key or "").strip()]
        if not judged:
            continue
        judge_key = str(max(judged, key=_score_recency_key).judge_key or "").strip()
        try:
            outcome = await promote_chapter_draft(
                session,
                project_id=project_id,
                chapter_id=chapter_id,
                judge_key=judge_key,
                min_overall=min_overall,
                min_core=min_core,
                workflow_run_id=workflow_run_id,
            )
        except Exception:  # noqa: BLE001 - 一章失败不该拖垮整轮清扫
            logger.warning(
                "repromote sweep failed for chapter %s", chapter_id, exc_info=True
            )
            continue
        if outcome.changed:
            promoted.append(chapter_id)
    return tuple(promoted)


async def promote_scene_draft(
    session: AsyncSession,
    *,
    project_id: UUID,
    scene_card_id: UUID,
    judge_key: str,
    min_overall: float = 0.85,
    min_core: float = 0.80,
    workflow_run_id: UUID | None = None,
) -> PromotionOutcome:
    return await promote_best_draft(
        session,
        project_id=project_id,
        draft_kind="scene",
        parent_id=scene_card_id,
        judge_key=judge_key,
        min_overall=min_overall,
        min_core=min_core,
        workflow_run_id=workflow_run_id,
    )


async def promote_chapter_draft(
    session: AsyncSession,
    *,
    project_id: UUID,
    chapter_id: UUID,
    judge_key: str,
    min_overall: float = 0.85,
    min_core: float = 0.80,
    workflow_run_id: UUID | None = None,
) -> PromotionOutcome:
    return await promote_best_draft(
        session,
        project_id=project_id,
        draft_kind="chapter",
        parent_id=chapter_id,
        judge_key=judge_key,
        min_overall=min_overall,
        min_core=min_core,
        workflow_run_id=workflow_run_id,
    )
