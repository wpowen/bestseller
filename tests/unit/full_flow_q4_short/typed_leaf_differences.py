"""Diagnostic-only complete JSON differences, including opaque metadata leaves."""

import hashlib
import json


def typed_summary(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    return {"type": type(value).__name__, "length": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def complete_leaf_differences(expected, actual):
    """No truncation, normalization, ignored metadata or inferred missing nulls."""
    def flatten(value, path, leaves, containers):
        if isinstance(value, (dict, list)):
            containers[path] = value
            if not value:
                leaves[path] = value
            entries = value.items() if isinstance(value, dict) else enumerate(value)
            for key, child in entries:
                pointer = path + "/" + str(key).replace("~", "~0").replace("/", "~1")
                flatten(child, pointer, leaves, containers)
        else:
            leaves[path] = value

    left, right, left_containers, right_containers = {}, {}, {}, {}
    flatten(expected, "", left, left_containers)
    flatten(actual, "", right, right_containers)
    differences = []
    for path in sorted(set(left) | set(right)):
        e = typed_summary(left[path]) if path in left else {"type": "missing", "length": 0, "sha256": None}
        a = typed_summary(right[path]) if path in right else {"type": "missing", "length": 0, "sha256": None}
        if path not in left or path not in right or e != a:
            differences.append({"field": path or "/", "expected": e, "actual": a})
    # Numeric object keys and list indices can have the same leaf paths. Keep
    # their container-type distinction without repeating every changed parent.
    for path in sorted(set(left_containers) & set(right_containers)):
        if type(left_containers[path]) is not type(right_containers[path]):
            differences.append({"field": path or "/", "expected": typed_summary(left_containers[path]),
                                "actual": typed_summary(right_containers[path])})
    return differences
