"""Pure synthetic lineage checks; no retained N files, database or provider access."""

from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from test_conception_structure_protocol_recovery import case as recovery_case

from bestseller.services import conception_checkpoints as checkpoints
from bestseller.services import conception_structure_protocol_recovery as recovery
from bestseller.services import llm
from bestseller.services.budget import execution_operation_id
from bestseller.services.conception import _conception_structure_diagnosis
from bestseller.services.conception_structure_budget import KEY as COUNTS_KEY
from bestseller.services.conception_structure_repair import STRUCTURE_REPAIR_PROMPT_VERSION
from bestseller.services.creation_intent_contract import (
    ConceptionAttemptInput,
    CreationIntentContract,
)
from bestseller.services.genre_intent_contract import GenreIntentContract

PATH = (
    Path(__file__).resolve().parents[2]
    / "reports/continuous-recovery-2026-10-16/prepare_fresh_acceptance.py"
)
spec = importlib.util.spec_from_file_location("fresh_protocol_history_subject", PATH)
intake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(intake)
pytestmark = pytest.mark.unit
case = recovery_case  # Reuse the existing deterministic editor/proof pytest fixture.


def chain(case):
    """Real contract/hash/checkpoint/derive APIs with entirely synthetic paid-input proof."""
    contract = CreationIntentContract(
        genre_intent=GenreIntentContract(
            genre_key="fantasy", genre_label="Fantasy", prompt_pack_key="fantasy"
        ),
        chapter_count=30,
        stop_after_conception=True,
    )
    task_id, child_id = str(uuid4()), str(uuid4())
    envelope = {
        "creation_intent_contract": contract.model_dump(mode="json"),
        "creation_input_receipt": {"contract_hash": contract.contract_hash()},
        "_conception_attempt_id": task_id + ":initial",
        "_conception_workflow_run_id": child_id,
        "premise": "",
        "genre": "fantasy",
        "sub_genre": None,
        "target_chapters": 30,
        "user_hints": {},
    }
    state = {"task_id": task_id, "scope": case.scope}
    rows = []
    for wid, attempt_id, parent in (
        (str(case.wid), envelope["_conception_attempt_id"], None),
        (child_id, task_id + ":resume:" + uuid4().hex, str(case.wid)),
    ):
        attempt = ConceptionAttemptInput(
            contract=contract,
            attempt_id=attempt_id,
            idempotency_key=attempt_id,
            input_payload={
                k: envelope[k]
                for k in ("premise", "genre", "sub_genre", "target_chapters", "user_hints")
            },
        )
        stable = checkpoints.stable_conception_input_hash(attempt)
        cp_state = deepcopy(case.checkpoint["state"])
        cp_state["execution_operation_identity"] = {
            "version": "conception-operation.v1",
            "namespace": execution_operation_id(
                "conception", subject="final-candidate-review", revision=stable
            ),
            "sample_policy": "explicit-slot-or-zero; effective-input-distinguishes-revisions",
        }
        if parent:
            cp_state[COUNTS_KEY] = {"v1": 1, STRUCTURE_REPAIR_PROMPT_VERSION: 0}
            cp_state["conception_log"].append({"agent": "intent_review", "status": "unknown"})
        cp = checkpoints.build_conception_checkpoint(cp_state, input_hash=stable)
        metadata = {
            "execution_scope_id": case.scope,
            "creation_intent_hash": contract.contract_hash(),
            "attempt_id": attempt_id,
            "input_hash": attempt.input_hash(),
            "conception_checkpoint_input_hash": stable,
            "conception_checkpoint": cp,
        }
        if parent:
            metadata["source_conception_workflow_run_id"] = parent
        rows.append(
            {
                "id": wid,
                "status": "failed",
                "project_id": None,
                "workflow_type": "conception_initial",
                "scope_type": "creation",
                "scope_id": None,
                "idempotency_key": attempt_id,
                "metadata_json": metadata,
            }
        )
    cp = rows[0]["metadata_json"]["conception_checkpoint"]
    source = deepcopy(cp["state"])
    source["last_failure"].pop("structure_repair_error")
    user = intake.encoded({"state": source, "diagnosis": _conception_structure_diagnosis(source)})
    request = case.request.model_copy(update={"user_prompt": user})
    serialized = intake.encoded(request.model_dump(mode="json"))
    proof = deepcopy(case.proof)
    proof.update(
        checkpoint_sha256=intake.digest(cp),
        checkpoint_state_sha256=cp["state_sha256"],
        reconstructed_request=serialized,
        reconstructed_request_sha256=hashlib.sha256(serialized.encode()).hexdigest(),
        prompt_hash=llm._effective_input_hash(request, case.settings.llm.editor),
        user_chars=len(user),
        user_sha256=hashlib.sha256(user.encode()).hexdigest(),
        source_state_sha256=intake.digest(source),
    )
    receipt = recovery.derive(
        cp,
        workflow_id=rows[0]["id"],
        scope=case.scope,
        proof=proof,
        actor="authorized-offline-test",
        reason="Synthetic protocol transition for lineage checks",
    )
    rows[0]["metadata_json"][recovery.KEY] = receipt
    return state, {"payload": envelope}, rows


