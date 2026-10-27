"""Fixed native policy epochs; every selector verifies all runtime bytes.

Issued history is original 8Y24, prior R09, A296, c90a and a reviewed fifth endpoint.
The superseded, unissued 3e51 endpoint is retained only as runtime-audit data.
Historical receipts are data to replay, never approval or runtime authority.
"""

import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OLD_POLICY = "1fd308264eae63f0f7ab761da701a50e971014d9e693c3c2bcec98181b111b3c"
PRIOR_POLICY = "db1b38eba0e68ad0bc0b431329865aa55ec7ddd5cb0c37c426031a745a1fd634"
ORIGINAL_SHA256 = "318cde073c31d22cbc46c0782f014a9afe1c864abe2ec9b3d714792079a1525b"
# current.json is the immutable R09 manifest, retained byte-for-byte.
CURRENT_SHA256 = "8dd6490f0dcf9984b98484ee73b582ad4e7e80a2dca9b79dff1ba91b1b6a685c"
A296_POLICY = "a2962a25cf438282381bc1e432a46496a6e2315148895f477f1f2fbc51b6623a"
A296_SHA256 = "ad84ee5bf4a0fcbee4a1d55ea39ef7d5039dc32718c2cd1ccf5c794bc55f3371"
# Final reviewed explicit source freezes, not runtime-discovered inventory.
# Never populate these from runtime bytes, environment, or an operator probe.
FINAL_SHA256 = "98d4f5ed89927c5235eebcea7e42c92edc613ad2f19464b0a5ef0d18378db1bb"
SCHEMA = "native-review-policy-compatibility.8y24-r04.v1"
_HELPER = "src/bestseller/services/fanqie_short_plan_resume.py"
_RECONCILIATION = "src/bestseller/services/chapter_review_reconciliation.py"
_SHORT_PIPELINE = "src/bestseller/services/fanqie_short_pipeline.py"
_BEAT = "src/bestseller/services/fanqie_beat_recovery.py"
_ADMISSION_READER = "src/bestseller/services/materialized_outline_admission_read.py"
_SHORT_PLANNER = "src/bestseller/services/fanqie_short_planner.py"
_SURGICAL_BUDGET = "src/bestseller/services/planning_surgical_budget.py"
_COMMERCIAL_RECOVERY = "src/bestseller/services/commercial_judge_recovery.py"
_EXECUTION_CONTROL_PURPOSE = "src/bestseller/services/execution_control_purpose.py"
C90A_POLICY = "c90a6d32172332d754ca4d59d39cd109e42b0b8444fcfa456ea73522a8aa9796"
C90A_SHA256 = "5a2697cc87cb330f9057160de77696d67eaab50d18aa67f91b2b0db5f2184d7d"
C90A_INITIALIZER_SHA256 = "ab82c88d9b8eb793fcd1dcc8ec28e9fd355ad7fd1d35db1cad5af87d2c7808e0"
C90A_INDEX_SHA256 = "dc30432eee11411ae979af3f5a85c4bc93c68fd847bba6304d4e4f724df196dd"
C90A_EDGES_SHA256 = "a1f55ecb445dc7ff40e69dc508adbfafb6a1b681921be1273af7acbbadfde32f"
EPOCH3_POLICY = "1a5a25074912ecd42191f4fdd5c0be4e80d00414cc6dc4e84526174e9ebf1530"
EPOCH3_FINAL_SHA256 = "98d4f5ed89927c5235eebcea7e42c92edc613ad2f19464b0a5ef0d18378db1bb"
REVIEWED_EPOCH3_SOURCE_SHA256 = {
    _COMMERCIAL_RECOVERY: "0c929fff84e3075996c25cc11b589df2c42bc619e6bd53acda1ef4e7d5a65fd7",
    _EXECUTION_CONTROL_PURPOSE:
        "70e09a76e170c9ba139c3135d597dfaabc05a018cc1013de0cb2c543bbc0a3c8",
    _RECONCILIATION: "1af250591dfb6cbd1fb014a6633332e383111e4c17ad2738ba7757fef36f1560",
}
REVIEWED_EPOCH3_PROOF_AST_SHA256 = (
    "123cbdd4d4786edacba18dd50d64071f74511a7f97827fafccaaf294531c2534"
)
REVIEWED_EPOCH3_PAYLOAD_AST_SHA256 = (
    "c48fbe4b92ccebe4efcbdddecb4b3d117f8f7afc0f5e39428c3c664a4384e33f"
)
REVIEWED_EPOCH3_PAID_RETURN_AST_SHA256 = (
    "d6adf5d3727ef69188bdee1d439dc963d9e87c2a4948a7ed8933f18767590ba0"
)
REVIEWED_EPOCH3_CONTROL_AST_SHA256 = (
    "5f3e94eb85ad166eef3157c2cdd0b4c7dd3653f7aa5bc76093df96c919b15342"
)
REVIEWED_EPOCH3_CONTROL_VERIFIER_AST_SHA256 = (
    "9069b7a04a3c3f6a32ae7b24332bad6b01cf3dc45db739ad78a022230228b382"
)
REVIEWED_EPOCH3_RECONCILIATION_AST_SHA256 = (
    "f2ec0040d728bc544e5f4afd8ce27d4834609f6e3f86347744d8c560cde3fa18"
)
_WRITING_CONSUMERS = frozenset(
    "src/bestseller/services/" + name + ".py" for name in (
        "outline_source_entry", "editable_plan_source", "chapter_writer_delivery",
        "chapter_scene_contract_materializer", "drafts",
    )
)
REVIEWED_BEAT_RESUME_SHA256 = {
    _HELPER: "d6825381f22bae63fb2be56e3c5cdaa5d0d76a300200e11dc78e5905a3738782",
    _SHORT_PIPELINE: "2e7eaaca62bdc471022d398f186d91f9209d7a746399f931e5abd87539699b72",
    _BEAT: "e3982c8d3152ef51c3903492a99bad54a32f6299ed47b8b8a6c835fc56e83dc4",
    _ADMISSION_READER: "a7b8d5012fa4eec5cb0d4fe95fb2cc15c7d0a6d947fbbde157b4586ba496965e",
    _SHORT_PLANNER: "5883bab27d45c6e18e625331482d01fb48dd40afcaa2add5d1b72da7a3cf9b5f",
    _SURGICAL_BUDGET: "959d5b2ceb19b40d368ac7ef0d9f2f827eab84e418203d2e55bcebc595b66974",
    "src/bestseller/services/outline_source_entry.py":
        "76faf343b2bb00eed318f96923835b521d6d9aba23a04d4097b0a710c39c3143",
    "src/bestseller/services/editable_plan_source.py":
        "6cb034751fd2889b8c827e4edaa7ca070fef95e2319784dfd8a94c1f4aaafd47",
    "src/bestseller/services/chapter_writer_delivery.py":
        "3d3bb88acd6191167f66d7191464af03c0951215cb0f202ed41fafd9ae20a963",
    "src/bestseller/services/chapter_scene_contract_materializer.py":
        "474dac3f7fffcac78495f31ec6deaa26ed7c2011ba9e56278f2d00702e2a2b86",
    "src/bestseller/services/drafts.py":
        "9d58795abfc76911d06751b056e512e19bcda24d7db05e33846c180e7c084bd4",
}
_A296_SNAPSHOTS = frozenset((_HELPER, _SHORT_PIPELINE, _BEAT, _RECONCILIATION))
UNISSUED_POLICY = "3e51eb1808c2f9970d6b668a5e27643a096c7522b59cc98fa464cc313022f40f"
UNISSUED_SHA256 = "78c202ca2204c2c12477558b2f590c294e2ee8f79d2e3de62dc19d1107e7e7c6"
UNISSUED_FILE_PINS = {
    "final.json": UNISSUED_SHA256,
    "__init__.py": "e52f4c79033f381033f7381d49dda0e1cec606dfbfda5ee78e6254faa2ccc1b4",
    _HELPER: "cc66105fc0cb43a7185464d25630981833c18ade5e6247b849bd61ad58491cf5",
    _SHORT_PIPELINE: "f22c3cac22e7070c4771a789f64e169e9486575ca0401cad88ae660aaa46da82",
    _BEAT: "e3982c8d3152ef51c3903492a99bad54a32f6299ed47b8b8a6c835fc56e83dc4",
    _RECONCILIATION: "2d6c6f3027f4483082c6858f4fbd2c8c2d43a8ecfd6952f9a65c88a0730c7df1",
}
# Literal pins for the main-supplied, read-only audit and its local archive index.
# They attest saved evidence, never a production read by this package/worker.
UNISSUED_AUDIT_SHA256 = "8069a95be024d4d6c33f2b3dab421b6e136286d173491830da7c7073be49ad00"
UNISSUED_INDEX_SHA256 = "1921f0a4629fed8087c81fb99e17329385ffb19b3fc168e2b2d7f07c1161a71a"
UNISSUED_CONSUMER_INDEX_SHA256 = "1e6fc8cdfb1cd49a2bf4807f799a34c90c76eb4136862d05792fee4ea7661539"
UNISSUED_CONSUMER_FILE_PINS = {
    _ADMISSION_READER: "37eadff37ee6101e0d591f2f655321f8cfca24df7f765ad43cf7efd601ae632d",
    "src/bestseller/services/outline_source_entry.py":
        "28029611429eb3561bc4780a4d3650b05c10e20d096f908444662a03826276a1",
    "src/bestseller/services/editable_plan_source.py":
        "46570e8ea27a3cc5884619cd930fdcc43e99790d26d8afbf6a0f605ac08ec656",
    "src/bestseller/services/chapter_writer_delivery.py":
        "2ab77454cdf8de2c7e5ade7d4eb82cd3fb20669b889935b5c4d967d338cd7eb7",
    "src/bestseller/services/chapter_scene_contract_materializer.py":
        "5479a8a365f81889cd3bdaf2bbab9c23d71ff2805d3138696cbb96f453b1e596",
    "src/bestseller/services/drafts.py":
        "243f4f9d280919455e6f7e2e97cc0f33f69418d6f0e55f79d6a866f9af2a9da1",
}
UNISSUED_PLANNER_SHA256 = "2e3475f459af2dd622cec591f8ff66eb57e38d259415d9f776f338d573c844c3"
UNISSUED_PLANNER_INDEX_SHA256 = "dc5bbffdd85d3d1b4aed1ad3b498514a5c66651e16fce806aa6b104e089de264"
UNISSUED_BUDGET_SHA256 = "cecd04653c244f9c4013730aef948df11d5a4c7a17d20b289301d50ace2cd13c"
UNISSUED_BUDGET_INDEX_SHA256 = "14722dca0522d9ac25133e3e858e1ca163717ec9f8273a91f4fe1013bd481173"
_ENDPOINT_SOURCE_FILES = {_HELPER, _SHORT_PIPELINE, _SHORT_PLANNER, _SURGICAL_BUDGET,
                          *UNISSUED_CONSUMER_FILE_PINS}
