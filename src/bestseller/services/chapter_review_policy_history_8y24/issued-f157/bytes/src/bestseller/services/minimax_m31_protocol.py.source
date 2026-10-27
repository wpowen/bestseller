"""Exact M3.1 Flash wire semantics, without selecting routes or credentials.

Official OpenAI protocol: reasoning is mandatory, output uses
max_completion_tokens, and full assistant reasoning/tool messages are retained.
Legacy/preview models do not enter this adapter.
"""
from copy import deepcopy

MODEL='minimax-m3.1-flash'
EFFORTS={'low','medium','high','xhigh','max'}


def applies(model):
 return str(model or '').split('/')[-1].casefold()==MODEL


def reasoning_effort(role, *, max_completion_tokens=None):
 if role.thinking_type not in (None,'adaptive','enabled'):
  raise ValueError('minimax_m31_thinking_cannot_be_disabled')
 # Mandatory reasoning shares the answer budget. A caller narrowing its
 # role's budget needs less default thinking, including medium-sized caps.
 # Explicit effort choices always win; no completion cap is increased.
 narrowed=max_completion_tokens is not None and max_completion_tokens<getattr(role,'max_tokens',max_completion_tokens)
 default='low' if max_completion_tokens is not None and (max_completion_tokens<=4096 or narrowed) else 'max'
 effort=role.reasoning_effort or default
 if effort not in EFFORTS:raise ValueError('minimax_m31_reasoning_effort_invalid')
 return effort


def wire_options(kwargs,role):
 """Return copied SDK kwargs; never redirect or import another role's auth."""
 reasoning=reasoning_effort(role,max_completion_tokens=kwargs.get('max_tokens'));out=deepcopy(kwargs)
 if 'max_completion_tokens' in out:raise ValueError('minimax_m31_duplicate_output_cap')
 out['max_completion_tokens']=out.pop('max_tokens')
 # The model thinks on demand. No old thinking=disabled parameter is valid.
 out.pop('extra_body',None)
 out['reasoning_effort']=reasoning
 # Current LiteLLM does not yet recognize this new formal model's effort field.
 # Permit only documented parameters; never enable drop_params globally.
 out['allowed_openai_params']=['reasoning_effort','max_completion_tokens']
 # Tool deltas stay on the complete-message path. Plain-text streaming uses
 # the dedicated accumulator that preserves reasoning separately from prose.
 out['stream']=bool(out.get('stream')) and not bool(out.get('tools'))
 if out['stream']:out['stream_options']={'include_usage':True}
 out.pop('n',None)
 return out


def assistant_message(message,*,content,tool_calls):
 """Keep complete SDK JSON, reject conflicting/legacy tool protocol fields."""
 if isinstance(message,dict):raw=deepcopy(message)
 else:
  dump=getattr(message,'model_dump',None)
  if not callable(dump):raise ValueError('minimax_m31_message_not_serializable')
  raw=dump(mode='json',exclude_none=False)
 if not isinstance(raw,dict) or raw.get('role')!='assistant':
  raise ValueError('minimax_m31_assistant_role_invalid')
 if raw.get('function_call') is not None:raise ValueError('minimax_m31_legacy_function_call')
 if raw.get('tool_calls') is not None:
  canonical=[{'id':c.get('id',''),'type':c.get('type','function'),'function':{'name':c.get('function',{}).get('name'),'arguments':c.get('function',{}).get('arguments')}} for c in raw['tool_calls']]
  if canonical!=tool_calls:raise ValueError('minimax_m31_native_tool_conflict')
 for call in raw.get('tool_calls') or []:
  if not isinstance(call.get('function',{}).get('arguments'),str):
   raise ValueError('minimax_m31_arguments_not_json_string')
 if raw.get('reasoning_content') is not None and not isinstance(raw['reasoning_content'],str):
  raise ValueError('minimax_m31_reasoning_not_text')
 # Never rewrite the raw content/reasoning or normalize it into an invented return.
 return raw
