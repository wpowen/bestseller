"""Read exact adopted-body knowledge closure without extracting or changing it."""
from collections.abc import Mapping
from typing import Any

from bestseller.services.feedback import KNOWLEDGE_SOURCE_KEYS, _matching_feedback_receipt
from bestseller.services.knowledge_evidence_reconciliation import feedback_evidence_complete


def adopted_bible_status(chapter: Any, lineage: Mapping) -> str:  # noqa: ANN401
    """Match the server's optional legacy / explicit latest-receipt contract."""
    receipts = (chapter.metadata_json or {}).get('story_bible_update_receipts', [])
    if not receipts:
        return 'unknown'
    latest = (
        next((r for r in reversed(receipts) if isinstance(r, Mapping)), None)
        if isinstance(receipts, list) else None
    )
    if latest is None:
        return 'pending'
    if latest.get('status') in {'llm_error', 'rejected_source'}:
        return 'error'
    source = latest.get('source_lineage')
    if (latest.get('status') in {'ok', 'empty', 'already_applied'}
            and isinstance(source, Mapping)
            and all(lineage.get(key) is not None and source.get(key) == lineage[key]
                    for key in KNOWLEDGE_SOURCE_KEYS)):
        return 'closed'
    return 'pending'


def adopted_knowledge_status(
    chapter: Any, draft: Any, snapshot: Any, lineage: Mapping,
    *, require_bible: bool = False,
) -> dict[str, bool]:
    """A successful transport/application receipt is not evidence closure.

    The caller validates adoption against DB truth first. Both projections must
    belong to that exact owner, version and body; an empty valid extraction can
    close knowledge, while missing/failed/unresolved evidence cannot.
    """
    receipt = _matching_feedback_receipt(chapter, dict(lineage))
    feedback_ok = bool(receipt is not None and feedback_evidence_complete(
        chapter, receipt, draft.content_md,
    ))
    contract = (snapshot.facts or {}).get('snapshot_contract') if snapshot is not None else None
    snapshot_ok = bool(
        snapshot is not None and snapshot.project_id == chapter.project_id
        and snapshot.chapter_id == chapter.id and snapshot.chapter_number == chapter.chapter_number
        and snapshot.extraction_status == 'ok_' + str(lineage.get('adoption_state'))
        and isinstance(contract, Mapping) and contract.get('is_usable') is True
        and all(lineage.get(key) is not None and contract.get(key) == lineage[key]
                for key in KNOWLEDGE_SOURCE_KEYS)
    )
    bible_status = adopted_bible_status(chapter, lineage)
    bible_ok = bible_status == 'closed' or (not require_bible and bible_status == 'unknown')
    return {'feedback_complete': feedback_ok, 'snapshot_complete': snapshot_ok,
            'bible_complete': bible_ok, 'closed': feedback_ok and snapshot_ok and bible_ok}
