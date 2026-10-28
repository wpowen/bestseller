"""Writer transport fixtures prove binding and preservation, not literary quality."""
from asyncio import current_task
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from bestseller.services import chapter_writer_delivery as delivery
from bestseller.services import source_bound_planning_content as protocol
from bestseller.services.prompt_compiler import PromptBudgetError, compile_prompt
from bestseller.services.prompt_source_policy import content_hash

pytestmark = pytest.mark.unit
V3 = "approved-current-chapter-delivery.v3-reader-first"


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    import socket
    from sqlalchemy.engine import Engine

    def forbidden(*args, **kwargs):
        raise AssertionError("Q4 writer tests must not connect to DB/network/model")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(Engine, "raw_connection", forbidden)


@pytest.fixture
def writer_settings(monkeypatch):
    # The required raw :memory: environment is accepted by the DB safety guard
    # but YAML-coerced by generic settings. Use the existing safe loader; keep
    # that exact environment and prohibit every actual connection above.
    from support.database_safety import load_test_database_settings
    from bestseller import settings as settings_module
    from bestseller.services import drafts
    settings = load_test_database_settings()
    monkeypatch.setattr(settings_module, "load_settings", lambda *a, **kw: settings)
    monkeypatch.setattr(drafts, "load_settings", lambda *a, **kw: settings)
    monkeypatch.setattr(drafts, "get_settings", lambda: settings)
    return settings


def _leaves(node, path=""):
    if isinstance(node, dict) and node:
        for key, value in node.items():
            if key == "_meta":
                continue
            yield from _leaves(value, path + "/" + key.replace("~", "~0").replace("/", "~1"))
    elif isinstance(node, list) and node:
        for index, value in enumerate(node):
            yield from _leaves(value, path + "/" + str(index))
    else:
        yield path, node


def _case(language="zh"):
    binding = protocol.new_reader_first_binding("A reconciliation; preserve the seal and 1200.50.")
    args = dict(project_id=uuid4(), chapter_id=uuid4(), chapter_number=7, artifact_id=uuid4())
    approved = {
        "chapter_number": 7, "goal": "Ask why she left before choosing to stay.",
        "precise/fact~": "09:17; 1200.50; do not open before tomorrow.",
        "constraints": ["Do not sign again.", "Keep all three seals intact."],
        "scenes": [{"scene_number": 1, "participants": ["Ming", "Lan"],
                    "entry_state": {"holder": "Lan", "amount": "1200.50"},
                    "exit_state": {"choice": "Ming stays; the seal remains intact."},
                    "action_sequence": ["Verify the seal because Lan refuses to stay otherwise."],
                    "voice": {"Ming": "brief questions", "Lan": "formal address"},
                    "unknown_new_clause": "No extra witness or debt is authorized.",
                    "empty": [], "nullable": None}],
    }
    # Roles are authored in the reviewed source, never inferred from its words.
    roles = {path: "continuity_reference" for path, _ in _leaves(approved)}
    roles["/goal"] = "narrative_task"
    roles["/scenes/0/action_sequence/0"] = "narrative_task"
    roles["/scenes/0/exit_state/choice"] = "narrative_task"
    approved["_meta"] = {"reader_first_planning": deepcopy(binding),
                         "writer_delivery_schema": V3, "reader_first_field_roles": roles}
    source = {"reader_first_planning": deepcopy(binding), "outline": {"chapters": [approved]},
              "whole_book_payoff_index": "Reconciliation remains the ending promise."}
    raw = {"schema": "reader-first-review.v1", "checkpoint": "outline",
           "source_sha256": content_hash(source), "config_sha256": binding["config_sha256"],
           "dimensions": {name: {"score": .8, "reason": "A choice changes the relationship.",
                                "evidence": [{"path": "/outline/chapters/0/goal", "quote": approved["goal"]}]}
                          for name in protocol.READER_FIRST_DIMENSIONS},
           "issues": [], "options": ["Keep the relationship and change the obstacle.",
                                      "Keep the promise and change the choice."]}
    reader_receipt = protocol.parse_reader_first_review(raw, source=source, binding=binding,
                                                       checkpoint="outline")
    assert reader_receipt["status"] == "passed"
    receipt = {"chapter_numbers": [7], "reader_first_source": {
        "schema": "reader-first-writer-source.v1", "binding": deepcopy(binding),
        "source_binding": {key: str(value) if key != "chapter_number" else value
                           for key, value in args.items()},
        "source": deepcopy(source), "chapter_pointer": "/outline/chapters/0",
        "review": raw, "reader_receipt": reader_receipt,
    }}
    return args, approved, receipt, binding


