"""Premium local draft. Pure input/metadata/envelope helpers; no keys generated here."""
import copy,hashlib,json,re
from crypto_chunks import seal,open_range
from match_segments import match_lesson,utf16_length
from header_merge import expected_chunks
PIPELINE_REVISION='premium-header-merge-v1-20261026'
CHUNK_POLICY='header-first-body-contiguous-v1'
POLICY_IDS={'AL-019','AL-033','AL-037'}
RECIPE={'engine':'Supertonic 3','recipe':'k2','recipe_code_sha256':'907f5ec81b4f7302ffbdadedd37a0d6c283b2d85696fa2f59a62cdf44dcd44a8','style':'F2','voice':'female','steps':16,'base_speed':1.05,'r':1.0,'pause_s':0.48,'model_revision':'3cadd1ee6394adea1bd021217a0e650ede09a323','helper_revision':'1e9799e964ea4c0dad7cde993b65c3c813a7b373'}
sha=lambda b:hashlib.sha256(b if isinstance(b,bytes) else b.encode()).hexdigest()
def encoded(x):return json.dumps(x,ensure_ascii=False,separators=(',',':')).encode()
def canonical_id(x):
 m=re.search(r'AL-0(?:1[1-9]|[2-6][0-9]|7[0-8])$',x)
 if not m:raise ValueError('invalid premium identity')
 return m[0]
def inverse(lesson):
 src=lesson['source_text'];parts=[];bounds={0:0};sp=np=0
 for c in lesson['changes']:
  a,b=c['source_start'],c['source_end']
  if not sp<=a<b<=len(src) or src[a:b]!=c['original']:raise ValueError('invalid source edit')
  v=src[sp:a];parts.append(v)
  for i in range(len(v)+1):bounds[np+i]=sp+i
  np+=len(v);bounds[np]=a;parts.append(c['replacement']);np+=len(c['replacement']);bounds[np]=b;sp=b
 v=src[sp:];parts.append(v)
 for i in range(len(v)+1):bounds[np+i]=sp+i
 if ''.join(parts)!=lesson['narration_text']:raise ValueError('normalization reconstruction')
 return bounds

def clean_md(text):
 out=[];raw=[];i=0
 while i<len(text):
  if text.startswith('**',i):i+=2;continue
  out.append(text[i]);raw.append(i);i+=1
 return ''.join(out),raw

def validate_input(data):
 if set(data)!={'version','lesson','panel'} or data['version']!='admin-law-premium-input/v1':raise ValueError('input schema')
 l=data['lesson'];p=data['panel'];ident=canonical_id(l['id'])
 if ident not in POLICY_IDS:raise ValueError('header merge policy identity')
 if p['id']!=ident or l['number']!=int(ident[-3:]):raise ValueError('identity mismatch')
 for field in ('source','narration'):
  if sha(l[field+'_text'])!=l[field+'_sha256']:raise ValueError('input hash')
 inverse(l)
 if '검증 메모' in l['narration_text']:raise ValueError('excluded notes')
 ids=set()
 for para in p['paragraphs']:
  if para['id'] in ids or not para['id'].startswith(ident+'-p'):raise ValueError('paragraph identity')
  ids.add(para['id'])
  if sha(para['text'])!=para['textSha256']:raise ValueError('paragraph hash')
  clean,_=clean_md(para['text'])
  if not clean or len(list(re.finditer(re.escape(clean),l['source_text'])))!=1:raise ValueError('paragraph exact mapping')
 # Educational references are carried, never invented; only JSON arrays accepted.
 for k in ('sources','diagrams','relatedQuestions','relatedMockExams','printQuestions','relatedPractice'):
  if not isinstance(p.get(k,[]),list):raise ValueError('panel collection type')
 return ident

def prepare_encrypted_input(lesson,panel,source_key):
 """Caller supplies existing authorized key; function never creates or prints keys."""
 data={'version':'admin-law-premium-input/v1','lesson':lesson,'panel':panel};ident=validate_input(data)
 plain=encoded(data);m,b=seal(source_key,plain,ident,'source','admin-law-premium-source-v1')
 if open_range(source_key,m,b)[0]!=plain:raise ValueError('input encryption roundtrip')
 return m,b

def checkpoint_hash(data):
 return sha(encoded({'input_sha256':sha(encoded(data)),'voice':RECIPE,'metadata_schema':'admin-law-private-metadata/v1','pipeline_revision':PIPELINE_REVISION}))

