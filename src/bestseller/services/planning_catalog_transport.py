"""Lossless front coding of redundant authority JSON-pointer lists only.

Every source field and classification remains unchanged. Prefix lengths count
Unicode code points in the previous pointer in the SAME ordered list. Legacy
source codecs remain untouched; the complete original logical hash is retained.
"""
from copy import deepcopy
import hashlib
import json
from bestseller.services.lossless_source_encoding import encode_lossless_source,decode_lossless_source

POLICY='planning-catalog-transport.v1'
INDEXED_POLICY='planning-catalog-transport.v2'
INSTRUCTION=('source_review_data uses planning-catalog-transport.v1. First expand value using the declared lossless-source key aliases and indexed references. '
 'Only input.source_authority is front-coded: in each roles/root ordered list, [n,suffix] means previousPointer[:n]+suffix; previousPointer starts empty for each list. '
 'Restore every original path, order and classification. All source text and logical hashes remain original; encoding grants no authority.\n')
INDEXED_INSTRUCTION=('source_review_data uses planning-catalog-transport.v2. Expand value using declared lossless-source key aliases and indexed references first. '
 'Only input.source_authority lists are segment-front-coded: [n,ids] means previousSegments[:n] plus segments[each id]; previousSegments starts empty in each roles/root list. '
 'Join restored segments with / to recover each exact original pointer; preserve all list order/classification. All source text and logical hashes remain original; encoding grants no authority.\n')



def need(ok,reason):
 if not ok:raise ValueError('planning_catalog_transport_'+reason)
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def applicable(data):return isinstance(data,dict) and isinstance(data.get('input'),dict) and isinstance(data['input'].get('source_authority'),dict)


def encode(data,*,version=1):
 need(applicable(data) and version in (1,2),'scope');original=deepcopy(data);packed=deepcopy(data);catalog={}
 segments=sorted({segment for groups in original['input']['source_authority'].values() for paths in groups.values() for path in paths for segment in path.split('/')}) if version==2 else []
 indices={segment:i for i,segment in enumerate(segments)}
 for role,groups in original['input']['source_authority'].items():
  need(isinstance(role,str) and isinstance(groups,dict),'catalog_role');out={}
  for root,paths in groups.items():
   need(isinstance(root,str) and isinstance(paths,list),'catalog_root');previous='' if version==1 else [];rows=[]
   for path in paths:
    need(isinstance(path,str),'path_type');sequence=path if version==1 else path.split('/');prefix=0
    while prefix<min(len(previous),len(sequence)) and previous[prefix]==sequence[prefix]:prefix+=1
    rows.append([prefix,sequence[prefix:] if version==1 else [indices[x] for x in sequence[prefix:]]]);previous=sequence
   out[root]=rows
  catalog[role]=out
 packed['input']['source_authority']=catalog
 wire={'schema':POLICY if version==1 else INDEXED_POLICY,'original_sha256':digest(original),'value':encode_lossless_source(packed,version=3)}
 if version==2:wire['segments']=segments
 need(canonical(decode(wire))==canonical(original),'roundtrip');return wire


def decode(wire):
 need(isinstance(wire,dict),'wire_shape');version=1 if wire.get('schema')==POLICY else 2 if wire.get('schema')==INDEXED_POLICY else 0
 need(version and set(wire)==({'schema','original_sha256','value'} if version==1 else {'schema','original_sha256','value','segments'}),'wire_shape')
 segments=wire.get('segments',[])
 need(isinstance(segments,list) and all(isinstance(x,str) for x in segments) and len(set(segments))==len(segments),'segments')
 original=decode_lossless_source(wire['value']);need(applicable(original),'decoded_scope');catalog={}
 for role,groups in original['input']['source_authority'].items():
  need(isinstance(role,str) and isinstance(groups,dict),'catalog_role');out={}
  for root,rows in groups.items():
   need(isinstance(root,str) and isinstance(rows,list),'catalog_root');previous='' if version==1 else [];paths=[]
   for row in rows:
    need(isinstance(row,list) and len(row)==2 and type(row[0]) is int and 0<=row[0]<=len(previous),'path_row')
    if version==1:need(isinstance(row[1],str),'path_suffix');suffix=row[1]
    else:
     need(isinstance(row[1],list) and all(type(i) is int and 0<=i<len(segments) for i in row[1]),'segment_indices');suffix=[segments[i] for i in row[1]]
    previous=previous[:row[0]]+suffix;paths.append(previous if version==1 else '/'.join(previous))
   out[root]=paths
  catalog[role]=out
 original['input']['source_authority']=catalog
 need(digest(original)==wire['original_sha256'],'original_hash');return original
