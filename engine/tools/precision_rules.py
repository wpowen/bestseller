"""Compatibility import for the offline sentence experiments."""
from bookengine.precision import locate, apply
from bookengine.q1_quality import metrics

def stats(text):
    hits=locate(text);measured=metrics(text)
    return dict(hits=len(hits),categories={c:sum(c in h['categories'] for h in hits) for c in ('summary','epiphany','poetic')},
                **{k:measured[k] for k in ('chars','single_sentence_ratio','dialogue_ratio')})
