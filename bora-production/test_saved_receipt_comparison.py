import copy
import json
import unittest

from saved_receipt_comparison import diagnostic_projection


class ComparisonTests(unittest.TestCase):
    def project(self, expected, recognized, critical=(), **candidate_changes):
        candidate = dict(asr={'recognized': recognized}, signal={'seconds': 1.25}, tts_seconds=2.5)
        candidate.update(candidate_changes)
        rows = {0: {'receipt': {'status': 'blocked_requires_review', 'candidates': [candidate]}}}
        plan = {'units': [{'mode': 'explain', 'spoken': expected, 'critical_terms': list(critical)}]}
        before = copy.deepcopy((rows, plan))
        out = diagnostic_projection(rows, plan)
        self.assertEqual(before, (rows, plan))
        self.assertFalse(out['production_eligible'])
        self.assertFalse(out['production_status_changed'])
        self.assertFalse(out['checkpoint_written'])
        return out

    def test_already_supported_sino_not_counted_as_new(self):
        for expected, recognized in [('제삼조', '제3조'), ('천구백구십육년', '1996년')]:
            result = self.project(expected, recognized)['totals']
            self.assertEqual(result['raw_exact_candidates'], 1)
            self.assertEqual(result['newly_exact_candidates'], 0)

    def test_representation_only_exact_cases(self):
        for expected, recognized in [('세 명', '3명'), ('한 시간', '1시간'),
                ('유월', '6월'), ('시월', '10월'), ('삼 점 일사', '3.14')]:
            with self.subTest(expected=expected):
                result = self.project(expected, recognized)['totals']
                self.assertEqual(result['newly_exact_candidates'], 1)
                self.assertEqual(result['blocked_units_with_any_normalized_exact'], 1)

    def test_wrong_number_missing_words_and_polarity_stay_nonexact(self):
        for expected, recognized in [('한 명', '2명'), ('제삼조', '제4조'),
                ('할 수 있다', '할 수 없다'), ('통합 방위법에 따른다', '방위법에 따른다'),
                ('삼 점 일사', '3.15'), ('유월', '7월')]:
            self.assertEqual(self.project(expected, recognized)['totals']['normalized_exact_candidates'], 0)

    def test_ambiguous_normalization_is_not_an_exact_match(self):
        result = self.project('K99', 'K99')['totals']
        self.assertEqual(result['normalization_flagged_candidates'], 1)
        self.assertEqual(result['normalized_exact_candidates'], 0)

    def test_missing_critical_term_not_ignored(self):
        self.assertEqual(self.project('세 명', '3명', ['제삼조'])['totals']['normalized_exact_candidates'], 0)

    def test_no_private_text_export_and_measurements(self):
        marker = 'PRIVATE_CANARY_DO_NOT_EXPORT'
        out = self.project(marker, marker)
        self.assertNotIn(marker, json.dumps(out))
        self.assertEqual(out['totals']['tts_seconds'], 2.5)
        self.assertEqual(out['totals']['audio_seconds'], 1.25)
        self.assertEqual(out['modes']['explain']['units'], 1)
        self.assertEqual(out['modes']['reading']['units'], 0)

    def test_nonfinite_negative_and_boolean_timings_not_exported(self):
        for value in (float('nan'), float('inf'), -1, True, 'private'):
            out = self.project('제일조', '제1조', tts_seconds=value, signal={'seconds': value})['totals']
            self.assertEqual(out['tts_seconds'], 0)
            self.assertEqual(out['audio_seconds'], 0)
            self.assertEqual(out['measured_tts_candidates'], 0)
            self.assertEqual(out['measured_audio_candidates'], 0)

    def test_any_and_all_counts_remain_distinct(self):
        rows = {0: {'receipt': {'status': 'blocked_requires_review', 'candidates': [
            {'asr': {'recognized': '3명'}}, {'asr': {'recognized': '2명'}}]}}}
        out = diagnostic_projection(rows, {'units': [{'mode': 'reading', 'spoken': '세 명'}]})['totals']
        self.assertEqual(out['blocked_units_with_any_normalized_exact'], 1)
        self.assertEqual(out['blocked_units_with_all_normalized_exact'], 0)

    def test_invalid_inputs_fail_closed(self):
        for rows, plan in [({}, {'units': []}), ({True: {}}, {'units': []}),
                ({0: {'receipt': {'status': 'unknown'}}}, {'units': [{'mode': 'other', 'spoken': '가'}]})]:
            with self.assertRaises(ValueError):
                diagnostic_projection(rows, plan)


if __name__ == '__main__':
    unittest.main()
