"""Lossless adapter for misplaced architecture fields; never repair story facts."""
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json

FIELDS = ("genre_contracts", "causal_units", "progression", "relationships",
          "conflicts", "opponent_adaptations", "story_engine")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def restore_architecture_field_placement(raw):
    """Only lift known exact fields from grammar; conflicts stay fail-closed.

    All values and types are preserved. Missing facts, bad enums, incomplete
    units, and insufficent capacity still travel through the original gate.
    """
    architecture = deepcopy(dict(raw))
    receipt = {"schema": "architecture-field-placement.v1", "moved": [], "duplicate_equal": [],
        "source_sha256": hashlib.sha256(_canonical(raw).encode()).hexdigest(), "facts_generated": False}
    grammar = architecture.get("grammar")
    if not isinstance(grammar, Mapping):
        return architecture, receipt
    grammar = dict(grammar)
    for key in FIELDS:
        if key not in grammar:
            continue
        if key in architecture:
            if _canonical(architecture[key]) != _canonical(grammar[key]):
                raise ValueError("AMBIGUOUS_ARCHITECTURE_FIELD_PLACEMENT:" + key)
            receipt["duplicate_equal"].append(key)
        else:
            architecture[key] = grammar[key]
            receipt["moved"].append({"from": "grammar." + key, "to": key,
                "value_sha256": hashlib.sha256(_canonical(grammar[key]).encode()).hexdigest()})
        del grammar[key]
    architecture["grammar"] = grammar
    return architecture, receipt