_CURRENT_DELTA_FILES = _A296_SNAPSHOTS | _ENDPOINT_SOURCE_FILES
# Structural review of exact reader/consumer ASTs supplements explicit final
# source pins. These values never populate a runtime or final manifest hash.
REVIEWED_ENDPOINT_AST_SHA256 = {
    _ADMISSION_READER: "e67077251b467c8d5b97af2981ba4a8d5246274c4357faa8262dfab2e6831ac0",
    "src/bestseller/services/outline_source_entry.py":
        "f1e53d10065acee7f74cb7b78143a0c1c449aa1af9fe9bae3991989b7c9dedd4",
    "src/bestseller/services/editable_plan_source.py":
        "11503e8a089c33bf185628a5202c6eda4f46388a3ea518c05af3c5e35cbdabfc",
    "src/bestseller/services/chapter_writer_delivery.py":
        "b57e983798074b5b3d462f07fcad724594acf5218f2d93350a9e95ff69058ad9",
    "src/bestseller/services/chapter_scene_contract_materializer.py":
        "9a0000e0253a551ea2fa4655e6d0529624f5256fd9daaeaf4dda6e97bf7bb2af",
    "src/bestseller/services/drafts.py":
        "dac311a89f21da2a574539fe0825e694f87adfbf63a544f7ba537d534ea61773",
}
REVIEWED_ADMISSION_INSERTION = '''
await _checkpoint_commit(session)
from bestseller.services.fanqie_outline_source_selection import admit_and_select_outline_source
await admit_and_select_outline_source(
    session, settings, project_id=project.id,
    artifact_id=outline_result.source_artifact_id,
    materialization_workflow_id=outline_result.workflow_run_id,
    requested_by=requested_by,
)
await _checkpoint_commit(session)
'''
REVIEWED_BUDGET_BRANCH = '''
if (request.logical_role == "planner"
        and request.prompt_template == FANQIE_FIELDS_TEMPLATE
        and request.prompt_version == FANQIE_FIELDS_VERSION):
    return True
'''
REVIEWED_BUDGET_REGISTRATION = '''
FANQIE_FIELDS_TEMPLATE = "planner_structured_field_correction"
FANQIE_FIELDS_VERSION = "fanqie-plan-fields-v1"
'''


