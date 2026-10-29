import unittest,json,zlib,copy,os,tempfile
from pathlib import Path
from unittest.mock import patch
import render_sealed as r
from lecture_crypto import enc,digest
class Contract(unittest.TestCase):
 def test_workflow_python_matches_numpy_minimum(self):
  workflow=(r.ROOT.parent/'.github/workflows/military-service-a-premium.yml').read_text()
  import re
  versions=re.findall(r"python-version: ['\"]([0-9.]+)['\"]",workflow)
  self.assertEqual(versions,['3.12']*3)
  self.assertIn('numpy==2.5.4',(r.ROOT/'requirements.txt').read_text())
 def payload(self):
  txt='가상의 예시 문장입니다. '
  c={'index':0,'id':'MS-001-c0001','sentence_id':'MS-001-s0001','text':txt,'tts_text':txt.strip(),'start_cp':0,'end_cp':len(txt),'source_start_cp':10,'source_end_cp':10+len(txt),'sha256':digest(txt.encode()),'tts_sha256':digest(txt.strip().encode())}
  spoken,edits,flags=r.normalize(c['tts_text']);c.update(spoken_text=spoken,spoken_sha256=digest(spoken.encode()),normalization_edits=edits,normalization_flags=flags,normalization_ruleset=r.RULESET,normalization_basis='tts_text')
  return {'version':'integrated-lecture-input/v1','course':'military-service-a','source_sha256':'a'*64,'lesson':{'id':'MS-001','title':'예시','source_sha256':digest(txt.encode()),'chunks':[c]},'recipe':json.loads((r.ROOT/'recipe.json').read_text())}
 def parse(self,p):return r.parse_payload(zlib.compress(enc(p)), 'MS-001')
 def test_roundtrip(self):self.assertEqual(self.parse(self.payload()),self.payload())
 def test_scope(self):
  p=self.payload();p['course']='directive'
  with self.assertRaises(ValueError):self.parse(p)
 def test_missing_text(self):
  p=self.payload();p['lesson']['chunks'][0]['text']='바꿈'
  with self.assertRaises(ValueError):self.parse(p)
 def test_tts_mutation(self):
  p=self.payload();p['lesson']['chunks'][0]['tts_text']='수정 문장'
  with self.assertRaises(ValueError):self.parse(p)
 def test_offsets(self):
  p=self.payload();p['lesson']['chunks'][0]['end_cp']+=1
  with self.assertRaises(ValueError):self.parse(p)
 def test_recipe(self):
  p=self.payload();p['recipe']['style']='F1'
  with self.assertRaises(ValueError):self.parse(p)
 def test_trailing(self):
  with self.assertRaises(ValueError):r.parse_payload(zlib.compress(enc(self.payload()))+b'extra','MS-001')
 def test_decompression_bomb(self):
  with self.assertRaises(ValueError):r.parse_payload(zlib.compress(b'A'*(16*1024*1024+1)),'MS-001')
 def test_child_env_no_keys(self):
  with patch.dict(os.environ,{'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64':'never-print','GH_TOKEN':'never-print'}):
   self.assertNotIn('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64',r.child_env());self.assertNotIn('GH_TOKEN',r.child_env())
 def test_checkpoint_roundtrip_and_input_pin(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);work=root/'work';out=root/'out';rest=root/'rest'
   for d in [work/'cache',out,rest/'cache']:d.mkdir(parents=True)
   (work/'cache'/('a'*64+'.wav')).write_bytes(b'fake synthetic wav')
   (work/'cache'/('a'*64+'.json')).write_text(json.dumps({'key':'a'*64,'wav_sha256':digest(b'fake synthetic wav'),'recipe':json.loads((r.ROOT/'recipe.json').read_text())}))
   (work/'cache'/'unfinished.tmp.wav').write_bytes(b'incomplete not checkpointed')
   key=b'k'*32
   with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}):self.assertTrue(r.save_checkpoint(key,'MS-001',work,out,'b'*64))
   r.restore_checkpoint(key,'MS-001',out,rest,'b'*64)
   self.assertEqual((rest/'cache'/('a'*64+'.wav')).read_bytes(),b'fake synthetic wav')
   with self.assertRaises(ValueError):r.restore_checkpoint(key,'MS-001',out,rest,'c'*64)
if __name__=='__main__':unittest.main()