def _payload(block):
    return json.loads(block.text.split("\n", 2)[2])


def _block(case, **kwargs):
    args, approved, receipt, binding = case
    return delivery.delivery_block(**args, approved=approved, receipt=receipt,
                                   reader_first=binding, **kwargs)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_v3_is_lossless_bound_and_role_aware(language):
    case = _case(language)
    before = deepcopy(case)
    block = _block(case, language=language)
    args, approved, receipt, binding = case
    payload = _payload(block)
    assert payload["schema"] == V3
    assert payload["reader_first_planning"] == binding
    assert payload["approved_source"] == approved
    assert payload["approved_chapter_sha256"] == content_hash(approved)
    assert payload["readiness_receipt_sha256"] == content_hash(receipt)
    assert payload["reader_receipt_sha256"] == content_hash(receipt["reader_first_source"]["reader_receipt"])
    assert payload["review_source_sha256"] == content_hash(receipt["reader_first_source"]["source"])
    assert payload["review_source"] == receipt["reader_first_source"]["source"]
    assert payload["raw_review"] == receipt["reader_first_source"]["review"]
    assert payload["source_binding"]["artifact_id"] == str(args["artifact_id"])
    roles = approved["_meta"]["reader_first_field_roles"]
    for path, _ in _leaves(approved):
        assert path in payload[roles[path]]
        assert path in block.provenance.field_paths
    assert "/precise~1fact~0" in payload["continuity_reference"]
    assert "/scenes/0/action_sequence/0" in payload["narrative_task"]
    assert block.provenance.source_hash == content_hash(approved)
    compiled = compile_prompt([block], total_budget_tokens=16000, require_provenance=True)
    assert not compiled.report.truncated and not compiled.report.dropped
    assert "09:17" in compiled.user and "1200.50" in compiled.user
    assert "Do not sign again." in compiled.user and "No extra witness" in compiled.user
    assert ("人物" if language == "zh" else "character") in compiled.user
    assert case == before
    payload["approved_source"]["scenes"][0]["entry_state"]["holder"] = "changed"
    assert case == before
    with pytest.raises(PromptBudgetError):
        compile_prompt([block], total_budget_tokens=30, require_provenance=True)