def _need(value, code):
    if not value:
        raise ValueError("native_review_policy_" + code)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _document(name, expected):
    raw = (HERE / name).read_bytes()
    _need(_sha(raw) == expected, "manifest_not_frozen")
    return json.loads(raw)


def _aggregate(files):
    digest = hashlib.sha256()
    for name, value in sorted(files.items()):
        digest.update(name.encode() + b"\0")
        digest.update(bytes.fromhex(value))
    return digest.hexdigest()


def original_bytes(relative):
    manifest = _document("original.json", ORIGINAL_SHA256)
    files = {**manifest["inventory"], **manifest["additional_sources"]}
    _need(relative in files, "unknown_original_source")
    raw = (HERE / "bytes" / (relative + ".source")).read_bytes()
    _need(_sha(raw) == files[relative], "original_source_changed")
    return raw


def prior_bytes(relative):
    """Reconstruct exact db1b bytes from original bytes plus its fixed delta."""
    original = _document("original.json", ORIGINAL_SHA256)
    prior = _document("current.json", CURRENT_SHA256)
    _need(
        (HERE / "prior-current.json").read_bytes() == (HERE / "current.json").read_bytes(),
        "prior_manifest_changed",
    )
    files = {**prior["inventory"], **prior["additional_sources"]}
    old = {**original["inventory"], **original["additional_sources"]}
    _need(relative in files and relative in old, "unknown_prior_source")
    raw = (
        original_bytes(relative)
        if files[relative] == old[relative]
        else (HERE / "prior-bytes" / (relative + ".source")).read_bytes()
    )
    _need(_sha(raw) == files[relative], "prior_source_changed")
    return raw


def a296_bytes(relative):
    """Recover A296 only from pinned historical bytes, never live files."""
    prior = _document("current.json", CURRENT_SHA256)
    current = _document("a296-final.json", A296_SHA256)
    files = {**current["inventory"], **current["additional_sources"]}
    previous = {**prior["inventory"], **prior["additional_sources"]}
    _need(relative in files and relative in previous, "unknown_a296_source")
    if relative in _A296_SNAPSHOTS:
        raw = (HERE / "a296-bytes" / (relative + ".source")).read_bytes()
    else:
        _need(files[relative] == previous[relative], "unarchived_a296_delta")
        raw = prior_bytes(relative)
    _need(_sha(raw) == files[relative], "a296_source_changed")
    return raw


def unissued_bytes(relative):
    """Reconstruct the unissued runtime for audit, never for alias selection."""
    manifest = _document("unissued-3e51/final.json", UNISSUED_SHA256)
    files = {**manifest["inventory"], **manifest["additional_sources"]}
    _need(relative in files, "unknown_unissued_source")
    if relative in _A296_SNAPSHOTS:
        raw = (HERE / "unissued-3e51" / "bytes" / (relative + ".source")).read_bytes()
        _need(_sha(raw) == UNISSUED_FILE_PINS[relative], "unissued_source_changed")
    elif relative in UNISSUED_CONSUMER_FILE_PINS:
        raw = (HERE / "unissued-3e51" / "consumer-bytes" / (relative + ".source")).read_bytes()
        _need(_sha(raw) == UNISSUED_CONSUMER_FILE_PINS[relative], "unissued_source_changed")
    elif relative == _SHORT_PLANNER:
        raw = (HERE / "unissued-3e51" / "consumer-bytes" / (relative + ".source")).read_bytes()
        _need(_sha(raw) == UNISSUED_PLANNER_SHA256, "unissued_source_changed")
    elif relative == _SURGICAL_BUDGET:
        raw = (HERE / "unissued-3e51" / "consumer-bytes" / (relative + ".source")).read_bytes()
        _need(_sha(raw) == UNISSUED_BUDGET_SHA256, "unissued_source_changed")
    else:
        raw = a296_bytes(relative)
    _need(_sha(raw) == files[relative], "unissued_source_changed")
    return raw


def verified_unissued_runtime_audit():
    """Verify main's saved zero-consumer evidence and all frozen runtime bytes."""
    index = _document("unissued-3e51/manifest.json", UNISSUED_INDEX_SHA256)
    audit = _document("unissued-3e51/consumer-audit.json", UNISSUED_AUDIT_SHA256)
    consumers = _document("unissued-3e51/consumer-preimages.json",
                          UNISSUED_CONSUMER_INDEX_SHA256)
    planner = _document("unissued-3e51/planner-preimage.json", UNISSUED_PLANNER_INDEX_SHA256)
    budget = _document("unissued-3e51/budget-preimage.json", UNISSUED_BUDGET_INDEX_SHA256)
    _need(budget.get("file_pins") == {_SURGICAL_BUDGET: UNISSUED_BUDGET_SHA256}
          and budget.get("policy_hash") == UNISSUED_POLICY
          and budget.get("manifest_sha256") == UNISSUED_SHA256
          and budget.get("runtime_audit_only") is True
          and budget.get("alias_authority") is False, "unissued_budget_index_changed")
    _need(planner.get("file_pins") == {_SHORT_PLANNER: UNISSUED_PLANNER_SHA256}
          and planner.get("policy_hash") == UNISSUED_POLICY
          and planner.get("manifest_sha256") == UNISSUED_SHA256
          and planner.get("runtime_audit_only") is True
          and planner.get("alias_authority") is False, "unissued_planner_index_changed")
    _need(consumers.get("file_pins") == UNISSUED_CONSUMER_FILE_PINS
          and consumers.get("policy_hash") == UNISSUED_POLICY
          and consumers.get("manifest_sha256") == UNISSUED_SHA256
          and consumers.get("runtime_audit_only") is True
          and consumers.get("alias_authority") is False, "unissued_consumer_index_changed")
    _need(
        index.get("runtime_audit_only") is True and index.get("alias_authority") is False
        and index.get("policy_hash") == UNISSUED_POLICY
        and index.get("manifest_sha256") == UNISSUED_SHA256
        and index.get("file_pins") == UNISSUED_FILE_PINS
        and index.get("consumer_audit", {}).get("sha256") == UNISSUED_AUDIT_SHA256
        and index.get("consumer_audit", {}).get(
            "production_read_performed_by_this_worker") is False,
        "unissued_audit_index_changed",
    )
    counts = audit.get("counts", {})
    _need(
        audit.get("schema") == "native-unissued-review-epoch-audit.v1"
        and audit.get("epoch") == UNISSUED_POLICY and audit.get("read_only") is True
        and set(counts) == {
            "llm_runs_since_e3_runtime", "execution_attempts_since_e3_runtime",
            "quality_scores_since_e3_runtime", "review_reports_since_e3_runtime",
            "chapter_metadata_epoch_mentions",
        }
        and all(type(value) is int and value == 0 for value in counts.values())
        and type(audit.get("provider_calls")) is int and audit["provider_calls"] == 0
        and type(audit.get("database_writes")) is int and audit["database_writes"] == 0
        and index.get("counts") == counts,
        "unissued_consumer_evidence_required",
    )
    for name in ("final.json", "__init__.py"):
        _need(_sha((HERE / "unissued-3e51" / name).read_bytes()) == UNISSUED_FILE_PINS[name],
              "unissued_initialization_changed")
    manifest = _document("unissued-3e51/final.json", UNISSUED_SHA256)
    a296 = _document("a296-final.json", A296_SHA256)
    _need(manifest["policy_hash"] == UNISSUED_POLICY
          and len(manifest["inventory"]) == 263
          and _aggregate(manifest["inventory"]) == UNISSUED_POLICY
          and set(manifest["inventory"]) == set(a296["inventory"])
          and set(manifest["additional_sources"]) == set(a296["additional_sources"]),
          "unissued_policy_inventory")
    for relative in {**manifest["inventory"], **manifest["additional_sources"]}:
        unissued_bytes(relative)
    return index


