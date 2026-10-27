"""Size full native Cast deliveries without changing story or approval policy.

MiniMax M3.1 Flash output includes mandatory thinking. Official reference:
https://platform.minimax.io/docs/api-reference/text-openai-api
https://platform.minimax.io/docs/api-reference/text-chat-anthropic
The documented model output ceiling is 524,288; input plus reserved output
still passes the separate actual model/context capacity gate before dispatch.
"""
from copy import deepcopy
from hashlib import sha256
import json
from math import ceil

POLICY='source-bound-cast-output-allocation.v2'
M31_OUTPUT_CEILING=524288
SOURCE_VERSIONS={'source-bound-planning-content.v2','v3-source-bound-kind-submit'}

def allocation(*,kind,project,projection,prompt_version):
    if kind!='cast_spec' or prompt_version not in SOURCE_VERSIONS or project is None:return None
    from bestseller.services.model_catalog import resolve_project_model_entry
    from bestseller.services.model_context_capacity import documented_capacity,M31_CONTEXT
    entry=resolve_project_model_entry(project.metadata_json or {})
    if entry is None or documented_capacity(entry.model,endpoint=entry.api_base)!=M31_CONTEXT:return None
    clean={k:deepcopy(v) for k,v in projection.items() if k!='_meta'}
    text=json.dumps(clean,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    people=[clean.get('protagonist'),clean.get('antagonist'),*(clean.get('supporting_cast') or [])]
    names={p['name'].strip() for p in people if isinstance(p,dict) and isinstance(p.get('name'),str) and p['name'].strip()}
    cjk=sum('\u3400'<=c<='\u9fff' for c in text)
    source_floor=ceil(cjk*2+(len(text)-cjk)/2)
    # Per-person typed life/voice/psychology/relationship structures, source
    # preservation and thinking headroom. This sizes a request, not an invoice
    # or a new dispatch grant. No field, roster entry or source is shortened.
    needed=max(32768,16384+6144*max(1,len(names))+2*source_floor)
    cap=ceil(needed/4096)*4096
    if cap>M31_OUTPUT_CEILING:raise ValueError('source_cast_delivery_requires_partitioned_output')
    return {'schema':POLICY,'kind':kind,'roster_count':len(names),
        'projection_sha256':sha256(text.encode()).hexdigest(),'source_token_floor':source_floor,
        'initial_output_tokens':cap,'model_output_ceiling':M31_OUTPUT_CEILING,
        'source_truncated':False,'roster_reduced':False,'quality_approval_granted':False}

def prompt_suffix(plan):
    public={k:plan[k] for k in ('schema','kind','roster_count','projection_sha256')}
    return ('\n\n[SOURCE-BOUND CAST DELIVERY]\n'+json.dumps(public,sort_keys=True,separators=(',',':'))+
        '\nSubmit the complete typed Cast asset and every source-required person and field. '
        'Use concise field values without dropping evidence, relationships or people. '
        'Every person, including the protagonist, must include their source-supported name. '
        'Follow each exact native schema type: a string-or-null property must not be an array; '
        'an unknown array is [] rather than an empty string, and an unknown nullable scalar is null. '
        'keep child properties inside their declared parent, never spill them into the asset root. '
        'These delivery diagnostics grant no author facts, canonical selection or quality approval.\n')

def next_after_length(current,plan):
    following=min(int(plan['model_output_ceiling']),int(current)*2)
    if following<=int(current):raise ValueError('source_cast_output_capacity_exhausted')
    return following
