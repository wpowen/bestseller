"""Documented model context ceilings; independent from money/call authorization.

MiniMax official model overview and SDK reference, checked 2026-10-21:
https://platform.minimax.cn/docs/guides/models-intro
https://platform.minimax.cn/docs/api-reference/text-anthropic-api
M3.1 Flash: 1,000,000 context tokens. Unknown models/routes retain 65,536.
This never changes an existing finite-run binding, output cap or dispatch grant.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from hashlib import sha256

DEFAULT_CONTEXT=65536
M31_CONTEXT=1_000_000
SOURCES=('https://platform.minimax.cn/docs/guides/models-intro',
         'https://platform.minimax.cn/docs/api-reference/text-anthropic-api')
_OFFICIAL_BASES=tuple('https://'+host+path for host in ('api.minimaxi.com','api.minimax.cn','api.minimax.io')
                      for path in ('/v1','/v1/'))
_ROUTE_HASHES={sha256(x.encode()).hexdigest() for x in _OFFICIAL_BASES}
_CURRENT=ContextVar('documented_model_context_ceiling',default=None)

def documented_capacity(model,*,endpoint=None,endpoint_hash=None):
    supported=model in ('MiniMax-M3.1-Flash','openai/MiniMax-M3.1-Flash')
    route=endpoint in _OFFICIAL_BASES if endpoint is not None else endpoint_hash in _ROUTE_HASHES
    return M31_CONTEXT if supported and route else DEFAULT_CONTEXT

def payload_capacity(payload):
    return documented_capacity(payload.get('model'),endpoint_hash=payload.get('endpoint_hash'))

def selected_context():return _CURRENT.get()

@contextmanager
def bind_model_capacity(model,endpoint):
    token=_CURRENT.set(documented_capacity(model,endpoint=endpoint))
    try:yield
    finally:_CURRENT.reset(token)

@contextmanager
def bind_selected_model_capacity(metadata):
    from bestseller.services.model_catalog import resolve_project_model_entry
    entry=resolve_project_model_entry(metadata)
    with bind_model_capacity(entry.model if entry else None,entry.api_base if entry else None):
        yield

def planning_model_capacity(function):
    @wraps(function)
    async def wrapped(session,settings,**kwargs):
        project=kwargs['project']
        with bind_selected_model_capacity(project.metadata_json or {}):
            return await function(session,settings,**kwargs)
    return wrapped
