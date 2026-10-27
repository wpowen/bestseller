"""Only an explicit additional schema dispatch may reuse the approved baseline."""
from pathlib import Path
import hashlib
import pytest
from bestseller.services import draft_continuity_reference_transition as service
pytestmark=pytest.mark.unit

@pytest.mark.parametrize('fault',[None,'legacy_branch','dispatch_body','outside_function','manifest_extra','wrong_old_bytes'])
def test_exact_additive_dispatch_cannot_waive_legacy_rules(tmp_path,monkeypatch,fault):
 dc='src/bestseller/services/draft_continuity.py';pipeline='src/bestseller/services/pipelines.py'
 old_dc="""async def select_reference(session,settings,*,project,chapter,_memo=None):
    if proof.get('schema')=='source-adjudicated-axis-continuity.v1':
        need(source_is_exact,'legacy_source_required')
    else:
        need(native_is_complete,'native_required')
OLD_RULE=True
"""
 new_dc=old_dc.replace("    if proof.get('schema')=='source-adjudicated-axis-continuity.v1':", "    if proof.get('schema')=='draft-continuity-source-corrected-reference.v1':\n        from bestseller.services.draft_continuity_source_actions import validate_reference\n        await validate_reference(session,settings,project=project,chapter=chapter,draft=draft,proof=proof,_memo=_memo)\n    elif proof.get('schema')=='source-adjudicated-axis-continuity.v1':")
 old_pipeline="def chapter_review_recovery_source_hash():\n    return {name for name in ('old.py',)}\n"
 names=tuple(Path(n).name for n in sorted(service.NEW_FILES))
 new_pipeline="def chapter_review_recovery_source_hash():\n    return {name for name in "+repr(('old.py',)+names)+"}\n"
 old={dc:old_dc,pipeline:old_pipeline};new={dc:new_dc,pipeline:new_pipeline,**{n:'pass\n' for n in service.NEW_FILES}}
 if fault=='legacy_branch':new[dc]=new[dc].replace('need(source_is_exact','need(True')
 if fault=='dispatch_body':new[dc]=new[dc].replace('draft=draft,proof=proof','draft=draft,proof=other_proof')
 if fault=='outside_function':new[dc]=new[dc].replace('OLD_RULE=True','OLD_RULE=False')
 if fault=='manifest_extra':new['unexpected.py']='pass\n'
 for name,text in new.items():
  path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
 sha=lambda text:hashlib.sha256(text.encode()).hexdigest()
 baseline={n:sha(text) for n,text in old.items()}
 if fault=='wrong_old_bytes':old[dc]+='\nEXTRA=True\n'
 monkeypatch.setattr(service.old_refresh,'policy_manifest',lambda root:{n:sha(text) for n,text in new.items()})
 if fault:
  with pytest.raises(ValueError):service.verify_dispatch_delta(baseline_manifest=baseline,baseline_sources=old,current_root=tmp_path)
 else:
  _,receipt=service.verify_dispatch_delta(baseline_manifest=baseline,baseline_sources=old,current_root=tmp_path)
  assert receipt['old_eligibility_parser_and_source_rules_unchanged'] is True
  assert set(receipt['changed_paths'])==service.NEW_FILES|service.OWNED
