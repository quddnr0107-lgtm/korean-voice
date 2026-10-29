import unittest,json
from render_sealed import safe_failure
class SafeFailure(unittest.TestCase):
 def test_secret_message_not_logged(self):
  result=safe_failure(ValueError('secret manuscript PRIVATE_KEY=abc123'));self.assertNotIn('secret',json.dumps(result));self.assertNotIn('abc123',json.dumps(result))
 def test_fixed_flags_only(self):
  result=safe_failure(ValueError("TB-001 chunk 3 failed audio checks: ['long_internal_silence']"));self.assertEqual(result['chunk_index'],3);self.assertEqual(result['quality_flags'],['long_internal_silence'])
 def test_matched_error_still_omits_text(self):
  result=safe_failure(ValueError('TB-001 chunk 3 failed audio checks: private manuscript'));self.assertNotIn('private',json.dumps(result));self.assertEqual(result['quality_flags'],[])
 def test_unknown_type_hidden(self):
  SecretType=type('PrivateManuscript',(Exception,),{});self.assertEqual(safe_failure(SecretType('hidden'))['error_type'],'OtherError')
 def test_no_traceback_source_lines(self):
  try:raise ValueError('private manuscript')
  except ValueError as exc:result=safe_failure(exc)
  self.assertEqual(result['code_locations'],[])
