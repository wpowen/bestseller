"""每个 create_workflow_run 调用点都必须传齐必需的关键字参数。

2026-08-24 端到端验证书真机崩溃：

    ERROR autowrite:heal:custom-xuanhuan-1787557783:run_autowrite_task failed,
    2403.59s TypeError: create_workflow_run() missing 3 required keyword-only
    arguments: 'status', 'scope_type', and 'scope_id'

跑了 40 分钟然后死在签名不匹配上。缺参的是 repair.py 里「书已完本则跳过修复」
那个分支（引入于 2026-07-28 fce11ebd），**潜伏近一个月**——因为它只在一本
**已完本**的书进入 run_project_repair 时才走到，而那是罕见路径。

单元测试测不到这种分支组合，但**签名不匹配是静态可查的**。这条守卫用 AST
扫描全部调用点，一次堵住整类：以后给 create_workflow_run 加必需参数，忘了
改的调用点会立刻红，而不是等某本书在生产里跑 40 分钟再崩。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import bestseller.services.workflows as workflows_mod
from bestseller.services.workflows import create_workflow_run

SRC = Path(workflows_mod.__file__).resolve().parents[2]


def _required_keyword_only() -> set[str]:
    sig = inspect.signature(create_workflow_run)
    return {
        name
        for name, p in sig.parameters.items()
        if p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty
    }


def _call_sites() -> list[tuple[str, int, set[str]]]:
    out: list[tuple[str, int, set[str]]] = []
    for path in SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - 语法错另有测试兜底
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name != "create_workflow_run":
                continue
            if any(kw.arg is None for kw in node.keywords):  # **kwargs 展开，跳过
                continue
            supplied = {kw.arg for kw in node.keywords if kw.arg}
            out.append((str(path.relative_to(SRC)), node.lineno, supplied))
    return out


def test_there_are_call_sites_to_check() -> None:
    """守卫本身不许静默空转——扫不到调用点说明扫描坏了。"""

    assert len(_call_sites()) >= 5


def test_every_call_site_supplies_all_required_keywords() -> None:
    required = _required_keyword_only()
    assert required, "签名里应有必需的关键字参数"
    missing_report = [
        f"{path}:{line} 缺 {sorted(required - supplied)}"
        for path, line, supplied in _call_sites()
        if required - supplied
    ]
    assert not missing_report, "\n".join(missing_report)
