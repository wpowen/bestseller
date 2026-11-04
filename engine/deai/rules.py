"""Pure experimental rule detector shared by calibration; no model calls."""
from pathlib import Path
import re
import statistics
import yaml

from .text import sentences,paragraphs,count
DATA=Path(__file__).resolve().parent/'data'
def _sentences(text):return [(r['start'],r['end']) for r in sentences(text)]
def _paragraph_spans(text):return [(p['start'],p['end']) for p in paragraphs(text)]
chinese_count=count
def metrics(text):
    ps=paragraphs(text);n=count(text);quoted=re.findall(r'“([^”]*)”|「([^」]*)」|"([^"\n]*)"',text)
    return dict(chars=n,paragraphs=len(ps),dialogue_ratio=round(sum(count(''.join(x)) for x in quoted)/max(1,n),4),average_paragraph_chars=round(n/max(1,len(ps)),2),single_sentence_ratio=round(sum(len(sentences(p['text']))==1 for p in ps)/max(1,len(ps)),4))

DEFAULT=yaml.safe_load((DATA/'deai_rules.yaml').read_text())
FACT_TYPES={'continuity_conflict','date_arithmetic','era_conflict'}

def sentence_rows(text):
    paragraphs=_paragraph_spans(text)
    return [dict(sid=f'S{i}',pid=f'P{next(j for j,(x,y) in enumerate(paragraphs,1) if x<=a<y)}',start=a,end=b,text=text[a:b]) for i,(a,b) in enumerate(_sentences(text),1)]

def _matches(text,item):
    terms=set()
    for key in ('terms','markers','cliche_terms'):
        terms.update(x for x in item.get(key,[]) if isinstance(x,str) and x)
    spans=[m.span() for term in terms for m in re.finditer(re.escape(term),text)]
    if item.get('regex'):spans.extend(m.span() for m in re.finditer(item['regex'],text))
    merged=[]
    for a,b in sorted(set(spans)):
        if merged and a<merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    return merged

def features(text,rows=None,rules=None):
    rules=DEFAULT if rules is None else rules;rows=sentence_rows(text) if rows is None else rows
    result=metrics(text);n=chinese_count(text);lengths=[chinese_count(r['text']) for r in rows];ps=[chinese_count(text[a:b]) for a,b in _paragraph_spans(text)]
    result.update(sentence_length_variance=round(statistics.pvariance(lengths),3) if lengths else 0,paragraph_length_quantiles={str(q):sorted(ps)[round((len(ps)-1)*q)] if ps else None for q in (.1,.25,.5,.75,.9)},word_band_pass=1870<=n<=2700,dialogue_ratio_note='quoted Han proxy, not confirmed speaker attribution')
    for name,item in rules.get('lexicons',{}).items():
        if not isinstance(item,dict):continue
        item=dict(item)
        if name=='cliche':item['cliche_terms']=list(item.get('cliche_terms',[]))+rules.get('lexicons',{}).get('simile',{}).get('cliche_terms',[])
        count=len(_matches(text,item));result[name+'_count']=count;result[name+'_per_1000']=round(1000*count/n,4) if n else None
    return result

def merge_hits(rule_hits,model_hits):
    result={}
    for hit in list(rule_hits)+list(model_hits):
        if hit.get('type') in FACT_TYPES:continue
        key=(hit.get('sid'),hit.get('type'))
        if key not in result:result[key]=dict(hit)
        else:
            result[key]['severity']=max(result[key].get('severity',1),hit.get('severity',1))
            if hit.get('source')=='rule':result[key].update(source='rule',rule=hit.get('rule'))
    return list(result.values())

def carry_hits(previous,current,text,before_text=None):
    rows={(r['pid'],r['text']):r for r in sentence_rows(text)}
    old={(r['pid'],r['text']) for r in sentence_rows(before_text)} if before_text is not None else set()
    observed=[dict(h,**rows[(h['pid'],h['text'])]) for h in current if (h['pid'],h['text']) in rows and (h['pid'],h['text']) not in old]
    unchanged=[dict(h,**rows[(h['pid'],h['text'])]) for h in previous if (h['pid'],h['text']) in rows]
    return merge_hits(observed,unchanged)

def detect(text,rows=None,rules=None):
    using_default=rules is None;rules=DEFAULT if rules is None else rules;rows=sentence_rows(text) if rows is None else rows
    surface=yaml.safe_load((DATA/'sentence_patterns.yaml').read_text());patterns=[]
    for kind,regexes in surface.items():
        if kind.startswith('deai_'):
            patterns.extend((kind[5:],kind,p) for p in regexes)
        elif kind in ('summary','epiphany','poetic'):
            patterns.extend(('paragraph_moral' if kind!='poetic' else 'cliche_simile','legacy_'+kind,p) for p in regexes)
    for name,item in rules.get('lexicons',{}).items():
        if not isinstance(item,dict):continue
        for term in item.get('cliche_terms',[]):
            if not using_default or not re.match(r'^(?:像|仿佛|好似|宛如|犹如)',term):patterns.append((item.get('type','cliche_simile'),name,re.escape(term)))
    patterns.extend((p['type'],p['id'],p['regex']) for p in rules.get('patterns',[]) if p.get('certain') and p.get('type') not in FACT_TYPES)
    for name in ('ominous',):
        item=rules.get('lexicons',{}).get(name,{})
        patterns.extend((item.get('type','ominous_rendering'),name,re.escape(t)) for t in item.get('terms',[]))
    result=[]
    for row in rows:
        for kind,rule,pattern in patterns:
            if re.search(pattern,row['text']):
                if kind=='negation_turn' and not any(term in row['text'] for term in rules.get('policy',{}).get('abstract_negation_terms',[])):continue
                result.append(dict(row,type=kind,rule=rule,severity=2,source='rule'))
    return merge_hits(result,[])
