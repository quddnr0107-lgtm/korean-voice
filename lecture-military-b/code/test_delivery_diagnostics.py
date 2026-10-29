import unittest,json,subprocess
from delivery import safe_delivery_failure
class DeliveryDiagnostics(unittest.TestCase):
 def test_only_allowlisted_reason(self):
  self.assertEqual(safe_delivery_failure(ValueError('asset upload incomplete'))['reason'],'asset upload incomplete')
 def test_arbitrary_message_hidden(self):
  r=safe_delivery_failure(ValueError('secret manuscript token=abc'))
  self.assertNotIn('reason',r);self.assertNotIn('abc',json.dumps(r))
 def test_subprocess_output_never_logged(self):
  e=subprocess.CalledProcessError(1,['gh','private-argument'],output='secret',stderr='private-stderr')
  r=safe_delivery_failure(e);self.assertEqual(r['exit_code'],1)
  self.assertNotIn('secret',json.dumps(r));self.assertNotIn('private',json.dumps(r))
