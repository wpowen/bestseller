from bestseller.services import conception


def _canonical_profile() -> dict[str, object]:
    return {
        "market": {"reader_promise": "在封锁扩大前重建边境废城"},
        "character": {"protagonist_core_drive": "守住城市居民的补给线"},
        "world": {"power_system_style": "可重组的流通规则"},
        "style": {"pov_type": "third-limited"},
        "serialization": {"opening_mandate": "先展示补给线断裂造成的损失"},
    }


def _canonical_spine() -> dict[str, str]:
    return {
        "who": "城市运营负责人",
        "wants": "在封锁扩大前重建边境废城",
        "why_now": "商路封锁切断了城市补给",
        "against": "封锁商路的盟约势力",
        "stakes": "城市居民将失去生存所需的物资",
        "question": "他能否重建流通规则并守住废城？",
    }


def _candidate(*, world_model: dict[str, object]) -> dict[str, object]:
    return {
        "title": "废城流转法则",
        "premise": "城市运营负责人读取并重组流通规则，重建边境废城。",
        "synopsis": "他必须在效率、民生与盟约之间做选择。",
        "tags": ["经营", "基建"],
        "writing_profile": _canonical_profile(),
        "story_spine": _canonical_spine(),
        "world_model": world_model,
    }


def test_repair_candidate_accepts_canonical_profile_and_uncompiled_world() -> None:
    assert conception._validate_materialized_repair_candidate_structure(
        _candidate(world_model={}),
        original_world_model={},
    ) == []


def test_repair_candidate_rejects_free_form_keys_before_profile_backfill() -> None:
    candidate = _candidate(world_model={})
    candidate["writing_profile"] = {
        "market": {"读者承诺": "重建城市"},
        "character": {"主角": "运营负责人"},
        "world": {"核心规则": "流通规则"},
        "style": {"叙事视角": "限知"},
        "serialization": {"开篇": "先展示损失"},
    }
    candidate["story_spine"] = {
        "主角": "运营负责人",
        "目标": "重建城市",
        "冲突": "商路封锁",
    }
    issues = conception._validate_materialized_repair_candidate_structure(
        candidate,
        original_world_model={},
    )
    assert "writing_profile_free_keys_only" in issues
    assert "story_spine_free_keys_only" in issues


def test_legacy_world_payload_keeps_shape_without_lineage_requirement() -> None:
    candidate = _candidate(world_model={"laws": ["规则改写会转移代价"]})
    assert conception._validate_materialized_repair_candidate_structure(
        candidate,
        original_world_model={"laws": ["旧规则"]},
    ) == []


def test_new_path_rejects_repair_inventing_formal_world_model() -> None:
    issues = conception._validate_materialized_repair_candidate_structure(
        _candidate(world_model={"laws": ["新造规则"]}),
        original_world_model={},
    )
    assert "world_model_must_remain_empty_until_compiled" in issues


def test_one_valid_section_cannot_mask_dropped_character_design():
    candidate = _candidate(world_model={})
    candidate["writing_profile"]["character"] = {"主角": "不会被生产schema读取"}
    issues = conception._validate_materialized_repair_candidate_structure(
        candidate, original_world_model={},
    )
    assert "writing_profile.character_has_no_canonical_values" in issues


def test_empty_tags_are_valid_and_profile_type_errors_are_rejected():
    candidate = _candidate(world_model={})
    candidate["tags"] = []
    assert conception._validate_materialized_repair_candidate_structure(
        candidate, original_world_model={},
    ) == []
    candidate["writing_profile"]["market"]["hook_deadline_words"] = -1
    assert any(issue.startswith("writing_profile_invalid:") for issue in
        conception._validate_materialized_repair_candidate_structure(
            candidate, original_world_model={},
        ))


def test_omitted_uncompiled_world_is_empty_but_legacy_omission_is_rejected():
    candidate = _candidate(world_model={})
    del candidate["world_model"]
    assert conception._validate_materialized_repair_candidate_structure(
        candidate, original_world_model={},
    ) == []
    assert "world_model_missing" in conception._validate_materialized_repair_candidate_structure(
        candidate, original_world_model={"laws": ["Existing legacy world"]},
    )
