"""Every whole-book advisory request retains native project billing scope."""
import ast
from pathlib import Path


def test_whole_litstyle_caller_passes_actual_project_id():
    source=Path(__file__).resolve().parents[3]/'src/bestseller/services/fanqie_short_pipeline.py'
    calls=[n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='judge_chapter_litstyle_stable']
    assert calls
    for call in calls:
        binding=next((k.value for k in call.keywords if k.arg=='project_id'),None)
        assert binding is not None and ast.unparse(binding)=='project.id'
