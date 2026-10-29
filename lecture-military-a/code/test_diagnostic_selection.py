import unittest
from types import SimpleNamespace
import delivery as d
class DiagnosticSelection(unittest.TestCase):
 def setUp(self):self.q={'lessons':[{'id':x} for x in sorted(d.IDS)]};self.pair={'MS-003.checkpoint.123.1.json':{},'MS-003.checkpoint.123.1.bin':{}}
 def test_default_full_queue_unchanged(self):self.assertEqual(len(d.selected_ids(self.q,SimpleNamespace(assets={}))),83)
 def test_existing_failed_lesson_only(self):self.assertEqual(d.selected_ids(self.q,SimpleNamespace(assets=self.pair),'MS-003'),['MS-003'])
 def test_completed_refused(self):
  with self.assertRaises(ValueError):d.selected_ids(self.q,SimpleNamespace(assets={**self.pair,'MS-003.descriptor.json':{}}),'MS-003')
 def test_missing_checkpoint_refused(self):
  with self.assertRaises(ValueError):d.selected_ids(self.q,SimpleNamespace(assets={}),'MS-003')
 def test_incomplete_checkpoint_refused(self):
  with self.assertRaises(ValueError):d.selected_ids(self.q,SimpleNamespace(assets={'MS-003.checkpoint.123.1.json':{}}),'MS-003')
 def test_foreign_id_refused(self):
  for ident in ['AL-001','MS-999','MS-003; echo nope','../../MS-003','MS-003\n']:
   with self.assertRaises(ValueError):d.selected_ids(self.q,SimpleNamespace(assets=self.pair),ident)
