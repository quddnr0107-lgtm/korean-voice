"""Public synthetic tests for the finite middle-dot pronunciation inventory."""
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import middot_readings


class MiddotReadingTests(unittest.TestCase):
    def test_reviewed_legal_enumeration_only(self):
        result, edits, flags = middot_readings.normalize('시·도와 시장·군수·구청장을 설명합니다.')
        # The exact reviewed first inflection is supported. The unreviewed
        # second inflection must not be approved merely because its root is.
        self.assertEqual(result, '시, 도와 시장·군수·구청장을 설명합니다.')
        self.assertEqual(len(edits), 1)
        self.assertEqual([f['original'] for f in flags], ['시장·군수·구청장을'])

    def test_three_typographic_variants_are_explicit(self):
        for separator in middot_readings.SEPARATORS:
            result, edits, flags = middot_readings.normalize(f'시{separator}도, 군{separator}경')
            self.assertEqual(result, '시, 도, 군, 경')
            self.assertFalse(flags)
            self.assertEqual(len(edits), 2)
            self.assertIn(separator, edits[0]['original'])

    def test_unknown_compound_and_partial_match_remain_unchanged(self):
        for value in ['새시·도', '시·도새말', '시·도·미등록', 'A시·도', '시·도2',
                      '공·사', '미등록·어휘', '시 · 도', '3·1', '·']:
            with self.subTest(value=value):
                result, edits, flags = middot_readings.normalize(value)
                self.assertEqual(result, value)
                self.assertFalse(edits)
                self.assertTrue(flags)

    def test_whole_chain_has_no_silent_partial_approval(self):
        result, edits, flags = middot_readings.normalize('시·도·새말 및 시·도')
        self.assertEqual(result, '시·도·새말 및 시, 도')
        self.assertEqual(len(edits), 1)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]['original'], '시·도·새말')

    def test_no_plain_text_or_source_content_is_reworded(self):
        value = '설명 😀입니다. 「시·도」를 봅니다.\r\n군·경, 미등록·어휘'
        result, edits, flags = middot_readings.normalize(value)
        self.assertEqual(result, '설명 😀입니다. 「시, 도」를 봅니다.\r\n군, 경, 미등록·어휘')
        reverse = result
        for edit in reversed(edits):
            self.assertEqual(value[edit['source_start']:edit['source_end']], edit['original'])
            a, b = edit['spoken_start'], edit['spoken_end']
            reverse = reverse[:a] + edit['original'] + reverse[b:]
        self.assertEqual(reverse, value)
        self.assertEqual(flags[0]['original'], '미등록·어휘')
        self.assertEqual(value[flags[0]['source_start']:flags[0]['source_end']], flags[0]['original'])

    def test_finite_inventory_preserves_all_lexical_characters(self):
        self.assertEqual(len(middot_readings.READINGS), 243)
        self.assertEqual(len(set(middot_readings.REVIEWED_LEXEMES)), 243)
        for lexeme, reading in middot_readings.READINGS.items():
            result, edits, flags = middot_readings.normalize(lexeme)
            self.assertEqual(result, reading)
            self.assertEqual(len(edits), 1)
            self.assertFalse(flags)
            self.assertEqual(''.join(c for c in lexeme if c.isalnum()),
                             ''.join(c for c in result if c.isalnum()))

    def test_idempotence_and_unknown_flag_persistence(self):
        first, _, flags = middot_readings.normalize('시·도와 미등록·어휘')
        second, edits, again_flags = middot_readings.normalize(first)
        self.assertEqual(first, second)
        self.assertFalse(edits)
        self.assertEqual([f['reason'] for f in flags], [f['reason'] for f in again_flags])
        self.assertEqual([f['original'] for f in flags], [f['original'] for f in again_flags])


if __name__ == '__main__':
    unittest.main()
