"""OFFLINE PROTOTYPE. Generic authenticated-input chunk boundary policy only.
No manuscript, private hash, or per-lesson boundary list belongs in this code.
"""
import hashlib,json
from chunk_plan import chunks,verify
VERSION='admin-law-chunk-policy/v1'
KEYS={'version','narration_sha256','baseline_plan_sha256','replace_indices','replacement_offsets','prior_queue_sha256','prior_record_sha256','prior_cipher_sha256'}
PRIOR_PINS={'AL-012': {'prior_queue_sha256': '05c4ac85b25e3f6dcb4451e6b16f352f50b16269e84d1b7ab1687e637b16daa7', 'prior_record_sha256': 'a47ed9be7630afc8587f9e7f06807723b2135bb7da8a4b7066bbf92d87c79aba', 'prior_cipher_sha256': '9a9bc20d048711ce92b09c45888f469a866003be8e2412cefded1990ffcde2a6'}, 'AL-040': {'prior_queue_sha256': '05c4ac85b25e3f6dcb4451e6b16f352f50b16269e84d1b7ab1687e637b16daa7', 'prior_record_sha256': 'fcc11f8b692df4ed7c28906bda073cd29add24c0dac0620961bc517b5ab7b5a2', 'prior_cipher_sha256': '3b8614773857eadc519af2f9b018f9de01e4bfa117564fb17869e8c9cf3f939c'}}
def encoded(v):return json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()
def digest(v):return hashlib.sha256(v if isinstance(v,bytes) else encoded(v)).hexdigest()
def offsets(text,parts):
 verify(text,parts);out=[];cursor=0
 for part in parts:
  while cursor<len(text) and text[cursor].isspace():cursor+=1
  out.append([cursor,cursor+len(part)]);cursor+=len(part)
 return out

def planned(data):
 from private_pipeline import inverse
 l=data['lesson']
 if 'chunk_policy' in l:raise ValueError('policy must not alter lesson')
 text=l['narration_text'];policy=data['lineage'].get('chunk_policy');pin=data['lineage'].get('chunk_policy_sha256')
 if policy is None:
  if pin is not None:raise ValueError('policy pin without policy')
  return chunks(text)
 if data['track']!='source' or l['id'] not in PRIOR_PINS:raise ValueError('policy identity')
 if type(policy)!=dict or set(policy)!=KEYS or policy['version']!=VERSION:raise ValueError('policy schema')
 if any(policy[k]!=v for k,v in PRIOR_PINS[l['id']].items()):raise ValueError('prior sealed input pins')
 if type(pin)!=str or digest(policy)!=pin:raise ValueError('policy digest')
 if policy['narration_sha256']!=hashlib.sha256(text.encode()).hexdigest():raise ValueError('policy narration')
 baseline=chunks(text);original=offsets(text,baseline)
 if policy['baseline_plan_sha256']!=digest(original):raise ValueError('baseline plan')
 ij=policy['replace_indices']
 if type(ij)!=list or len(ij)!=2 or any(type(x)!=int for x in ij):raise ValueError('replacement indices')
 first,last=ij
 if not 0<=first<=last<len(original):raise ValueError('replacement range')
 repl=policy['replacement_offsets']
 if type(repl)!=list or not 0<len(repl)<150:raise ValueError('replacement count')
 bounds=inverse(l);cursor=original[first][0];end=original[last][1];pieces=[]
 for pair in repl:
  if type(pair)!=list or len(pair)!=2 or any(type(x)!=int for x in pair):raise ValueError('offset types')
  a,b=pair
  if not cursor<=a<b<=end or b-a>220 or text[cursor:a].strip():raise ValueError('offset coverage')
  if a not in bounds or b not in bounds:raise ValueError('normalized token interior')
  # Never cut an original lexical token in two either. Gaps are only whitespace.
  if (a>0 and not text[a-1].isspace()) or (b<len(text) and not text[b].isspace()):raise ValueError('lexical boundary')
  if text[a].isspace() or text[b-1].isspace():raise ValueError('untrimmed chunk')
  pieces.append(text[a:b]);cursor=b
 if repl[0][0]!=original[first][0] or cursor!=end:raise ValueError('region endpoints')
 result=baseline[:first]+pieces+baseline[last+1:];verify(text,result)
 if result==baseline:raise ValueError('unchanged policy')
 return result

def for_attempt(data,attempt):
 if 'chunk_policy' in data['lesson']:raise ValueError('policy must not alter lesson')
 has='chunk_policy' in data['lineage'] or 'chunk_policy_sha256' in data['lineage']
 if attempt==3:
  if not has or 'chunk_policy' not in data['lineage'] or 'chunk_policy_sha256' not in data['lineage']:raise ValueError('boundary policy required')
  return planned(data)
 if has:raise ValueError('custom chunks only attempt3')
 return None

def validate_segments(data,segments):
 if data['lineage'].get('generation',{}).get('attempt')==3:for_attempt(data,3)
 if 'chunk_policy' in data['lineage'] or 'chunk_policy_sha256' in data['lineage']:
  from retry_policy import validate_generation
  g=data['lineage'].get('generation')
  if not g or g.get('attempt')!=3:raise ValueError('policy metadata generation')
  validate_generation(g,data['track'],data['lesson']['id'])
  if [s['text'] for s in segments]!=planned(data):raise ValueError('actual segments policy mismatch')
