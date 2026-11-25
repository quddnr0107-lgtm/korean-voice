import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.request = Path(self.tmp.name) / 'request.json'
        self.request.write_bytes(b'TEST_ONLY')
        self.owner = types.SimpleNamespace(REQUEST=self.request, RELEASES={'TB': ('bora-fp32-tb-single-pass-v1-20261114', '174fce697dc12f9aa3d6da7d6e98ddd8e9c1a3c79ac7e6ba39ef1b9de2dd1290', 25)}, recipient=Mock(), download=Mock(), export=Mock())
        with patch.dict(sys.modules, {'owner_video_export': self.owner}):
            spec = importlib.util.spec_from_file_location('scoped', Path(__file__).with_name('tb001_owner_export.py'))
            self.scoped = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.scoped)
        self.scoped.RECIPIENT_BLOB = hashlib.sha1(b'blob 9\0TEST_ONLY').hexdigest()

    def test_download_only_tb001(self):
        self.scoped.main(['download', 'input'])
        self.owner.download.assert_called_once_with(Path('input'), 'TB', ['TB-001'])
        self.owner.export.assert_not_called()

    def test_export_only_tb001(self):
        self.scoped.main(['export', 'input', 'output', '1001', 'net:[123]'])
        self.owner.export.assert_called_once_with(Path('input'), Path('output'), 'TB', ['TB-001'], 1001, 'net:[123]')
        self.owner.download.assert_not_called()

    def test_recipient_change_rejected(self):
        self.request.write_bytes(b'DIFFERENT')
        with self.assertRaises(ValueError):
            self.scoped.main(['export', 'input', 'output', '1001', 'net:[123]'])
        self.owner.export.assert_not_called()

    def test_release_change_rejected(self):
        self.owner.RELEASES['TB'] = ('wrong', 'bad', 25)
        with self.assertRaises(ValueError):
            self.scoped.main(['download', 'input'])
        self.owner.export.assert_not_called()

    def test_scope_not_user_selectable(self):
        with self.assertRaises(SystemExit), patch('sys.stderr'):
            self.scoped.main(['download', 'input', '--lesson', 'TB-002'])
        self.owner.download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
