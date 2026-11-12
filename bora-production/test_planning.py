"""Synthetic, public planning regressions. No production manuscript is used."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import planning


class PlanningTests(unittest.TestCase):
    def plan(self, source, lesson='DR-001'):
        return planning.build_plan(lesson, source)

    def test_schema_identity_and_determinism(self):
        first = self.plan('가상 강의의 순서를 살펴보겠습니다.')
        self.assertEqual(first, self.plan(first['source']))
        self.assertEqual(first['schema'], 'kim-bora-production-paragraph-plan/v1')
        self.assertEqual(first['planner_schema'], planning.SCHEMA)
        expected = hashlib.sha256(json.dumps(first, ensure_ascii=True, sort_keys=True,
                                            separators=(',', ':')).encode('ascii')).hexdigest()
        self.assertEqual(planning.identity(first), expected)
        self.assertEqual(first['review_unit_count'], 0)

    def test_all_303_supported_ids_and_out_of_range_rejected(self):
        count = 0
        for course, maximum in [('TB', 25), ('DR', 78), ('RL', 34), ('MS', 166)]:
            for number in range(1, maximum + 1):
                result = self.plan('합성 예제를 살펴봅니다.', f'{course}-{number:03d}')
                self.assertEqual(result['lesson_id'], f'{course}-{number:03d}')
                count += 1
            for number in (0, maximum + 1):
                with self.assertRaises(ValueError):
                    self.plan('합성 예제입니다.', f'{course}-{number:03d}')
        self.assertEqual(count, 303)

    def test_raw_source_coverage_crlf_blank_lines_and_non_bmp(self):
        source = '\ufeff  예제 😀를 살펴봅니다.\r\n\r\n\t다음 예제입니다.  \r\n'
        result = self.plan(source)
        self.assertEqual(''.join(u['source'] for u in result['units']), source)
        self.assertEqual(''.join(p['source'] for p in result['paragraphs']), source)
        self.assertEqual(len(result['paragraphs']), 2)
        for unit in result['units']:
            self.assertEqual(source[unit['start_cp']:unit['end_cp']], unit['source'])
            self.assertEqual(len(source[:unit['start_cp']].encode('utf-16-le')) // 2,
                             unit['start_utf16'])
            self.assertEqual(len(unit['source'].encode('utf-16-le')) // 2,
                             unit['end_utf16'] - unit['start_utf16'])
            self.assertTrue(unit['reading_mode_verified'])
            self.assertFalse(unit['reading_mode_verification']['human_review_claimed'])

    def test_number_and_article_normalization_resolves_only_numeric_flags(self):
        result = self.plan('2025년 6월 5일에는 12개의 예제를 3시간 동안 봅니다.\n제12조제2항을 확인합니다.')
        spoken = ' '.join(u['spoken'] for u in result['units'])
        self.assertIn('유월', spoken)
        self.assertIn('열두 개', spoken)
        self.assertIn('세 시간', spoken)
        self.assertIn('제십이조제이항', spoken)
        self.assertNotRegex(spoken, '[0-9]')
        self.assertEqual(result['review_unit_count'], 0)
        self.assertIn('review_remaining_numeric_notation', result['units'][0]['normalization']['resolved_preparation_flags'])

    def test_ambiguous_range_and_unknown_roman_remain_blocking(self):
        for source, expected in [('범위는 3-4개입니다.', 'normalization_ambiguous-range'),
                                 ('형식 Ⅱ를 보겠습니다.', 'normalization_ambiguous-roman'),
                                 ('설명에 example을 적습니다.', 'normalization_english-word-review')]:
            result = self.plan(source)
            self.assertIn(expected, result['units'][0]['review'])
            self.assertEqual(result['review_unit_count'], 1)

    def test_middot_known_exact_alias_only(self):
        known = self.plan('통합·운용과 침투·도발을 설명합니다.')
        self.assertEqual(known['review_unit_count'], 0)
        self.assertIn('통합 운용', known['units'][0]['spoken'])
        unknown = self.plan('도구·부품을 설명합니다.')
        self.assertIn('review_remaining_middot_do_not_globally_split', unknown['units'][0]['review'])
        self.assertIn('도구·부품', unknown['units'][0]['spoken'])

    def test_explicit_legal_article_is_reading_but_title_is_explanation(self):
        result = self.plan('제1조(목적)\n제2조(예제) 이 법은 예제의 범위를 정한다.\n1. 첫 번째 설명입니다.')
        self.assertEqual([u['mode'] for u in result['units']], ['explain', 'reading', 'explain'])
        self.assertEqual(result['review_unit_count'], 0)

    def test_legal_numbered_list_context_continues_and_resets(self):
        source = '제2조 이 법에서 정하는 예제는 다음 각 호로 정한다.\n1. 첫째 예제\n2. 둘째 예제\n설명을 이어갑니다.\n3. 새 제목'
        result = self.plan(source)
        self.assertEqual([u['mode'] for u in result['units']],
                         ['reading', 'reading', 'reading', 'explain', 'explain'])

    def test_circled_legal_clause_has_independent_structural_evidence(self):
        result = self.plan('① 누구든지 정해진 절차를 따라야 한다.')
        self.assertEqual(result['units'][0]['mode'], 'reading')
        self.assertEqual(result['review_unit_count'], 0)

    def test_full_sentence_quote_and_quoted_title(self):
        result = self.plan('「가상법」을 설명합니다.\n규정은 “누구든지 정해진 절차에 따라야 한다.”라고 말합니다.')
        self.assertEqual([u['mode'] for u in result['units']], ['explain', 'explain', 'reading', 'explain'])
        reading = [u for u in result['units'] if u['mode'] == 'reading'][0]
        self.assertEqual(reading['source'], '“누구든지 정해진 절차에 따라야 한다.”')
        self.assertEqual(reading['reading_mode_verification']['method'], 'full_sentence_quotation')

    def test_unbalanced_legal_quotation_is_reviewed(self):
        result = self.plan('이 법의 원문은 “모든 예제의 기준을 정한다.')
        self.assertIn('reading_mode_ambiguous_unbalanced_legal_quote', result['units'][0]['review'])
        self.assertFalse(result['units'][0]['reading_mode_verified'])

    def test_exact_annotation_offsets_only_on_matching_hash(self):
        source = '도입 문장입니다.\n검토된 예제 문장입니다.'
        start = source.index('검토된')
        spans = [[start, len(source)]]
        approved = dict(spans=spans, critical_terms=['검토된 예제'], config_sha256='a' * 64,
                        method='existing_exact_source_annotations')
        with patch.object(planning, '_approved_annotations', return_value=approved) as lookup:
            result = self.plan(source, 'TB-001')
        lookup.assert_called_once_with('TB-001', hashlib.sha256(source.encode()).hexdigest())
        self.assertTrue(result['approved_annotation_match'])
        self.assertEqual([u['mode'] for u in result['units']], ['explain', 'reading'])
        self.assertEqual(result['units'][1]['critical_terms'], ['검토된 예제'])
        unmatched = self.plan(source, 'TB-001')
        self.assertFalse(unmatched['approved_annotation_match'])
        self.assertEqual([u['mode'] for u in unmatched['units']], ['explain', 'explain'])

    def test_no_legacy_delivery_prefixes_or_source_rewrite(self):
        result = self.plan('도입입니다.\n둘째 설명입니다.\n셋째 설명입니다.', 'TB-001')
        self.assertEqual([u['spoken'] for u in result['units']],
                         ['도입입니다.', '둘째 설명입니다.', '셋째 설명입니다.'])

    def test_normalization_failure_is_blocked_with_source_preserved(self):
        source = '12개의 예제를 살펴봅니다.'
        with patch.object(planning.tts_normalize, 'normalize', side_effect=ValueError('private details')):
            result = self.plan(source)
        self.assertEqual(result['source'], source)
        self.assertIn('normalization_error_requires_review', result['units'][0]['review'])
        self.assertTrue(result['units'][0]['normalization']['failed'])
        self.assertNotIn('private details', json.dumps(result))

    def test_semantic_split_bound_and_no_cross_paragraph_merging(self):
        source = ('예제 문장을 조심스럽게 설명합니다. ' * 25) + '\n다른 문단입니다.'
        result = self.plan(source)
        self.assertGreater(len(result['units']), 2)
        self.assertTrue(all(len(u['source'].strip()) <= 180 for u in result['units']))
        self.assertFalse(result['merge_across_paragraphs'])
        self.assertEqual(''.join(u['source'] for u in result['units']), source)
        oversized = self.plan('가' * 250)
        self.assertIn('long_unbroken_span_review', oversized['units'][0]['review'])

    def test_review_flags_bind_plan_identity(self):
        result = self.plan('예제입니다.')
        changed = copy.deepcopy(result)
        changed['units'][0]['review'] = ['reading_mode_unverified']
        self.assertNotEqual(planning.identity(result), planning.identity(changed))


if __name__ == '__main__':
    unittest.main()
