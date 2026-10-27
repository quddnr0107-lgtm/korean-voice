"""OFFLINE candidate: one lesson, bounded chunk-local inference; no IO or keys."""
import hashlib,json,copy
VERSION='admin-law-inference-policy/v1'
def encoded(x):return json.dumps(x,ensure_ascii=False,separators=(',',':')).encode()
def expected():return {'version':VERSION,'seed_version':'sha256-chunk-attempt-u32be/v1','max_per_chunk':4,'total_budget':52,'chunk_count':26,'base_seed':20271037}
def attach_policy(data):
 if 'inference_policy' in data['lineage'] or 'inference_policy_sha256' in data['lineage']:raise ValueError('sealed inference policy forbidden')
 out=copy.deepcopy(data);out['lineage']['inference_policy']=expected();out['lineage']['inference_policy_sha256']=hashlib.sha256(encoded(expected())).hexdigest();validate(out);return out

def validate(data):
 from retry_policy import validate_generation
 from chunk_policy import planned
 g=data['lineage'].get('generation')
 if data['namespace']!='admin-law/v3/source' or data['track']!='source' or data['lesson']['id']!='AL-012' or not g or g.get('attempt')!=4:raise ValueError('inference scope')
 validate_generation(g,'source','AL-012')
 p=data['lineage'].get('inference_policy');pin=data['lineage'].get('inference_policy_sha256')
 if type(p)!=dict or encoded(p)!=encoded(expected()) or pin!=hashlib.sha256(encoded(p)).hexdigest():raise ValueError('inference policy mismatch')
 if len(planned(data))!=26 or g['seed']!=p['base_seed']:raise ValueError('inference plan/seed')
 return p

def seed_for(data,index,text,attempt):
 # Caller validates plan/policy once before creating state; verifier checks again.
 if type(index)!=int or not 0<=index<26 or type(attempt)!=int or not 1<=attempt<=4:raise ValueError('seed coordinates')
 context=[VERSION,data['lineage']['inference_policy']['seed_version'],data['namespace'],data['lesson']['id'],20271037,data['lineage']['chunk_policy_sha256'],index,hashlib.sha256(text.encode()).hexdigest(),attempt]
 return int.from_bytes(hashlib.sha256(encoded(context)).digest()[:4],'big')

class Budget:
 def __init__(self,data):
  self.policy=validate(data);self.data=copy.deepcopy(data);self.used=0;self.next_index=0;self.failed=False
 def infer(self,index,text,callback,set_seed):
  from chunk_policy import planned
  if self.failed:raise ValueError('inference already failed')
  if index!=self.next_index or text!=planned(self.data)[index]:raise ValueError('inference sequence')
  last=None
  for attempt in range(1,5):
   unvisited=26-index-1
   if 52-self.used<=unvisited:
    self.failed=True;raise ValueError('inference budget exhausted')
   self.used+=1 # Exceptions consume budget; caller never silently retries them.
   seed=seed_for(self.data,index,text,attempt)
   try:
    set_seed(seed);y,hum=callback()
   except Exception:
    self.failed=True;raise
   last=(y,hum,attempt,seed)
   if not hum:self.next_index+=1;return last
  self.failed=True
  return last # Existing Backend full QC rejects persistent hum; no extra call.

def validate_segments(data,segments):
 has='inference_policy' in data['lineage'] or 'inference_policy_sha256' in data['lineage']
 attempt=data['lineage'].get('generation',{}).get('attempt')
 if not has and attempt!=4:
  if any('accepted_seed' in s for s in segments):raise ValueError('unexpected accepted seed')
  return
 validate(data)
 from chunk_policy import planned
 plan=planned(data)
 if len(segments)!=26 or [s['text'] for s in segments]!=plan:raise ValueError('inference segment plan')
 total=0
 for i,s in enumerate(segments):
  a=s.get('attempts');seed=s.get('accepted_seed')
  if type(a)!=int or not 1<=a<=4 or type(seed)!=int or seed!=seed_for(data,i,plan[i],a):raise ValueError('inference provenance')
  total+=a
 if total>52:raise ValueError('total inference budget')
