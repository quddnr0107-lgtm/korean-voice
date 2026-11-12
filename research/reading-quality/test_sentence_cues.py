"""Synthetic alignment-contract tests, not speech/listening verification."""
import copy
import unittest

from paragraph_plan import build_plan, digest, utf16
from sentence_cues import build_sentence_cues, sentence_spans


def fixture(source, max_chars=220, word_groups=None, reading_spans=()):
    plan = build_plan(source, max_chars=max_chars, reading_spans=reading_spans)
    measurements, recognitions = [], []
    for index, unit in enumerate(plan['units']):
        pcm_hash = digest(unit['id'] + ':synthetic PCM')
        measurements.append(dict(id=unit['id'], source_sha256=unit['source_sha256'],
            pcm_sha256=pcm_hash, measurement_kind='final_decoded_pcm',
            sample_rate=100, sample_count=10000, gap_before_samples=0 if index == 0 else 25))
        tokens = word_groups[index] if word_groups is not None else unit['spoken'].split()
        words = [dict(text=text, start_s=.1 + n*.4, end_s=.3+n*.4, probability=.95)
                 for n, text in enumerate(tokens)]
        recognitions.append(dict(id=unit['id'], source_sha256=unit['source_sha256'],
            spoken_sha256=digest(unit['spoken']), pcm_sha256=pcm_hash,
            timing_basis='final_decoded_pcm', sample_rate=100, sample_count=10000, words=words))
    return plan, dict(source_sha256=plan['source_sha256'], units=measurements), dict(
        source_sha256=plan['source_sha256'], units=recognitions)


