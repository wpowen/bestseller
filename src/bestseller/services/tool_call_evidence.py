"""Compare exact tool identities/arguments while validating SDK transport indices.

Original wire dictionaries remain unchanged. Only the optional raw list-position
index is transport metadata; all function argument bytes and other fields match.
"""

def same_tool_call_evidence(raw_calls, normalized_calls):
    if raw_calls is None:raw_calls=[]
    if normalized_calls is None:normalized_calls=[]
    if not isinstance(raw_calls,list) or not isinstance(normalized_calls,list):return False
    if len(raw_calls)!=len(normalized_calls):return False
    for position,(raw,normalized) in enumerate(zip(raw_calls,normalized_calls)):
        if not isinstance(raw,dict) or not isinstance(normalized,dict):return False
        if 'index' in raw:
            if type(raw['index']) is not int or raw['index']!=position:return False
            raw={key:value for key,value in raw.items() if key!='index'}
        if raw!=normalized:return False
    return True
