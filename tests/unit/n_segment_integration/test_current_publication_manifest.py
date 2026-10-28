"""RED contract for current title-only manifest binding, not title adoption."""

from copy import deepcopy

import pytest

from bestseller.services import native_publication_title as publication

pytestmark = pytest.mark.unit


def validate(bundle):
    validator = getattr(publication, "validate_current_manifest", None)
    assert callable(validator), "missing current manifest binding; v1 _manifest must remain historical"
    return validator(
        bundle.before, bundle.manifest, title=bundle.project.title, full_text=bundle.full_text,
        actual_project=bundle.project, native_plan=bundle.native_plan, canonical=bundle.canonical,
    )


@pytest.mark.parametrize("count", range(4, 9))
def test_title_only_transition_binds_actual_project_and_complete_native_plan(source_bundle, count):
    bundle = source_bundle(count)
    before = deepcopy(vars(bundle))
    assert validate(bundle) is None
    assert vars(bundle) == before


@pytest.mark.parametrize("fault", [
    "caller_n", "foreign_plan", "unapproved_plan", "plan_prefix", "plan_reordered",
    "missing_tail_scene", "tail_marker_hash", "tail_marker_artifact", "foreign_source",
    "tail_source_missing", "duplicate_draft", "duplicate_chapter", "body_changed",
    "genre_changed", "stale_project_title", "scope_changed", "canonical_changed",
])
def test_manifest_rejects_partial_foreign_or_changed_sources(source_bundle, fault):
    bundle = source_bundle()
    if fault == "caller_n":
        bundle.project.target_chapters = 4
        bundle.manifest["segment_count"] = 8
    elif fault == "foreign_plan":
        bundle.native_plan["project_id"] = bundle.canonical["id"]
    elif fault == "unapproved_plan":
        bundle.native_plan["status"] = "draft"
    elif fault == "plan_prefix":
        bundle.native_plan["content"]["chapters"] = bundle.native_plan["content"]["chapters"][:4]
    elif fault == "plan_reordered":
        bundle.native_plan["content"]["chapters"].reverse()
    elif fault == "missing_tail_scene":
        bundle.native_plan["content"]["chapters"][-1]["scenes"] = []
    elif fault == "tail_marker_hash":
        for manifest in (bundle.before, bundle.manifest):
            manifest["sources"][-1]["lineage"]["source_plan_binding"]["source_content_sha256"] = "0" * 64
    elif fault == "tail_marker_artifact":
        for manifest in (bundle.before, bundle.manifest):
            manifest["sources"][-1]["lineage"]["source_plan_binding"]["artifact_id"] = bundle.canonical["id"]
    elif fault == "foreign_source":
        for manifest in (bundle.before, bundle.manifest):
            manifest["sources"][-1]["lineage"]["project_id"] = bundle.canonical["id"]
    elif fault == "tail_source_missing":
        for manifest in (bundle.before, bundle.manifest):
            manifest["sources"].pop()
    elif fault in {"duplicate_draft", "duplicate_chapter"}:
        key = "draft_id" if fault == "duplicate_draft" else "chapter_id"
        for manifest in (bundle.before, bundle.manifest):
            manifest["sources"][-1][key] = manifest["sources"][0][key]
    elif fault == "body_changed":
        bundle.full_text += "额外改稿。"
    elif fault == "genre_changed":
        bundle.manifest["project"]["genre"] = "悬疑"
    elif fault == "stale_project_title":
        bundle.project.title = "另一次采用的新标题"
    elif fault == "scope_changed":
        for manifest in (bundle.before, bundle.manifest):
            manifest["execution_scope_id"] += ":different"
    else:
        bundle.canonical["content"]["chapter_count"] = 4
    with pytest.raises(ValueError):
        validate(bundle)


@pytest.mark.parametrize("missing", ["project", "native_plan"])
def test_caller_manifest_target_alone_is_not_authority(source_bundle, missing):
    bundle = source_bundle(6)
    setattr(bundle, missing, None)
    validator = getattr(publication, "validate_current_manifest", None)
    assert callable(validator), "missing current manifest binding"
    with pytest.raises(ValueError):
        validator(bundle.before, bundle.manifest, title=bundle.manifest["project"]["title"],
                  full_text=bundle.full_text, actual_project=bundle.project,
                  native_plan=bundle.native_plan, canonical=bundle.canonical)


def test_original_four_manifest_validator_is_still_exact(source_bundle):
    bundle = source_bundle(4, historical=True)
    before = deepcopy(vars(bundle))
    assert publication._manifest(
        bundle.before, bundle.manifest, title=bundle.project.title, full_text=bundle.full_text,
        project_id=bundle.project.id, canonical=bundle.canonical,
    ) is None
    assert vars(bundle) == before


@pytest.mark.parametrize("count", range(5, 9))
def test_historical_manifest_authorization_is_not_upgraded_to_n(source_bundle, count):
    bundle = source_bundle(count, historical=True)
    with pytest.raises(publication.PublicationTitleError, match="manifest_identity"):
        publication._manifest(
            bundle.before, bundle.manifest, title=bundle.project.title, full_text=bundle.full_text,
            project_id=bundle.project.id, canonical=bundle.canonical,
        )
