"""Pure diagnostic controls; no application, model, database or authority."""

from full_flow_q4_short.typed_leaf_differences import complete_leaf_differences, typed_summary


def test_missing_expected_is_distinct_from_null_empty_and_literal_unknown():
    for value in (None, "", "Unknown"):
        differences = complete_leaf_differences({"kept": 1}, {"kept": 1, "field": value})
        assert differences == [{"field": "/field",
                                "expected": {"type": "missing", "length": 0, "sha256": None},
                                "actual": typed_summary(value)}]


def test_missing_actual_is_explicit_and_preserves_the_present_null_summary():
    differences = complete_leaf_differences({"kept": 1, "field": None}, {"kept": 1})
    assert differences == [{"field": "/field", "expected": typed_summary(None),
                            "actual": {"type": "missing", "length": 0, "sha256": None}}]


def test_equal_literal_unknown_is_an_ordinary_string_not_a_missing_operand():
    assert complete_leaf_differences({"field": "Unknown"}, {"field": "Unknown"}) == []
    assert typed_summary("Unknown")["type"] == "str"
    assert typed_summary("Unknown")["sha256"] is not None
