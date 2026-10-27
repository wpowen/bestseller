"""Pure lossless JSON data encoding; references confer no story or instruction authority.

Pointers are RFC 6901 paths relative to ``value``. Only paths explicitly listed
in ``references`` are substitutions; authored $ref objects stay ordinary data.
V1 is the unchanged default. Explicit V2 markers hold the zero-based index of
their matching reference-table entry; target pointers still occur in the table.
Hashes use sorted compact JSON, UTF-8, without ASCII escaping or nonfinite values.
The encoded hash/byte count cover the container excluding its receipt. ``shrunk``
compares the complete transmitted container with the original canonical JSON.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import re
import string
from typing import Any

SCHEMA = "lossless-source-encoding.v1"
INDEXED_SCHEMA = "lossless-source-encoding.v2"
KEY_DICTIONARY_SCHEMA = "lossless-source-encoding.v3"
VALUE_DICTIONARY_SCHEMA = "lossless-source-encoding.v4"
MIN_STRING_CHARS = 64


def _require(condition: object, reason: str) -> None:
    if not condition:
        raise ValueError("lossless_source:" + reason)


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _bytes(value: object) -> bytes:
    return _json(value).encode("utf-8")


def _copy_json(value: object, active: set[int] | None = None) -> Any:  # noqa: ANN401
    active = active if active is not None else set()
    if type(value) in (dict, list):
        _require(id(value) not in active, "cyclic_json")
        active.add(id(value))
        try:
            if type(value) is dict:
                _require(all(type(key) is str for key in value), "object_keys")
                return {key: _copy_json(value[key], active) for key in sorted(value)}
            return [_copy_json(child, active) for child in value]
        finally:
            active.remove(id(value))
    _require(value is None or type(value) in (str, int, float, bool), "json_type")
    _require(type(value) is not float or math.isfinite(value), "nonfinite_json")
    return value


def _child(path: str, key: str | int) -> str:
    return path + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _pointer(value: object) -> bool:
    return (
        isinstance(value, str)
        and (value == "" or value.startswith("/"))
        and not re.search(r"~(?![01])", value)
    )


def _container(
    value: object, references: list[dict[str, str]], original: bytes, *, version: int = 1,
) -> dict:
    core = {
        "schema": SCHEMA if version == 1 else INDEXED_SCHEMA,
        "encoding": ("json-pointer" if version == 1 else "indexed-json-pointer")
        if references else "identity",
        "value": value,
        "references": references,
    }
    encoded = _bytes(core)
    return {
        **core,
        "receipt": {
            "original_sha256": hashlib.sha256(original).hexdigest(),
            "encoded_sha256": hashlib.sha256(encoded).hexdigest(),
            "original_bytes": len(original),
            "encoded_bytes": len(encoded),
            "shrunk": bool(references),
        },
    }


def _key_counts(value: object) -> Counter[str]:
    counts: Counter[str] = Counter()

    def visit(node: object) -> None:
        if type(node) is dict:
            counts.update(node.keys())
            for child in node.values():
                visit(child)
        elif type(node) is list:
            for child in node:
                visit(child)

    visit(value)
    return counts


def _encode_key_dictionary(value: object) -> dict:
    inner = encode_lossless_source(value, version=2)
    inner_bytes = _bytes(inner)
    original = _bytes(value)
    counts = _key_counts(inner)
    original_keys = set(counts) | set(_key_counts(value))
    available = iter(key for key in string.ascii_uppercase + string.ascii_lowercase
                     if key not in original_keys)
    aliases: dict[str, str] = {}
    # The ranking and the alias bank are deterministic; array/value order is untouched.
    for key, count in sorted(counts.items(), key=lambda row: (
        -row[1] * (len(_bytes(row[0])) - 3), row[0]
    )):
        # All available single ASCII aliases have identical encoded length.
        if count * (len(_bytes(key)) - 3) <= len(_bytes(key)) + 5:
            continue
        alias = next(available, None)
        if alias is None:
            break
        aliases[alias] = key
    if not aliases:
        return inner
    reverse = {target: alias for alias, target in aliases.items()}

    def rename(node: object) -> object:
        if type(node) is dict:
            return {reverse.get(key, key): rename(child) for key, child in node.items()}
        if type(node) is list:
            return [rename(child) for child in node]
        return node

    core = {"schema": KEY_DICTIONARY_SCHEMA, "encoding": "key-dictionary",
            "key_aliases": aliases, "value": rename(inner)}
    raw = _bytes(core)
    candidate = {**core, "receipt": {
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "original_bytes": len(original),
        "inner_sha256": hashlib.sha256(inner_bytes).hexdigest(), "inner_bytes": len(inner_bytes),
        "encoded_sha256": hashlib.sha256(raw).hexdigest(), "encoded_bytes": len(raw),
        "shrunk": True,
    }}
    if len(_bytes(candidate)) < min(len(inner_bytes), len(original)):
        return candidate
    return inner


def _decode_key_dictionary(data: dict) -> Any:  # noqa: ANN401
    _require(set(data) == {"schema", "encoding", "key_aliases", "value", "receipt"}
             and data["encoding"] == "key-dictionary", "key_dictionary_fields")
    receipt = data["receipt"]
    _require(type(receipt) is dict and set(receipt) == {
        "original_sha256", "original_bytes", "inner_sha256", "inner_bytes",
        "encoded_sha256", "encoded_bytes", "shrunk",
    }, "key_dictionary_receipt")
    raw = _bytes({key: value for key, value in data.items() if key != "receipt"})
    _require(all(type(receipt[key]) is int and receipt[key] > 0 for key in (
        "original_bytes", "inner_bytes", "encoded_bytes"
    )) and receipt["shrunk"] is True, "key_dictionary_sizes")
    _require(receipt["encoded_bytes"] == len(raw)
             and receipt["encoded_sha256"] == hashlib.sha256(raw).hexdigest(),
             "key_dictionary_integrity")
    aliases = data["key_aliases"]
    _require(type(aliases) is dict and bool(aliases)
             and all(len(key) == 1 and key in string.ascii_letters
                     and type(target) is str for key, target in aliases.items()),
             "key_dictionary_aliases")
    _require(len(set(aliases.values())) == len(aliases), "duplicate_key_target")
    used: set[str] = set()

    def restore(node: object) -> object:
        if type(node) is dict:
            result = {}
            for key, child in node.items():
                target = aliases.get(key, key)
                _require(target not in result, "restored_key_collision")
                if key in aliases:
                    used.add(key)
                result[target] = restore(child)
            return result
        if type(node) is list:
            return [restore(child) for child in node]
        return node

    inner = restore(data["value"])
    _require(used == set(aliases), "unused_key_alias")
    _require(not set(aliases).intersection(_key_counts(inner)), "original_key_alias_collision")
    _require(type(inner) is dict and inner.get("schema") == INDEXED_SCHEMA,
             "key_dictionary_inner_schema")
    inner_bytes = _bytes(inner)
    _require(len(inner_bytes) == receipt["inner_bytes"]
             and hashlib.sha256(inner_bytes).hexdigest() == receipt["inner_sha256"],
             "key_dictionary_inner_integrity")
    value = decode_lossless_source(inner)
    _require(not set(aliases).intersection(_key_counts(value)), "authored_key_alias_collision")
    original = _bytes(value)
    _require(len(original) == receipt["original_bytes"]
             and hashlib.sha256(original).hexdigest() == receipt["original_sha256"],
             "key_dictionary_original_integrity")
    _require(len(_bytes(data)) < min(len(inner_bytes), len(original)), "not_smaller")
    return value


def _extended_keys(value: dict) -> tuple[dict, dict[str, str]]:
    """Use as many profitable aliases as needed, excluding every authored key."""
    counts = _key_counts(value)
    forbidden = set(counts)
    aliases: dict[str, str] = {}
    bank = list(string.ascii_uppercase + string.ascii_lowercase)
    index = 0
    for key, count in sorted(counts.items(), key=lambda row: (-row[1] * len(_bytes(row[0])), row[0])):
        while True:
            alias = bank[index] if index < len(bank) else "@" + str(index - len(bank))
            index += 1
            if alias not in forbidden:
                break
        if count * (len(_bytes(key)) - len(_bytes(alias))) <= len(_bytes(key)) + len(_bytes(alias)) + 5:
            continue
        aliases[alias] = key
    reverse = {target: alias for alias, target in aliases.items()}

    def rename(node):
        if type(node) is dict:
            return {reverse.get(key, key): rename(child) for key, child in node.items()}
        if type(node) is list:
            return [rename(child) for child in node]
        return node

    return rename(value), aliases


def _restore_extended_keys(value: dict, aliases: dict[str, str]) -> dict:
    _require(type(aliases) is dict and all(type(k) is str and type(v) is str for k,v in aliases.items())
             and len(set(aliases.values())) == len(aliases), "value_dictionary_aliases")
    used = set()

    def restore(node):
        if type(node) is dict:
            result = {}
            for key, child in node.items():
                target = aliases.get(key, key)
                _require(target not in result, "restored_key_collision")
                if key in aliases:
                    used.add(key)
                result[target] = restore(child)
            return result
        if type(node) is list:
            return [restore(child) for child in node]
        return node

    restored = restore(value)
    _require(used == set(aliases) and not set(aliases).intersection(_key_counts(restored)),
             "value_dictionary_alias_collision")
    return restored


def encode_lossless_source(value: object, *, version: int = 1) -> dict:
    """Deduplicate exact prior complete subtrees/long strings; never summarize data.

    Return an identity container if the entire encoded envelope is not smaller.
    Pass version=2 for integer markers, or version=3 for an optional key dictionary
    around unchanged V2. If V3 is not smaller, return V2. V1 output is unchanged.
    The function neither mutates input nor reads configuration, files or services.
    """
    _require(type(version) is int and version in (1, 2, 3, 4), "version")
    original_value = _copy_json(value)
    if version == 4:
        from bestseller.services import planning_string_pool as pool

        inner = encode_lossless_source(original_value,version=2)
        renamed, aliases = _extended_keys(inner)
        # An alias may never collide with a source key hidden in a reference.
        if set(aliases).intersection(_key_counts(original_value)):
            return _encode_key_dictionary(original_value)
        pooled = pool.encode(renamed)
        original = _bytes(original_value)
        core = {"schema": VALUE_DICTIONARY_SCHEMA, "encoding": "key-and-value-dictionary",
                "key_aliases": aliases, "value": pooled}
        raw = _bytes(core)
        candidate = {**core, "receipt": {
            "original_sha256": hashlib.sha256(original).hexdigest(), "original_bytes": len(original),
            "encoded_sha256": hashlib.sha256(raw).hexdigest(), "encoded_bytes": len(raw),
            "shrunk": True,
        }}
        fallback = _encode_key_dictionary(original_value)
        return candidate if len(_bytes(candidate)) < min(len(_bytes(fallback)), len(original)) else fallback
    if version == 3:
        return _encode_key_dictionary(original_value)
    original = _bytes(original_value)
    seen: dict[bytes, str] = {}
    references: list[dict[str, str]] = []

    def visit(node: object, path: str) -> object:
        eligible = (type(node) in (dict, list) and bool(node)) or (
            isinstance(node, str) and len(node) >= MIN_STRING_CHARS
        )
        full = _bytes(node) if eligible else None
        if full is not None and full in seen:
            target = seen[full]
            marker = {"$ref": target if version == 1 else len(references)}
            declaration = {"path": path, "target": target}
            # Keep the V1 declaration/target selection exactly: allowing extra
            # tiny V2 substitutions can displace more profitable later targets.
            if len(full) > len(_bytes({"$ref": target})) + len(_bytes(declaration)) + 1:
                references.append(declaration)
                return marker
        if type(node) is dict:
            result = {key: visit(node[key], _child(path, key)) for key in sorted(node)}
        elif type(node) is list:
            result = [visit(child, _child(path, i)) for i, child in enumerate(node)]
        else:
            result = node
        # Only completed nodes can be targets: no ancestor/self/forward edges.
        if full is not None:
            seen.setdefault(full, path)
        return result

    candidate = _container(visit(original_value, ""), references, original, version=version)
    if references and len(_bytes(candidate)) < len(original):
        return candidate
    return _container(original_value, [], original, version=version)


def decode_lossless_source(encoded: object) -> Any:  # noqa: ANN401
    """Validate and restore exact JSON values; malformed or unbound references fail closed.

    Receipts establish integrity, not authentication; the caller retains custody
    and source/financial verification. No undeclared authored object is resolved.
    """
    data = _copy_json(encoded)
    if type(data) is dict and data.get("schema") == VALUE_DICTIONARY_SCHEMA:
        from bestseller.services import planning_string_pool as pool

        _require(set(data) == {"schema", "encoding", "key_aliases", "value", "receipt"}
                 and data["encoding"] == "key-and-value-dictionary", "value_dictionary_fields")
        receipt = data["receipt"]
        _require(type(receipt) is dict and set(receipt) == {
            "original_sha256", "original_bytes", "encoded_sha256", "encoded_bytes", "shrunk",
        } and receipt["shrunk"] is True, "value_dictionary_receipt")
        raw = _bytes({key: value for key, value in data.items() if key != "receipt"})
        _require(type(receipt["encoded_bytes"]) is int and receipt["encoded_bytes"] == len(raw)
                 and receipt["encoded_sha256"] == hashlib.sha256(raw).hexdigest(), "value_dictionary_integrity")
        inner = _restore_extended_keys(pool.decode(data["value"]),data["key_aliases"])
        _require(inner.get("schema") == INDEXED_SCHEMA, "value_dictionary_inner_schema")
        value = decode_lossless_source(inner)
        original = _bytes(value)
        _require(type(receipt["original_bytes"]) is int and receipt["original_bytes"] == len(original)
                 and receipt["original_sha256"] == hashlib.sha256(original).hexdigest(), "value_dictionary_original_integrity")
        _require(not set(data["key_aliases"]).intersection(_key_counts(value)), "authored_key_alias_collision")
        _require(len(_bytes(data)) < min(len(_bytes(inner)), len(original)), "not_smaller")
        return value
    if type(data) is dict and data.get("schema") == KEY_DICTIONARY_SCHEMA:
        return _decode_key_dictionary(data)
    _require(
        isinstance(data, dict)
        and set(data)
        == {
            "schema",
            "encoding",
            "value",
            "references",
            "receipt",
        },
        "container_fields",
    )
    _require(
        data["schema"] in (SCHEMA, INDEXED_SCHEMA)
        and isinstance(data["encoding"], str)
        and data["encoding"] in {
            "identity", "json-pointer" if data["schema"] == SCHEMA else "indexed-json-pointer"
        },
        "schema",
    )
    receipt = data["receipt"]
    _require(
        isinstance(receipt, dict)
        and set(receipt)
        == {
            "original_sha256",
            "encoded_sha256",
            "original_bytes",
            "encoded_bytes",
            "shrunk",
        },
        "receipt_fields",
    )
    core = {key: value for key, value in data.items() if key != "receipt"}
    raw_core = _bytes(core)
    _require(
        type(receipt["encoded_bytes"]) is int
        and receipt["encoded_bytes"] == len(raw_core)
        and receipt["encoded_sha256"] == hashlib.sha256(raw_core).hexdigest(),
        "encoded_integrity",
    )
    _require(
        type(receipt["original_bytes"]) is int
        and receipt["original_bytes"] > 0
        and type(receipt["shrunk"]) is bool,
        "receipt_types",
    )
    declarations = data["references"]
    _require(isinstance(declarations, list), "reference_table")
    references = {}
    for index, declaration in enumerate(declarations):
        _require(
            isinstance(declaration, dict) and set(declaration) == {"path", "target"},
            "reference_fields",
        )
        path, target = declaration["path"], declaration["target"]
        _require(_pointer(path) and path != "" and _pointer(target), "reference_pointer")
        _require(path not in references, "duplicate_reference_path")
        references[path] = (index, target)
    compressed = data["encoding"] != "identity"
    _require(bool(declarations) == compressed and receipt["shrunk"] is compressed, "encoding_mode")
    restored, used = {}, []

    def visit(node: object, path: str) -> object:
        if path in references:
            index, target = references[path]
            if data["schema"] == INDEXED_SCHEMA:
                _require(
                    type(node) is dict and set(node) == {"$ref"}
                    and type(node["$ref"]) is int and node["$ref"] == index,
                    "reference_index",
                )
            else:
                _require(node == {"$ref": target}, "reference_marker")
            _require(target in restored, "reference_not_backward")
            result = deepcopy(restored[target])
            used.append(path)
        elif type(node) is dict:
            result = {key: visit(node[key], _child(path, key)) for key in sorted(node)}
        elif type(node) is list:
            result = [visit(child, _child(path, i)) for i, child in enumerate(node)]
        else:
            result = node
        restored[path] = result
        return result

    value = visit(data["value"], "")
    _require(used == list(references), "reference_path_missing_or_unordered")
    original = _bytes(value)
    _require(
        len(original) == receipt["original_bytes"]
        and hashlib.sha256(original).hexdigest() == receipt["original_sha256"],
        "original_integrity",
    )
    _require(not compressed or len(_bytes(data)) < len(original), "not_smaller")
    return value


__all__ = ["decode_lossless_source", "encode_lossless_source"]