def _ast(node):
    return ast.dump(node, include_attributes=False)


def c90a_bytes(relative):
    """Read issued c90a bytes only from its complete, manifest-pinned archive."""
    manifest = _document("issued-c90a/final.json", C90A_SHA256)
    files = {**manifest["inventory"], **manifest["additional_sources"]}
    _need(relative in files, "unknown_c90a_source")
    raw = (HERE / "issued-c90a" / "bytes" / (relative + ".source")).read_bytes()
    _need(_sha(raw) == files[relative], "c90a_source_changed")
    return raw


def verified_c90a_archive():
    """c90a has real issued consumers; never classify it as an unissued epoch."""
    index = _document("issued-c90a/manifest.json", C90A_INDEX_SHA256)
    manifest = _document("issued-c90a/final.json", C90A_SHA256)
    files = {**manifest["inventory"], **manifest["additional_sources"]}
    _need(index.get("issued") is True and index.get("policy_hash") == C90A_POLICY
          and index.get("manifest_sha256") == C90A_SHA256
          and index.get("initializer_sha256") == C90A_INITIALIZER_SHA256
          and index.get("file_pins") == files
          and index.get("inventory_count") == 263
          and index.get("additional_source_count") == len(manifest["additional_sources"]),
          "issued_c90a_index_changed")
    _need(manifest["policy_hash"] == C90A_POLICY and len(manifest["inventory"]) == 263
          and _aggregate(manifest["inventory"]) == C90A_POLICY, "issued_c90a_inventory")
    _need(_sha((HERE / "issued-c90a" / "__init__.py").read_bytes())
          == C90A_INITIALIZER_SHA256, "issued_c90a_initializer_changed")
    for relative in files:
        c90a_bytes(relative)
    return manifest


def known_c90a_transitions():
    """Exact six issued dictionaries, including their original manifest digests."""
    c90a = verified_c90a_archive()
    saved = _document("issued-c90a/known-forward-edges.json", C90A_EDGES_SHA256)
    documents = (
        _document("original.json", ORIGINAL_SHA256),
        _document("current.json", CURRENT_SHA256),
        _document("a296-final.json", A296_SHA256), c90a,
    )
    shas = (ORIGINAL_SHA256, CURRENT_SHA256, A296_SHA256, C90A_SHA256)
    expected = [_proof(documents[i], documents[j], shas[i], shas[j])
                for i in range(4) for j in range(i + 1, 4)]
    _need(saved.get("issued_c90a") is True and saved.get("transitions") == expected,
          "issued_c90a_edges_changed")
    return tuple(saved["transitions"])


