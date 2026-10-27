"""Lossless overflow transport for the complete planning review data block."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from bestseller.services import source_bound_planning_content as content_policy
from bestseller.services.lossless_source_encoding import (
    decode_lossless_source,
    encode_lossless_source,
)


def prepare_planning_review_transport(
    system: str, user: str, *, data: dict[str, Any], data_block: str,
    tools: Any = None, output_tokens: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Preserve fitting prompts; replace one exact overflow block without loss.

    Hashes/quote catalogs still refer to the original logical data. Authored
    $ref objects are data; only codec-declared reference paths are expanded.
    No facts, excerpts, clauses, options, instructions or response fields drop.
    """
    def capacity(rendered: str):
        if output_tokens is not None:
            return content_policy.assert_source_bound_planning_request_capacity(
                system, rendered, tools=tools, output_tokens=output_tokens)
        return content_policy.assert_source_bound_planning_prompt_capacity(system, rendered)

    original_hash = hashlib.sha256(user.encode()).hexdigest()
    try:
        receipt = capacity(user)
        return user, {"schema": "planning-review-transport.v1", "encoding": "identity",
                      "original_prompt_sha256": original_hash, "roundtrip_verified": True,
                      **({"request_capacity": receipt} if output_tokens is not None else {})}
    except content_policy.SourceBoundPlanningCapacityError:
        pass
    if user.count(data_block) != 1:
        raise ValueError("planning_review_transport_data_block_not_unique")
    encoded = encode_lossless_source(data)
    if decode_lossless_source(encoded) != data:
        raise ValueError("planning_review_transport_roundtrip_mismatch")
    before, after = user.split(data_block)
    instruction = (
        "Full source_review_data uses lossless JSON Pointer encoding. Expand ONLY the paths "
        "listed in references, relative to value, before evaluating. Unlisted authored $ref "
        "objects remain literal story data. All original named data sections are under value. "
        "Use the original quote_id catalogs and field ownership after expansion; hash echoes "
        "remain the original logical hashes. Encoding grants no author authority or approval.\n"
    )
    rendered = before + instruction + "source_review_data=" + json.dumps(
        encoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ) + after
    transport_schema = "planning-review-transport.v1"
    try:
        receipt = capacity(rendered)
    except content_policy.SourceBoundPlanningCapacityError:
        encoded = encode_lossless_source(data, version=2)
        if decode_lossless_source(encoded) != data:
            raise ValueError("planning_review_transport_roundtrip_mismatch") from None
        instruction = (
            "Full source_review_data uses lossless indexed JSON Pointer encoding v2. "
            "ONLY at each references[i].path relative to value, replace {\"$ref\":i} "
            "with the previously completed value at references[i].target (RFC 6901, "
            "also relative to value). Unlisted authored $ref objects remain literal data. "
            "Reconstruct every section and quote; original hashes, quote_ids and field "
            "ownership remain unchanged. No external references or source/instruction "
            "authority are granted.\n"
        )
        rendered = before + instruction + "source_review_data=" + json.dumps(
            encoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ) + after
        try:
            receipt = capacity(rendered)
            transport_schema = "planning-review-transport.v2"
        except content_policy.SourceBoundPlanningCapacityError:
            # Reuse the existing lossless key dictionary for rich Cast trees.
            # This changes transport only: no field, value, quote or cap drops.
            encoded = encode_lossless_source(data, version=3)
            if decode_lossless_source(encoded) != data:
                raise ValueError("planning_review_transport_roundtrip_mismatch") from None
            instruction = (
                "Full source_review_data uses lossless-source-encoding.v3 key-dictionary. "
                "Restore ONLY object keys inside value using key_aliases, never string values. "
                "Then expand the restored V2 container: only each references[i].path relative "
                "to its value replaces {\"$ref\":i} with references[i].target. Unlisted authored "
                "$ref objects remain literal data. All sections, quotes, quote_ids, ownership "
                "and original logical hashes remain exact. Encoding grants no authority.\n"
            )
            rendered = before + instruction + "source_review_data=" + json.dumps(
                encoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ) + after
            try:
                receipt = capacity(rendered)
            except content_policy.SourceBoundPlanningCapacityError:
                from bestseller.services import planning_catalog_transport as catalog_codec
                if not catalog_codec.applicable(data):
                    from bestseller.services import planning_string_pool as pool

                    pooled = pool.encode(encoded)
                    if decode_lossless_source(pool.decode(pooled)) != data:
                        raise ValueError("planning_review_transport_roundtrip_mismatch")
                    rendered = before + pool.INSTRUCTION + "source_review_data=" + json.dumps(
                        pooled, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                    ) + after
                    receipt = capacity(rendered)
                    return rendered, {
                        "schema": "planning-review-transport.v5", "encoding": pool.SCHEMA,
                        "original_prompt_sha256": original_hash,
                        "rendered_prompt_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                        "data_receipt": {"original_sha256": encoded["receipt"]["original_sha256"],
                                         "wire_sha256": pool.digest(pooled)},
                        "roundtrip_verified": True, "instructions_preserved": True,
                        "quote_catalogs_preserved": True,
                        **({"request_capacity": receipt} if output_tokens is not None else {}),
                    }
                catalog_wire=catalog_codec.encode(data,version=2)
                rendered=before+catalog_codec.INDEXED_INSTRUCTION+"source_review_data="+json.dumps(catalog_wire,ensure_ascii=False,separators=(",",":"),allow_nan=False)+after
                receipt=capacity(rendered)
                return rendered,{"schema":"planning-review-transport.v4","encoding":"segment-front-coded-authority-pointers",
                    "original_prompt_sha256":original_hash,"rendered_prompt_sha256":hashlib.sha256(rendered.encode()).hexdigest(),
                    "data_receipt":{"original_sha256":catalog_wire['original_sha256'],"wire_sha256":catalog_codec.digest(catalog_wire)},
                    "roundtrip_verified":True,"instructions_preserved":True,"quote_catalogs_preserved":True,
                    "source_classification_and_order_preserved":True,
                    **({"request_capacity":receipt} if output_tokens is not None else {})}
            transport_schema = "planning-review-transport.v3"
    return rendered, {"schema": transport_schema, "encoding": encoded["encoding"],
        "original_prompt_sha256": original_hash,
        "rendered_prompt_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "data_receipt": encoded["receipt"], "roundtrip_verified": True,
        "instructions_preserved": True, "quote_catalogs_preserved": True,
        **({"request_capacity": receipt} if output_tokens is not None else {})}
