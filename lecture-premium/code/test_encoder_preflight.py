import unittest
from unittest.mock import patch
import encoder_preflight as e
class EncoderPreflight(unittest.TestCase):
 def test_missing_fails_before_any_subprocess(self):
  with patch.object(e.shutil,'which',return_value=None),patch.object(e.subprocess,'run') as run:
   with self.assertRaisesRegex(RuntimeError,'ENCODER_NOT_FOUND'):e.verify_encoder()
   run.assert_not_called()