@pytest.mark.parametrize("fault", [
    "old_source", "source_protocol", "source_input", "source_schema", "receipt_missing",
    "source_binding_project", "source_binding_chapter", "source_binding_artifact",
    "chapter_pointer", "source_changed", "whole_outline_changed", "review_hash",
    "review_config", "review_checkpoint", "review_quote", "review_failed", "review_unknown",
    "receipt_forged", "role_missing", "role_unknown", "role_extra",
])
def test_v3_refuses_stale_forged_incomplete_or_historical_source(fault):
    case = _case()
    args, approved, receipt, binding = case
    envelope = receipt["reader_first_source"]
    if fault == "old_source": approved.pop("_meta")
    elif fault == "source_protocol": approved["_meta"]["reader_first_planning"]["protocol"] = "latest"
    elif fault == "source_input": approved["_meta"]["reader_first_planning"]["user_input_sha256"] = "0" * 64
    elif fault == "source_schema": approved["_meta"]["writer_delivery_schema"] = "approved-current-chapter-delivery.v2"
    elif fault == "receipt_missing": receipt.pop("reader_first_source")
    elif fault.startswith("source_binding_"):
        key = {"project": "project_id", "chapter": "chapter_id", "artifact": "artifact_id"}[fault.rsplit("_", 1)[1]]
        envelope["source_binding"][key] = str(uuid4())
    elif fault == "chapter_pointer": envelope["chapter_pointer"] = "/chapters/1"
    elif fault == "source_changed": approved["goal"] = "A different choice."
    elif fault == "whole_outline_changed": envelope["source"]["whole_book_payoff_index"] = "A different promise."
    elif fault == "review_hash": envelope["review"]["source_sha256"] = "0" * 64
    elif fault == "review_config": envelope["review"]["config_sha256"] = "0" * 64
    elif fault == "review_checkpoint": envelope["review"]["checkpoint"] = "skeleton"
    elif fault == "review_quote": next(iter(envelope["review"]["dimensions"].values()))["evidence"][0]["quote"] = "invented"
    elif fault == "review_failed": next(iter(envelope["review"]["dimensions"].values()))["score"] = .2
    elif fault == "review_unknown": envelope["review"]["fallback"] = True
    elif fault == "receipt_forged": envelope["reader_receipt"]["mean_score"] = .99
    elif fault == "role_missing": approved["_meta"]["reader_first_field_roles"].pop("/goal")
    elif fault == "role_unknown": approved["_meta"]["reader_first_field_roles"]["/goal"] = "discard"
    elif fault == "role_extra": approved["_meta"]["reader_first_field_roles"]["/invented"] = "narrative_task"
    before = deepcopy(case)
    with pytest.raises(ValueError, match="reader_first"):
        _block(case)
    assert case == before


def test_reviewed_role_assignment_is_exact_not_keyword_filtering():
    case = _case()
    assert "/scenes/0/action_sequence/0" in _payload(_block(case))["narrative_task"]
    # The action contains "Verify" and "seal"; its authored task role survives.
    assert "Verify the seal" in _block(case).text


