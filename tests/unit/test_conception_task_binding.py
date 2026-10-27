"""Projectless Web entry points must bind receipts to the actual task."""
import ast
from pathlib import Path


def test_web_conception_entries_bind_current_task_id() -> None:
    source = Path(__file__).resolve().parents[2] / "src/bestseller/web/server.py"
    tree = ast.parse(source.read_text())
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        bindings = [item.context_expr for item in node.items
                    if isinstance(item.context_expr, ast.Call)
                    and isinstance(item.context_expr.func, ast.Name)
                    and item.context_expr.func.id == "bind_conception_model"]
        if not bindings:
            continue
        calls = {child.func.id for child in ast.walk(node)
                 if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)}
        targets = calls & {"architect_story_facets", "run_conception_pipeline"}
        if targets:
            assert all(any(keyword.arg == "task_id"
                           and isinstance(keyword.value, ast.Name)
                           and keyword.value.id == "task_id"
                           for keyword in binding.keywords) for binding in bindings)
            found.update(targets)
    assert found == {"architect_story_facets", "run_conception_pipeline"}
