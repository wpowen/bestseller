"""Offline regression for the native E05b harness UUID comparison failure."""
import ast
import json
from pathlib import Path
from uuid import UUID

import pytest


@pytest.fixture
def compare():
    path = Path(__file__).resolve().parents[3] / "scripts/full_flow/e05b_native_handoff.py"
    function = next(n for n in ast.parse(path.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == "full_equal")
    namespace = {"json": json, "UUID": UUID}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["full_equal"]


@pytest.mark.parametrize("left,right,expected", [
    (UUID(int=17), UUID(int=17), True),
    (UUID(int=17), UUID(int=18), False),
    (UUID(int=17), str(UUID(int=17)), False),
    ({"id": UUID(int=17), "values": [1, b"a"]},
     {"values": [1, b"a"], "id": UUID(int=17)}, True),
    ({"id": UUID(int=17), "extra": None}, {"id": UUID(int=17)}, False),
    ({"id": UUID(int=17), "n": True}, {"id": UUID(int=17), "n": 1}, False),
    ([UUID(int=17), UUID(int=18)], [UUID(int=18), UUID(int=17)], False),
    (b"original", b"changed", False),
])
def test_native_uuid_comparison_preserves_full_typed_values(compare, left, right, expected):
    assert compare(left, right) is expected


def test_unknown_objects_do_not_gain_string_equivalence(compare):
    class Unsupported:
        def __str__(self):
            return "same"

    with pytest.raises(TypeError):
        compare(Unsupported(), Unsupported())
