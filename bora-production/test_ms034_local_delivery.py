"""Synthetic dedicated-recipient tests. No media or secret required."""
import unittest
from unittest.mock import patch
import ms034_local_delivery as delivery


class RecipientTests(unittest.TestCase):
    def test_pinned_public_recipient(self):
        self.assertEqual(delivery.recipient().key_size, 3072)

    def test_changed_public_recipient_rejected(self):
        with patch.object(delivery, 'PUBLIC_DER_B64', 'YWJj'):
            with self.assertRaises(ValueError):
                delivery.recipient()

    def test_changed_pin_rejected(self):
        with patch.object(delivery, 'PUBLIC_DER_SHA256', '0' * 64):
            with self.assertRaises(ValueError):
                delivery.recipient()


if __name__ == '__main__':
    unittest.main()
