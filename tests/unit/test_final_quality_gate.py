from types import SimpleNamespace

from bestseller.services import pipelines


def _config(*, ai_enabled: bool = False):
    return SimpleNamespace(
        ai_flavor=SimpleNamespace(enabled=ai_enabled),
        prose_quality=SimpleNamespace(
            anti_meta_enabled=False,
            anti_meta_severity="block",
            in_scene_ending_severity="block",
            show_dont_tell_enabled=False,
            show_dont_tell_severity="warn",
        ),
    )


def test_final_quality_gate_passes_clean_text(monkeypatch):
    monkeypatch.setattr(pipelines, "get_quality_gates_config", lambda: _config())

    result = pipelines.run_final_quality_gates(
        chapter_number=1,
        content_md="雨停了。她把伞靠在门边。",
        project=SimpleNamespace(language="zh-CN", metadata_json={}),
    )

    assert result.passed is True
    assert result.errors == []


def test_final_quality_gate_blocks_evaluator_exception(monkeypatch):
    monkeypatch.setattr(
        pipelines, "get_quality_gates_config", lambda: _config(ai_enabled=True)
    )

    def explode(**_kwargs):
        raise RuntimeError("judge unavailable")

    from bestseller.services import ai_flavor_gate

    monkeypatch.setattr(ai_flavor_gate, "run_ai_flavor_gate", explode)
    result = pipelines.run_final_quality_gates(
        chapter_number=2,
        content_md="她推门进去。",
        project=SimpleNamespace(language="zh-CN", slug="demo", metadata_json={}),
    )

    assert result.passed is False
    assert result.errors
    assert result.errors[0].startswith("evaluator_error:RuntimeError:")


def test_final_quality_gate_reports_ai_flavor_block(monkeypatch):
    monkeypatch.setattr(
        pipelines, "get_quality_gates_config", lambda: _config(ai_enabled=True)
    )
    from bestseller.services import ai_flavor_gate

    monkeypatch.setattr(
        ai_flavor_gate,
        "run_ai_flavor_gate",
        lambda **_kwargs: SimpleNamespace(
            decision="block", after_score=91.5, patched_text=None
        ),
    )
    result = pipelines.run_final_quality_gates(
        chapter_number=2,
        content_md="她推门进去。",
        project=SimpleNamespace(language="zh-CN", slug="demo", metadata_json={}),
    )

    assert result.passed is False
    assert "ai_flavor:91.50" in result.issues
