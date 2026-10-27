"""Frozen source failure-only attempt2 policy; first run terminal evidence verified."""
import copy,re
FROZEN=True
PRIOR_RUN_ID=40433228248
PRIOR_COMMIT='b07dc282c67796a7b26cc3aa3ec325c82a3c523b'
FAILED_JOBS={'AL-012': 121955760864, 'AL-016': 121955760838, 'AL-026': 121955761053, 'AL-035': 121955760944, 'AL-037': 121955761005, 'AL-040': 121955762753, 'AL-043': 121955763007, 'AL-052': 121955764699, 'AL-067': 121955767974, 'AL-071': 121955768607, 'AL-077': 121955769635, 'AL-078': 121955769691}
GEN_KEYS={'attempt','seed','prior_run_id','prior_commit','prior_job_id'}
def generation(track,ident,attempt):
 if type(attempt)!=int or attempt not in (1,2):raise ValueError('generation attempt')
 if track not in ('source','easy') or not re.fullmatch(r'AL-0(?:1[1-9]|[2-6][0-9]|7[0-8])',ident):raise ValueError('generation identity')
 if attempt==1:return None
 if not FROZEN or track!='source' or ident not in FAILED_JOBS:raise ValueError('retry scope not frozen/allowed')
 return {'attempt':2,'seed':20261026+int(ident[-3:])-1+10000,'prior_run_id':PRIOR_RUN_ID,'prior_commit':PRIOR_COMMIT,'prior_job_id':FAILED_JOBS[ident]}
def validate_generation(value,track,ident):
 if type(value)!=dict or set(value)!=GEN_KEYS:raise ValueError('generation schema')
 if any(type(value[k])!=int for k in ('attempt','seed','prior_run_id','prior_job_id')) or type(value['prior_commit'])!=str:raise ValueError('generation types')
 if value!=generation(track,ident,2):raise ValueError('generation mismatch')
 return value

def attach(data,attempt):
 if type(data.get('lineage'))!=dict or 'generation' in data['lineage']:raise ValueError('sealed generation forbidden')
 ident=data['lesson']['id'];g=generation(data['track'],ident,attempt)
 out=copy.deepcopy(data)
 if g is not None:out['lineage']['generation']=g
 return out,20261026+int(ident[-3:])-1 if g is None else g['seed']

def check_descriptor(d,track,ident,expected=None,enforce_expected=False):
 if type(d)!=dict or set(d) not in ({'id','audio','content'},{'id','audio','content','generation'}) or d['id']!=ident:raise ValueError('descriptor schema')
 if 'generation' in d:validate_generation(d['generation'],track,ident)
 if enforce_expected and (('generation' in d)!=(expected is not None) or d.get('generation')!=expected):raise ValueError('descriptor generation expected')
 return d.get('generation')

def launch_attempt(launch):
 a=launch.get('generation_attempt',1)
 if type(a)!=int or a not in (1,2):raise ValueError('launch attempt')
 if FROZEN and launch['track']=='source' and a!=2:raise ValueError('source first pass already completed')
 if a==2:
  if set(launch['ids'])!=set(FAILED_JOBS):raise ValueError('exact final failures required')
  for ident in launch['ids']:generation(launch['track'],ident,a)
 return a
