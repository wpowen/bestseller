"""Every whole-book advisory request retains native project billing scope."""

import ast
from pathlib import Path


def test_whole_litstyle_caller_passes_actual_project_id():
    source = (
        Path(__file__).resolve().parents[3] / "src/bestseller/services/fanqie_short_pipeline.py"
    )
    calls = [
        n
        for n in ast.walk(ast.parse(source.read_text()))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "judge_chapter_litstyle_stable"
    ]
    assert calls
    for call in calls:
        binding = next((k.value for k in call.keywords if k.arg == "project_id"), None)
        assert binding is not None and ast.unparse(binding) == "project.id"


def test_title_repair_reads_native_sources_before_creating_or_paying_workflow():
    source = Path(__file__).resolve().parents[3] / "src/bestseller/services/fanqie_whole_repair.py"
    tree = ast.parse(source.read_text())
    title = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_title")
    calls = {n.func.id: n.lineno for n in ast.walk(title)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "read_current_title_sources" in calls
    assert calls["read_current_title_sources"] < calls["create_workflow_run"]
    assert calls["read_current_title_sources"] < calls["_refresh_story_grounded_project_title"]
