"""RED contract: a general source checker must not widen the v1 paid operator."""

from copy import deepcopy

import pytest

from bestseller.services.native_title_operator import title_operator as op

pytestmark = pytest.mark.unit


def check(bundle, *, paid=False):
    checker = getattr(op, "check_current_snapshot", None)
    assert callable(checker), "missing general current-source snapshot checker; keep v1 historical"
    return checker(bundle.snapshot, bundle.snapshot["manifest"],
                   actual_project=bundle.project, native_plan=bundle.native_plan, paid=paid)


@pytest.mark.parametrize("count", range(4, 9))
@pytest.mark.parametrize("paid", [False, True])
def test_current_title_source_covers_every_legal_segment_without_mutation(source_bundle, count, paid):
    bundle = source_bundle(count)
    before = deepcopy(vars(bundle))
    assert check(bundle, paid=paid) is None
    assert vars(bundle) == before


@pytest.mark.parametrize("fault", [
    "missing_tail_source", "tail_knowledge", "tail_body_sha", "duplicate_tail_draft",
    "duplicate_tail_chapter", "wrong_scope", "foreign_canonical", "open_tail_review",
    "open_rewrite", "blocked_dispatch", "blocked_adoption", "caller_only_n", "tail_selected_plan",
])
def test_eighth_segment_and_existing_guard_failures_still_block(source_bundle, fault):
    bundle = source_bundle()
    snapshot = bundle.snapshot
    if fault == "missing_tail_source":
        snapshot["manifest"]["sources"].pop()
    elif fault == "tail_knowledge":
        snapshot["knowledge_closed"].pop()
    elif fault == "tail_body_sha":
        snapshot["manifest"]["sources"][-1]["lineage"]["source_body_sha256"] = "0" * 64
    elif fault == "duplicate_tail_draft":
        snapshot["manifest"]["sources"][-1]["draft_id"] = snapshot["manifest"]["sources"][0]["draft_id"]
    elif fault == "duplicate_tail_chapter":
        snapshot["manifest"]["sources"][-1]["chapter_id"] = snapshot["manifest"]["sources"][0]["chapter_id"]
    elif fault == "wrong_scope":
        snapshot["manifest"]["execution_scope_id"] = op.SCOPE
    elif fault == "foreign_canonical":
        snapshot["canonical_snapshot"]["project_id"] = op.PROJECT_ID
    elif fault == "open_tail_review":
        snapshot["chapters"][-1]["metadata"]["requires_human_review"] = True
    elif fault == "open_rewrite":
        snapshot["open_rewrite_tasks"] = [{"id": "unresolved-tail-task"}]
    elif fault == "blocked_dispatch":
        snapshot["dispatch_admissible"] = False
    elif fault == "blocked_adoption":
        snapshot["adoption_admissible"] = False
    elif fault == "tail_selected_plan":
        snapshot["chapters"][-1]["metadata"]["fanqie_outline_source_selection"]["projection_hash"] = "0" * 64
    else:
        bundle.project.target_chapters = 4
        snapshot["segment_count"] = 8
    with pytest.raises(ValueError):
        check(bundle, paid=fault == "blocked_adoption")


@pytest.mark.parametrize("count", [3, 9, True, 6.0, "6"])
def test_actual_project_segment_count_is_strict_and_bounded(source_bundle, count):
    bundle = source_bundle()
    bundle.project.target_chapters = count
    with pytest.raises(ValueError):
        check(bundle)


def test_v1_original_four_snapshot_still_has_its_exact_route(source_bundle):
    bundle = source_bundle(4, historical=True)
    before = deepcopy(bundle.snapshot)
    assert op.check_snapshot(bundle.snapshot, bundle.snapshot["manifest"]) is None
    assert op.check_snapshot(bundle.snapshot, bundle.snapshot["manifest"], paid=True) is None
    assert bundle.snapshot == before


@pytest.mark.parametrize("count", range(5, 9))
def test_old_four_scope_cannot_be_expanded_by_editing_its_manifest(source_bundle, count):
    bundle = source_bundle(count, historical=True)
    with pytest.raises(op.Stop, match="four_chapters_required"):
        op.check_snapshot(bundle.snapshot, bundle.snapshot["manifest"])


@pytest.mark.parametrize("count", range(4, 9))
def test_current_snapshot_cannot_enter_the_historical_paid_prepare(source_bundle, count):
    bundle = source_bundle(count)
    def no_messages(*args, **kwargs):
        raise AssertionError("historical paid request builder must not see a current N source")
    with pytest.raises(op.Stop, match="source_schema"):
        op.prepare(bundle.snapshot, bundle.snapshot["manifest"], no_messages)


@pytest.mark.parametrize("count", range(4, 9))
def test_current_context_cannot_borrow_old_paid_prepare_by_downgrading_schema(source_bundle, count):
    bundle = source_bundle(count)
    bundle.snapshot["schema"] = "title-operator-source.v1"
    def no_messages(*args, **kwargs):
        raise AssertionError("a current native project cannot borrow the original paid identity")
    with pytest.raises(op.Stop, match="project_identity"):
        op.prepare(bundle.snapshot, bundle.snapshot["manifest"], no_messages, paid=True)
