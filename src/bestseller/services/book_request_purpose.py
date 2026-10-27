"""Closed request families for this finite manuscript objective, not global policy."""

import re

PRIMARY = {
    "write": {"chapter_first_writer", "scene_writer"},
    "review": {"draft_continuity_hard_safety", "book_source_safety"},
    "knowledge": {"book_provisional_knowledge"},
    "ending": {"book_ending"},
    "admit": {
        "continuation_outline_rebase",
        "outline_commercial_judge",
        "planning_draft_admission",
        "outline_commercial_judge_consistency_adjudication",
    },
    "plan": {
        "planner_source_bound_expansion_semantic_review",
        "outline_commercial_judge",
        "outline_commercial_judge_consistency_adjudication",
    },
}
REPAIR = {
    "plan": {
        "planner_source_bound_expansion_evidence_repair",
        "planner_source_bound_world_revision",
        "planner_structured_field_correction",
    },
    "admit": {"continuation_source_hard_repair", "chapter_outline_prefix_surgical_repair"},
    "write": {"scene_writer_regen"},
    "review": {
        "chapter_rewrite",
        "chapter_rewrite_repair",
        "chapter_rewrite_quality_retrofit_repair",
        "chapter_localized_opening_repair",
        "chapter_localized_ending_repair",
    },
    "ending": {
        "chapter_rewrite",
        "chapter_rewrite_repair",
        "chapter_rewrite_quality_retrofit_repair",
        "draft_continuity_hard_safety",
        "book_provisional_knowledge",
        "chapter_localized_opening_repair",
        "chapter_localized_ending_repair",
    },
}


def classify(stage, template):
    stage = stage.rsplit(":", 1)[-1]
    if stage == "plan":
        batch = re.fullmatch(
            r"planner_volume_[1-9]\d*_chapter_outline_batch_([1-9]\d*)_([1-9]\d*)"
            r"(?:_(story_enhancer_route_repair|commercial_surgical_repair))?",
            template or "",
        )
        if batch and int(batch[1]) <= int(batch[2]):
            return "hard_repair" if batch[3] else "primary"
    if template in REPAIR.get(stage, set()):
        return "hard_repair"
    if stage == "plan" and re.fullmatch(
        r"planner_volume_\d+_(cast_expansion|world_disclosure|chapter_outline)_(story_enhancer_route_repair|commercial_surgical_repair)",
        template or "",
    ):
        return "hard_repair"
    if template in PRIMARY.get(stage, set()):
        return "primary"
    if stage == "plan" and re.fullmatch(
        r"planner_volume_\d+_(cast_expansion|world_disclosure|chapter_outline)", template or ""
    ):
        return "primary"
    raise ValueError("finite_unknown_request_family:" + str(stage) + ":" + str(template))


def primary_occurrences(stage, template):
    # Cast and world each have one source review in the normal new-window producer.
    return (
        2
        if stage.rsplit(":", 1)[-1] == "plan"
        and template == "planner_source_bound_expansion_semantic_review"
        else 1
    )
