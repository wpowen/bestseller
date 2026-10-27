"""Actual selected loop: strict protocol belongs inside the compiled transport."""

import hashlib
import json

import pytest
from test_rolling_outline_dispatch_capacity import (
    assert_capacity,
    dispatch,  # noqa: F401 -- fixture
)
from test_source_bound_planning_content import _compile
from test_source_bound_planning_model_completion_entry import (
    entry,  # noqa: F401 -- fixture dependency
)

from bestseller.services import planner
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.rolling_outline_source_transport import (
    CLOSE,
    OPEN,
    verify_source_complete_outline_transport,
)
from bestseller.services.source_complete_outline_json import FORMAT_INSTRUCTION, PROMPT_VERSION

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_selected_loop_wire_hash_receipt_and_decoder_bind_exact_final_strict_request(
    dispatch,  # noqa: F811 -- imported pytest fixture
    monkeypatch,
):
    state = dispatch
    book, world, _, volumes = _compile(state.entry.project)
    cast = {
        "protagonist": {"name": "于岚", "role": "observer", "gender": "female", "age": 43},
        "supporting_cast": [],
        "unknown_retained": {"$ref": "/authored"},
    }
    book["unknown_full_archive"] = (
        "ARCHIVE_BEGIN:" + "".join(f"{i:08x};" for i in range(3500)) + ":ARCHIVE_END"
    )
    # Force genuine lossless deduplication. A single unique 63k archive
    # cannot fit the preferred 16k compiler allocation under either estimator.
    book["unknown_full_archive_copy"] = book["unknown_full_archive"]
    logical, physical = [], []
    actual_compile = planner._compile_volume_outline_prompt

    def record_compile(project, settings, *, system_prompt, user_prompt):
        assert user_prompt.endswith(FORMAT_INSTRUCTION)
        assert user_prompt.count(FORMAT_INSTRUCTION) == 1
        logical.append((system_prompt, user_prompt))
        return actual_compile(
            project, settings, system_prompt=system_prompt, user_prompt=user_prompt
        )

    class BoundaryReached(BaseException):
        """Stop before any provider; do not invent an outline grade."""

    async def capture(session, settings, request):
        assert session.bind.dialect.name == "sqlite"
        physical.append(request)
        raise BoundaryReached

    monkeypatch.setattr(planner, "_compile_volume_outline_prompt", record_compile)
    monkeypatch.setattr(planner, "complete_text", capture)
    with pytest.raises(BoundaryReached):
        await planner._generate_volume_outline_with_repair_loop(
            state.entry.session,
            state.settings,
            project=state.entry.project,
            workflow_run_id=state.entry.workflow.id,
            logical_name="volume_1_chapter_outline",
            book_spec=book,
            world_spec=world,
            cast_spec=cast,
            volume_plan=volumes,
            volume_entry={"volume_number": 1, "chapter_count_target": 1, "chapter_range": [1, 1]},
            fallback_payload={"chapters": []},
            volume_number=1,
            expected_count=1,
            chapter_number_offset=1,
            revealed_ledger_block=None,
            base_constraints=["Keep the exact source and selected range."],
        )
    assert len(logical) == len(physical) == 1
    request = physical[0]
    receipt = state.entry.project.metadata_json["planner_prompt_compiler_latest"][
        "source_transport"
    ]
    assert request.prompt_version == PROMPT_VERSION
    assert request.user_prompt.endswith(FORMAT_INSTRUCTION)
    assert request.user_prompt.count(FORMAT_INSTRUCTION) == 1
    assert receipt["wire_user_sha256"] == hashlib.sha256(request.user_prompt.encode()).hexdigest()
    assert (
        receipt["wire_system_sha256"] == hashlib.sha256(request.system_prompt.encode()).hexdigest()
    )
    assert (
        verify_source_complete_outline_transport(
            *logical[0], request.system_prompt, request.user_prompt, receipt
        )
        == receipt
    )
    wire_source = json.loads(request.user_prompt.split(OPEN)[1].split(CLOSE)[0])
    decoded = (
        decode_lossless_source(wire_source)
        if wire_source.get("schema", "").startswith("lossless-")
        else wire_source
    )
    original = json.loads(logical[0][1].split(OPEN)[1].split(CLOSE)[0])
    assert decoded == original
    assert (
        decoded["story_assets"]["book_spec"]["unknown_full_archive"] == book["unknown_full_archive"]
    )
    assert_capacity(request)
