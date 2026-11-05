import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
spec = importlib.util.spec_from_file_location('reader_assistance_asr', ROOT / 'tools/reader_assistance_asr.py')
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)


class LegalASR(unittest.TestCase):
    def test_spacing_digits_and_number_reading_can_be_equivalent(self):
        for target, transcript in [('6개월 이상 1년 미만입니다.', '육 개월 이상 일 년 미만입니다.'),
                                   ('한 명입니다.', '1명입니다.'), ('한 해입니다.', '1년입니다.'),
                                   ('제3조제1항을 적용합니다.', '제 삼조 제 일항을 적용합니다.')]:
            out = A.assess(target, transcript)
            self.assertEqual(out['status'], 'normalized_match', out)

    def test_changed_numbers_bounds_negation_or_duty_never_pass(self):
        cases = [('6개월 이상입니다.', '7개월 이상입니다.'),
                 ('6개월 이상 1년 미만입니다.', '6개월 미만 1년 이상입니다.'),
                 ('보충역에 편입하지 않습니다.', '보충역에 편입합니다.'),
                 ('허가를 받아야 합니다.', '허가를 받을 수 있습니다.'),
                 ('6개월 이하입니다.', '6개월 미만입니다.')]
        for target, transcript in cases:
            out = A.assess(target, transcript)
            self.assertEqual(out['status'], 'review_required')
            self.assertFalse(out['criticalMatch'], out)

    def test_actor_or_noncritical_word_errors_also_require_review(self):
        out = A.assess('지방병무청장이 확인해야 합니다.', '병무청장이 확인해야 합니다.')
        self.assertTrue(out['criticalMatch'])
        self.assertEqual(out['status'], 'review_required')

    def test_truncated_tail_cannot_hide_permission_or_exception(self):
        out = A.assess('대상자는 신청할 수 있습니다. 다만 예외에 해당하면 제외합니다.', '대상자는 신청할 수 있습니다.')
        self.assertEqual(out['status'], 'review_required')
        self.assertFalse(out['criticalMatch'])

    def test_no_prompt_or_full_corpus_success_is_fabricated(self):
        source = (ROOT / 'tools/reader_assistance_asr.py').read_text()
        self.assertIn('initial_prompt=None', source)
        self.assertIn("'allCorpusAssessed': False", source)
        self.assertIn('PILOT_SOURCE_SHA', source)
        self.assertNotIn('paid_tts', source)


if __name__ == '__main__':
    unittest.main()
