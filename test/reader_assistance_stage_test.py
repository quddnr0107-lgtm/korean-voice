import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import reader_assistance as R
import reader_assistance_stage as S


def document(partial=True):
    text = '정해진 기한 안에 제출해야 합니다.'
    return {'schema': 'reader-assistance.v1', 'partial': partial,
            'coverage': {'bodyRows': 2 if partial else 1,
                         'explainedRows': 1, 'unexplainedBodyRows': int(partial)},
            'entries': [{'id': 'reviewed-row', 'text': text,
                         'review': {'status': 'approved', 'textSha256': R.sha256(text)}}]}


def data_bytes(data):
    return json.dumps(data, ensure_ascii=False).encode()


class StagedSynthesis(unittest.TestCase):
    def setUp(self):
        self.recipe = json.loads((ROOT / 'reader-assistance/recipe.json').read_text())

    def test_partial_stage_preserves_exact_approved_audio_key_without_mutating_source(self):
        source = document()
        before = copy.deepcopy(source)
        stage = S.build_stage_plan(data_bytes(source), self.recipe)
        full = R.build_plan(data_bytes(document(partial=False)), self.recipe)
        self.assertEqual(stage['tasks'], full['tasks'])
        self.assertEqual(source, before)
        self.assertFalse(stage['completeEligible'])
        self.assertEqual(stage['unexplainedBodyRows'], 1)

    def test_even_a_full_source_stage_cannot_emit_a_complete_catalog(self):
        stage = S.build_stage_plan(data_bytes(document(partial=False)), self.recipe)
        key, task = next(iter(stage['tasks'].items()))
        mp3 = b'ID3' + bytes(512)
        metadata = {'sha256': R.sha256(mp3), 'textSha256': task['textSha256'], 'durationMs': 3000}
        packed, index, pack = R.build_pack([(key, metadata, mp3)], stage['recipeSha256'], stage['sourceDataSha256'])
        state = {'clips': {key: {**index['clips'][key], 'pack': pack}},
                 'packs': {pack: {'url': 'unused', 'sha256': pack, 'size': len(packed)}}}
        catalog = R.assemble(stage, state)
        self.assertFalse(catalog['complete'])
        with self.assertRaisesRegex(R.ContractError, 'production data branch'):
            R.publish_catalog(None, catalog)
        report = S.stage_report(stage, state)
        self.assertFalse(report['productionActivationAllowed'])
        self.assertTrue(report['allStagedClipsReady'])
        self.assertEqual(report['unexplainedBodyRows'], 0)

    def test_stage_rejects_unapproved_or_changed_text_instead_of_skipping_it(self):
        for change in ('status', 'text', 'hash'):
            source = document()
            entry = source['entries'][0]
            if change == 'status':
                entry['review']['status'] = 'blocked'
            elif change == 'text':
                entry['text'] += ' 변경'
            else:
                entry['review']['textSha256'] = '0' * 64
            with self.subTest(change=change), self.assertRaisesRegex(R.ContractError, 'not approved'):
                S.build_stage_plan(data_bytes(source), self.recipe)

    def test_inconsistent_coverage_and_non_boolean_completeness_are_rejected(self):
        for change in ('flag', 'missing', 'count', 'boolean-count'):
            source = document()
            if change == 'flag':
                source['partial'] = 'true'
            elif change == 'missing':
                source['coverage']['unexplainedBodyRows'] = 0
            elif change == 'count':
                source['coverage']['explainedRows'] += 1
            else:
                source['coverage']['explainedRows'] = True
            with self.subTest(change=change), self.assertRaises(R.ContractError):
                S.build_stage_plan(data_bytes(source), self.recipe)

    def test_stage_report_cannot_claim_ready_when_even_one_audio_clip_is_missing(self):
        stage = S.build_stage_plan(data_bytes(document()), self.recipe)
        summary = S.missing_summary(stage, {'clips': {}})
        self.assertEqual(summary['missing'], 1)
        with self.assertRaisesRegex(R.ContractError, 'Audio remains missing'):
            S.stage_report(stage, {'clips': {}, 'packs': {}})


if __name__ == '__main__':
    unittest.main()
