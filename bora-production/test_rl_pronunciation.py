"""Exercise the read-only Reserve Forces pronunciation overlay."""
import copy
import hashlib
import unittest
from unittest.mock import patch

import middot_readings
import planning
import rl_pronunciation as overlay


class ReserveForcesPronunciationTests(unittest.TestCase):
    def setUp(self):
        self.lexeme = sorted(overlay._rules()[0])[0]

    def test_only_whole_allowlisted_lexemes_change_and_other_flags_remain(self):
        base = middot_readings.REVIEWED_LEXEMES[0]
        unknown = '가상·미정'
        source = f'{base} 및 {self.lexeme}, {self.lexeme}가 아니라 {unknown} 용례입니다.'
        original = planning.build_plan('RL-001', source)
        baseline = copy.deepcopy(original)
        actual = overlay.apply_to_plan(original)
        self.assertEqual(original, baseline)
        self.assertEqual(actual['source'], source)
        self.assertEqual(actual['source_sha256'], original['source_sha256'])
        self.assertEqual(actual['paragraphs'], original['paragraphs'])
        before, after = original['units'][0], actual['units'][0]
        for field in ('start_cp', 'end_cp', 'start_utf16', 'end_utf16', 'source', 'mode',
                      'id', 'source_sha256', 'reading_mode_verification'):
            self.assertEqual(before[field], after[field])
        self.assertIn(self.lexeme.replace('·', ', '), after['spoken'])
        self.assertIn(self.lexeme + '가', after['spoken'])
        self.assertIn(unknown, after['spoken'])
        self.assertIn(overlay.REMAINING_FLAG, after['review'])
        self.assertIn('normalization_' + overlay.LEXEME_REASON, after['review'])
        self.assertEqual(after['spoken_sha256'], hashlib.sha256(after['spoken'].encode()).hexdigest())
        self.assertIs(overlay.apply_to_plan(actual), actual)

    def test_exact_context_hash_offset_and_lesson_are_all_required(self):
        source = '청구 · 처리 절차를 안내합니다.'
        original = planning.build_plan('RL-001', source)
        unit = original['units'][0]
        offset = unit['spoken'].index('·')
        digest = unit['normalization']['middot_input_sha256']
        good_pin = ('RL-001', digest, offset, '·')
        variants = [good_pin, ('RL-002', digest, offset, '·'),
                    ('RL-001', '0'*64, offset, '·'), ('RL-001', digest, offset+1, '·'),
                    ('RL-001', digest, offset, 'ᆞ')]
        for pin in variants:
            with self.subTest(pin=pin), patch.object(
                overlay, '_rules', return_value=(frozenset(), frozenset({pin}), 'a'*64)
            ):
                actual = overlay.apply_to_plan(original)
                if pin == good_pin:
                    self.assertEqual(actual['review_unit_count'], 0)
                    self.assertNotIn('·', actual['units'][0]['spoken'])
                    self.assertEqual(actual['source'], source)
                else:
                    self.assertIs(actual, original)

    def test_no_op_for_tb_and_preserve_unrelated_review(self):
        source = f'{self.lexeme} 및 UNKNOWN 합성 검토입니다.'
        tb = planning.build_plan('TB-002', source)
        self.assertIs(overlay.apply_to_plan(tb), tb)
        rl = planning.build_plan('RL-001', source)
        actual = overlay.apply_to_plan(rl)
        self.assertNotEqual(actual['units'][0]['spoken'], rl['units'][0]['spoken'])
        before = set(rl['units'][0]['review'])
        after = set(actual['units'][0]['review'])
        self.assertIn('normalization_english-word-review', after)
        self.assertLessEqual(after, before)
        self.assertNotIn(overlay.REMAINING_FLAG, after)


if __name__ == '__main__':
    unittest.main()
