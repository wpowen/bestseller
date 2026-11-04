"""Isolated experiment transport; shared cost ceiling includes missing usage."""
from pathlib import Path
import threading
from bookengine.client import Client,MODEL
from bookengine.storage import load,save

PRICES={MODEL:(.15,.60),'MiniMax-M3':(.30,1.20)}
LOCK=threading.RLock();INFLIGHT={}

def totals(root,exclude_inflight=False):
    cost=tokens=unknown=calls=failures=0
    for path in Path(root).rglob('calls.json'):
        for c in load(path):
            active=INFLIGHT.get(str(path.parent))
            if exclude_inflight and active and c['seq']==active['seq']:continue
            calls+=1;u=c.get('usage') or {};p=PRICES.get(c.get('requested_model',c.get('model')),PRICES['MiniMax-M3'])
            tokens+=u.get('total_tokens',0);failures+=c.get('status') not in ('ok','pending')
            if all(type(u.get(k)) is int for k in ('prompt_tokens','completion_tokens')):cost+=(u['prompt_tokens']*p[0]+u['completion_tokens']*p[1])/1e6
            else:unknown+=1;cost+=(c.get('input_bytes',0)*p[0]+c.get('output_token_cap',0)*p[1])/1e6
    return dict(estimated_usd=round(cost,9),calls=calls,tokens=tokens,unknown_usage=unknown,failures=failures)

class ExperimentClient(Client):
    def __init__(self,out,env,root,plan_model=MODEL,fantasy=False):
        super().__init__(out,env,'',180);self.root,self.plan_model,self.fantasy=root,plan_model,fantasy
        self.relaxed=True;self.context_limit=10000;self.clip_context=True;self.style_b=False;self.minimal_prose=True

    def stage(self,name,prompt,*args,**kwargs):
        if self.fantasy and name.endswith('-draft'):
            prompt=prompt.replace('本章正文2000—2400汉字','本章正文1900—2300汉字').replace('各约1012—1100字','各约950—1150字')
        return super().stage(name,prompt,*args,**kwargs)

    def request(self,stage,prompt,cap,effort,attempt):
        model=self.plan_model if stage.startswith('plan-') else MODEL;p=PRICES[model]
        upper=(len(prompt.encode())*p[0]+cap*p[1])/1e6;key=str(self.out)
        with LOCK:
            if totals(self.root,True)['estimated_usd']+sum(x['upper'] for x in INFLIGHT.values())+upper>.5:raise RuntimeError('blind-2 shared $0.50 budget reached')
            INFLIGHT[key]=dict(upper=upper,seq=len(self.calls)+1)
        self.model=model;self.thinking={'type':'disabled'} if model=='MiniMax-M3' else None
        try:return super().request(stage,prompt,cap,effort,attempt)
        finally:
            with LOCK:
                if self.calls:self.calls[-1]['requested_model']=model;save(self.history_path,self.calls)
                INFLIGHT.pop(key,None)
            self.model=MODEL;self.thinking=None
