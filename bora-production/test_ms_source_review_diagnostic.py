import ast
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import ms_source_review_diagnostic as diag


def sample(ident, *, flagged=True):
    body = '이 내용은 로그로 내보내지 않는다. 가상ㆍ미정의'
    return dict(lesson_id=ident, source=body, source_sha256=diag.sha(body),
                paragraphs=[dict(id=ident+':p0001', source=body)],
                units=[dict(paragraph_id=ident+':p0001', source=body, spoken='private output',
                            review=['reading_mode_ambiguous_legal_fragment','do not print raw flag'] if flagged else [],
                            normalization=dict(middot_input_sha256=diag.sha(body),
                                               middot_review_details=[dict(original='가상ㆍ미정의',
                                                   source_start=21, source_end=27,
                                                   reason='unreviewed_middle_dot_lexeme')]))])


REQ = dict(request_id=diag.REQUEST_ID, course='MS', retry_blocked=False)


class TestReview(unittest.TestCase):
    def test_opt_in_exact_scope_and_no_profile_spill(self):
        self.assertFalse(diag.enabled({}, diag.IDS, 'single_pass'))
        self.assertTrue(diag.enabled(REQ, diag.IDS, 'single_pass'))
        for request, ids, profile in [
            (REQ, diag.IDS[:-1], 'single_pass'), (REQ, diag.IDS, 'eager'),
            (dict(REQ, course='DR'), diag.IDS, 'single_pass'),
            (dict(REQ, selected_shards=[]), diag.IDS, 'single_pass'),
            (dict(REQ, assembly_only=False), diag.IDS, 'single_pass'),
            (dict(REQ, retry_blocked=True), diag.IDS, 'single_pass'),
        ]:
            with self.assertRaisesRegex(ValueError, 'scope'):
                diag.enabled(request, ids, profile)

    def test_never_emits_plaintext_digests_offsets_or_raw_flags(self):
        ident = diag.IDS[0]
        plan = sample(ident)
        source = plan['source']
        pin = (ident, diag.sha(source+'\n'), ident+':p0001', diag.sha(source))
        lexeme = diag.sha('가상·미정의')
        row = diag.record(plan, ident, ({lexeme}, set(), {pin}, set(), None))
        self.assertEqual(row['reference'], 'known_reference_match')
        self.assertEqual(row['classes']['fragment_exact_match'], 1)
        self.assertEqual(row['classes']['middot_known_lexeme'], 1)
        self.assertEqual(row['classes']['other_review'], 1)
        self.assertEqual(set(row), {'lesson_id','reference','review_unit_count','classes'})
        output = json.dumps(row, ensure_ascii=False)
        for text in (source, '가상ㆍ미정의', 'private output', 'do not print raw flag',
                     lexeme, *pin[1:], 'offset', 'source_sha', 'paragraph_id'):
            self.assertNotIn(text, output)
        old = (ident, '0'*64, pin[2], pin[3])
        row = diag.record(plan, ident, (set(), set(), {old}, set(), None))
        self.assertEqual(row['classes']['fragment_same_paragraph_different_source'], 1)
        self.assertEqual(row['classes']['middot_unknown_lexeme'], 1)
        with self.assertRaisesRegex(ValueError, 'identity'):
            diag.record(dict(plan, source_sha256='0'*64), ident, (set(),)*5)

    def test_real_public_plan_cannot_write_matrix_manifest_or_render_even_if_clear(self):
        tree = ast.parse(Path(__file__).with_name('runtime.py').read_text())
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == 'public_plan')
        def enforce(value, code):
            if not value:
                raise ValueError(code)
        scope = dict(config=lambda profile: ({}, {}, REQ, diag.IDS), check=enforce,
                     HASH=diag.HASH, protected_keys=lambda profile: (object(), b''),
                     course_plan=lambda ident, *args: (sample(ident, flagged=False), None), json=json)
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'runtime.py', 'exec'), scope)
        stream = io.StringIO()
        with patch.dict('sys.modules', {'ms_pronunciation': SimpleNamespace(_rules=lambda: (set(),)*5)}), \
             contextlib.redirect_stdout(stream), self.assertRaisesRegex(
                ValueError, 'ms_source_review_diagnostic_complete_no_render'):
            scope['public_plan'](SimpleNamespace(settings_sha='0'*64), 'single_pass')
        rows = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(tuple(row['lesson_id'] for row in rows), diag.IDS)
        self.assertTrue(all(row['review_unit_count'] == 0 for row in rows))


if __name__ == '__main__':
    unittest.main()