def test_ancestor_historical_policy_does_not_rederive_but_raw_checkpoint_stays_strict(
    case, monkeypatch
):
    state, task, rows = chain(case)
    receipt = rows[0]["metadata_json"][recovery.KEY]
    receipt["source_policy"] = {
        "protocol": STRUCTURE_REPAIR_PROMPT_VERSION,
        "files": {"services/intent_review_submission.py": "old-deployment-v2-hash"},
    }
    before = deepcopy((state, task, rows))
    derive = Mock(side_effect=AssertionError("Historical receipt must not be regranted"))
    monkeypatch.setattr(recovery, "derive", derive)
    result = intake.validate_linear_workflow_chain(state, task, rows)
    assert result["lineage_workflow_run_ids"] == [row["id"] for row in rows]
    child_cp = rows[1]["metadata_json"]["conception_checkpoint"]
    assert result["current_checkpoint"]["selected_checkpoint_sha256"] == intake.digest(child_cp)
    assert child_cp["state"][COUNTS_KEY] == {"v1": 1, STRUCTURE_REPAIR_PROMPT_VERSION: 0}
    assert child_cp["state"]["structure_repair_attempts"] == 1
    assert (state, task, rows) == before
    derive.assert_not_called()

    corrupt = deepcopy(rows)
    corrupt[0]["metadata_json"]["conception_checkpoint"]["state"]["title"] += "tampered"
    with pytest.raises(ValueError, match="state_sha256 mismatch"):
        intake.validate_linear_workflow_chain(state, task, corrupt)
    derive.assert_not_called()


def test_current_tip_matching_receipt_rederives_and_stale_policy_or_proof_fails(case, monkeypatch):
    state, task, rows = chain(case)
    rows = rows[:1]
    task["payload"]["_conception_workflow_run_id"] = rows[0]["id"]
    receipt = rows[0]["metadata_json"][recovery.KEY]
    before = deepcopy((state, task, rows))
    derive = Mock(wraps=recovery.derive)
    monkeypatch.setattr(recovery, "derive", derive)
    result = intake.validate_linear_workflow_chain(state, task, rows)
    derive.assert_called_once_with(
        rows[0]["metadata_json"]["conception_checkpoint"],
        workflow_id=rows[0]["id"],
        scope=state["scope"],
        proof=receipt["legacy_paid_proof"],
        actor=receipt["actor"],
        reason=receipt["reason"],
    )
    assert result["current_checkpoint"]["selected_checkpoint_sha256"] == intake.digest(
        receipt["derived_checkpoint"]
    )
    assert (state, task, rows) == before

    stale = deepcopy(rows)
    stale[0]["metadata_json"][recovery.KEY]["source_policy"] = {"old_deployment": True}
    with pytest.raises(ValueError, match="fresh_acceptance:resume_protocol_recovery_changed"):
        intake.validate_linear_workflow_chain(state, task, stale)
    assert derive.call_count == 2
    bad_proof = deepcopy(rows)
    bad_proof[0]["metadata_json"][recovery.KEY]["legacy_paid_proof"]["prompt_hash"] = "0" * 64
    with pytest.raises(ValueError, match="original_effective_input_hash"):
        intake.validate_linear_workflow_chain(state, task, bad_proof)
    assert derive.call_count == 3
    assert (state, task, rows) == before
