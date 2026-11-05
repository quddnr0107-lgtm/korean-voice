import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import reader_assistance as R
import reader_assistance_asr as A
import reader_assistance_verify as V
import reader_assistance_crosscheck as C
import reader_assistance_subset as S
import reader_assistance_final_verify as F
import reader_assistance_revision_verify as M
from reader_assistance_subset_test import fixture, encoded, state_for


class SecondModelEvidence(unittest.TestCase):
    def setUp(self):
        self.plan = {'sourceDataSha256': 'd' * 64, 'recipeSha256': 'e' * 64, 'tasks': {}}
        self.selection = {'entries': [], 'selectedUniqueClips': 3}
        self.state, results = {'clips': {}}, {}
        for index, text in enumerate(('정당한 사유가 있으면 제외합니다.', '벌금은 1년 이하입니다.', '허가할 수 없습니다.')):
            key = str(index) * 64
            self.plan['tasks'][key] = {'text': text, 'textSha256': R.sha256(text)}
            self.state['clips'][key] = {'sha256': R.sha256('mp3-' + key)}
            self.selection['entries'].append({'audioKey': key})
            transcript = text if index == 0 else text.replace('1년', '2년').replace('없습니다', '있습니다')
            results[key] = {**A.assess(text, transcript), 'audioKey': key,
                            'mp3Sha256': self.state['clips'][key]['sha256'], 'textSha256': R.sha256(text),
                            'durationMs': 3000, 'asrSeconds': 1}
        self.profile = {'modelRevision': V.MODEL_REVISION}
        self.primary = V.evidence_report(self.selection, self.profile, self.plan, self.state, results, 3, 0)

    def test_every_disagreement_is_selected_without_rewriting_primary_decisions(self):
        before = copy.deepcopy(self.primary)
        keys = C.disagreement_keys(self.primary, self.selection, self.profile, self.plan, self.state)
        self.assertEqual(keys, ['1' * 64, '2' * 64])
        self.assertEqual(self.primary, before)

    def test_incomplete_or_conveniently_modified_primary_evidence_is_rejected(self):
        for mutation in ('coverage', 'missing', 'status', 'counts', 'mp3'):
            primary = copy.deepcopy(self.primary)
            if mutation == 'coverage': primary['coverage']['allSelectedClipsAssessed'] = False
            elif mutation == 'missing': primary['results'].pop('1' * 64)
            elif mutation == 'status': primary['results']['1' * 64]['status'] = 'normalized_match'
            elif mutation == 'counts': primary['counts']['review_required'] = 0
            else: primary['results']['1' * 64]['mp3Sha256'] = 'f' * 64
            with self.subTest(mutation=mutation), self.assertRaises(R.ContractError):
                C.disagreement_keys(primary, self.selection, self.profile, self.plan, self.state)

    def test_secondary_match_remains_evidence_without_approval_and_is_bound_to_both_transcripts(self):
        keys = C.disagreement_keys(self.primary, self.selection, self.profile, self.plan, self.state)
        key = keys[0]
        contract = {'sha256': 'a' * 64}
        profile = {'modelRevision': C.MODEL_REVISION}
        report = {'schema': C.REPORT_SCHEMA, 'approvalGranted': False, 'primaryReport': contract,
                  'asrProfile': profile, 'selectedDisagreementKeys': keys,
                  'results': {key: {'audioKey': key, 'mp3Sha256': self.state['clips'][key]['sha256'],
                                   'textSha256': self.plan['tasks'][key]['textSha256'], 'primary': self.primary['results'][key],
                                   'secondary': A.assess(self.plan['tasks'][key]['text'], self.plan['tasks'][key]['text'])}}}
        results = C.validate_reuse(report, contract, profile, keys, self.primary, self.plan, self.state)
        self.assertEqual(results[key]['secondary']['status'], 'normalized_match')
        self.assertEqual(results[key]['primary']['status'], 'review_required')
        for mutation in ('approval', 'secondary', 'primary', 'selection'):
            bad = copy.deepcopy(report)
            if mutation == 'approval': bad['approvalGranted'] = True
            elif mutation == 'secondary': bad['results'][key]['secondary']['transcript'] = '2년 이하'
            elif mutation == 'primary': bad['results'][key]['primary']['transcript'] = '고침'
            else: bad['selectedDisagreementKeys'].pop()
            with self.subTest(mutation=mutation), self.assertRaises(R.ContractError):
                C.validate_reuse(bad, contract, profile, keys, self.primary, self.plan, self.state)


