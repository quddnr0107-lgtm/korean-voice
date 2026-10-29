import unittest
from types import SimpleNamespace
import delivery as d
class PilotSelection(unittest.TestCase):
 def setUp(self):self.q={'lessons':[{'id':x}for x in sorted(d.IDS)]};self.ident=min(d.IDS)
 def test_fresh_exact_one(self):self.assertEqual(d.selected_ids(self.q,SimpleNamespace(assets={}),pilot_id=self.ident),[self.ident])
 def test_foreign_id(self):
  for ident in ['TB-001','MS-000','MS-167','MS-001;echo bad', 'MS-001\n']:
   with self.assertRaises(ValueError):d.selected_ids(self.q,SimpleNamespace(assets={}),pilot_id=ident)
 def test_checkpoint_or_descriptor_requires_nonpilot(self):
  for suffix in ['descriptor.json','checkpoint.123.1.json','audio.'+'a'*32+'.bin']:
   with self.assertRaises(ValueError):d.selected_ids(self.q,SimpleNamespace(assets={self.ident+'.'+suffix:{}}),pilot_id=self.ident)
 def test_mixed_selectors_rejected(self):
  with self.assertRaises(ValueError):d.selected_ids(self.q,SimpleNamespace(assets={}),only_id=self.ident,pilot_id=self.ident)
 def test_whole_queue_unchanged(self):self.assertEqual(len(d.selected_ids(self.q,SimpleNamespace(assets={}))),83)
 def test_omitted_id_rejected(self):
  with self.assertRaises(ValueError):d.selected_ids({'lessons':[]},SimpleNamespace(assets={}),pilot_id=self.ident)

 def test_workflow_pilot_scope_and_finalize(self):
  from pathlib import Path
  root=Path(__file__).resolve().parents[1]
  group=root.name[-1]
  workflow=(root.parent/'.github/workflows'/f'military-service-{group}-premium.yml').read_text()
  self.assertIn('max-parallel: 40',workflow)
  self.assertIn('group: integrated-lectures-premium-v1-20261028',workflow)
  self.assertIn("inputs.diagnostic_lesson == '' && inputs.pilot_lesson == ''",workflow)
  self.assertIn('--pilot-id "$PILOT_LESSON"',workflow)
