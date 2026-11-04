"""Flash/M3 experiment calls, with shared .8 and per-track .2/.6 bounds."""
from bookengine.client import Client
from bookengine.storage import save
from dialogue_client import LOCK,INFLIGHT,PRICES,totals

class VoiceClient(Client):
    def __init__(self,out,env,root,track):
        super().__init__(out,env,'',180);self.root,self.track=root,track
        self.relaxed=True;self.context_limit=10000;self.clip_context=True

    def request(self,stage,prompt,cap,effort,attempt):
        model=self.model;p=PRICES[model];key=str(self.out);trackroot=self.root/self.track
        upper=((len(prompt.encode())+2048)*p[0]+cap*p[1])/1e6
        with LOCK:
            reserve=sum(x['upper'] for x in INFLIGHT.values())
            local=sum(x['upper'] for k,x in INFLIGHT.items() if k.startswith(str(trackroot)))
            if totals(self.root,True)['estimated_usd']+reserve+upper>.8 or totals(trackroot,True)['estimated_usd']+local+upper>({'t15':.2,'t16':.6}[self.track]):raise RuntimeError('blind-3 shared/track budget reached')
            INFLIGHT[key]=dict(upper=upper,seq=len(self.calls)+1)
        self.thinking={'type':'disabled'} if model=='MiniMax-M3' else None
        try:return super().request(stage,prompt,cap,effort,attempt)
        finally:
            with LOCK:
                if self.calls:self.calls[-1]['requested_model']=model;save(self.history_path,self.calls)
                INFLIGHT.pop(key,None)