@pytest.mark.parametrize("fault", ["field_pointer", "field_hash", "role", "receipt_hash", "approved_hash", "scope"])
def test_writer_rejects_block_drift_after_verified_delivery(fault):
    from bestseller.services.chapter_writer_delivery import reader_first_writer_blocks
    case = _case()
    args, _, _, binding = case
    block = _block(case)
    payload = _payload(block)
    if fault == "field_pointer": payload["field_sources"]["/goal"]["review_source_pointer"] = "/chapters/1/goal"
    elif fault == "field_hash": payload["field_sources"]["/goal"]["approved_chapter_sha256"] = "0" * 64
    elif fault == "role": payload["narrative_task"].remove("/goal")
    elif fault == "receipt_hash": payload["reader_receipt_sha256"] = "0" * 64
    elif fault == "approved_hash": payload["approved_chapter_sha256"] = "0" * 64
    elif fault == "scope": payload["source_binding"]["chapter_id"] = str(uuid4())
    changed = block.model_copy(update={"text": block.text.rsplit("\n", 1)[0] + "\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))})
    project = SimpleNamespace(id=args["project_id"], metadata_json={"reader_first_planning": binding})
    chapter = SimpleNamespace(id=args["chapter_id"], chapter_number=7)
    with pytest.raises(ValueError, match="reader_first"):
        reader_first_writer_blocks(project, chapter, (changed,), language="zh")


@pytest.mark.parametrize("fault", ["rehashed_goal", "nonexistent_chapter_pointer", "rehashed_whole_source",
                                   "missing_review_source", "missing_raw_review", "changed_raw_review"])
def test_writer_reparses_retained_review_instead_of_trusting_rehashed_delivery(fault):
    case = _case()
    args, _, _, binding = case
    block = _block(case)
    payload = _payload(block)
    provenance = block.provenance
    if fault in {"rehashed_goal", "rehashed_whole_source"}:
        payload["approved_source"]["goal"] = "An unreviewed replacement goal."
        approved_hash = content_hash(payload["approved_source"])
        payload["approved_chapter_sha256"] = approved_hash
        for row in payload["field_sources"].values():
            row["approved_chapter_sha256"] = approved_hash
        provenance = provenance.model_copy(update={"source_hash": approved_hash})
        if fault == "rehashed_whole_source":
            payload["review_source"]["outline"]["chapters"][0] = deepcopy(payload["approved_source"])
            payload["review_source_sha256"] = content_hash(payload["review_source"])
    elif fault == "nonexistent_chapter_pointer":
        payload["source_chapter_pointer"] = "/outline/chapters/99"
        for path, row in payload["field_sources"].items():
            row["review_source_pointer"] = payload["source_chapter_pointer"] + path
    elif fault == "missing_review_source": payload.pop("review_source")
    elif fault == "missing_raw_review": payload.pop("raw_review")
    elif fault == "changed_raw_review": payload["raw_review"]["source_sha256"] = "0" * 64
    changed = block.model_copy(update={"text": delivery._reader_first_delivery_text(payload, "zh"),
                                       "provenance": provenance})
    project = SimpleNamespace(id=args["project_id"], metadata_json={"reader_first_planning": binding})
    chapter = SimpleNamespace(id=args["chapter_id"], chapter_number=7)
    with pytest.raises(ValueError, match="reader_first"):
        delivery.reader_first_writer_blocks(project, chapter, (changed,), language="zh")


def _legacy_case():
    approved = {'chapter_number':7,'goal':'Return without replaying yesterday.','target_word_count':2000,'scenes':[{'scene_number':1,'entry_state':{'time':'09:17','amount':'1200.50'},'exit_state':{'holder':'Ming'},'action_sequence':['Do not sign again; ask why she left.'],'forbidden_actions':['Do not open the seal.'],'key_dialogue_beats':['old example']}], '_meta':{'audit':'retained'}}
    return dict(project_id='project',chapter_id='chapter',chapter_number=7,artifact_id='artifact',approved=approved,receipt={'chapter_numbers':[7]})


@pytest.mark.parametrize("version,text_hash,provenance_hash", [
    ("v1", "7d1e9c2edf76246cfb8bf7acc0a82ae495b7602e71d614b85fbeb053751a6c53", "14a7490663450e802d57e9f79f206ae9be5169a7bf02196ecf76222062514494"),
    ("v2", "0454ef622a3095fdb71643c2a75ccc2b6d683c41d9ed5bd285c99851c96218c5", "0cee1f1c8ac8b90eb70a19fb47ea5db4ad85be3ed82f6325182f28995e43684c"),
])
def test_legacy_delivery_exact_frozen_bytes_and_provenance(version, text_hash, provenance_hash):
    constructor = delivery._source_protocol_delivery_block_v1 if version == "v1" else delivery.delivery_block
    block = constructor(**_legacy_case())
    assert sha256(block.text.encode()).hexdigest() == text_hash
    assert content_hash(block.provenance.model_dump(mode="json")) == provenance_hash


def test_new_source_cannot_downgrade_when_marker_is_lost():
    args, approved, receipt, _ = _case()
    with pytest.raises(ValueError, match="reader_first"):
        delivery.delivery_block(**args, approved=approved, receipt=receipt)


@pytest.mark.asyncio
async def test_loader_requires_source_and_scope_and_preserves_legacy_reference(monkeypatch):
    from bestseller.services import editable_plan_source
    args, approved, receipt, binding = _case()
    project = SimpleNamespace(id=args["project_id"], metadata_json={"reader_first_planning": binding})
    chapter = SimpleNamespace(id=args["chapter_id"], chapter_number=7, metadata_json={})

    async def missing(*a, **kw): return None

    monkeypatch.setattr(editable_plan_source, "load_selected_plan_source", missing)
    with pytest.raises(ValueError, match="reader_first"):
        await delivery.load_delivery_blocks(None, None, project=project, chapter=chapter, scenes=[])

    async def selected(*a, **kw):
        content = receipt.get("reader_first_source", {}).get("source", {}).get("outline", {})
        return SimpleNamespace(id=args["artifact_id"], content=deepcopy(content)), approved, receipt

    monkeypatch.setattr(editable_plan_source, "load_selected_plan_source", selected)
    block, = await delivery.load_delivery_blocks(None, None, project=project, chapter=chapter, scenes=[])
    assert _payload(block)["schema"] == V3
    project.metadata_json = {}
    old = _legacy_case()
    approved, receipt = old["approved"], old["receipt"]
    token = delivery._REFERENCE_DELIVERY_SCHEMA.set((current_task(), "approved-current-chapter-delivery.v1", project.id, chapter.id))
    try:
        block, = await delivery.load_delivery_blocks(None, None, project=project, chapter=chapter, scenes=[])
        assert _payload(block)["schema"] == "approved-current-chapter-delivery.v1"
    finally:
        delivery._REFERENCE_DELIVERY_SCHEMA.reset(token)
    block, = await delivery.load_delivery_blocks(None, None, project=project, chapter=chapter, scenes=[])
    assert _payload(block)["schema"] == "approved-current-chapter-delivery.v2"


@pytest.mark.asyncio
async def test_loader_requires_exact_whole_reviewed_artifact_not_only_chapter(monkeypatch):
    from bestseller.services import editable_plan_source
    args, approved, receipt, binding = _case()
    project = SimpleNamespace(id=args["project_id"], metadata_json={"reader_first_planning": binding})
    chapter = SimpleNamespace(id=args["chapter_id"], chapter_number=7, metadata_json={})

    async def selected(*a, **kw):
        artifact = SimpleNamespace(id=args["artifact_id"], content={"chapters": [approved],
                                                                  "other_future_promise": "unreviewed"})
        return artifact, approved, receipt

    monkeypatch.setattr(editable_plan_source, "load_selected_plan_source", selected)
    with pytest.raises(ValueError, match="reader_first"):
        await delivery.load_delivery_blocks(None, None, project=project, chapter=chapter, scenes=[])


def test_nested_metadata_is_retained_and_matches_planner_non_meta_roles():
    case = _case()
    args, approved, receipt, binding = case
    approved["scenes"][0]["_meta"] = {"audit": "09:17; retain 1200.50 exactly"}
    source = receipt["reader_first_source"]["source"]
    source["outline"]["chapters"][0] = deepcopy(approved)
    raw = receipt["reader_first_source"]["review"]
    raw["source_sha256"] = content_hash(source)
    receipt["reader_first_source"]["reader_receipt"] = protocol.parse_reader_first_review(
        raw, source=source, binding=binding, checkpoint="outline")
    payload = _payload(_block(case))
    assert payload["approved_source"] == approved
    assert "/scenes/0/_meta" in payload["continuity_reference"]
    assert "/scenes/0/_meta" in payload["field_sources"]


def test_actual_planner_coverage_accepts_writer_source_shape():
    from bestseller.services.planner import _reader_first_outline_coverage
    case = _case()
    args, approved, receipt, binding = case
    args["chapter_number"] = approved["chapter_number"] = 1
    receipt["chapter_numbers"] = [1]
    envelope = receipt["reader_first_source"]
    envelope["source_binding"]["chapter_number"] = 1
    envelope["source"]["outline"]["chapters"][0] = deepcopy(approved)
    envelope["review"]["source_sha256"] = content_hash(envelope["source"])
    envelope["reader_receipt"] = protocol.parse_reader_first_review(
        envelope["review"], source=envelope["source"], binding=binding, checkpoint="outline")
    coverage = _reader_first_outline_coverage(envelope["source"], SimpleNamespace(target_chapters=1), binding)
    assert coverage["complete"] is True
    assert coverage["chapters"][0]["chapter_sha256"] == content_hash(approved)
    assert _payload(_block(case))["approved_chapter_sha256"] == coverage["chapters"][0]["chapter_sha256"]


def test_existing_mainline_capacity_consumer_blocks_v3_without_weakening():
    from bestseller.services.writer_source_capacity import _delivery_binding
    with pytest.raises(ValueError, match="writer_source_capacity_verified_delivery_required"):
        _delivery_binding(_block(_case()))


def _caller_inputs(language):
    from test_chapter_first_prompt_compiler_integration import _inputs
    project, chapter, scene, packet = _inputs(language, 7)
    packet.story_bible = {}
    packet.previous_scene_summaries = []
    packet.retrieval_chunks = []
    case = _case(language)
    args, _, _, binding = case
    project.id, chapter.id = args["project_id"], args["chapter_id"]
    chapter.project_id = project.id
    scene.project_id, scene.chapter_id = project.id, chapter.id
    project.metadata_json = {"writing_profile": {"reader_first_planning": binding}}
    return project, chapter, scene, packet, case


@pytest.mark.parametrize("language", ["zh-CN", "en"])
@pytest.mark.parametrize("path", ["scene", "chapter_first"])
def test_both_writer_callers_require_v3_and_keep_craft_and_source(language, path, writer_settings):
    from bestseller.services.drafts import build_chapter_first_draft_prompts, build_scene_draft_prompts
    project, chapter, scene, packet, case = _caller_inputs(language)

    def build(blocks):
        if path == "scene":
            return build_scene_draft_prompts(project, chapter, scene, None, prompt_mode="compiled",
                chapter_contract=packet.chapter_contract, compiler_additional_blocks=blocks,
                total_input_budget_tokens=30000)
        return build_chapter_first_draft_prompts(project, chapter, [scene], None, packet,
            target_word_count=2600, prompt_mode="compiled", compiler_additional_blocks=blocks,
            total_input_budget_tokens=30000)

    with pytest.raises(ValueError, match="reader_first"):
        build(())
    legacy = delivery.delivery_block(**_legacy_case())
    with pytest.raises(ValueError, match="reader_first"):
        build((legacy,))
    block = _block(case, language=language)
    compiled = build((block,))
    assert V3 in compiled.user
    assert "09:17" in compiled.user and "1200.50" in compiled.user
    assert ("情感重点场景" if language.startswith("zh") else "Emotional scenes") in compiled.user
    assert "reader-first-planning.v1:writer" in compiled.user
    assert block.key in compiled.report.required_blocks_kept
    wrong_case = _case(language)
    with pytest.raises(ValueError, match="reader_first"):
        build((_block(wrong_case, language=language),))


@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_full_chapter_builder_uses_reader_first_causal_roles_and_outer_instruction(language, writer_settings):
    from bestseller.services.drafts import build_chapter_first_draft_prompts
    project, chapter, scene, packet, case = _caller_inputs(language)
    packet.chapter_contract = {
        "contract_summary": "Ask why she left.",
        "causal_contract": {"protagonist_choice": "Choose to stay.",
                            "state_change": "09:17; 1200.50 remains with Lan; no new confirmation."},
    }
    _, approved, receipt, binding = case
    approved.update(deepcopy(packet.chapter_contract))
    roles = approved["_meta"]["reader_first_field_roles"]
    roles.update({"/contract_summary": "narrative_task",
                  "/causal_contract/protagonist_choice": "narrative_task",
                  "/causal_contract/state_change": "continuity_reference"})
    envelope = receipt["reader_first_source"]
    envelope["source"]["outline"]["chapters"][0] = deepcopy(approved)
    envelope["review"]["source_sha256"] = content_hash(envelope["source"])
    envelope["reader_receipt"] = protocol.parse_reader_first_review(
        envelope["review"], source=envelope["source"], binding=binding, checkpoint="outline")
    block = _block(case, language=language)
    assert "/causal_contract/state_change" in _payload(block)["continuity_reference"]
    prompt = build_chapter_first_draft_prompts(project, chapter, [scene], None, packet,
        target_word_count=2600, prompt_mode="compiled", prose_prompt_profile="full",
        compiler_additional_blocks=(block,), total_input_budget_tokens=30000)
    assert "09:17; 1200.50 remains with Lan; no new confirmation." in prompt.user
    assert ("未变化状态不逐条宣告" if language.startswith("zh") else "unchanged states are not a spoken checklist") in prompt.user
    assert "这些不是参考资料，而是正文必须让读者直接看见的交付项" not in prompt.user
    assert "这些必须落成读者可见的选择、代价、反应和状态变化" not in prompt.user
    assert "Render these as reader-visible choices, costs, reactions, and state changes" not in prompt.user


@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_contract_renderers_preserve_legacy_and_new_facts(language):
    from bestseller.services.drafts import _render_contract_section, _render_character_causal_contract_block
    binding = _case()[3]
    chapter = {"contract_summary": "Choose reconciliation.", "causal_contract": {
        "protagonist_choice": "Stay.", "state_change": "09:17; the 1200.50 remains with Lan."}}
    before = deepcopy(chapter)
    old = _render_contract_section(chapter, None, language=language)
    new = _render_contract_section(chapter, None, language=language, reader_first=binding)
    assert "09:17" in new and "1200.50" in new
    assert "Stay." in new
    assert ("不逐条宣告" if language.startswith("zh") else "not a spoken checklist") in new
    assert chapter == before
    assert _render_contract_section(chapter, None, language=language, reader_first=None) == old
    assert _render_character_causal_contract_block(chapter, language=language, reader_first=None) == _render_character_causal_contract_block(chapter, language=language)


@pytest.mark.parametrize("path,language,expected", [
    ("scene", "zh-CN", "4ce5be3c92706274dc49c90fe0feda521a11e43889207d0a9c0814fe7834ec89"),
    ("chapter_first", "zh-CN", "7ea4cbd671fdd7c2e1591f5185b158a5e90b2127ea2ee53a5c7d03faf28a2e30"),
    ("scene", "en", "dc4ef918bb047fa6caf5d78821a924cec314440f453194e52c422c8c3aa3486f"),
    ("chapter_first", "en", "d5be11a201c544d217f1c52a1d94657f51bc57678c1bbdda44926b68a76256af"),
])
def test_unmarked_writer_full_prompt_bytes_match_original_head(path, language, expected, writer_settings):
    from test_chapter_first_prompt_compiler_integration import _inputs
    from bestseller.services import drafts
    project, chapter, scene, packet = _inputs(language, 7)
    project.id, chapter.id, scene.id = UUID(int=1), UUID(int=2), UUID(int=3)
    chapter.project_id = scene.project_id = project.id
    scene.chapter_id = chapter.id
    packet.story_bible = {}
    packet.previous_scene_summaries = []
    packet.retrieval_chunks = []
    if path == "scene":
        prompt = drafts.build_scene_draft_prompts(project, chapter, scene, None,
            chapter_contract=packet.chapter_contract, prompt_mode="compiled", total_input_budget_tokens=30000)
    else:
        prompt = drafts.build_chapter_first_draft_prompts(project, chapter, [scene], None, packet,
            target_word_count=2600, prompt_mode="compiled", total_input_budget_tokens=30000)
    assert sha256(json.dumps((prompt.system, prompt.user), ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["v1", "v2"])
async def test_historical_reference_is_task_project_and_chapter_scoped(version, monkeypatch):
    import asyncio
    from bestseller.services import editable_plan_source
    args, _, _, binding = _case()
    project = SimpleNamespace(id=args["project_id"], metadata_json={"reader_first_planning": binding})
    chapter = SimpleNamespace(id=args["chapter_id"], chapter_number=7, metadata_json={})
    old = _legacy_case()

    async def selected(*a, **kw):
        return SimpleNamespace(id=args["artifact_id"]), old["approved"], old["receipt"]

    async def load():
        return await delivery.load_delivery_blocks(None, None, project=project, chapter=chapter, scenes=[])

    monkeypatch.setattr(editable_plan_source, "load_selected_plan_source", selected)
    token = delivery._REFERENCE_DELIVERY_SCHEMA.set((current_task(), "approved-current-chapter-delivery." + version,
                                                     project.id, chapter.id))
    try:
        block, = await load()
        assert _payload(block)["schema"] == "approved-current-chapter-delivery." + version
        # A child task inherits ContextVar values but cannot inherit this authority.
        with pytest.raises(ValueError, match="reader_first"):
            await asyncio.create_task(load())
        chapter.id = uuid4()
        with pytest.raises(ValueError, match="reader_first"):
            await load()
        chapter.id = args["chapter_id"]
        project.id = uuid4()
        with pytest.raises(ValueError, match="reader_first"):
            await load()
    finally:
        delivery._REFERENCE_DELIVERY_SCHEMA.reset(token)
    with pytest.raises(ValueError, match="reader_first"):
        await load()
