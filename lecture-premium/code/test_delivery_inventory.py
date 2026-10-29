import copy,unittest,subprocess
from unittest.mock import patch
import delivery as d
class Inventory(unittest.TestCase):
 def asset(self,name='TB-001.checkpoint.1.1.json',size=10,state='uploaded'):
  return {'name':name,'size':size,'state':state}
 def run_retry(self,side_effect):
  return patch.object(d,'gh',side_effect=side_effect)
 def test_partial_multpage_then_complete(self):
  a=self.asset();b=self.asset('TB-002.checkpoint.1.1.json');pending={**b,'state':'starter','size':0}
  with self.run_retry([[[a],[pending]],[[a],[b]]]) as gh,patch.object(d.time,'sleep') as sleep:
   self.assertEqual(len(d.stable_asset_inventory(1)),2);self.assertEqual(gh.call_count,2);sleep.assert_called_once_with(2)
   self.assertTrue(all(c.args[0]=='api' for c in gh.call_args_list))
 def test_duplicate_page_then_stable(self):
  a=self.asset()
  with self.run_retry([[[a],[a]],[[a]]]),patch.object(d.time,'sleep'):
   self.assertEqual(len(d.stable_asset_inventory(1)),1)
 def test_permanent_pending_fails_bounded(self):
  with patch.object(d,'gh',return_value=[[self.asset(size=0)]]) as gh,patch.object(d.time,'sleep') as sleep:
   with self.assertRaisesRegex(ValueError,'asset upload incomplete'):d.stable_asset_inventory(1)
   self.assertEqual(gh.call_count,6);self.assertEqual(sleep.call_count,5)
 def test_permanent_duplicate_fails_bounded(self):
  a=self.asset()
  with patch.object(d,'gh',return_value=[[a,a]]) as gh,patch.object(d.time,'sleep'):
   with self.assertRaisesRegex(ValueError,'duplicate asset'):d.stable_asset_inventory(1)
   self.assertEqual(gh.call_count,6)
 def test_cap_unknown_name_http_not_retried(self):
  for value in ([[self.asset(size=d.MAX_JSON+1)]],[[self.asset(name='unsafe',state='starter',size=0)]],subprocess.CalledProcessError(1,['gh'])):
   with self.subTest(value=type(value).__name__),patch.object(d,'gh',side_effect=[value]) as gh,patch.object(d.time,'sleep') as sleep:
    with self.assertRaises((ValueError,subprocess.CalledProcessError)):d.stable_asset_inventory(1)
    self.assertEqual(gh.call_count,1);sleep.assert_not_called()
 def test_unknown_state_rejected_without_retry(self):
  with patch.object(d,'gh',return_value=[[self.asset(state='unknown')]]) as gh,patch.object(d.time,'sleep'):
   with self.assertRaisesRegex(ValueError,'asset state'):d.stable_asset_inventory(1)
   self.assertEqual(gh.call_count,1)
