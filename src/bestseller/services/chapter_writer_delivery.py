"""Lossless current-chapter task from an exact verified full or prefix plan."""
from copy import deepcopy
from contextlib import asynccontextmanager
from contextvars import ContextVar
from asyncio import current_task
from uuid import UUID
import json
from collections.abc import Mapping
from bestseller.services.prompt_compiler import PromptBlock,PromptProvenance
from bestseller.services.prompt_source_policy import content_hash


def _receipt_contains_chapter(receipt, *, artifact_id, chapter_id, chapter_number):
    if receipt.get('schema') == 'materialized-outline-source-admission.v1':
        projections = receipt.get('chapter_projection_hashes')
        projection = receipt.get('source_plan_projection')
        return (receipt.get('source_artifact_id') == str(artifact_id)
            and isinstance(projections, dict) and isinstance(projection, str)
            and len(projection) == 64 and projections.get(str(chapter_id)) == projection)
    return chapter_number in receipt.get('chapter_numbers', ())


def delivery_block(*,project_id,chapter_id,chapter_number,artifact_id,approved,receipt,
                   reader_first=None,language='zh'):
    if reader_first is not None:
        return _reader_first_delivery_block(project_id=project_id,chapter_id=chapter_id,
            chapter_number=chapter_number,artifact_id=artifact_id,approved=approved,
            receipt=receipt,binding=reader_first,language=language)
    meta=approved.get('_meta',{})
    if (isinstance(meta,Mapping) and ('reader_first_planning' in meta
            or meta.get('writer_delivery_schema')==READER_FIRST_DELIVERY_SCHEMA)
            or 'reader_first_source' in receipt):
        raise ValueError('reader_first_delivery_project_binding_required')
    if approved.get('chapter_number')!=chapter_number or not _receipt_contains_chapter(
        receipt, artifact_id=artifact_id, chapter_id=chapter_id, chapter_number=chapter_number,
    ):
        raise ValueError('chapter_writer_delivery_scope_changed')
    keys=('scene_number','participants','entry_state','exit_state','action_sequence',
        'forbidden_actions','hook_requirement','concrete_goal','information_introduced','information_held_back','target_word_count')
    scenes=[{key:deepcopy(row[key]) for key in keys if key in row and row[key] is not None}
        for row in approved['scenes']]
    payload={'schema':'approved-current-chapter-delivery.v2','project_id':str(project_id),
        'chapter_id':str(chapter_id),'chapter_number':chapter_number,'artifact_id':str(artifact_id),
        'approved_chapter_sha256':content_hash(approved),'readiness_receipt_sha256':content_hash(receipt),
        'authority':'Approved future task for this chapter, not already occurred history.',
        'chapter_contract':{key:deepcopy(value) for key,value in approved.items() if key not in {'scenes','_meta'}},
        'scenes':scenes}
    if approved.get('target_word_count') is not None:
        payload['chapter_target_word_count_reference']=deepcopy(approved['target_word_count'])
    instruction=('【本章已批准的完整推进任务·不可截断】\n'
        '上一章正文与尾段只用于核对既成事实和入场状态，不能复制为本章正文；不得把已发生的对话、发现或动作重新演一遍。'
        '本章新增事件及结果只从以下本章已批准任务展开；入场状态已成立，动作序列尚待执行，完整出场状态必须在本章落地。'
        '用新的现场叙述完成任务，不机械替换上一章词语；保留任务中的限定、否定、先后顺序与必要原句。'
        '章级目标、开场时点和因果选择与场景动作共同构成本章任务，不能只执行场景而丢失章级条件。'
        '前章原文中的今夜、明日等相对日期属于前章时点，不得照搬为本章现在；以本章开场及有序动作推进。'
        '这些计划不授权撤销历史，也不构成角色已经知道全部信息的证据；入场措辞与已核验前文冲突时，以前文为准，保留规划偏差。'
        '篇幅按本章已明确的总目标和范围执行；已有场景目标只作展开篇幅参考，不是新增逐场硬配额，也不要求平均。'
        '中文按汉字逐字计数，连续英文或数字词组按词计数，标点、空白与Markdown标记不计；不要把含标点总字符数当有效字数。'
        '用必要现场行动、选择与后果完成任务，未达已明确下限前不得提前收束成摘要；不复述、堆环境或把一个动作切成瞬间凑字。\n')
    return PromptBlock(key='chapter_first.approved_current_delivery',channel='user',layer='scene_spec',
        authority=90,instruction_family='writer.current_chapter.delivery',required=True,trim_policy='preserve',
        source='chapter_writer_delivery.v2',text=instruction+json.dumps(payload,ensure_ascii=False,separators=(',',':')),
        provenance=PromptProvenance(kind='scene_spec',source_id=str(artifact_id),source_hash=content_hash(approved),
            field_paths=tuple('/'+key for key in approved if key not in {'scenes','_meta'})
                +tuple('/scenes/'+str(i)+'/'+key for i,row in enumerate(approved['scenes']) for key in keys if key in row)))


