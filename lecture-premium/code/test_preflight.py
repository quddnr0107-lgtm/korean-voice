import unittest,json,tempfile
from pathlib import Path
from unittest.mock import patch
import preflight
class Preflight(unittest.TestCase):
 def test_pending_fails(self):
  a=json.loads((preflight.ROOT/'authorization.json').read_text());a.update(approved=False,approval_reference=None)
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'authorization.json').write_text(json.dumps(a))
   with patch.object(preflight,'ROOT',p),self.assertRaises(ValueError):preflight.require_approval()
 def test_exact_approved_scope(self):
  a=json.loads((preflight.ROOT/'authorization.json').read_text());a.update(approved=True,approval_reference='owner-approval-20261028T1036Z')
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'authorization.json').write_text(json.dumps(a))
   with patch.object(preflight,'ROOT',p):self.assertEqual(preflight.require_approval()['courses'],{'TB':25,'DR':78,'RL':34})
   a['courses']['TB']=26;(p/'authorization.json').write_text(json.dumps(a))
   with patch.object(preflight,'ROOT',p),self.assertRaises(ValueError):preflight.require_approval()
if __name__=='__main__':unittest.main()
