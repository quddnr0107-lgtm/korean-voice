import unittest,json
from render_sealed import safe_failure
class SafeFailure(unittest.TestCase):
 def test_secret_message_not_logged(self):
  result=safe_failure(ValueError('secret manuscript PRIVATE_KEY=abc123'));self.assertNotIn('secret',json.dumps(result));self.assertNotIn('abc123',json.dumps(result))
 def test_fixed_flags_only(self):
  result=safe_failure(ValueError("MS-084 chunk 3 failed audio checks: ['long_internal_silence']"));self.assertEqual(result['chunk_index'],3);self.assertEqual(result['quality_flags'],['long_internal_silence'])
 def test_matched_error_still_omits_text(self):
  result=safe_failure(ValueError('MS-084 chunk 3 failed audio checks: private manuscript'));self.assertNotIn('private',json.dumps(result));self.assertEqual(result['quality_flags'],[])
 def test_unknown_type_hidden(self):
  SecretType=type('PrivateManuscript',(Exception,),{});self.assertEqual(safe_failure(SecretType('hidden'))['error_type'],'OtherError')
 def test_no_traceback_source_lines(self):
  try:raise ValueError('private manuscript')
  except ValueError as exc:result=safe_failure(exc)
  self.assertEqual(result['code_locations'],[])
 def test_part_numeric_diagnostics(self):
  r=safe_failure(ValueError('part_decode_validation part=2 finite=1 clipped=3 silence_ms=120 sample_delta=-4'))
  self.assertEqual(r['reason'],'independent_part_decode');self.assertEqual(r['clipped_samples'],3);self.assertEqual(r['sample_delta'],-4)
 def test_part_message_injection_hidden(self):
  r=safe_failure(ValueError('part_decode_validation part=2 finite=1 clipped=3 silence_ms=120 sample_delta=-4 SECRET'))
  self.assertNotIn('reason',r);self.assertNotIn('SECRET',json.dumps(r))
