import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import reader_assistance as R
import reader_assistance_asr as A
import reader_assistance_verify as V


def source():
    texts = [f'{i + 1}일 이상 {i + 2}일 미만이면 예외를 제외하고 허가할 수 있습니다.' for i in range(80)]
    texts.append('학대피해아동쉼터에 5년 이상 보호된 사실을 확인받아야 합니다.')
    entries = [{'id': f'row-{i}', 'sourceIndex': 4384 if i == 80 else i,
                'text': text, 'review': {'status': 'approved', 'textSha256': R.sha256(text)}}
               for i, text in enumerate(texts)]
    return json.dumps({'schema': 'reader-assistance.v1', 'partial': True,
                       'coverage': {'bodyRows': 82, 'explainedRows': 81, 'unexplainedBodyRows': 1},
                       'entries': entries}, ensure_ascii=False).encode()


class IndependentVerification(unittest.TestCase):
    def setUp(self):
        self.data = source()
        self.recipe = json.loads((ROOT / 'reader-assistance/recipe.json').read_text())
        self.selection = V.make_selection(self.data, self.recipe)
        self.plan = V.validate_selection(self.selection, self.data, self.recipe)

    def test_selection_is_deterministic_includes_first_pack_and_lexical_risk(self):
        self.assertEqual(self.selection, V.make_selection(self.data, self.recipe))
        selected = {entry['audioKey'] for entry in self.selection['entries']}
        for bucket in '0123456789abcdef':
            self.assertTrue(set(key for key in self.plan['tasks'] if key[0] == bucket)
                            .intersection(set(list(key for key in self.plan['tasks'] if key[0] == bucket)[:2])) <= selected)
        self.assertTrue(any(entry['sourceIndex'] == 4384 and 'pilot-lexical-risk' in entry['reasons']
                            for entry in self.selection['entries']))

    def test_posthoc_sample_removal_or_changed_source_hash_is_rejected(self):
        removed = copy.deepcopy(self.selection)
        removed['entries'].pop()
        removed['selectedUniqueClips'] -= 1
        with self.assertRaisesRegex(R.ContractError, 'predeclared'):
            V.validate_selection(removed, self.data, self.recipe)
        changed = copy.deepcopy(self.selection)
        changed['sourceDataSha256'] = '0' * 64
        with self.assertRaisesRegex(R.ContractError, 'predeclared'):
            V.validate_selection(changed, self.data, self.recipe)

    def test_partial_report_preserves_unassessed_keys_and_cannot_approve(self):
        report = V.evidence_report(self.selection, {}, self.plan, {'clips': {}}, {}, 0, 0)
        self.assertFalse(report['coverage']['allSelectedClipsAssessed'])
        self.assertEqual(len(report['coverage']['missingAudioKeys']), self.selection['selectedUniqueClips'])
        self.assertEqual(report['coverage']['unassessedAudioKeys'], report['coverage']['missingAudioKeys'])
        self.assertFalse(report['approvalGranted'])
        self.assertFalse(report['directListeningCompleted'])
        self.assertFalse(report['coverage']['allCorpusAssessed'])

    def test_cached_result_must_bind_exact_audio_text_and_reproducible_assessment(self):
        key = self.selection['entries'][0]['audioKey']
        task = self.plan['tasks'][key]
        clip = {'sha256': '1' * 64}
        state = {'clips': {key: clip}}
        result = {**A.assess(task['text'], task['text']), 'audioKey': key,
                  'mp3Sha256': clip['sha256'], 'textSha256': task['textSha256'],
                  'durationMs': 1000, 'asrSeconds': 1}
        profile = {'modelRevision': V.MODEL_REVISION}
        report = V.evidence_report(self.selection, profile, self.plan, state, {key: result}, 1, 0)
        self.assertEqual(V.validate_cached(report, self.selection, profile, self.plan, state), {key: result})
        for field, value in [('mp3Sha256', '0' * 64), ('textSha256', '0' * 64),
                             ('canonicalCER', 0.1), ('status', 'approved')]:
            altered = copy.deepcopy(report)
            altered['results'][key][field] = value
            with self.subTest(field=field), self.assertRaises(R.ContractError):
                V.validate_cached(altered, self.selection, profile, self.plan, state)


if __name__ == '__main__':
    unittest.main()
