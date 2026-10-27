from copy import deepcopy
import pytest
from bestseller.services.chapter_review_prerequisite_resume import original_waiting_attempt
pytestmark=pytest.mark.unit
@pytest.mark.parametrize('fault',[None,'body','cycle','state','attempt','namespace','completed_field','history'])
def test_original_slot_retains_every_old_receipt_and_no_new_credit(fault):
 cycle={'cycle_id':'cycle','source_draft_id':'draft','source_body_hash':'a'*64,'state':'waiting_infrastructure','attempts':[{'attempt':1,'state':'waiting_infrastructure','operation_namespace':'chapter-critic:cycle:1','infrastructure_history':[{'original':'raw unknown receipt'}]}]}
 proof={'reuse_kind':'waiting_infrastructure','cycle_id':'cycle','draft_id':'draft','body_sha256':'a'*64}
 if fault=='body':proof['body_sha256']='b'*64
 if fault=='cycle':proof['cycle_id']='other'
 if fault=='state':cycle['state']='completed'
 if fault=='attempt':cycle['attempts'][0]['attempt']=2
 if fault=='namespace':cycle['attempts'][0]['operation_namespace']='different'
 if fault=='completed_field':cycle['attempts'][0]['outcome']='available'
 if fault=='history':cycle['attempts'][0]['infrastructure_history']=[]
 before=deepcopy(cycle)
 if fault:
  with pytest.raises(ValueError):original_waiting_attempt(cycle,proof=proof,authority_receipt_id='authority')
 else:
  after=original_waiting_attempt(cycle,proof=proof,authority_receipt_id='authority')
  assert len(after['attempts'])==1 and after['attempts'][0]['attempt']==1
  assert after['attempts'][0]['operation_namespace']==before['attempts'][0]['operation_namespace']
  assert after['attempts'][0]['infrastructure_history']==before['attempts'][0]['infrastructure_history']
  assert after['attempts'][0]['prerequisite_resume_history'][0]['new_attempt_credit'] is False
 assert cycle==before
