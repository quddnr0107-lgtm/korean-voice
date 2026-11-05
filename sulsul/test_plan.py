import gzip,hashlib,json,pathlib,unittest,yaml
ROOT=pathlib.Path(__file__).resolve().parent
class Contracts(unittest.TestCase):
 def test_complete_unique_and_hashes(self):
  with gzip.open(ROOT/'corpus.json.gz','rt') as f:plan=json.load(f)
  keys=set();bundles=set();chars=0
  for job in plan['jobs']:
   for bundle in job['bundles']:
    self.assertNotIn(bundle['id'],bundles);bundles.add(bundle['id'])
    for item in bundle['items']:
     self.assertEqual(item['key'],hashlib.sha256(item['text'].encode()).hexdigest())
     self.assertNotIn(item['key'],keys);keys.add(item['key']);chars+=len(item['text'])
  self.assertEqual(len(keys),61550);self.assertEqual(len(bundles),1189);self.assertEqual(chars,3574021)
  self.assertLessEqual(len(plan['jobs']),256)
 def test_frozen_approved_recipe(self):
  with gzip.open(ROOT/'corpus.json.gz','rt') as f:recipe=json.load(f)['recipe']
  self.assertEqual(recipe['style'],'F4:0.6,F2:0.4');self.assertEqual(recipe['steps'],16)
  self.assertEqual(recipe['baseSpeed'],1.06);self.assertIsNone(recipe['profile'])
  self.assertEqual(recipe['approvedSample'],'faaef1bd9b77f0be2b65377a218a329f73fbb6c9ceebfe50233ed150115555e2')
 def test_workflow_no_telemetry_or_other_application_write(self):
  workflow=yaml.safe_load((ROOT.parent/'.github/workflows/sulsul.yml').read_text())
  self.assertEqual(workflow['env']['ORT_DISABLE_TELEMETRY'],'1')
  self.assertEqual(workflow['jobs']['bake']['strategy']['max-parallel'],32)
  self.assertNotIn('id-token',workflow['permissions'])
if __name__=='__main__':unittest.main()