class SentenceCuesTests(unittest.TestCase):
    def test_exact_word_edges_and_unchanged_source(self):
        args = fixture('첫 문장입니다. 다음 문장입니다.')
        before = copy.deepcopy(args)
        result = build_sentence_cues(*args)
        self.assertEqual(args, before)
        self.assertTrue(result['complete'])
        self.assertFalse(result['production_ready'])
        self.assertTrue(result['perceptual_review_required'])
        self.assertEqual([(x['start_sample'], x['end_sample']) for x in result['segments']], [(10, 70), (90, 150)])

    def test_js_sentence_regex_and_utf16_leading_emoji(self):
        plan = build_plan('\ufeff  😀입니다.다음이에요.그렇죠? 네!\n\n 둘째 줄입니다.  \r\n')
        rows = sentence_spans(plan)
        self.assertEqual([x['source'] for x in rows], ['😀입니다.', '다음이에요.', '그렇죠?', '네!', '둘째 줄입니다.'])
        self.assertEqual(rows[0]['start_utf16'], 3)
        self.assertEqual(rows[0]['end_utf16'], 3 + utf16('😀입니다.'))
        self.assertNotEqual(rows[0]['paragraph_id'], rows[-1]['paragraph_id'])

    def test_word_crosses_boundary_no_interpolation(self):
        args = fixture('첫 문장입니다. 다음 문장입니다.', word_groups=[['첫', '문장입니다.다음', '문장입니다.']])
        result = build_sentence_cues(*args)
        self.assertEqual(result['segments'], [])
        self.assertIn('sentence_boundary_inside_asr_word', [x['reason'] for x in result['flags']])

    def test_mismatch_omits_only_unmatched_sentence(self):
        args = fixture('첫 문장입니다. 다음 문장입니다.', word_groups=[['첫', '오류입니다.', '다음', '문장입니다.']])
        result = build_sentence_cues(*args)
        self.assertFalse(result['complete'])
        self.assertEqual([x['sentence_index'] for x in result['segments']], [1])
        self.assertIn('unit_transcript_disagreement', [x['reason'] for x in result['flags']])

    def test_repeated_exact_unit_uses_order(self):
        result = build_sentence_cues(*fixture('맞습니다. 맞습니다.'))
        self.assertTrue(result['complete'])
        self.assertEqual([x['start_sample'] for x in result['segments']], [10, 50])

    def test_repeated_mismatched_unit_is_ambiguous(self):
        result = build_sentence_cues(*fixture('맞습니다. 맞습니다.', word_groups=[['맞습니다.', '맞습니다.', '추가']]))
        self.assertEqual(result['segments'], [])
        self.assertIn('ambiguous_repeated_fragment', [x['reason'] for x in result['flags']])

    def test_sentence_crossing_units_combines_actual_timestamps_and_gap(self):
        text = '대한민국의 여러 기관이 함께 모여서 각자의 역할을 나누고 협력하면서 모든 국가 방위 요소를 통합하여 운영하는 원칙입니다.'
        args = fixture(text, max_chars=40)
        self.assertGreater(len(args[0]['units']), 1)
        result = build_sentence_cues(*args)
        self.assertTrue(result['complete'])
        self.assertEqual(result['sentence_count'], 1)
        cue = result['segments'][0]
        self.assertEqual(len(cue['unit_ids']), len(args[0]['units']))
        last = args[2]['units'][-1]['words'][-1]
        expected = (len(args[0]['units'])-1)*10025 + round(last['end_s']*100)
        self.assertEqual(cue['end_sample'], expected)

    def test_number_and_middot_normalization_map_to_original(self):
        args = fixture('제2조 공·사 기업체입니다. 다음입니다.', word_groups=[['제2조', '공사', '기업체입니다.', '다음입니다.']])
        result = build_sentence_cues(*args)
        self.assertTrue(result['complete'])
        self.assertEqual(result['segments'][0]['end_utf16'], len('제2조 공·사 기업체입니다.'))

    def test_legal_araea_separator_is_not_required_spoken_content(self):
        args = fixture('통합ᆞ운용합니다.', word_groups=[['통합', '운용합니다.']])
        self.assertTrue(build_sentence_cues(*args)['complete'])

    def test_quote_punctuation_across_reading_unit_boundary(self):
        source = '먼저 보세요. 「제2조」를 읽습니다.'
        a, b = source.index('제2조'), source.index('」')
        args = fixture(source, reading_spans=[(a, b)])
        result = build_sentence_cues(*args)
        self.assertTrue(result['complete'])
        self.assertEqual(result['flags'], [])
        self.assertEqual(len(result['segments'][-1]['unit_ids']), 2)

    def test_unexplained_spoken_replacement_requires_review(self):
        args = fixture('원문입니다.')
        args[0]['units'][0]['spoken'] = '다른 문장입니다.'
        args[2]['units'][0]['spoken_sha256'] = digest('다른 문장입니다.')
        result = build_sentence_cues(*args)
        self.assertFalse(result['complete'])
        self.assertIn('source_to_spoken_mapping_requires_review', [x['reason'] for x in result['flags']])

    def test_raw_candidate_timestamps_and_stale_pcm_rejected(self):
        for field, value in [('timing_basis', 'raw_candidate_asr_not_forced_source_alignment'), ('pcm_sha256', 'b'*64), ('sample_count', 10001), ('spoken_sha256', 'b'*64)]:
            with self.subTest(field=field):
                args = fixture('원문입니다.')
                args[2]['units'][0][field] = value
                with self.assertRaises(ValueError):
                    build_sentence_cues(*args)

    def test_invalid_or_overlapping_word_times_flagged(self):
        for field, value in [('start_s', -1), ('end_s', float('nan')), ('end_s', 100.1)]:
            with self.subTest(field=field):
                args = fixture('첫 원문입니다.')
                args[2]['units'][0]['words'][0][field] = value
                result = build_sentence_cues(*args)
                self.assertEqual(result['segments'], [])
                self.assertIn('invalid_word_timestamps', [x['reason'] for x in result['flags']])
        args = fixture('첫 원문입니다.')
        args[2]['units'][0]['words'][1]['start_s'] = .2
        self.assertEqual(build_sentence_cues(*args)['segments'], [])

    def test_low_probability_sentence_requires_review(self):
        args = fixture('원문입니다.')
        args[2]['units'][0]['words'][0]['probability'] = .2
        result = build_sentence_cues(*args)
        self.assertEqual(result['segments'], [])
        self.assertIn('low_probability_fragment', [x['reason'] for x in result['flags']])

    def test_subsample_word_extent_is_not_fabricated(self):
        args = fixture('원문입니다.')
        args[2]['units'][0]['words'][0].update(start_s=.1001, end_s=.1002)
        result = build_sentence_cues(*args)
        self.assertEqual(result['segments'], [])
        self.assertIn('invalid_word_timestamps', [x['reason'] for x in result['flags']])

    def test_missing_duplicate_or_other_unit_rejected(self):
        for mutation in ('missing', 'duplicate', 'identity'):
            with self.subTest(mutation=mutation):
                args = fixture('첫째입니다.\n둘째입니다.')
                if mutation == 'missing':
                    args[2]['units'].pop()
                elif mutation == 'duplicate':
                    args[2]['units'][1] = copy.deepcopy(args[2]['units'][0])
                else:
                    args[2]['units'][1]['id'] = 'unknown'
                with self.assertRaises(ValueError):
                    build_sentence_cues(*args)


if __name__ == '__main__':
    unittest.main()
