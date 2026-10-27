"""Explicit zero-provider deterministic refresh; original references stay immutable."""
import ast
from copy import deepcopy
import hashlib
from pathlib import Path
from uuid import UUID,uuid5,NAMESPACE_URL,uuid4
from sqlalchemy import select
from bestseller.infra.db import models as m
from bestseller.services.prompt_source_policy import content_hash

SCHEMA='pending-deterministic-revalidation.v1'
NEW_FILES={'src/bestseller/services/chapter_writer_delivery.py','src/bestseller/services/draft_continuity_revalidation.py','src/bestseller/services/chapter_repair_attempt_history.py','src/bestseller/services/draft_continuity_axis_adjudication.py'}
OWNED={'src/bestseller/services/drafts.py':'generate_chapter_draft_once',
       'src/bestseller/services/draft_continuity.py':'_verify_deterministic_receipt',
       'src/bestseller/services/pipelines.py':'chapter_review_recovery_source_hash'}

def need(ok,code):
    if not ok:raise ValueError('pending_revalidation_'+code)

def sha(data):return hashlib.sha256(data).hexdigest()

def policy_paths(root):
    source=(root/'src/bestseller/services/pipelines.py').read_text()
    fn=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='chapter_review_recovery_source_hash')
    comp=next(n for n in ast.walk(fn) if isinstance(n,ast.comprehension) and isinstance(n.target,ast.Name) and n.target.id=='name')
    names=ast.literal_eval(comp.iter)
    paths={root/'src/bestseller/services'/n for n in names}
    paths.update((root/'src/bestseller/services/quality_levers').rglob('*.py'))
    paths.update(root/n for n in ['src/bestseller/domain/llm_quality_judge.py','src/bestseller/domain/ideology.py','config/quality_gates.yaml','config/model_catalog.yaml'])
    return sorted(paths)

def policy_manifest(root):
    return {str(p.relative_to(root)):sha(p.read_bytes()) for p in policy_paths(root)}

def manifest_hash(manifest):
    digest=hashlib.sha256()
    for name,value in sorted(manifest.items()):
        digest.update(name.encode()+b'\0');digest.update(bytes.fromhex(value))
    return digest.hexdigest()