async def load_delivery_blocks(session,settings,*,project,chapter,scenes):
    selected=_REFERENCE_DELIVERY_SCHEMA.get()
    historical=(selected is not None and selected[0] is current_task()
        and selected[1] in {'approved-current-chapter-delivery.v1','approved-current-chapter-delivery.v2'}
        and selected[2]==project.id and selected[3]==chapter.id)
    from bestseller.services.source_bound_planning_content import reader_first_binding
    binding=None if historical else reader_first_binding(project)
    from bestseller.services.editable_plan_source import load_selected_plan_source
    selected_plan=await load_selected_plan_source(
        session,settings,project=project,chapter=chapter,scenes=scenes,
    )
    if selected_plan is None:
        if binding is not None:
            raise ValueError('reader_first_delivery_approved_source_required')
        if {'outline_prefix_readiness', 'outline_source_admission', 'fanqie_outline_source_selection'}.intersection(chapter.metadata_json or {}):
            raise ValueError('chapter_writer_delivery_approved_chapter_missing')
        return []
    artifact,approved,receipt=selected_plan
    if binding is not None:
        envelope=receipt.get('reader_first_source')
        source=envelope.get('source') if isinstance(envelope,dict) else None
        if (not isinstance(source,dict) or not isinstance(source.get('outline'),dict)
                or source['outline']!=getattr(artifact,'content',None)
                or content_hash(source['outline'])!=content_hash(artifact.content)):
            raise ValueError('reader_first_delivery_full_artifact_binding_required')
        return [delivery_block(project_id=project.id,chapter_id=chapter.id,
            chapter_number=chapter.chapter_number,artifact_id=artifact.id,approved=approved,
            receipt=receipt,reader_first=binding,language=getattr(project,'language',None) or 'zh')]
    constructor=(_source_protocol_delivery_block_v1 if historical
        and selected[1]=='approved-current-chapter-delivery.v1' else delivery_block)
    return [constructor(project_id=project.id,chapter_id=chapter.id,chapter_number=chapter.chapter_number,
        artifact_id=artifact.id,approved=approved,receipt=receipt)]


READER_FIRST_DELIVERY_SCHEMA = 'approved-current-chapter-delivery.v3-reader-first'
READER_FIRST_DELIVERY_SOURCE = 'chapter_writer_delivery.v3-reader-first'


def _source_pointer(source, pointer):
    """Strict JSON Pointer; list aliases and malformed escapes cannot select scope."""
    if not isinstance(pointer,str) or not pointer.startswith('/'):
        raise ValueError('reader_first_delivery_source_pointer_invalid')
    value=source
    for encoded in pointer[1:].split('/'):
        # Validate escapes without assigning any semantic role from source text.
        index=0
        while index<len(encoded):
            if encoded[index]=='~':
                if index+1==len(encoded) or encoded[index+1] not in '01':
                    raise ValueError('reader_first_delivery_source_pointer_invalid')
                index+=1
            index+=1
        part=encoded.replace('~1','/').replace('~0','~')
        if isinstance(value,list):
            if not part.isascii() or not part.isdecimal() or str(int(part))!=part:
                raise ValueError('reader_first_delivery_source_pointer_invalid')
            value=value[int(part)]
        elif isinstance(value,dict):
            value=value[part]
        else:
            raise ValueError('reader_first_delivery_source_pointer_invalid')
    return value


