from copy import deepcopy
import pytest
from bestseller.services.volume_source_admission_input import preview_reference_only_review_input
from bestseller.services.prompt_source_policy import content_hash
pytestmark=pytest.mark.unit

def test_preview_excludes_old_repair_instructions_but_preserves_actual_source_even_same_words():
 marker='REJECTED_INSTRUCTION_ADD_UNRELATED_POWER'
 diagnostics={'decision':'revise','required_fix':marker,'nested':{'repair':marker},'old_review_refs':[{'id':'old-review','verdict':'revise','content_hash':'old-hash','llm_run_id':'paid-run','required_fix':marker}]}
 source={'approved_world_spec':{'quoted_story_fact':marker},
  'approved_cast_spec':{'knowledge_state':{'old':'unknown'}},
  'candidate_payload':{'volumes':[{'unknown_authored_field':marker}]},
  'untrusted_legacy_design_context':{'serialization':'前三章1变3，与当前计划有冲突'},
  'verified_source_components':{'schema':'fixture','receipt_sha256':'bound','partial_component_review':{'original_decision':'revise','repair':marker}},'untrusted_commercial_report':diagnostics,'source_authority':{'world_rule':{'/approved_world_spec':['/quoted_story_fact']}}}
 before=deepcopy(source);view,proof=preview_reference_only_review_input(source)
 assert source==before and source['untrusted_commercial_report']['decision']=='revise'
 assert marker not in str(view['untrusted_commercial_report'])
 assert view['untrusted_commercial_report']['old_review_refs']==[{'id':'old-review','verdict':'revise','content_hash':'old-hash','llm_run_id':'paid-run'}]
 assert 'original_decision' not in str(view['verified_source_components'])
 assert view['verified_source_components']['receipt_sha256']=='bound'
 assert view['approved_world_spec']==source['approved_world_spec']
 assert view['candidate_payload']==source['candidate_payload']
 assert view['untrusted_legacy_design_context']==source['untrusted_legacy_design_context']
 assert view['source_authority']==source['source_authority']
 assert proof['original_diagnostics_sha256']==content_hash(diagnostics)
 assert proof['dispatch_allowed'] is False and proof['old_false_upgraded'] is False

def test_preview_is_not_active_policy_or_state_mutation():
 from bestseller.services import planning_draft_admission as a
 old=(a.VOLUME_HARD_ADMISSION_POLICY,a.MAX_PROTOCOL_REPAIRS,a.HARD_ADMISSION_POLICY)
 state={'status':'protocol_exhausted','graded_input_hash':'paid-original','attempts':[{'draft_eligible':False},{'draft_eligible':False}]}
 source={'untrusted_commercial_report':{'state':deepcopy(state)},'candidate_payload':{'volumes':[{'text':'original'}]}}
 view,receipt=preview_reference_only_review_input(source)
 assert old==(a.VOLUME_HARD_ADMISSION_POLICY,a.MAX_PROTOCOL_REPAIRS,a.HARD_ADMISSION_POLICY)
 assert state['status']=='protocol_exhausted' and len(state['attempts'])==2
 assert receipt['existing_grade_or_journal_changed'] is False
 assert 'draft_eligible' not in view


def test_volume_diagnostics_accepts_valid_dot_path_without_broadening_chapter_policy():
 import json
 from bestseller.services import planning_draft_admission as a
 from bestseller.services.planning_admission_diagnostics import collect_protocol_feedback
 c={'volumes':[{'information_flow_scene_cards':[{'actual_action':'A plain inventory.'}]}]}
 supplied={'approved_world_spec':{'rule':'Touch required.'}};supplied['source_authority']=a._source_authority(supplied)
 raw={'draft_eligible':False,'hard_violations':[{'code':'SCENE_NOT_EXECUTABLE','path':'volumes[0].information_flow_scene_cards[0]','evidence':'A plain inventory.','source_requirement':'A plain inventory.','source_refs':['candidate:volumes[0].information_flow_scene_cards[0].actual_action']}],'editorial_notes':[], 'projection_checks':[{**t,'status':'conflict','explanation':'fixture','related_violation_paths':['volumes[0].information_flow_scene_cards[0]']} for t in a.projection_audit_targets(c)]}
 errors=collect_protocol_feedback(json.dumps(raw),c,supplied)
 assert not any(e.get('code')=='admission_candidate_ref_invalid' for e in errors)