def _a296_delta_boundary(previous, current, relative):
    """Extra structural fence, in addition to exact full-file hash pins."""
    before, after = ast.parse(previous), ast.parse(current)
    if relative == _RECONCILIATION:
        allowed = {
            "validate_native_policy_alias",
            "native_waiting_record_binding",
            "_prepare_native_chapter_resume",
        }
        # These are the only old definitions changed. Newly added internals
        # are still exact-byte pinned by final.json.
        old_defs = {
            n.name: n for n in before.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        new_defs = {
            n.name: n for n in after.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        _need(set(old_defs) <= set(new_defs), "reconciliation_definition_removed")
        _need(
            {n for n in old_defs if _ast(old_defs[n]) != _ast(new_defs[n])} == allowed,
            "reconciliation_delta_boundary",
        )
        old_other = [
            n for n in before.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        new_other = [
            n for n in after.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        _need(
            [_ast(n) for n in old_other] == [_ast(n) for n in new_other],
            "reconciliation_module_boundary",
        )
    elif relative == _HELPER:
        name = "read_existing_complete_fanqie_plan"
        old_fn = next(
            n for n in before.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name
        )
        new_fn = next(
            n for n in after.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name
        )

        def strict_tail(fn):
            block = next(n for n in fn.body if isinstance(n, ast.With))
            # Every original materialized proof statement from target_range
            # through the function return must stay exactly the same.
            index = next(
                i
                for i, n in enumerate(block.body)
                if isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Call)
                and any(
                    isinstance(a, ast.Constant) and a.value == "target_range" for a in n.value.args
                )
            )
            return [_ast(n) for n in block.body[index:]]

        _need(strict_tail(old_fn) == strict_tail(new_fn), "materialized_proof_changed")

        # Every top-level class/function except this classifier stays exact.
        def unaffected(module):
            return {
                n.name: _ast(n)
                for n in module.body
                if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name != name
            }

        _need(unaffected(before) == unaffected(after), "planning_other_definition_changed")
    else:
        _need(False, "unknown_epoch_delta")


def _proof(previous, current, previous_manifest_sha, current_manifest_sha):
    old = {**previous["inventory"], **previous["additional_sources"]}
    new = {**current["inventory"], **current["additional_sources"]}
    return {
        "schema": SCHEMA,
        "previous_policy_hash": previous["policy_hash"],
        "current_policy_hash": current["policy_hash"],
        "original_manifest_sha256": previous_manifest_sha,
        "current_manifest_sha256": current_manifest_sha,
        "reviewed_delta_files": sorted(n for n in old if old[n] != new[n]),
        "provider_calls": 0,
        "new_credit": False,
        "dispatch_authorized": False,
        "grade_recomputed": False,
    }


def _historical_beat_resume_boundary(previous, current, relative):
    """Structural fences supplement, never replace, reviewed whole-file pins."""
    if relative == _HELPER:
        before, after = ast.parse(previous), ast.parse(current)
        name = "read_existing_complete_fanqie_plan"
        function = next(n for n in after.body if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == name)
        block = next(n for n in function.body if isinstance(n, ast.With))
        index = next(i for i, n in enumerate(block.body)
                     if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                     and any(isinstance(a, ast.Constant) and a.value == "target_range"
                             for a in n.value.args))
        branch = block.body[index - 1]
        _need(isinstance(branch, ast.If) and any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "read_selected_foundation_stage"
            for n in ast.walk(branch)), "foundation_classification_boundary")
        del block.body[index - 1]
        _need(_ast(before) == _ast(after), "foundation_materialized_or_other_code_changed")
    elif relative == _SHORT_PIPELINE:
        before, after = ast.parse(previous), ast.parse(current)
        name = "run_fanqie_short_pipeline"

        def parts(module):
            function = next(n for n in module.body if isinstance(n, ast.AsyncFunctionDef)
                            and n.name == name)
            read_index = next(i for i, n in enumerate(function.body)
                              if isinstance(n, ast.Assign) and isinstance(n.value, ast.Await)
                              and isinstance(n.value.value, ast.Call)
                              and isinstance(n.value.value.func, ast.Name)
                              and n.value.value.func.id
                              == "require_or_read_existing_complete_fanqie_plan")
            stage_index = next(i for i, n in enumerate(function.body)
                               if isinstance(n, ast.If) and any(
                                   isinstance(v, ast.Assign) and isinstance(v.value, ast.Call)
                                   and isinstance(v.value.func, ast.Name)
                                   and v.value.func.id == "build_fanqie_segment_outline_batch"
                                   for v in n.body))
            stage = function.body[stage_index]
            projection_index = next(i for i, n in enumerate(stage.body)
                                    if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                                    and isinstance(n.value.func, ast.Name)
                                    and n.value.func.id == "build_fanqie_segment_outline_batch")
            beat = next(n for n in stage.body if isinstance(n, ast.Assign)
                        and isinstance(n.value, ast.Await)
                        and isinstance(n.value.value, ast.Call)
                        and isinstance(n.value.value.func, ast.Name)
                        and n.value.value.func.id == "generate_fanqie_beat_sheet")
            protected = [*function.body[:read_index + 1], *stage.body[projection_index:],
                         *stage.orelse, *function.body[stage_index + 1:]]
            signature = (_ast(function.args), [_ast(n) for n in function.decorator_list],
                         _ast(function.returns) if function.returns else None,
                         function.type_comment, _ast(stage.test), _ast(beat))
            function.body = []
            return [_ast(n) for n in protected], signature, _ast(module)

        _need(parts(before) == parts(after), "short_pipeline_nonplanning_code_changed")
    else:
        # Beat producer and reconciliation are approved by complete-file pins;
        # their internals are not normalized out of runtime verification.
        _need(relative in (_BEAT, _RECONCILIATION), "unknown_beat_resume_delta")


def _exact_delta_boundary(previous, current, relative):
    """Allow literal planning insertions only; compare the entire remaining AST."""
    _need(relative in _ENDPOINT_SOURCE_FILES, "unknown_endpoint_delta")
    _need(previous == unissued_bytes(relative), "endpoint_preimage_changed")
    before, after = ast.parse(previous), ast.parse(current)
    if relative == _SURGICAL_BUDGET:
        _need(REVIEWED_BUDGET_BRANCH is not None, "prospective_budget_api_not_reviewed")
        function = next(n for n in before.body if isinstance(n, ast.FunctionDef)
                        and n.name == "applies")
        index = before.body.index(function)
        before.body[index:index] = ast.parse(REVIEWED_BUDGET_REGISTRATION).body
        function.body[0:0] = ast.parse(REVIEWED_BUDGET_BRANCH).body
        _need(_ast(before) == _ast(after), "budget_nonprospective_changed")
        return
    if relative in UNISSUED_CONSUMER_FILE_PINS:
        expected = REVIEWED_ENDPOINT_AST_SHA256[relative]
        _need(expected is not None, "endpoint_ast_not_reviewed:" + relative)
        _need(_sha(_ast(after).encode()) == expected, "endpoint_consumer_ast_changed:" + relative)
        return
    if relative == _SHORT_PLANNER:
        function = next(n for n in before.body if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == "persist_fanqie_chapter_outline")
        function.body[1:1] = ast.parse('''
content = batch.model_dump(mode="json")
if "_meta" in outline_payload:
    from copy import deepcopy
    if not isinstance(outline_payload["_meta"], dict):
        raise ValueError("fanqie_outline_metadata_must_be_object")
    content["_meta"] = deepcopy(outline_payload["_meta"])
''').body
        matches = [keyword for node in ast.walk(function)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "PlanningArtifactCreate" for keyword in node.keywords
                   if keyword.arg == "content"]
        _need(len(matches) == 1 and _ast(matches[0].value) == _ast(
            ast.parse('batch.model_dump(mode="json")', mode="eval").body),
            "planner_typed_body_preimage_changed")
        matches[0].value = ast.Name(id="content", ctx=ast.Load())
        _need(_ast(before) == _ast(after), "planner_nonmetadata_changed")
        return
    if relative == _HELPER:
        function = next(n for n in before.body if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == "read_existing_complete_fanqie_plan")
        block = next(n for n in function.body if isinstance(n, ast.With))
        index = next(i for i, n in enumerate(block.body)
                     if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                     and any(isinstance(a, ast.Constant) and a.value == "target_range"
                             for a in n.value.args))
        branch = block.body[index - 1]
        _need(isinstance(branch, ast.If) and isinstance(branch.test, ast.BoolOp),
              "endpoint_helper_prefix_boundary")
        old = ast.parse('not any(a.artifact_type in {"chapter_outline_batch", '
                        '"volume_chapter_outline"} for a in artifacts)', mode="eval").body
        matches = [i for i, n in enumerate(branch.test.values) if _ast(n) == _ast(old)]
        _need(len(matches) == 1, "endpoint_helper_prefix_boundary")
        branch.test.values[matches[0]] = ast.parse(
            'not any(a.artifact_type == "volume_chapter_outline" for a in artifacts)',
            mode="eval").body
        marker = next(i for i, n in enumerate(block.body)
                      if isinstance(n, ast.Assign) and len(n.targets) == 1
                      and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "markers")
        block.body[marker:marker + 1] = ast.parse('''
from bestseller.services.fanqie_outline_source_selection import SELECTION_KEY
full_selected = any(MARKER_KEY in (c.metadata_json or {}) for c in chapters)
ordinary_selected = any(SELECTION_KEY in (c.metadata_json or {}) for c in chapters)
_need(not (full_selected and ordinary_selected), "conflicting_selection_kinds")
selection_key = SELECTION_KEY if ordinary_selected else MARKER_KEY
markers = [(c.metadata_json or {}).get(selection_key) for c in chapters]
if ordinary_selected:
    _need(not any("outline_prefix_readiness" in (c.metadata_json or {})
                  for c in chapters), "conflicting_selection_kinds")
''').body
        _need(_ast(before) == _ast(after), "endpoint_helper_nonprefix_changed")
    else:
        _need(REVIEWED_ADMISSION_INSERTION is not None, "outline_admission_api_not_reviewed")
        function = next(n for n in before.body if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == "run_fanqie_short_pipeline")
        stage = next(n for n in function.body if isinstance(n, ast.If) and any(
            isinstance(v, ast.Assign) and isinstance(v.value, ast.Call)
            and isinstance(v.value.func, ast.Name)
            and v.value.func.id == "build_fanqie_segment_outline_batch" for v in n.body))
        projection = next(i for i, n in enumerate(stage.body)
                          if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                          and isinstance(n.value.func, ast.Name)
                          and n.value.func.id == "build_fanqie_segment_outline_batch")
        completion = ast.parse('''
from bestseller.services.fanqie_outline_contract_completion import complete_missing_outline_contract
outline_payload = await complete_missing_outline_contract(
    session, settings, project_id=project.id, outline_payload=outline_payload,
    requested_by=requested_by,
)
''').body
        stage.body[projection + 1:projection + 1] = completion
        # The reviewed literal hook will be inserted immediately after chapter
        # materialization. It cannot remove, reorder or rewrite any old code.
        materialization = next(i for i, n in enumerate(stage.body)
                               if isinstance(n, ast.Assign) and isinstance(n.value, ast.Await)
                               and isinstance(n.value.value, ast.Call)
                               and isinstance(n.value.value.func, ast.Name)
                               and n.value.value.func.id
                               == "materialize_latest_chapter_outline_batch")
        stage.body[materialization + 1:materialization + 1] = ast.parse(
            REVIEWED_ADMISSION_INSERTION).body
        _need(_ast(before) == _ast(after), "endpoint_pipeline_nonplanning_changed")


def _beat_resume_boundary(previous, current, relative):
    frozen = unissued_bytes(relative)
    if relative in {*UNISSUED_CONSUMER_FILE_PINS, _SHORT_PLANNER, _SURGICAL_BUDGET}:
        _need(previous == frozen, "consumer_historical_preimage_changed")
    else:
        _historical_beat_resume_boundary(previous, frozen, relative)
    if relative in _ENDPOINT_SOURCE_FILES:
        _exact_delta_boundary(frozen, current, relative)
    else:
        _need(current == frozen, "fixed_beat_or_reconciliation_changed")


def _epoch3_delta_boundary(previous, current, relative):
    """Only the reviewed proof adapter or the exact fifth-target depth context."""
    _need(relative in REVIEWED_EPOCH3_SOURCE_SHA256, "unknown_fifth_delta")
    _need(previous == c90a_bytes(relative), "fifth_preimage_changed")
    before, after = ast.parse(previous), ast.parse(current)
    if relative == _EXECUTION_CONTROL_PURPOSE:
        function = next(n for n in after.body if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == "verify_non_dispatch_control")
        prefix = function.body.pop(0)
        _need(isinstance(prefix, ast.If) and _sha(_ast(prefix).encode())
              == REVIEWED_EPOCH3_CONTROL_VERIFIER_AST_SHA256,
              "fifth_control_verifier_prefix_changed")
        _need(_ast(before) == _ast(after), "fifth_control_verifier_other_ast_changed")
        return
    if relative == _COMMERCIAL_RECOVERY:
        additions = [n for n in after.body if (
            isinstance(n, ast.AsyncFunctionDef) and n.name in (
                "prove_native_conception_request", "_prove_consumed_logline_control")
        ) or (
            isinstance(n, ast.FunctionDef) and n.name in (
                "_native_conception_payloads", "_native_conception_paid_return")
        ) or (isinstance(n, ast.Assign) and len(n.targets) == 1
              and isinstance(n.targets[0], ast.Name)
              and n.targets[0].id == "_NATIVE_CONCEPTION_ROUTES")]
        readers = [n for n in additions if isinstance(n, ast.FunctionDef)]
        _need(len(additions) == 5 and len(readers) == 2,
              "fifth_proof_additions_changed")
        reader_pins = {
            "_native_conception_payloads": REVIEWED_EPOCH3_PAYLOAD_AST_SHA256,
            "_native_conception_paid_return": REVIEWED_EPOCH3_PAID_RETURN_AST_SHA256,
        }
        _need(all(reader_pins.values()), "fifth_payload_reader_not_reviewed")
        _need(all(_sha(_ast(reader).encode()) == reader_pins[reader.name]
                  for reader in readers), "fifth_payload_reader_changed")
        proof = next(n for n in additions if isinstance(n, ast.AsyncFunctionDef)
                     and n.name == "prove_native_conception_request")
        control = next(n for n in additions if isinstance(n, ast.AsyncFunctionDef)
                       and n.name == "_prove_consumed_logline_control")
        _need(_sha(_ast(control).encode()) == REVIEWED_EPOCH3_CONTROL_AST_SHA256,
              "fifth_consumed_control_proof_changed")
        routes = next(n for n in additions if isinstance(n, ast.Assign))
        expected = ast.parse('''
_NATIVE_CONCEPTION_ROUTES = frozenset({
    ("critic", "conception_source_effect_contract", None),
    ("critic", "conception_explicit_seed_fidelity_judge", None),
    ("critic", "conception_explicit_seed_fidelity_arbitration", None),
    ("critic", "conception_final_material_seed_fidelity_judge", None),
    ("critic", "conception_reconciled_seed_fidelity_judge", None),
    ("planner", "conception_story_spine_polish", None),
    ("editor", "concept_tournament_winner_seed_repair", None),
    ("editor", "conception_explicit_seed_fidelity_repair", None),
})
''').body[0]
        _need(_ast(routes) == _ast(expected), "fifth_proof_routes_changed")
        _need(_sha(_ast(proof).encode()) == REVIEWED_EPOCH3_PROOF_AST_SHA256,
              "fifth_physical_proof_changed")
        for node in additions:
            after.body.remove(node)
        expected_call = ast.parse('''
if settings is not None:
    root = operation_by_key[parent_key]
    native_purpose = await prove_native_conception_request(
        session, settings, project_id=project_id, operation=root,
        actual_attempts=[a for a in attempts if a.operation_key == parent_key],
        original_runs=[r for r in route_runs if (
            (r.metadata_json or {}).get("execution_receipt", {}).get("operation_key") == parent_key
            and (r.metadata_json or {}).get("execution_receipt", {}).get("scope_id") == scope
            and r.prompt_hash == root.input_hash)],
        transport_results=[o for o in operations if o.status == "transport_result"
            and isinstance(o.result_json, Mapping)
            and o.result_json.get("operation_key") == parent_key],
    )
    if native_purpose is not None:
        continue
''').body[0]
        function = next(n for n in after.body if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == "_assert_no_unbound_commercial_spend")
        matches = []
        for node in ast.walk(function):
            for _, value in ast.iter_fields(node):
                if isinstance(value, list):
                    matches.extend((value, i) for i, item in enumerate(value)
                                   if isinstance(item, ast.AST)
                                   and _ast(item) == _ast(expected_call))
        _need(len(matches) == 1, "fifth_native_call_changed")
        block, index = matches[0]
        _need(index + 1 < len(block) and any(
            isinstance(n, ast.ImportFrom)
            and n.module == "bestseller.services.legacy_request_purpose"
            for n in ast.walk(block[index + 1])), "fifth_native_call_position_changed")
        del block[index]
        _need(_ast(before) == _ast(after), "fifth_commercial_other_ast_changed")
    else:
        name = "_validate_native_alias_chain"
        old = next(n for n in before.body if isinstance(n, ast.FunctionDef) and n.name == name)
        new = next(n for n in after.body if isinstance(n, ast.FunctionDef) and n.name == name)
        _need(REVIEWED_EPOCH3_RECONCILIATION_AST_SHA256 is not None,
              "fifth_reconciliation_not_reviewed")
        _need(_sha(_ast(new).encode()) == REVIEWED_EPOCH3_RECONCILIATION_AST_SHA256,
              "fifth_alias_depth_context_changed")
        after.body[after.body.index(new)] = old
        _need(_ast(before) == _ast(after), "fifth_reconciliation_other_ast_changed")


def _verified_c90a_epochs(*, runtime=True):
    _need(FINAL_SHA256 is not None
          and all(REVIEWED_BEAT_RESUME_SHA256.values()), "final_epoch_not_reviewed")
    verified_unissued_runtime_audit()
    original = _document("original.json", ORIGINAL_SHA256)
    prior = _document("current.json", CURRENT_SHA256)
    a296 = _document("a296-final.json", A296_SHA256)
    current = verified_c90a_archive()
    _need(
        original["policy_hash"] == OLD_POLICY and prior["policy_hash"] == PRIOR_POLICY
        and a296["policy_hash"] == A296_POLICY
        and current["policy_hash"] not in (OLD_POLICY, PRIOR_POLICY, A296_POLICY, UNISSUED_POLICY),
        "historical_policy",
    )
    for manifest in (original, prior, a296, current):
        _need(
            len(manifest["inventory"]) == 263
            and _aggregate(manifest["inventory"]) == manifest["policy_hash"],
            "policy_inventory",
        )
        _need(
            set(manifest["inventory"]) == set(original["inventory"])
            and set(manifest["additional_sources"]) == set(original["additional_sources"]),
            "inventory_changed",
        )
    old_files = {**original["inventory"], **original["additional_sources"]}
    prior_files = {**prior["inventory"], **prior["additional_sources"]}
    a296_files = {**a296["inventory"], **a296["additional_sources"]}
    new_files = {**current["inventory"], **current["additional_sources"]}
    _need(
        sorted(n for n in old_files if old_files[n] != prior_files[n])
        == prior["reviewed_delta_files"],
        "prior_delta_set_changed",
    )
    _need(
        sorted(n for n in old_files if old_files[n] != a296_files[n])
        == a296["reviewed_delta_files"],
        "a296_delta_set_changed",
    )
    _need(
        sorted(n for n in old_files if old_files[n] != new_files[n])
        == current["reviewed_delta_files"],
        "delta_set_changed",
    )
    _need(
        sorted(n for n in prior_files if prior_files[n] != a296_files[n])
        == sorted((_HELPER, _RECONCILIATION)),
        "a296_epoch_delta_set_changed",
    )
    _need(
        sorted(n for n in a296_files if a296_files[n] != new_files[n])
        == sorted(_CURRENT_DELTA_FILES)
        == current["epoch_delta_files"]
        and current["prior_policy_hash"] == A296_POLICY,
        "epoch_delta_set_changed",
    )
    _need(
        all(new_files[n] == value for n, value in REVIEWED_BEAT_RESUME_SHA256.items()),
        "unreviewed_beat_resume_source",
    )
    for relative in sorted(old_files):
        original_bytes(relative)
        previous = prior_bytes(relative)
        intermediate = a296_bytes(relative)
        if prior_files[relative] != a296_files[relative]:
            _a296_delta_boundary(previous, intermediate, relative)
        actual = (ROOT / relative).read_bytes() if runtime else c90a_bytes(relative)
        _need(_sha(actual) == new_files[relative], "unlisted_runtime_delta:" + relative)
        if a296_files[relative] != new_files[relative]:
            _beat_resume_boundary(intermediate, actual, relative)
    return original, prior, a296, current


def _verified_five_epochs():
    _need(EPOCH3_FINAL_SHA256 is not None and EPOCH3_POLICY is not None
          and set(REVIEWED_EPOCH3_SOURCE_SHA256)
          == {_COMMERCIAL_RECOVERY, _EXECUTION_CONTROL_PURPOSE, _RECONCILIATION}
          and all(REVIEWED_EPOCH3_SOURCE_SHA256.values()), "fifth_epoch_not_reviewed")
    _need(FINAL_SHA256 is not None and all(REVIEWED_BEAT_RESUME_SHA256.values()),
          "final_epoch_not_reviewed")
    _need(FINAL_SHA256 == EPOCH3_FINAL_SHA256, "fifth_final_pin_mismatch")
    original, prior, a296, c90a = _verified_c90a_epochs(runtime=False)
    current = _document("final.json", EPOCH3_FINAL_SHA256)
    _need(current["policy_hash"] == EPOCH3_POLICY
          and EPOCH3_POLICY not in (OLD_POLICY, PRIOR_POLICY, A296_POLICY,
                                   C90A_POLICY, UNISSUED_POLICY), "fifth_policy_changed")
    _need(len(current["inventory"]) == 263
          and _aggregate(current["inventory"]) == EPOCH3_POLICY
          and set(current["inventory"]) == set(c90a["inventory"])
          and set(current["additional_sources"]) == set(c90a["additional_sources"]),
          "fifth_inventory_changed")
    old = {**c90a["inventory"], **c90a["additional_sources"]}
    new = {**current["inventory"], **current["additional_sources"]}
    _need({n for n in c90a["inventory"] if old[n] != new[n]}
          == {_COMMERCIAL_RECOVERY, _EXECUTION_CONTROL_PURPOSE}
          and {n for n in c90a["additional_sources"] if old[n] != new[n]} == {_RECONCILIATION}
          and current["epoch_delta_files"] == sorted(REVIEWED_EPOCH3_SOURCE_SHA256)
          and current["prior_policy_hash"] == C90A_POLICY, "fifth_delta_set_changed")
    first = {**original["inventory"], **original["additional_sources"]}
    _need(current["reviewed_delta_files"] == sorted(n for n in first if first[n] != new[n]),
          "fifth_original_delta_changed")
    _need(all(new[n] == value for n, value in REVIEWED_EPOCH3_SOURCE_SHA256.items()),
          "fifth_source_not_reviewed")
    for relative, digest in sorted(new.items()):
        actual = (ROOT / relative).read_bytes()
        _need(_sha(actual) == digest, "unlisted_runtime_delta:" + relative)
        if old[relative] != digest:
            _epoch3_delta_boundary(c90a_bytes(relative), actual, relative)
    known_c90a_transitions()
    return original, prior, a296, c90a, current


def _verified_four_epochs():
    """Legacy shape: original/prior/A296/actual current, with all epochs verified."""
    if EPOCH3_FINAL_SHA256 is not None:
        original, prior, a296, _, current = _verified_five_epochs()
        return original, prior, a296, current
    return _verified_c90a_epochs()


def _verified_epochs():
    """Preserve the existing three-result internal API: original, prior, latest."""
    original, prior, _, current = _verified_four_epochs()
    return original, prior, current


def verified_transition_from(previous_policy):
    """Internal selector: only a native row's known epoch may be supplied."""
    sources_allowed = (OLD_POLICY, PRIOR_POLICY, A296_POLICY) + (
        (C90A_POLICY,) if EPOCH3_FINAL_SHA256 is not None else ())
    _need(previous_policy in sources_allowed,
          "unsupported_previous_policy")
    original, prior, a296, current = _verified_four_epochs()
    sources = {OLD_POLICY: (original, ORIGINAL_SHA256),
               PRIOR_POLICY: (prior, CURRENT_SHA256), A296_POLICY: (a296, A296_SHA256)}
    if EPOCH3_FINAL_SHA256 is not None:
        sources[C90A_POLICY] = (verified_c90a_archive(), C90A_SHA256)
    _need(previous_policy in sources, "unsupported_previous_policy")
    source, digest = sources[previous_policy]
    return _proof(source, current, digest, FINAL_SHA256)


def verified_alias_transition(claimed):
    """Replay an exact issued transition after verifying actual CURRENT bytes."""
    _need(type(claimed) is dict and UNISSUED_POLICY not in (
        claimed.get("previous_policy_hash"), claimed.get("current_policy_hash")),
        "unknown_alias_transition")
    if EPOCH3_FINAL_SHA256 is not None:
        original, prior, a296, c90a, current = _verified_five_epochs()
        recognized = known_c90a_transitions() + tuple(
            _proof(source, current, digest, EPOCH3_FINAL_SHA256)
            for source, digest in ((original, ORIGINAL_SHA256), (prior, CURRENT_SHA256),
                                   (a296, A296_SHA256), (c90a, C90A_SHA256)))
        _need(type(claimed) is dict and claimed in recognized, "unknown_alias_transition")
        return claimed
    original, prior, a296, current = _verified_four_epochs()
    recognized = (
        _proof(original, prior, ORIGINAL_SHA256, CURRENT_SHA256),
        _proof(original, a296, ORIGINAL_SHA256, A296_SHA256),
        _proof(prior, a296, CURRENT_SHA256, A296_SHA256),
        _proof(original, current, ORIGINAL_SHA256, FINAL_SHA256),
        _proof(prior, current, CURRENT_SHA256, FINAL_SHA256),
        _proof(a296, current, A296_SHA256, FINAL_SHA256),
    )
    _need(type(claimed) is dict and claimed in recognized, "unknown_alias_transition")
    return claimed


def verified_alias_depth_limit(root_transition):
    """A fourth alias requires a known edge into the exact reviewed fifth target."""
    transition = verified_alias_transition(root_transition)
    return (4 if EPOCH3_FINAL_SHA256 is not None
            and transition["current_policy_hash"] == EPOCH3_POLICY else 3)


def verified_transition():
    """No operator source/hash/probe input; default original-to-current epoch."""
    return verified_transition_from(OLD_POLICY)