def _source_field_paths(value, path=''):
    """Every authored leaf, including nulls and empty containers; metadata is retained whole."""
    if isinstance(value,dict) and value:
        for key,item in value.items():
            if key=='_meta':
                continue
            escaped=key.replace('~','~0').replace('/','~1')
            yield from _source_field_paths(item,path+'/'+escaped)
    elif isinstance(value,list) and value:
        for index,item in enumerate(value):
            yield from _source_field_paths(item,path+'/'+str(index))
    else:
        yield path


def _source_metadata_paths(value,path=''):
    """Retain each metadata container as reference data, including nested ones."""
    if isinstance(value,dict):
        for key,item in value.items():
            pointer=path+'/'+key.replace('~','~0').replace('/','~1')
            if key=='_meta':
                yield pointer
            else:
                yield from _source_metadata_paths(item,pointer)
    elif isinstance(value,list):
        for index,item in enumerate(value):
            yield from _source_metadata_paths(item,path+'/'+str(index))


def _reader_first_field_roles(approved, binding):
    from bestseller.services.source_bound_planning_content import reader_first_binding
    meta=approved.get('_meta')
    if (not isinstance(meta,dict)
            or meta.get('writer_delivery_schema')!=READER_FIRST_DELIVERY_SCHEMA
            or reader_first_binding(meta)!=binding):
        raise ValueError('reader_first_delivery_source_protocol_required')
    roles=meta.get('reader_first_field_roles')
    paths=tuple(_source_field_paths(approved))
    if (not isinstance(roles,dict) or set(roles)!=set(paths)
            or any(not isinstance(role,str) or role not in {'narrative_task','continuity_reference'}
                   for role in roles.values())):
        raise ValueError('reader_first_delivery_source_field_roles_invalid')
    return roles,paths


