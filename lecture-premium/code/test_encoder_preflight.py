import unittest
from unittest.mock import patch
import encoder_preflight as e
class EncoderPreflight(unittest.TestCase):
 def test_missing_fails_before_any_subprocess(self):
  with patch.object(e.shutil,'which',return_value=None),patch.object(e.subprocess,'run') as run:
   with self.assertRaisesRegex(RuntimeError,'ENCODER_NOT_FOUND'):e.verify_encoder()
   run.assert_not_called()
 def test_workflow_supplies_pinned_encoder_before_both_checks(self):
  from pathlib import Path
  root=Path(__file__).resolve().parents[1]
  workflow=(root.parent/'.github/workflows/integrated-lectures-premium.yml').read_text()
  marker='      - name: Keyless encoder availability and exact sample contract'
  self.assertEqual(workflow.count(marker),2)
  for prefix in workflow.split(marker)[:-1]:
   self.assertIn('--no-deps --only-binary=:all: --require-hashes -r lecture-premium/encoder-requirements.txt',prefix)
   self.assertIn('sudo install -m 0755 "$encoder" /usr/local/bin/ffmpeg',prefix)
  self.assertIn('imageio-ffmpeg==0.6.0 --hash=sha256:c7e46fcec401dd990405049d2e2f475e2b397779df2519b544b8aab515195282',(root/'encoder-requirements.txt').read_text())
