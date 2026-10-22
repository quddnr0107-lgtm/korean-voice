import ast,copy,hashlib,io,json,pathlib,subprocess,tempfile,unittest,zipfile
from types import SimpleNamespace
from unittest.mock import patch
import bake,build_catalog

ROOT=pathlib.Path(__file__).resolve().parent

class RecoverySafety(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name)
  self.content=b'audio-fixture';self.key=hashlib.sha256(b'test text').hexdigest()
  self.bundle={'id':'GEN:1','items':[{'key':self.key,'text':'test text'}]}
  self.data={'schema':1,'version':'fixture','recipe':{'id':'sulsul-female-b-v1'},'jobs':[{'id':'000','bundles':[self.bundle]}]}
  folder=self.root/'fixture';folder.mkdir();self.archive=folder/'GEN-1.zip'
  self.record={'version':'fixture','sha256':hashlib.sha256(self.content).hexdigest(),'bytes':len(self.content),'seconds':1.0}
  self.manifest={'schema':1,'version':'fixture','recipe':'sulsul-female-b-v1','bundle':'GEN:1','files':{self.key:self.record}}
  self.make_zip()
  self.pin={'url':'https://github.com/'+bake.REPO+'/releases/download/sulsul-b1-fixture-GEN/001.zip','bytes':self.archive.stat().st_size,'sha256':bake.digest(self.archive),'units':1,'job':'000'}
  self.asset={'name':'001.zip','state':'uploaded','size':self.pin['bytes'],'digest':'sha256:'+self.pin['sha256'],'browser_download_url':self.pin['url']}
  self.args=SimpleNamespace(job='000',bundle=None,output=self.root,budget_minutes=320,publish=True)
 def make_zip(self,extra=None):
  with zipfile.ZipFile(self.archive,'w') as z:
   z.writestr('manifest.json',json.dumps(self.manifest));z.writestr(self.key+'.mp3',self.content)
   if extra:z.writestr(extra,b'bad')
 def pin_current(self):self.pin.update(bytes=self.archive.stat().st_size,sha256=bake.digest(self.archive))
 def test_api_network_error_is_not_missing(self):
  with patch.object(bake,'gh',return_value=SimpleNamespace(returncode=1,stdout='',stderr='timeout')):
   with self.assertRaises(bake.ApiError):bake.api('path',missing_ok=True)
 def test_api_403_is_not_missing(self):
  with patch.object(bake,'gh',return_value=SimpleNamespace(returncode=1,stdout='',stderr='gh: rate limit (HTTP 403)')):
   with self.assertRaises(bake.ApiError):bake.api('path',missing_ok=True)
 def test_api_explicit404_can_be_missing(self):
  with patch.object(bake,'gh',return_value=SimpleNamespace(returncode=1,stdout='',stderr='gh: Not Found (HTTP 404)')):
   self.assertIsNone(bake.api('path',missing_ok=True))
   with self.assertRaises(bake.ApiError):bake.api('path')
 def test_api_malformed_response_blocks(self):
  with patch.object(bake,'gh',return_value=SimpleNamespace(returncode=0,stdout='broken',stderr='')):
   with self.assertRaises(bake.ApiError):bake.api('path')
 def test_valid_archive(self):self.assertEqual(bake.validate_archive(self.archive,self.bundle,self.data,self.pin),self.manifest)
 def test_missing_or_changed_original_refuses_regeneration(self):
  self.archive.unlink()
  with self.assertRaisesRegex(ValueError,'Exact original ZIP'):bake.validate_archive(self.archive,self.bundle,self.data,self.pin)
 def test_repack_changed_bytes_rejected(self):
  self.archive.write_bytes(self.archive.read_bytes()+b'changed')
  with self.assertRaisesRegex(ValueError,'Exact original ZIP'):bake.validate_archive(self.archive,self.bundle,self.data,self.pin)
 def test_unexpected_zip_member_rejected(self):
  self.make_zip('../escape');self.pin_current()
  with self.assertRaisesRegex(ValueError,'member'):bake.validate_archive(self.archive,self.bundle,self.data,self.pin)
 def test_unit_hash_rejected(self):
  self.record['sha256']='0'*64;self.make_zip();self.pin_current()
  with self.assertRaisesRegex(ValueError,'MP3 record'):bake.validate_archive(self.archive,self.bundle,self.data,self.pin)
 def test_wrong_manifest_version_rejected(self):
  self.manifest['version']='other';self.make_zip();self.pin_current()
  with self.assertRaisesRegex(ValueError,'manifest'):bake.validate_archive(self.archive,self.bundle,self.data,self.pin)
 def test_published_digest_mismatch_blocks(self):
  self.asset['digest']='sha256:'+'0'*64
  with self.assertRaises(ValueError):bake.check_asset(self.asset,self.pin,'GEN:1')
 def test_published_state_mismatch_blocks(self):
  self.asset['state']='starter'
  with self.assertRaises(ValueError):bake.check_asset(self.asset,self.pin,'GEN:1')
 def test_completed_bundle_is_skipped_without_archive_or_engine(self):
  self.archive.unlink()
  with patch.object(bake,'catalog',return_value={'bundles':{'GEN:1':self.pin}}),patch.object(bake,'remote_bundle',return_value=self.asset),patch.object(bake,'load_engine') as engine,patch.object(bake,'publish_original') as publish:
   self.assertEqual(bake.restore(self.args,self.data),{'restored':0,'skipped':1,'generated':0});engine.assert_not_called();publish.assert_not_called()
 def test_only_original_missing_bundle_restored(self):
  with patch.object(bake,'catalog',return_value={'bundles':{'GEN:1':self.pin}}),patch.object(bake,'remote_bundle',return_value=None),patch.object(bake,'load_engine') as engine,patch.object(bake,'publish_original',return_value=True) as publish:
   self.assertEqual(bake.restore(self.args,self.data),{'restored':1,'skipped':0,'generated':0});engine.assert_not_called();publish.assert_called_once()
 def test_soft_budget_stops_before_network_or_work(self):
  self.args.budget_minutes=-1
  with patch.object(bake,'catalog',return_value={'bundles':{'GEN:1':self.pin}}),patch.object(bake,'remote_bundle') as remote:
   with self.assertRaises(TimeoutError):bake.restore(self.args,self.data)
   remote.assert_not_called()
 def test_no_publish_without_explicit_flag(self):
  self.args.publish=False
  with patch.object(bake,'catalog',return_value={'bundles':{'GEN:1':self.pin}}),patch.object(bake,'remote_bundle',return_value=None),patch.object(bake,'publish_original') as publish:
   bake.restore(self.args,self.data);publish.assert_not_called()
 def test_audit_has_no_work_and_complete_missing_selection(self):
  report={'missing':['GEN:1']}
  self.assertEqual(bake.select_jobs(self.data,report),[])
  self.assertEqual(bake.select_jobs(self.data,report,'retry-missing'),['000'])
  self.assertEqual(bake.select_jobs(self.data,{'missing':[]},'retry-missing'),[])
 def test_failed_selection_intersects_missing_and_requested(self):
  with patch.object(bake,'failed_jobs',return_value={'000'}):self.assertEqual(bake.select_jobs(self.data,{'missing':['GEN:1']},'retry-failed','000','123'),['000'])
  with patch.object(bake,'failed_jobs',return_value=set()):self.assertEqual(bake.select_jobs(self.data,{'missing':['GEN:1']},'retry-failed','000','123'),[])
 def test_unknown_job_fails(self):
  with self.assertRaises(ValueError):bake.select_jobs(self.data,{'missing':['GEN:1']},'retry-missing','999')
 def test_failed_run_parsing_ignores_success_and_unrelated_jobs(self):
  response=[{'status':'completed','path':'.github/workflows/sulsul.yml'},[{'jobs':[{'name':'bake (000)','conclusion':'failure'},{'name':'unrelated (000)','conclusion':'failure'},{'name':'bake (001)','conclusion':'success'}]}]]
  with patch.object(bake,'api',side_effect=response):self.assertEqual(bake.failed_jobs('123',self.data),{'000'})
 def test_wrong_workflow_run_is_rejected(self):
  with patch.object(bake,'api',return_value={'status':'completed','path':'.github/workflows/other.yml'}):
   with self.assertRaises(ValueError):bake.failed_jobs('123',self.data)
 def test_large_release_paginates_all_assets(self):
  release={'id':10,'assets':[{'name':str(i)} for i in range(100)]}
  pages=[[{'name':str(i)} for i in range(100)],[{'name':str(i)} for i in range(100,150)]]
  with patch.object(bake,'api',return_value=pages) as api:self.assertEqual(len(bake.assets(release)),150);self.assertTrue(api.call_args.kwargs['paginate'])
 def test_upload_error_reconciles_identical_remote_and_never_clobbers(self):
  with patch.object(bake,'remote_bundle',side_effect=[None,self.asset]),patch.object(bake,'api',return_value={'id':1}),patch.object(bake,'gh',return_value=SimpleNamespace(returncode=1,stderr='connection lost')) as gh:
   self.assertTrue(bake.publish_original(self.archive,self.bundle,self.data,self.pin));self.assertNotIn('--clobber',gh.call_args.args)
 def test_upload_error_without_remote_keeps_original(self):
  with patch.object(bake,'remote_bundle',return_value=None),patch.object(bake,'api',return_value={'id':1}),patch.object(bake,'gh',return_value=SimpleNamespace(returncode=1,stderr='connection lost')):
   with self.assertRaises(bake.ApiError):bake.publish_original(self.archive,self.bundle,self.data,self.pin)
   self.assertTrue(self.archive.is_file())

class CatalogTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.data=bake.plan();cls.snapshot=json.loads((ROOT/'qa/20261022/release-snapshot.json').read_text())
 def test_full_catalog_and_counts(self):
  built=build_catalog.build(self.snapshot,self.data);self.assertEqual(built,json.loads((ROOT/'release-catalog.json').read_text()));self.assertEqual(built['totals'],{'books':66,'bundles':1189,'units':61550,'archiveBytes':6162955606})
 def test_missing_asset_rejected(self):
  value=copy.deepcopy(self.snapshot);value['releases'][0]['assets'].pop()
  with self.assertRaises(ValueError):build_catalog.build(value,self.data)
 def test_duplicate_asset_rejected(self):
  value=copy.deepcopy(self.snapshot);value['releases'][0]['assets'].append(value['releases'][0]['assets'][0])
  with self.assertRaises(ValueError):build_catalog.build(value,self.data)
 def test_digest_missing_rejected(self):
  value=copy.deepcopy(self.snapshot);value['releases'][0]['assets'][0]['digest']=''
  with self.assertRaises(ValueError):build_catalog.build(value,self.data)

if __name__=='__main__':unittest.main()