class CorrectedPrimaryEvidence(unittest.TestCase):
    def setUp(self):
        self.recipe = json.loads((ROOT / 'reader-assistance/recipe.json').read_text())
        old_data, self.policy = fixture()
        self.old_bytes = encoded(old_data)
        self.old_plan = S.build_subset_plan(self.old_bytes, self.recipe, self.policy)
        old_state = state_for(self.old_plan)
        old_catalog = S.assemble_subset(self.old_plan, old_state, self.policy)
        stage = copy.deepcopy(old_data)
        stage['entries'].pop()
        stage['coverage'].update(explainedRows=1, unexplainedBodyRows=2)
        self.stage_bytes = encoded(stage)
        stage_plan = V.plan_for(self.stage_bytes, self.recipe)
        baseline_profile = {'modelRepository': V.MODEL_REPOSITORY, 'modelRevision': V.MODEL_REVISION,
                            'targetPromptUsed': False}
        for key, path in {'judgeGitBlob': 'research/judge.py', 'koVoiceGitBlob': 'public/ko-voice.js',
                          'assessmentGitBlob': 'tools/reader_assistance_asr.py',
                          'verificationGitBlob': 'tools/reader_assistance_verify.py'}.items():
            baseline_profile[key] = R.git_blob((ROOT / path).read_bytes())
        self.baseline = V.evidence_report(V.make_selection(self.stage_bytes, self.recipe), baseline_profile,
                                         stage_plan, old_state, {}, 0, 0)
        old_selection = F.make_selection(self.old_bytes, self.stage_bytes, self.recipe, self.policy, self.baseline)
        old_results = self.results_for(self.old_plan, old_catalog)
        self.old_report = V.evidence_report(old_selection, M.profile_for(self.baseline), self.old_plan,
                                           old_catalog, old_results, 0, 0)
        self.previous = {'data.json': self.old_bytes, 'audio-manifest.json': encoded(old_catalog),
                         'policy.json': encoded(self.policy), 'report.json': encoded(self.old_report)}
        for name, value in {'PRIOR': {name: R.git_blob(raw) for name, raw in self.previous.items()},
                            'PRIOR_SOURCE_SHA': R.sha256(self.old_bytes)}.items():
            pin = patch.object(M, name, value)
            pin.start()
            self.addCleanup(pin.stop)
        data = copy.deepcopy(old_data)
        changed = data['entries'][1]
        changed['text'] = '정당한 사유가 있으면 제외할 수 있습니다.'
        changed['textSha256'] = changed['review']['textSha256'] = R.sha256(changed['text'])
        self.data_bytes = encoded(data)
        self.plan = S.build_subset_plan(self.data_bytes, self.recipe, self.policy)
        self.catalog = S.assemble_subset(self.plan, state_for(self.plan), self.policy)
        self.catalog_bytes = encoded(self.catalog)
        self.selection = M.make_selection(self.data_bytes, self.stage_bytes, self.recipe, self.policy,
                                           self.baseline, self.old_plan, self.old_report)
        self.profile = M.profile_for(self.baseline, revision=True)
        self.primary = V.evidence_report(self.selection, self.profile, self.plan, self.catalog,
                                        self.results_for(self.plan, self.catalog), 0, 0)
        self.primary['catalogGitBlob'] = R.git_blob(self.catalog_bytes)

    @staticmethod
    def results_for(plan, catalog):
        return {key: {**A.assess(task['text'], task['text'].replace('합니다', '함니다')),
                      'audioKey': key, 'mp3Sha256': catalog['clips'][key]['sha256'],
                      'textSha256': task['textSha256'], 'durationMs': 3000, 'asrSeconds': 1}
                for key, task in plan['tasks'].items()}

    def validate(self, primary=None, previous=None, catalog_bytes=None):
        return C.validate_primary_selection(self.primary if primary is None else primary,
                    self.data_bytes, self.stage_bytes, self.recipe, self.policy, self.baseline,
                    previous=self.previous if previous is None else previous,
                    catalog_bytes=self.catalog_bytes if catalog_bytes is None else catalog_bytes)

    def test_corrected_sample_is_reproduced_and_all_disagreements_remain_visible(self):
        before = copy.deepcopy(self.primary)
        plan, selection = self.validate()
        self.assertEqual(plan, self.plan)
        self.assertEqual(selection, self.selection)
        keys = C.disagreement_keys(self.primary, selection, self.profile, plan, self.catalog)
        self.assertEqual(keys, sorted(key for key, result in self.primary['results'].items()
                                      if result['status'] == 'review_required'))
        self.assertTrue(keys)
        self.assertEqual(self.primary, before)
        self.assertFalse(self.primary['approvalGranted'])

    def test_sample_deletions_stale_catalog_or_changed_history_cannot_use_revision_path(self):
        for mutation in ('drop-sample', 'drop-new-text', 'source', 'catalog-pin', 'catalog-bytes',
                         'stale-catalog', 'previous-report'):
            primary, previous, catalog_bytes = copy.deepcopy(self.primary), dict(self.previous), self.catalog_bytes
            if mutation == 'drop-sample': primary['selection']['entries'].pop(0)
            elif mutation == 'drop-new-text':
                new = set(self.plan['tasks']) - set(self.old_plan['tasks'])
                primary['selection']['entries'] = [item for item in primary['selection']['entries'] if item['audioKey'] not in new]
            elif mutation == 'source': primary['sourceDataSha256'] = '0' * 64
            elif mutation == 'catalog-pin': primary['catalogGitBlob'] = '0' * 40
            elif mutation == 'catalog-bytes': catalog_bytes += b'\n'
            elif mutation == 'stale-catalog':
                catalog_bytes = previous['audio-manifest.json']
                primary['catalogGitBlob'] = R.git_blob(catalog_bytes)
            else: previous['report.json'] += b'\n'
            with self.subTest(mutation=mutation), self.assertRaises(R.ContractError):
                self.validate(primary, previous, catalog_bytes)

    def test_legacy_sample_still_validates_and_revision_requires_pinned_context(self):
        plan, selection = C.validate_primary_selection(self.old_report, self.old_bytes, self.stage_bytes,
                                                       self.recipe, self.policy, self.baseline)
        self.assertEqual(plan, self.old_plan)
        self.assertEqual(selection, self.old_report['selection'])
        with self.assertRaisesRegex(R.ContractError, 'pinned previous context'):
            C.validate_primary_selection(self.primary, self.data_bytes, self.stage_bytes,
                                         self.recipe, self.policy, self.baseline)


if __name__ == '__main__':
    unittest.main()