def source_spans(data,meta):
 l=data['lesson'];bounds=inverse(l);paras=[]
 for p in data['panel']['paragraphs']:
  clean,raw=clean_md(p['text']);start=l['source_text'].index(clean);paras.append((start,start+len(clean),p,raw))
 offsets=match_lesson(meta)
 for seg,offset in zip(meta['segments'],offsets['segments']):
  seg.update(offset);a,b=offset['char_start_cp'],offset['char_end_cp'];seg['sourceSpans']=[]
  if a not in bounds or b not in bounds:
   seg['source_mapping_status']='unmapped';seg['source_mapping_reason']='boundary_inside_normalization_token';continue
  sa,sb=bounds[a],bounds[b];covered=[False]*(sb-sa);spans=[]
  for pa,pb,p,raw in paras:
   ia,ib=max(sa,pa),min(sb,pb)
   if ia>=ib:continue
   ra,rb=raw[ia-pa],raw[ib-pa-1]+1;fragment=p['text'][ra:rb]
   if clean_md(fragment)[0]!=l['source_text'][ia:ib]:raise ValueError('raw source span')
   start,end=utf16_length(p['text'][:ra]),utf16_length(p['text'][:rb]);original=p['text'].encode('utf-16-le')
   if original[2*start:2*end].decode('utf-16-le')!=fragment:raise ValueError('UTF16 source slice')
   spans.append({'paragraph_id':p['id'],'start_utf16':start,'end_utf16':end,'text':fragment,'text_sha256':sha(fragment),'paragraph_text_sha256':sha(p['text']),'provenance':'verified_normalization_span','source_start_cp':ia,'source_end_cp':ib})
   for i in range(ia-sa,ib-sa):covered[i]=True
  if spans and not any(not x and not l['source_text'][sa+i].isspace() for i,x in enumerate(covered)):
   seg.update(sourceSpans=spans,source_mapping_status='verified')
  elif spans and seg['index']==0 and all(x or l['source_text'][sa+i].isspace() or sa+i<min(p['source_start_cp'] for p in spans) for i,x in enumerate(covered)):
   seg.update(sourceSpans=spans,source_mapping_status='partial',source_mapping_reason='heading_and_verified_body',chunk_split_provenance='merged_original_chunks_0_1')
  else:seg.update(source_mapping_status='unmapped',source_mapping_reason='heading_or_nonparagraph_source')
 return meta

def private_metadata(data,audio,segments,metrics):
 ident=validate_input(data);l=data['lesson'];p=data['panel']
 if [s['text'] for s in segments]!=expected_chunks(l['narration_text']):raise ValueError('exact header merge chunk policy')
 meta={'version':'admin-law-private-metadata/v1','id':ident,'number':l['number'],'title':l['title'],'topic_ids':l['topic_ids'],'narration_text':l['narration_text'],'narration_sha256':l['narration_sha256'],'audio_sha256':sha(audio),'checkpoint_hash':checkpoint_hash(data),'voice':RECIPE,'duration_s':metrics['decoded_duration_s'],'segments':segments,'validation':metrics,'listening_verified':False,'timeline_provenance':'measured_chunk_boundaries','paragraphs':p['paragraphs'],'sources':p.get('sources',[]),'diagrams':p.get('diagrams',[]),'relatedQuestions':p.get('relatedQuestions',[]),'relatedMockExams':p.get('relatedMockExams',[]),'panel':p}
 for key in ('printQuestions','printQuestionsStatus','relatedPractice','relatedQuestionsStatus','relatedMockExamsStatus','diagramsStatus','diagramsUnavailableReason','mappingStatus','unmappedParagraphCount','syncMode','sourceDraftSha256'):
  if key in p:meta[key]=p[key]
 meta.update(chunk_policy=CHUNK_POLICY,pipeline_revision=PIPELINE_REVISION)
 source_spans(data,meta)
 if meta['segments'][0].get('source_mapping_status')!='partial':raise ValueError('merged header/body partial mapping required')
 if len(encoded(meta))>1024*1024:raise ValueError('private metadata exceeds consumer limit')
 return meta

def seal_outputs(data,audio,meta,media_key):
 ident=validate_input(data)
 if meta['checkpoint_hash']!=checkpoint_hash(data) or meta['audio_sha256']!=sha(audio):raise ValueError('output evidence mismatch')
 result={}
 for field,kind,plain in [('audio','audio',audio),('content','private-metadata',encoded(meta))]:
  m,b=seal(media_key,plain,ident,kind,'admin-law-premium-media-v1')
  if open_range(media_key,m,b)[0]!=plain:raise ValueError('output encrypt roundtrip')
  result[field]=(m,b)
 return result

def authenticated_checkpoint(data,descriptors,blobs,media_key):
 ident=validate_input(data)
 for field,kind in [('audio','audio'),('content','private-metadata')]:
  m=descriptors[field]
  if m['lesson_id']!=ident or m['kind']!=kind or m['key_id']!='admin-law-premium-media-v1':raise ValueError('checkpoint identity')
 audio=open_range(media_key,descriptors['audio'],blobs['audio'])[0]
 meta=json.loads(open_range(media_key,descriptors['content'],blobs['content'])[0])
 if meta['id']!=ident or meta['checkpoint_hash']!=checkpoint_hash(data) or meta['voice']!=RECIPE or meta['audio_sha256']!=sha(audio):raise ValueError('stale checkpoint')
 if meta['narration_sha256']!=data['lesson']['narration_sha256']:raise ValueError('checkpoint narration')
 # Reconstruct all derived metadata from current input, never repair cached metadata.
 # Only measured synthesis fields may be carried across; unknown/obsolete fields fail equality.
 measured=('index','text','start_s','end_s','speed','tail_hum','attempts')
 segments=[{k:copy.deepcopy(v) for k,v in s.items() if k in measured} for s in meta['segments']]
 expected=private_metadata(data,audio,segments,copy.deepcopy(meta['validation']))
 if meta!=expected:raise ValueError('stale checkpoint metadata')
 return audio,meta