def permitted_source_edit(name,old,new):
    before,after=ast.parse(old),ast.parse(new)
    target=OWNED[name]
    new_fn=next(n for n in after.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==target)
    if name.endswith('/drafts.py'):
        old_history=next(n for n in before.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_load_chapter_repair_history')
        index_history=next(i for i,n in enumerate(after.body) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_load_chapter_repair_history')
        expected_history=ast.parse('async def _load_chapter_repair_history(session: Any, chapter: ChapterModel, *, limit: int = 12) -> list[dict[str, Any]]:\n from bestseller.services.chapter_repair_attempt_history import load_repair_attempt_history\n return await load_repair_attempt_history(session, chapter=chapter, limit=limit)').body[0]
        need(ast.dump(after.body[index_history])==ast.dump(expected_history),'attempt_history_dispatch_changed')
        after.body[index_history]=deepcopy(old_history)
        calls=[n for n in ast.walk(new_fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='build_chapter_first_draft_prompts']
        need(len(calls)==1,'writer_call_inventory')
        keywords=[k for k in calls[0].keywords if k.arg=='compiler_additional_blocks']
        need(len(keywords)==1 and ast.dump(keywords[0].value)==ast.dump(ast.Name(id='approved_delivery_blocks',ctx=ast.Load())),'writer_task_binding')
        calls[0].keywords.remove(keywords[0])
        snippet=ast.parse('from bestseller.services.chapter_writer_delivery import load_delivery_blocks\napproved_delivery_blocks = await load_delivery_blocks(session, effective_settings, project=project, chapter=chapter, scenes=scenes)').body
        index=next(i for i,n in enumerate(new_fn.body) if isinstance(n,ast.ImportFrom) and n.module=='bestseller.services.chapter_writer_delivery')
        need(ast.dump(ast.Module(body=new_fn.body[index:index+2],type_ignores=[]))==ast.dump(ast.Module(body=snippet,type_ignores=[])),'writer_source_loader_changed')
        del new_fn.body[index:index+2]
    elif name.endswith('/draft_continuity.py'):
        snippet=ast.parse("if proof.get('hard_policy_sha256')!=hard_policy_hash():\n from bestseller.services.draft_continuity_revalidation import resolve_revalidation\n proof=await resolve_revalidation(session,draft=draft,original_proof=proof)").body[0]
        matches=[i for i,n in enumerate(new_fn.body) if ast.dump(n)==ast.dump(snippet)]
        need(len(matches)==1,'deterministic_dispatch_changed')
        new_fn.body.pop(matches[0])
        # Exact additional schema dispatch only. Legacy adjudication and the
        # native parser branches below it remain byte-for-byte AST-equivalent.
        selector=next(n for n in after.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='select_reference')
        branch=next(n for n in selector.body if isinstance(n,ast.If) and ast.dump(n.test)==ast.dump(ast.parse("proof.get('schema')=='source-adjudicated-axis-continuity.v1'",mode='eval').body))
        expected=ast.parse("from bestseller.services.draft_continuity_axis_adjudication import validate_reference\nawait validate_reference(session,settings,project=project,chapter=chapter,draft=draft,proof=proof,_memo=_memo)").body
        need(ast.dump(ast.Module(body=branch.body,type_ignores=[]))==ast.dump(ast.Module(body=expected,type_ignores=[])) and len(branch.orelse)==1 and isinstance(branch.orelse[0],ast.If),'independent_axis_dispatch_changed')
        selector.body[selector.body.index(branch)]=branch.orelse[0]
    else:
        tuple_node=next(n.iter for n in ast.walk(new_fn) if isinstance(n,ast.comprehension) and isinstance(n.target,ast.Name) and n.target.id=='name')
        added={Path(n).name for n in NEW_FILES}
        removed=[n for n in tuple_node.elts if isinstance(n,ast.Constant) and n.value in added]
        need({n.value for n in removed}==added and len(removed)==len(NEW_FILES),'source_manifest_additions')
        tuple_node.elts=[n for n in tuple_node.elts if n not in removed]
    need(ast.dump(before)==ast.dump(after),'non_writer_or_review_rules_changed')

def verify_source_transition(*,old_manifest,old_sources,current_root,old_policy,new_policy):
    need(manifest_hash(old_manifest)==old_policy,'original_policy_manifest')
    current=policy_manifest(current_root)
    need(manifest_hash(current)==new_policy,'current_policy_manifest')
    changed={n for n in set(old_manifest)|set(current) if old_manifest.get(n)!=current.get(n)}
    if changed=={'src/bestseller/services/chapter_writer_delivery.py','src/bestseller/services/draft_continuity_revalidation.py'}:
        for name in changed:
            need(sha(old_sources[name].encode())==old_manifest[name],'original_source_bytes')
        _verify_presentation_delta(old_sources=old_sources,current_root=current_root)
        return {'schema':'writer-length-reference-transition.v1','old_policy_sha256':old_policy,'current_policy_sha256':new_policy,'old_manifest_sha256':content_hash(old_manifest),'current_manifest_sha256':content_hash(current),'changed_paths':sorted(changed),'substantive_old_review_rules_unchanged':True,'original_grade_relabelled':False}
    need(changed==set(OWNED)|NEW_FILES,'unrelated_policy_change')
    for name in OWNED:
        need(sha(old_sources[name].encode())==old_manifest[name],'original_source_bytes')
        permitted_source_edit(name,old_sources[name],(current_root/name).read_text())
    return {'schema':'producer-and-repair-accounting-transition.v2','old_policy_sha256':old_policy,
        'current_policy_sha256':new_policy,'old_manifest_sha256':content_hash(old_manifest),
        'current_manifest_sha256':content_hash(current),'changed_paths':sorted(changed),
        'substantive_old_review_rules_unchanged':True,'original_grade_relabelled':False}

def identity(original_proof,current_policy):
    return uuid5(NAMESPACE_URL,SCHEMA+':'+content_hash(original_proof)+':'+current_policy)

async def resolve_revalidation(session,*,draft,original_proof):
    from bestseller.services import draft_continuity as dc
    from bestseller.services.operator_action_approval import require_operator_action_approval
    current=dc.hard_policy_hash();row=await session.get(m.WorkflowRunModel,identity(original_proof,current),populate_existing=True)
    need(row is not None and row.project_id==draft.project_id and row.scope_id==draft.chapter_id
        and row.workflow_type=='draft_continuity_deterministic_revalidation' and row.status=='completed','current_receipt_missing')
    data=row.metadata_json;fresh=data['proof']
    need(fresh['schema']==SCHEMA and fresh['original_proof_sha256']==content_hash(original_proof)
        and fresh['hard_policy_sha256']==current and fresh['draft_id']==str(draft.id)
        and fresh['body_sha256']==dc.body_hash(draft.content_md)==original_proof['body_sha256']
        and fresh['source_sha256']==original_proof['source_sha256']
        and fresh['source_transition']['old_policy_sha256']==original_proof['hard_policy_sha256']
        and fresh['source_transition']['current_policy_sha256']==current
        and fresh['source_transition']['substantive_old_review_rules_unchanged'] is True,'current_receipt_binding')
    binding={'project_id':str(draft.project_id),'target_id':str(row.id),'proof_sha256':content_hash(fresh),
        'draft_id':str(draft.id),'body_sha256':fresh['body_sha256'],'source_sha256':fresh['source_sha256'],'formal_adoption':False}
    need(data['binding']==binding,'approval_binding')
    await require_operator_action_approval(session,approval_id=UUID(data['approval_id']),purpose='draft_continuity_reference',binding=binding,capability='reference_pending_manuscript')
    return {**original_proof,**{k:fresh[k] for k in ['hard_policy_sha256','quality_report_id','deterministic_outcome','deterministic_binding']}}

async def revalidate_reference(session,settings,*,project,chapter,reference_id,old_manifest,old_sources,actor,authorization_ref,explicit_user_approval=False):
    from bestseller.services import draft_continuity as dc,draft_continuity_adjudication as adjudicate
    from bestseller.services.operator_action_approval import require_operator_action_approval,record_operator_action_approval
    from bestseller.services.drafts import _evaluate_chapter_quality_gate,_collect_post_assembly_duplicate_findings
    need(explicit_user_approval is True and actor and authorization_ref,'explicit_authorization')
    row=await session.get(m.WorkflowRunModel,reference_id,populate_existing=True)
    need(row is not None and row.project_id==project.id and row.scope_id==chapter.id and row.workflow_type=='draft_continuity_reference' and row.status=='completed','original_reference')
    original=deepcopy(row.metadata_json);proof=original['proof']
    from bestseller.services import draft_continuity_axis_adjudication as axis_adjudicate
    validator=adjudicate if proof.get('schema')==adjudicate.POLICY else axis_adjudicate if proof.get('schema')==axis_adjudicate.POLICY else None
    need(validator is not None and proof['adjudicator_source_sha256']==validator.policy_hash()
        and original['binding']['proof_sha256']==content_hash(proof),'original_adjudication')
    await require_operator_action_approval(session,approval_id=UUID(original['approval_id']),purpose='draft_continuity_reference',binding=original['binding'],capability='reference_pending_manuscript')
    draft=await session.get(m.ChapterDraftVersionModel,UUID(proof['draft_id']))
    need(draft.project_id==project.id and draft.chapter_id==chapter.id and dc.body_hash(draft.content_md)==proof['body_sha256'],'original_body')
    sources=await dc.current_sources(session,settings,project=project,chapter=chapter)
    need(content_hash(sources)==proof['source_sha256'],'current_sources_changed')
    current=dc.hard_policy_hash();current_root=Path(__file__).resolve().parents[3]
    transition=verify_source_transition(old_manifest=old_manifest,old_sources=old_sources,current_root=current_root,old_policy=proof['hard_policy_sha256'],new_policy=current)
    raw,physical=await adjudicate.verify_paid_evidence(session,settings,project=project,chapter=chapter,draft=draft,sources=sources,evidence=proof['native_evidence'])
    actions=(adjudicate.derive_axis_evidence(raw,body=draft.content_md,sources=sources,decisions=proof['decisions']) if validator is adjudicate
        else axis_adjudicate.derive_actions(raw,body=draft.content_md,sources=sources,axis_action=proof['axis_action'],decisions=proof['decisions']))
    need(physical==proof['physical_evidence'] and actions==proof['source_adjudication'],'old_physical_or_positive_evidence_changed')
    ident=identity(proof,current)
    if await session.get(m.WorkflowRunModel,ident) is not None:
        await dc._verify_deterministic_receipt(session,draft=draft,proof=proof)
        return {'id':str(ident),'reused':True,'original_reference_id':str(row.id)}
    tag={'evaluation_id':str(uuid4()),'draft_id':str(draft.id),'body_sha256':proof['body_sha256'],'source_sha256':proof['source_sha256']}
    outcome=await _evaluate_chapter_quality_gate(session=session,project=project,chapter_number=chapter.chapter_number,content=draft.content_md,register_diversity=False,extra_report_payload={'draft_continuity_hard_safety':tag})
    duplicates=await _collect_post_assembly_duplicate_findings(session,project=project,chapter=chapter,content_md=draft.content_md)
    need(outcome=='ok' and not duplicates,'fresh_deterministic_not_positive')
    reports=list(await session.scalars(select(m.ChapterQualityReportModel).where(m.ChapterQualityReportModel.chapter_id==chapter.id).order_by(m.ChapterQualityReportModel.created_at.desc()).limit(10)))
    qr=next(r for r in reports if r.report_json.get('draft_continuity_hard_safety')==tag)
    fresh={'schema':SCHEMA,'original_reference_id':str(row.id),'original_proof_sha256':content_hash(proof),
        'draft_id':str(draft.id),'body_sha256':proof['body_sha256'],'source_sha256':proof['source_sha256'],
        'hard_policy_sha256':current,'quality_report_id':str(qr.id),'deterministic_outcome':outcome,'deterministic_binding':tag,
        'source_transition':transition,'new_model_calls':0,'original_paid_or_grade_changed':False}
    binding={'project_id':str(project.id),'target_id':str(ident),'proof_sha256':content_hash(fresh),'draft_id':str(draft.id),
        'body_sha256':proof['body_sha256'],'source_sha256':proof['source_sha256'],'formal_adoption':False}
    approval=await record_operator_action_approval(session,purpose='draft_continuity_reference',binding=binding,capabilities=['reference_pending_manuscript'],actor=actor,authorization_ref=authorization_ref,explicit_user_approval=True)
    session.add(m.WorkflowRunModel(id=ident,project_id=project.id,workflow_type='draft_continuity_deterministic_revalidation',scope_type='chapter',scope_id=chapter.id,status='completed',current_step='current_deterministic_verified',requested_by=actor,metadata_json={'proof':fresh,'binding':binding,'approval_id':str(approval)}))
    await session.flush()
    need(row.metadata_json==original and chapter.metadata_json.get(dc.POINTER)==str(row.id),'original_reference_mutated')
    return {'id':str(ident),'reused':False,'original_reference_id':str(row.id),'proof':fresh}


def _verify_presentation_delta(*, old_sources, current_root):
    """Only exact length references/instructions and old-schema readback dispatch."""
    delivery='src/bestseller/services/chapter_writer_delivery.py'
    revalidation='src/bestseller/services/draft_continuity_revalidation.py'
    old=old_sources[delivery];new=(current_root/delivery).read_text()
    added="'information_held_back','target_word_count')"
    original="'information_held_back')"
    need(new.count(added)==1,'presentation_target_projection')
    new=new.replace(added,original)
    insertion="    if approved.get('target_word_count') is not None:\n        payload['chapter_target_word_count_reference']=deepcopy(approved['target_word_count'])\n"
    need(new.count(insertion)==1,'presentation_chapter_target')
    new=new.replace(insertion,'')
    suffix=("'这些计划不授权撤销历史，也不构成角色已经知道全部信息的证据；入场措辞与已核验前文冲突时，以前文为准，保留规划偏差。'\n"
        "        '篇幅按本章已明确的总目标和范围执行；已有场景目标只作展开篇幅参考，不是新增逐场硬配额，也不要求平均。'\n"
        "        '中文按汉字逐字计数，连续英文或数字词组按词计数，标点、空白与Markdown标记不计；不要把含标点总字符数当有效字数。'\n"
        "        '用必要现场行动、选择与后果完成任务，未达已明确下限前不得提前收束成摘要；不复述、堆环境或把一个动作切成瞬间凑字。\\n')")
    need(new.count(suffix)==1,'presentation_exact_instruction_delta')
    new=new.replace(suffix,"'这些计划不授权撤销历史，也不构成角色已经知道全部信息的证据。\\n')")
    need(ast.dump(ast.parse(old))==ast.dump(ast.parse(new)),'presentation_unrelated_delivery_edit')
    before=ast.parse(old_sources[revalidation]);after=ast.parse((current_root/revalidation).read_text())
    fn=next(n for n in after.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='revalidate_reference')
    replacements=[
        ("from bestseller.services import draft_continuity_axis_adjudication as axis_adjudicate\nvalidator=adjudicate if proof.get('schema')==adjudicate.POLICY else axis_adjudicate if proof.get('schema')==axis_adjudicate.POLICY else None\nneed(validator is not None and proof['adjudicator_source_sha256']==validator.policy_hash() and original['binding']['proof_sha256']==content_hash(proof),'original_adjudication')",
         "need(proof['schema']==adjudicate.POLICY and proof['adjudicator_source_sha256']==adjudicate.policy_hash() and original['binding']['proof_sha256']==content_hash(proof),'original_adjudication')"),
        ("actions=(adjudicate.derive_axis_evidence(raw,body=draft.content_md,sources=sources,decisions=proof['decisions']) if validator is adjudicate else axis_adjudicate.derive_actions(raw,body=draft.content_md,sources=sources,axis_action=proof['axis_action'],decisions=proof['decisions']))\nneed(physical==proof['physical_evidence'] and actions==proof['source_adjudication'],'old_physical_or_positive_evidence_changed')",
         "need(physical==proof['physical_evidence'] and adjudicate.derive_axis_evidence(raw,body=draft.content_md,sources=sources,decisions=proof['decisions'])==proof['source_adjudication'],'old_physical_or_positive_evidence_changed')")]
    for added_code,original_code in replacements:
        snippet=ast.parse(added_code).body
        matches=[i for i in range(len(fn.body)-len(snippet)+1) if ast.dump(ast.Module(body=fn.body[i:i+len(snippet)],type_ignores=[]))==ast.dump(ast.Module(body=snippet,type_ignores=[]))]
        need(len(matches)==1,'presentation_exact_revalidation_dispatch')
        fn.body[matches[0]:matches[0]+len(snippet)]=ast.parse(original_code).body
    transition=next(n for n in after.body if isinstance(n,ast.FunctionDef) and n.name=='verify_source_transition')
    branch=ast.parse("if changed=={'src/bestseller/services/chapter_writer_delivery.py','src/bestseller/services/draft_continuity_revalidation.py'}:\n for name in changed:\n  need(sha(old_sources[name].encode())==old_manifest[name],'original_source_bytes')\n _verify_presentation_delta(old_sources=old_sources,current_root=current_root)\n return {'schema':'writer-length-reference-transition.v1','old_policy_sha256':old_policy,'current_policy_sha256':new_policy,'old_manifest_sha256':content_hash(old_manifest),'current_manifest_sha256':content_hash(current),'changed_paths':sorted(changed),'substantive_old_review_rules_unchanged':True,'original_grade_relabelled':False}").body[0]
    matches=[i for i,n in enumerate(transition.body) if ast.dump(n)==ast.dump(branch)]
    need(len(matches)==1,'presentation_exact_transition_branch')
    transition.body.pop(matches[0])
    after.body=[n for n in after.body if not isinstance(n,ast.FunctionDef) or n.name!='_verify_presentation_delta']
    need(ast.dump(before)==ast.dump(after),'presentation_other_revalidation_edit')
