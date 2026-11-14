import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import dr_pronunciation as overlay
import planning
import rl_pronunciation
import runtime


def scope(plan):
    unit = plan['units'][0]
    norm = unit['normalization']
    return (plan['lesson_id'], 0, plan['source_sha256'], norm['input_sha256'],
            unit['spoken_sha256'])


class DirectivePronunciationTests(unittest.TestCase):
    def test_allowlisted_hangul_middot_preserves_source_modes_and_unrelated_flags(self):
        lexeme = sorted(overlay._rules()[0])[0]
        source = f'{lexeme}, 가상·미정 등의 용어와 Unknown 의미를 설명합니다.'
        old = planning.build_plan('DR-001', source)
        baseline = copy.deepcopy(old)
        plan = overlay.apply_to_plan(old)
        self.assertEqual(old, baseline)
        self.assertEqual(plan['source'], source)
        self.assertEqual(plan['paragraphs'], old['paragraphs'])
        unit = plan['units'][0]
        self.assertIn(lexeme.replace('·', ', '), unit['spoken'])
        self.assertIn('가상·미정', unit['spoken'])
        self.assertIn('normalization_english-word-review', unit['review'])
        for field in ('source', 'start_cp', 'end_cp', 'start_utf16', 'end_utf16', 'mode',
                      'reading_mode_verified', 'reading_mode_verification'):
            self.assertEqual(unit[field], old['units'][0][field])
        self.assertIs(overlay.apply_to_plan(plan), plan)

    def test_english_requires_exact_normalization_scope_and_complete_flag_tokens(self):
        source = '알파 Unknown Name 의미를 설명합니다.'
        plan = planning.build_plan('DR-001', source)
        spoken = plan['units'][0]['spoken']
        start = spoken.index('Unknown')
        edits = ((start, start + len('Unknown Name'), 'Unknown Name', '언노운 네임'),)
        current = scope(plan)
        variants = {
            'exact': {current: (('Unknown', 'Name'), edits)},
            'wrong_lesson': {('DR-002', *current[1:]): (('Unknown', 'Name'), edits)},
            'wrong_unit': {(current[0], 1, *current[2:]): (('Unknown', 'Name'), edits)},
            'wrong_source': {(*current[:2], '0'*64, *current[3:]): (('Unknown', 'Name'), edits)},
            'wrong_normalization': {(*current[:3], '0'*64, current[4]): (('Unknown', 'Name'), edits)},
            'wrong_tokens': {current: (('Unknown',), edits)},
        }
        for name, aliases in variants.items():
            with self.subTest(name=name), patch.object(
                overlay, '_rules', return_value=(frozenset(), {}, aliases, '0'*64)
            ):
                actual = overlay.apply_to_plan(plan)
                if name == 'exact':
                    self.assertIn('언노운 네임', actual['units'][0]['spoken'])
                    self.assertEqual(actual['review_unit_count'], 0)
                else:
                    self.assertIs(actual, plan)

    def test_contextual_separator_requires_exact_scope_including_offset(self):
        plan = planning.build_plan('DR-001', '가격 · 조건을 확인합니다.')
        unit = plan['units'][0]
        offset = unit['spoken'].index('·')
        exact = (*scope(plan)[:3], unit['normalization']['middot_input_sha256'],
                 unit['spoken_sha256'], offset, '·')
        for pin in (exact, (*exact[:-2], offset+1, '·'), (*exact[:-1], 'ㆍ')):
            with self.subTest(exact=pin == exact), patch.object(
                overlay, '_rules', return_value=(frozenset(), {pin: ','}, {}, '0'*64)
            ):
                actual = overlay.apply_to_plan(plan)
                self.assertEqual(actual['review_unit_count'], 0 if pin == exact else 1)
                self.assertEqual(actual is plan, pin != exact)

    def test_runtime_keeps_existing_tb_and_rl_fingerprints(self):
        variants = (('TB', None), ('RL', rl_pronunciation), ('DR', overlay))
        with patch.dict(runtime.os.environ, {'BORA_MODEL': 'public-fixture'}):
            for course, module in variants:
                with self.subTest(course=course), patch(
                    'single_pass_renderer.SinglePassRenderer',
                    return_value=SimpleNamespace(settings={'base': 'immutable'}, settings_sha256='base'),
                ):
                    obj = runtime.make_renderer({'max_new_tokens': 1100}, 'single_pass', course=course)
                    if module is None:
                        self.assertEqual(obj.settings, {'base': 'immutable'})
                        self.assertEqual(obj.settings_sha256, 'base')
                    else:
                        expected = {'base': 'immutable', 'course_pronunciation': {
                            'ruleset': module.RULESET, 'rules_sha256': module.rules_sha256(),
                            'implementation_sha256': runtime.sha(runtime.Path(module.__file__).read_bytes()),
                        }}
                        self.assertEqual(obj.settings, expected)
                        self.assertEqual(obj.settings_sha256, runtime.identity(expected))
        source = '정의와 적용 범위를 설명합니다.'
        for lesson_id in ('TB-003', 'RL-001', 'MS-001'):
            plan = planning.build_plan(lesson_id, source)
            self.assertIs(overlay.apply_to_plan(plan), plan)
        self.assertIsNone(runtime.course_pronunciation('eager', 'DR'))


if __name__ == '__main__':
    unittest.main()
