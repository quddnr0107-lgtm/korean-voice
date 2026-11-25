"""Network-free fail-closed checks for one-worker recovery."""
import unittest
from unittest.mock import patch

import ms018_capacity_guard as guard


class CapacityGuardTests(unittest.TestCase):
    def test_short_inventory_is_rejected(self):
        with patch.object(guard, 'fetch_json', return_value={'total_count': 40, 'jobs':[{'id':1}]}):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                guard.items('test?filter=latest','jobs','fake')

    def test_duplicate_inventory_is_rejected(self):
        with patch.object(guard, 'fetch_json', return_value={'total_count': 2,'jobs':[{'id':1},{'id':1}]}):
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                guard.items('test?filter=latest','jobs','fake')

    def test_complete_inventory(self):
        with patch.object(guard, 'fetch_json', return_value={'total_count':2,'jobs':[{'id':1},{'id':2}]}):
            self.assertEqual(len(guard.items('test?filter=latest','jobs','fake')),2)

    def test_unknown_active_run_is_rejected(self):
        own = dict(id=1,status='in_progress',head_sha='a'*40)
        other = dict(id=2,status='in_progress',head_sha='b'*40,path='unknown')
        with patch.object(guard, 'items', side_effect=lambda url,*args:
                [own,other] if url.endswith('status=in_progress') else []):
            with self.assertRaisesRegex(ValueError,'unclassified'):
                guard.snapshot('fake',1,'a'*40)


if __name__ == '__main__':
    unittest.main()