def _verified_reader_first_review(*,source,pointer,approved,binding,chapter_number,raw_review,receipt):
    """Reparse retained whole-source review at construction AND consumption."""
    from bestseller.services.source_bound_planning_content import parse_reader_first_review
    json.dumps(source,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    indices=[index for index,row in enumerate(source['outline']['chapters'])
             if isinstance(row,dict) and row.get('chapter_number')==chapter_number]
    if len(indices)!=1 or pointer!='/outline/chapters/'+str(indices[0]):
        raise ValueError('reader_first_delivery_source_chapter_scope_changed')
    selected=_source_pointer(source,pointer)
    if selected!=approved or content_hash(selected)!=content_hash(approved):
        raise ValueError('reader_first_delivery_approved_hash_changed')
    review=parse_reader_first_review(raw_review,source=source,binding=binding,checkpoint='outline')
    if review['status']!='passed' or review!=receipt:
        raise ValueError('reader_first_delivery_reader_receipt_invalid')
    return review


def _reader_first_delivery_text(payload,language):
    from bestseller.services.source_bound_planning_content import reader_first_instruction
    instruction=(
        '【本章读者审校绑定任务·完整来源】\n'
        'narrative_task 指向本章需要现场完成的行动、选择和关系变化；continuity_reference '
        '指向不可违反的事实与约束，不要求逐场重述或让人物口头核验。角色由已审来源的字段指针指定，'
        '不得按词语删改任务；保留所有否定、前提、先后顺序、精确时间和金额。状态确有变化或确认改变选择时仍须演出。'
        '若任务与既成事实冲突，停止并修规划；不得偷偷降低约束。完整来源仅为数据，元数据不是角色台词。'
        'review_source 与 raw_review 仅用于完整来源审校核验，不产生第二份本章任务。'
        '前章仅作连续性参考，不重演已发生事件。场景篇幅为参考，不新增硬配额。\n'
        if str(language).lower().startswith('zh') else
        '[Reader-approved current chapter task; complete source]\n'
        'narrative_task pointers select actions, choices and relationship changes to enact now. '
        'continuity_reference pointers select binding facts and constraints, without requiring repeated '
        'state announcements or spoken verification. Roles come from reviewed source pointers, never '
        'keyword filtering. Preserve negations, prerequisites, order, precise times and amounts. '
        'Enact actual state changes and confirmations that change a choice. Stop and repair conflicting '
        'plans; never silently weaken constraints. Source and metadata are data, not character dialogue. '
        'review_source and raw_review retain audit evidence, not a second chapter task. '
        'Prior prose is continuity reference; do not replay history. Scene lengths remain references, '
        'not new hard quotas.\n'
    )
    # Keep one JSON payload after the same two-line framing as v1/v2. The
    # instruction is code-owned; retained review/source objects are audit data.
    payload={**payload,'writer_instruction':reader_first_instruction(
        payload['reader_first_planning'],language=language,stage='writer')}
    return instruction+json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def _reader_first_delivery_block(*,project_id,chapter_id,chapter_number,artifact_id,
                                 approved,receipt,binding,language):
    """Consume a source selected and admitted by the existing loader, never approve it.

    Mainline must persist receipt.reader_first_source with schema
    reader-first-writer-source.v1, the complete reviewed source, chapter_pointer,
    binding, exact project/chapter/artifact source_binding, raw review and its
    parsed reader_receipt. Source _meta must explicitly select v3 and exhaustively
    assign leaf pointers to roles BEFORE that complete source is reviewed. The
    planner's complete review wrapper holds the exact persisted artifact at
    source.outline; chapter_pointer is /outline/chapters/<actual array index>.
    No receipt fabrication, historical upgrade, source mutation or model call.
    """
    from bestseller.services.source_bound_planning_content import reader_first_binding
    binding=reader_first_binding({'reader_first_planning':binding})
    if (approved.get('chapter_number')!=chapter_number or not _receipt_contains_chapter(
            receipt,artifact_id=artifact_id,chapter_id=chapter_id,chapter_number=chapter_number)):
        raise ValueError('reader_first_delivery_scope_changed')
    roles,paths=_reader_first_field_roles(approved,binding)
    expected={'project_id':str(project_id),'chapter_id':str(chapter_id),
              'chapter_number':chapter_number,'artifact_id':str(artifact_id)}
    try:
        envelope=receipt['reader_first_source']
        if (envelope['schema']!='reader-first-writer-source.v1'
                or envelope['binding']!=binding or envelope['source_binding']!=expected):
            raise ValueError('reader_first_delivery_source_binding_changed')
        source=envelope['source']
        review=_verified_reader_first_review(source=source,pointer=envelope['chapter_pointer'],
            approved=approved,binding=binding,chapter_number=chapter_number,
            raw_review=envelope['review'],receipt=envelope['reader_receipt'])
    except (KeyError,TypeError,IndexError,AttributeError) as exc:
        raise ValueError('reader_first_delivery_approved_source_required') from exc
    approved_hash=content_hash(approved)
    metadata_paths=tuple(_source_metadata_paths(approved))
    payload={'schema':READER_FIRST_DELIVERY_SCHEMA,**expected,
        'approved_chapter_sha256':approved_hash,'readiness_receipt_sha256':content_hash(receipt),
        'reader_first_planning':deepcopy(binding),'source_binding':expected,
        'source_chapter_pointer':envelope['chapter_pointer'],
        'review_source_sha256':review['source_sha256'],
        'review_source':deepcopy(source),'raw_review':deepcopy(envelope['review']),
        'reader_receipt':deepcopy(review),'reader_receipt_sha256':content_hash(review),
        'authority':'Approved future task for this chapter, not already occurred history.',
        'approved_source':deepcopy(approved),
        'narrative_task':[path for path in paths if roles[path]=='narrative_task'],
        'continuity_reference':[path for path in paths if roles[path]=='continuity_reference']+list(metadata_paths),
        'field_sources':{path:{'approved_chapter_sha256':approved_hash,
                               'review_source_pointer':envelope['chapter_pointer']+path}
                         for path in (*paths,*metadata_paths)}}
    return PromptBlock(key='chapter_first.approved_current_delivery',channel='user',layer='scene_spec',
        authority=90,instruction_family='writer.current_chapter.delivery',required=True,trim_policy='preserve',
        source=READER_FIRST_DELIVERY_SOURCE,text=_reader_first_delivery_text(payload,language),
        provenance=PromptProvenance(kind='scene_spec',source_id=str(artifact_id),source_hash=approved_hash,
                                   field_paths=(*paths,*metadata_paths)))


def reader_first_writer_blocks(project,chapter,blocks,*,language):
    """Gate both writer prompt entry points before compilation or model work.

    Typed source blocks are produced by load_delivery_blocks; this check fences
    scope/hash drift between that verified read and either prompt consumer.
    """
    from bestseller.services.source_bound_planning_content import reader_first_binding
    selected=_REFERENCE_DELIVERY_SCHEMA.get()
    if (selected is not None and selected[0] is current_task()
            and selected[1] in {'approved-current-chapter-delivery.v1','approved-current-chapter-delivery.v2'}
            and selected[2]==project.id and selected[3]==chapter.id):
        return None
    binding=reader_first_binding(project)
    candidates=[b for b in blocks if b.source==READER_FIRST_DELIVERY_SOURCE]
    if binding is None:
        if candidates:
            raise ValueError('reader_first_delivery_project_binding_required')
        return None
    if len(candidates)!=1:
        raise ValueError('reader_first_delivery_v3_integration_required')
    block=candidates[0]
    try:
        payload=json.loads(block.text.split('\n',2)[2])
        approved=payload['approved_source']
        roles,paths=_reader_first_field_roles(approved,binding)
        metadata_paths=tuple(_source_metadata_paths(approved))
        expected={'project_id':str(project.id),'chapter_id':str(chapter.id),
                  'chapter_number':chapter.chapter_number,'artifact_id':payload['artifact_id']}
        review=_verified_reader_first_review(source=payload['review_source'],
            pointer=payload['source_chapter_pointer'],approved=approved,binding=binding,
            chapter_number=chapter.chapter_number,raw_review=payload['raw_review'],
            receipt=payload['reader_receipt'])
        if (payload['schema']!=READER_FIRST_DELIVERY_SCHEMA
                or payload['reader_first_planning']!=binding
                or approved['chapter_number']!=chapter.chapter_number
                or any(payload[key]!=value for key,value in expected.items())
                or payload['source_binding']!=expected
                or payload['approved_chapter_sha256']!=content_hash(approved)
                or payload['reader_receipt_sha256']!=content_hash(payload['reader_receipt'])
                or payload['reader_receipt']['status']!='passed'
                or payload['reader_receipt']['checkpoint']!='outline'
                or payload['reader_receipt']['config_sha256']!=binding['config_sha256']
                or payload['reader_receipt']['source_sha256']!=payload['review_source_sha256']
                or review['source_sha256']!=content_hash(payload['review_source'])
                or payload['narrative_task']!=[p for p in paths if roles[p]=='narrative_task']
                or payload['continuity_reference']!=[p for p in paths if roles[p]=='continuity_reference']+list(metadata_paths)
                or payload['field_sources']!={p:{'approved_chapter_sha256':content_hash(approved),
                    'review_source_pointer':payload['source_chapter_pointer']+p}
                    for p in (*paths,*metadata_paths)}
                or not block.required or block.trim_policy!='preserve'
                or block.provenance is None or block.provenance.source_id!=payload['artifact_id']
                or block.provenance.source_hash!=content_hash(approved)
                or block.provenance.field_paths!=(*paths,*metadata_paths)
                or block.text!=_reader_first_delivery_text(payload,language)):
            raise ValueError('reader_first_delivery_writer_binding_changed')
    except (KeyError,TypeError,IndexError,AttributeError) as exc:
        raise ValueError('reader_first_delivery_writer_binding_changed') from exc
    # The required v3 block already carries the instruction once. Return only
    # the validated marker for renderers; do not duplicate its source obligations.
    return binding


# Immutable historical rendering protocol; default new writing uses v2.
def _source_protocol_delivery_block_v1(*,project_id,chapter_id,chapter_number,artifact_id,approved,receipt):
    if approved.get('chapter_number')!=chapter_number or chapter_number not in receipt['chapter_numbers']:
        raise ValueError('chapter_writer_delivery_scope_changed')
    keys=('scene_number','participants','entry_state','exit_state','action_sequence',
        'forbidden_actions','hook_requirement','concrete_goal','information_introduced','information_held_back','target_word_count')
    scenes=[{key:deepcopy(row[key]) for key in keys if key in row and row[key] is not None}
        for row in approved['scenes']]
    payload={'schema':'approved-current-chapter-delivery.v1','project_id':str(project_id),
        'chapter_id':str(chapter_id),'chapter_number':chapter_number,'artifact_id':str(artifact_id),
        'approved_chapter_sha256':content_hash(approved),'readiness_receipt_sha256':content_hash(receipt),
        'authority':'Approved future task for this chapter, not already occurred history.', 'scenes':scenes}
    if approved.get('target_word_count') is not None:
        payload['chapter_target_word_count_reference']=deepcopy(approved['target_word_count'])
    instruction=('【本章已批准的完整推进任务·不可截断】\n'
        '上一章正文与尾段只用于核对既成事实和入场状态，不能复制为本章正文；不得把已发生的对话、发现或动作重新演一遍。'
        '本章新增事件及结果只从以下本章已批准任务展开；入场状态已成立，动作序列尚待执行，完整出场状态必须在本章落地。'
        '用新的现场叙述完成任务，不机械替换上一章词语；保留任务中的限定、否定、先后顺序与必要原句。'
        '这些计划不授权撤销历史，也不构成角色已经知道全部信息的证据；入场措辞与已核验前文冲突时，以前文为准，保留规划偏差。'
        '篇幅按本章已明确的总目标和范围执行；已有场景目标只作展开篇幅参考，不是新增逐场硬配额，也不要求平均。'
        '中文按汉字逐字计数，连续英文或数字词组按词计数，标点、空白与Markdown标记不计；不要把含标点总字符数当有效字数。'
        '用必要现场行动、选择与后果完成任务，未达已明确下限前不得提前收束成摘要；不复述、堆环境或把一个动作切成瞬间凑字。\n')
    return PromptBlock(key='chapter_first.approved_current_delivery',channel='user',layer='scene_spec',
        authority=90,instruction_family='writer.current_chapter.delivery',required=True,trim_policy='preserve',
        source='chapter_writer_delivery.v1',text=instruction+json.dumps(payload,ensure_ascii=False,separators=(',',':')),
        provenance=PromptProvenance(kind='scene_spec',source_id=str(artifact_id),source_hash=content_hash(approved),
            field_paths=tuple('/scenes/'+str(i)+'/'+key for i,row in enumerate(approved['scenes']) for key in keys if key in row)))



_REFERENCE_DELIVERY_SCHEMA = ContextVar('bestseller_reference_delivery_schema', default=None)


@asynccontextmanager
async def reference_source_delivery_scope(session,settings,*,project,proof):
    """Select a historical rendering only from its retained physical request.

    Scope selection asserts no grade. The unchanged source verifier must still
    match the entire current plan, original request, tools and effective hash.
    """
    from bestseller.infra.db.models import LlmRunModel
    from bestseller.services.retained_review_input_proof import _retained_request
    from bestseller.services.lossless_source_encoding import decode_lossless_source
    from bestseller.services.draft_continuity_adjudication import strict_json
    evidence=proof.get('receipt',{}).get('supplement_evidence',{})
    if proof.get('schema')!='draft-continuity-source-corrected-reference.v1' or not evidence.get('run_id'):
        raise ValueError('chapter_writer_delivery_retained_reference_protocol_missing')
    run=await session.get(LlmRunModel,UUID(str(evidence['run_id'])),populate_existing=True)
    if run is None or run.project_id!=project.id or str(run.workflow_run_id)!=str(evidence['workflow_id']):
        raise ValueError('chapter_writer_delivery_retained_reference_scope')
    request=_retained_request(settings,run)
    if request.prompt_template!='draft_continuity_hard_safety' or request.prompt_version!='4.0-completion-supplement':
        raise ValueError('chapter_writer_delivery_retained_reference_protocol')
    outer=strict_json(request.user_prompt)
    plan=decode_lossless_source(outer['value'])['approved_current_chapter_task']
    schema=plan.get('schema')
    if (schema not in {'approved-current-chapter-delivery.v1','approved-current-chapter-delivery.v2'} or content_hash(plan)!=proof['approved_plan_sha256']
            or plan.get('project_id')!=str(project.id) or plan.get('chapter_id')!=proof.get('chapter_id')):
        raise ValueError('chapter_writer_delivery_retained_plan_binding')
    token=_REFERENCE_DELIVERY_SCHEMA.set((current_task(),schema,project.id,UUID(proof['chapter_id'])))
    try:
        yield
    finally:
        _REFERENCE_DELIVERY_SCHEMA.reset(token)
