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
