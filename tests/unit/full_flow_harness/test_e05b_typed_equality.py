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


@pytest.fixture
def driver_uuid():
    # The actual asyncpg decoder class, not a locally invented UUID subclass.
    from asyncpg.pgproto.pgproto import UUID as AsyncpgUUID

    value = AsyncpgUUID(str(UUID(int=17)))
    assert type(value).__module__ == "asyncpg.pgproto.pgproto"
    assert type(value) is not UUID and isinstance(value, UUID)
    assert len(value.bytes) == 16
    return AsyncpgUUID


@pytest.mark.parametrize("case,expected", [
    ("same", True),
    ("different", False),
    ("to_stdlib", False),
    ("from_stdlib", False),
    ("to_string", False),
    ("from_string", False),
    ("nested_reordered_dict", True),
    ("nested_byte_difference", False),
    ("nested_missing_field", False),
    ("list_order_difference", False),
])
def test_real_asyncpg_uuid_keeps_exact_type_bytes_and_full_nested_comparison(
    compare, driver_uuid, case, expected,
):
    one, same, other = (driver_uuid(str(UUID(int=n))) for n in (17, 17, 18))
    pairs = {
        "same": (one, same),
        "different": (one, other),
        "to_stdlib": (one, UUID(int=17)),
        "from_stdlib": (UUID(int=17), one),
        "to_string": (one, str(one)),
        "from_string": (str(one), one),
        "nested_reordered_dict": ({"id": one, "body": b"a"}, {"body": b"a", "id": same}),
        "nested_byte_difference": ({"id": one, "body": b"a"}, {"id": same, "body": b"b"}),
        "nested_missing_field": ({"id": one, "extra": None}, {"id": same}),
        "list_order_difference": ([one, other], [other, same]),
    }
    left, right = pairs[case]
    assert compare(left, right) is expected
