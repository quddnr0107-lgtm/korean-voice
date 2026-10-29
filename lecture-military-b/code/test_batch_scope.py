import unittest,json,copy,tempfile
from pathlib import Path
import lecture_crypto as c
import delivery as d
from tts_normalize import normalize,verify
from render_sealed import parse_payload
class BatchScope(unittest.TestCase):
 def test_queue(self):
  q=d.validate_queue(Path(__file__).resolve().parents[1]);self.assertEqual(len(q['lessons']),83)
  self.assertEqual({r['id']for r in q['lessons']},d.IDS)
 def test_cross_batch_delivery(self):
  foreign='MS-084' if 'MS-001'in d.IDS else 'MS-001'
  for name in [foreign+'.descriptor.json',foreign+'.checkpoint.1.1.json']:
   with self.assertRaises(ValueError):d.asset_kind(name)
  for bad in ['TB-001','DR-001','RL-001','MS-000','MS-167',foreign]:
   with self.assertRaises(ValueError):d.descriptor_name(bad)
   with self.assertRaises(ValueError):parse_payload(b'invalid',bad)
 def test_domain_separation(self):
  self.assertEqual(c.namespace_for('MS-001'),'integrated-lectures/v1/military-service-a')
  self.assertEqual(c.namespace_for('MS-083'),'integrated-lectures/v1/military-service-a')
  self.assertEqual(c.namespace_for('MS-084'),'integrated-lectures/v1/military-service-b')
  self.assertEqual(c.namespace_for('MS-166'),'integrated-lectures/v1/military-service-b')
  self.assertNotEqual(c.namespace_for('MS-001'),c.namespace_for('TB-001'))
  for ident in ['MS-000','MS-167','MS-01','MS-001\n']:
   with self.assertRaises(ValueError):c.namespace_for(ident)
 def test_scope_and_cipher(self):
  root=Path(__file__).resolve().parents[1]
  for row in d.validate_queue(root)['lessons']:
   import base64
   obj=json.loads((root/'inputs'/row['file']).read_text())
   self.assertEqual(set(obj),{'record','cipher_b64'})
   ct=base64.b64decode(obj['cipher_b64'],validate=True);h=obj['record']['header']
   self.assertEqual(c.digest(ct),row['cipher_sha256']);self.assertEqual(c.digest(ct),obj['record']['cipher_sha256'])
   self.assertEqual(h['lesson_id'],row['id']);self.assertEqual(h['namespace'],c.namespace_for(row['id']))
   self.assertEqual(h['receiver'],c.PINNED_PUBLIC)
 def test_capacity(self):
  names=set()
  for ident in d.IDS:
   for run in range(1,4):
    names.add(ident+f'.checkpoint.{run}.1.json');names.add(ident+f'.checkpoint.{run}.1.bin')
   names.add(ident+'.descriptor.json')
  self.assertEqual(len(names),581);d.validate_asset_capacity(names|{'index.json'})
  ident=min(d.IDS)
  with self.assertRaises(ValueError):d.validate_asset_capacity(names,{ident+'.checkpoint.4.1.json'})
 def test_explicit_han_once(self):
  source='연한(年限)';spoken,edits,flags=normalize(source)
  self.assertEqual(spoken,'연한');self.assertEqual(flags,[])
  self.assertTrue(verify(source,spoken,edits));self.assertEqual(normalize(spoken),(spoken,[],[]))
  self.assertTrue(normalize('年限')[2]);self.assertTrue(normalize('다른말(年限)')[2])
 def test_original_namespace_unchanged(self):
  for i,v in [('TB-025','tongbang'),('DR-078','directive'),('RL-034','reserve-law')]:
   self.assertEqual(c.namespace_for(i),'integrated-lectures/v1/'+v)
if __name__=='__main__':unittest.main()
