import copy
import unittest
from unittest.mock import patch

import assemble
from production_plan import digest
import fp32_renderer
import ms_pronunciation as overlay
import planning


class MilitaryServicePronunciationTests(unittest.TestCase):
    def test_only_ms_whole_approved_lexemes_and_no_legal_content_change(self):
        line = '😀 장교·부사관과 징집·소집 및 가상·미정의 예입니다.'
        original = planning.build_plan('MS-001', line)
        baseline = copy.deepcopy(original)
        modified = overlay.apply_to_plan(original)
        self.assertEqual(original, baseline)
        self.assertEqual(modified['paragraphs'], original['paragraphs'])
        unit = modified['units'][0]
        self.assertEqual(unit['source'], line)
        self.assertIn('징집, 소집', unit['spoken'])
        self.assertIn('가상·미정의', unit['spoken'])
        self.assertIn(overlay.REMAINING, unit['review'])
        self.assertEqual(digest(unit['spoken']), unit['spoken_sha256'])
        self.assertIs(overlay.apply_to_plan(modified), modified)
        for course in ('TB', 'RL', 'DR'):
            unrelated = planning.build_plan(f'{course}-002', line)
            self.assertIs(overlay.apply_to_plan(unrelated), unrelated)

    def test_contextual_separator_needs_exact_lesson_hash_and_offset(self):
        line = '청구 · 처리 절차를 안내합니다.'
        original = planning.build_plan('MS-001', line)
        input_hash = original['units'][0]['normalization']['middot_input_sha256']
        separator = line.index('·')
        approved = ('MS-001', input_hash, separator, '·')
        for item in (approved, ('MS-002', input_hash, separator, '·'),
                     ('MS-001', '0'*64, separator, '·'),
                     ('MS-001', input_hash, separator+1, '·')):
            with self.subTest(pin=item), patch.object(
                overlay, '_rules', return_value=(set(), {item}, set(), set(), 'a'*64)
            ):
                result = overlay.apply_to_plan(original)
                if item == approved:
                    self.assertEqual(result['review_unit_count'], 0)
                    self.assertEqual(result['units'][0]['spoken'], '청구 , 처리 절차를 안내합니다.')
                    assemble.validate_plan(result)
                else:
                    self.assertIs(result, original)

    def test_date_separator_is_pinned_to_context_and_course(self):
        for ident, line, applied in (
            ('MS-138', '자료는 6·25 전쟁을 다룹니다.', True),
            ('MS-138', '자료는 6·25 용례를 다룹니다.', False),
            ('MS-139', '자료는 6·25 전쟁을 다룹니다.', False),
        ):
            with self.subTest(lesson=ident, line=line):
                original = planning.build_plan(ident, line)
                result = overlay.apply_to_plan(original)
                self.assertEqual(result['source'], line)
                self.assertEqual(result['review_unit_count'], int(not applied))
                if applied:
                    self.assertIn('육 이십오 전쟁', result['units'][0]['spoken'])
                else:
                    self.assertIs(result, original)

    def test_exact_numbered_legal_paragraph_gets_reading_style(self):
        line = ('3. 이 법 또는 「예시법」에 따라 공적 기준의 대상자 명단을 작성하는 데 필요한 '
                '자료의 하나 이상을 빠짐없이 제출하는 경우')
        original = planning.build_plan('MS-001', line)
        paragraph = original['paragraphs'][0]
        approved = ('MS-001', digest(line), paragraph['id'], digest(paragraph['source']))
        self.assertIn(overlay.FRAGMENT, original['units'][0]['review'])
        for item in (approved, ('MS-001', '0'*64, paragraph['id'], digest(line))):
            with self.subTest(pin=item), patch.object(
                overlay, '_rules', return_value=(set(), set(), {item}, set(), 'a'*64)
            ):
                result = overlay.apply_to_plan(original)
                if item != approved:
                    self.assertIs(result, original)
                    continue
                self.assertEqual(result['source'], line)
                self.assertEqual(result['paragraphs'], original['paragraphs'])
                self.assertEqual(result['reading_spans'], [[0, len(line)]])
                unit = result['units'][0]
                self.assertEqual(unit['spoken'], original['units'][0]['spoken'])
                self.assertEqual(unit['mode'], 'reading')
                self.assertTrue(unit['reading_mode_verified'])
                self.assertFalse(unit['reading_mode_verification']['human_review_claimed'])
                self.assertEqual(result['review_unit_count'], 0)
                self.assertEqual(fp32_renderer.validate_unit(unit), [])
                assemble.validate_plan(result)

    def test_hanja_is_removed_only_as_an_exact_duplicate_gloss(self):
        line = '수업 연한(年限)을 설명합니다.'
        original = planning.build_plan('MS-072', line)
        unit = original['units'][0]
        pin = ('MS-072', unit['id'], digest(unit['spoken']))
        with patch.object(overlay, '_rules', return_value=(set(), set(), set(), {pin}, 'a'*64)):
            result = overlay.apply_to_plan(original)
            self.assertEqual(result['source'], line)
            self.assertEqual(result['units'][0]['spoken'], '수업 연한을 설명합니다.')
            self.assertEqual(result['review_unit_count'], 0)
            assemble.validate_plan(result)
            unrelated = planning.build_plan('MS-072', '수업 기간(年限)을 설명합니다.')
            self.assertIs(overlay.apply_to_plan(unrelated), unrelated)


if __name__ == '__main__':
    unittest.main()
