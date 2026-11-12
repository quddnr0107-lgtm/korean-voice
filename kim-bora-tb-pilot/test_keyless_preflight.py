import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import download_models

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('keyless_preflight',HERE/'keyless-preflight.py')
preflight=importlib.util.module_from_spec(spec);spec.loader.exec_module(preflight)


class KeylessPreflightTests(unittest.TestCase):
    def test_public_smoke_refuses_keys_before_loading_inference(self):
        for name in ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64'):
            with patch.dict(os.environ,{name:'synthetic-test-value'}):
                with patch.object(preflight,'package_versions') as packages:
                    with self.assertRaises(RuntimeError):preflight.main()
                    packages.assert_not_called()
                with self.assertRaises(RuntimeError):download_models.require_keyless()

    def test_runtime_versions_cannot_silently_drift_from_smoked_pins(self):
        with patch('importlib.metadata.version',side_effect=lambda name:preflight.EXPECTED_PACKAGES[name]):
            self.assertEqual(preflight.package_versions(),preflight.EXPECTED_PACKAGES)
        with patch('importlib.metadata.version',return_value='unexpected-version'):
            with self.assertRaises(RuntimeError):preflight.package_versions()

    def test_asset_download_checks_missing_empty_partial_invalid_and_symlink_files(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);required={'weights.bin':5,'config.json':None}
            with self.assertRaises(ValueError):download_models.verify_assets(root,required)
            (root/'weights.bin').write_bytes(b'12345');(root/'config.json').write_text('{}')
            download_models.verify_assets(root,required)
            (root/'weights.bin').write_bytes(b'1234')
            with self.assertRaises(ValueError):download_models.verify_assets(root,required)
            (root/'weights.bin').write_bytes(b'12345');(root/'config.json').write_bytes(b'')
            with self.assertRaises(ValueError):download_models.verify_assets(root,required)
            (root/'config.json').write_text('invalid-json')
            with self.assertRaises(ValueError):download_models.verify_assets(root,required)
            (root/'config.json').unlink();(root/'real.json').write_text('{}');(root/'config.json').symlink_to(root/'real.json')
            with self.assertRaises(ValueError):download_models.verify_assets(root,required)

    def test_unprotected_smoke_never_receives_keys_and_render_remains_protected(self):
        import yaml
        workflow=yaml.safe_load((HERE.parent/'.github/workflows/kim-bora-tb-pilot.yml').read_text())
        public=workflow['jobs']['preflight'];render=workflow['jobs']['render']
        self.assertNotIn('environment',public)
        self.assertNotIn('secrets.',json.dumps(public))
        self.assertEqual(render['environment'],'admin-law-premium-production')
        self.assertEqual(render['needs'],'preflight')
        self.assertIn("needs.preflight.result == 'success'",render['if'])
        self.assertIn('fromJSON(needs.preflight.outputs.matrix)',render['strategy']['matrix'])
        for job in (public,render):
            calls=[step.get('run','') for step in job['steps']]
            self.assertIn('bash kim-bora-tb-pilot/setup_runtime.sh',calls)
            self.assertIn('python kim-bora-tb-pilot/download_models.py',calls)
            self.assertTrue(any('unshare --net --setgid' in call for call in calls))
        self.assertFalse(any('upload-artifact' in step.get('uses','') for step in public['steps']))


if __name__=='__main__':
    unittest.main()
