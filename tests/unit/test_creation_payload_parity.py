from pathlib import Path

HTML = Path(__file__).parents[2] / "src/bestseller/web/novel_quickstart.html"


def test_start_and_schedule_share_the_same_creation_payload_builder() -> None:
    source = HTML.read_text(encoding="utf-8")

    assert "const body = buildQuickstartPayload();" in source
    assert "const body = { ...buildQuickstartPayload(), scheduled_at: isoWithTz };" in source
    assert source.count("function buildQuickstartPayload()") == 1
    builder = source.split("function buildQuickstartPayload()", 1)[1].split(
        "  function _localIsoWithOffset", 1
    )[0]
    for field in (
        "creation_mode",
        "genre_key",
        "selection",
        "audience_orientation",
        "narrative_scale",
        "tone_preference",
        "concept_lab_bundle_id",
        "concept_lab_bundle",
        "chapter_count",
        "length_key",
        "pov",
        "draft_mode",
        "stop_after_conception",
        "story_enhancers",
        "llm_model_id",
        "concept_seed",
    ):
        assert f"{field}:" in builder


def test_creation_payload_omits_untouched_story_defaults() -> None:
    """Default UI state is not an explicit creative instruction."""

    source = HTML.read_text(encoding="utf-8")
    builder = source.split("function buildQuickstartPayload()", 1)[1].split(
        "  function _localIsoWithOffset", 1
    )[0]

    assert "const storyEnhancers = collectStoryEnhancers();" in builder
    assert "const hasStoryEnhancers" in builder
    assert "selectedNarrativeScale === 'epic' ? 'epic' : undefined" in builder
    assert "story_enhancers: hasStoryEnhancers ? storyEnhancers : undefined" in builder
    assert "draftModeToggle').checked ? true : undefined" in builder
    assert "stopAfterConceptionToggle').checked ? true : undefined" in builder


def test_creation_payload_carries_client_schema_and_server_receipt_check() -> None:
    source = HTML.read_text(encoding="utf-8")

    assert "client_schema_version:" in source
    assert "creation_input_receipt" in source
    assert "concept_seed_hash" in source
    assert "服务端回执" in source
    assert "function utf8ByteLength(value)" in source
    assert "concept_seed_length: utf8ByteLength(seed)" in source
    assert "receipt.concept_seed_length !== utf8ByteLength(localSeed)" in source


def test_server_marks_only_non_default_story_controls_explicit() -> None:
    """API callers that send the old full default object remain safe."""

    server = (
        Path(__file__).parents[2] / "src/bestseller/web/server.py"
    ).read_text(encoding="utf-8")
    assert '"narrative_scale": "explicit" if _narrative_scale == "epic"' in server
    assert '"story_enhancers": "explicit" if not selected_enhancers.is_default()' in server
    assert '"draft_mode": "explicit" if bool(payload.get("draft_mode"))' in server
    assert 'if bool(payload.get("stop_after_conception"))' in server


def test_new_book_reset_clears_taxonomy_and_creation_choices() -> None:
    source = HTML.read_text(encoding="utf-8")
    reset = source.split("function resetWizardState()", 1)[1].split(
        "window.startNewCreationFlow", 1
    )[0]
    for marker in (
        "customSelection = null",
        "txSel = { channel: '', genreKey: '', subKey: '', tags: [] }",
        "selectedNarrativeScale = 'serial'",
        "selectedTonePreference = ''",
        "seDirection = null",
        "seCostStyle = 'standard'",
        "fanqiePov = 'first_person'",
        "conceptSeedInput.value = ''",
        "未填写，将按页面选项生成",
        "createModelSelect",
    ):
        assert marker in reset


def test_language_switch_drops_previous_taxonomy_selection() -> None:
    source = HTML.read_text(encoding="utf-8")
    switch = source.split("function switchLangTab(lang)", 1)[1].split(
        "$('#langTabZh')", 1
    )[0]
    assert "customSelection = null" in switch
    assert "txSel = { channel: '', genreKey: '', subKey: '', tags: [] }" in switch
