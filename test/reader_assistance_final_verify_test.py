import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import reader_assistance as R
import reader_assistance_asr as A
import reader_assistance_subset as S
import reader_assistance_verify as V
import reader_assistance_final_verify as F
from reader_assistance_subset_test import fixture, encoded, state_for


class FinalScopeVerification(unittest.TestCase):
    def setUp(self):
        self.recipe = json.loads((ROOT / 'reader-assistance/recipe.json').read_text())
        self.data, self.policy = fixture()
        self.data_bytes = encoded(self.data)
        self.plan = S.build_subset_plan(self.data_bytes, self.recipe, self.policy)
        self.state = state_for(self.plan)
        stage = copy.deepcopy(self.data)
        stage['entries'].pop()
        stage['coverage'].update(explainedRows=1, unexplainedBodyRows=2)
        self.stage_bytes = encoded(stage)
        self.stage_plan = V.plan_for(self.stage_bytes, self.recipe)
        selection = V.make_selection(self.stage_bytes, self.recipe)
        key, task = next(iter(self.stage_plan['tasks'].items()))
        result = {**A.assess(task['text'], task['text'].replace('합니다', '함니다')), 'audioKey': key,
                  'mp3Sha256': self.state['clips'][key]['sha256'], 'textSha256': task['textSha256'],
                  'durationMs': 3000, 'asrSeconds': 1}
        self.profile = {'modelRevision': V.MODEL_REVISION}
        self.baseline = V.evidence_report(selection, self.profile, self.stage_plan, self.state, {key: result}, 1, 0)
        self.selection = F.make_selection(self.data_bytes, self.stage_bytes, self.recipe, self.policy, self.baseline)

    def test_final_selection_adds_new_reviewed_clips_and_retains_all_prior_evidence(self):
        expected = F.make_selection(self.data_bytes, self.stage_bytes, self.recipe, self.policy, self.baseline)
        self.assertEqual(self.selection, expected)
        self.assertEqual(self.selection['corpusApprovedEntries'], 2)
        self.assertEqual(self.selection['newReviewedUniqueClips'], 1)
        self.assertEqual(self.selection['selectedUniqueClips'], 2)
        prior = next(iter(self.baseline['results']))
        item = next(entry for entry in self.selection['entries'] if entry['audioKey'] == prior)
        self.assertIn('prior-assessed-staged-clip', item['reasons'])
        self.assertTrue(any('new-reviewed-numeric-or-modal' in entry['reasons'] for entry in self.selection['entries']))

    def test_changed_selection_or_mutated_baseline_cannot_redefine_the_sample(self):
        bad = copy.deepcopy(self.selection)
        bad['entries'].pop()
        bad['selectedUniqueClips'] -= 1
        with self.assertRaisesRegex(R.ContractError, 'post-hoc'):
            F.validate_selection(bad, self.data_bytes, self.stage_bytes, self.recipe, self.policy, self.baseline)
        changed = copy.deepcopy(self.baseline)
        changed['results'].clear()
        with self.assertRaisesRegex(R.ContractError, 'post-hoc'):
            F.validate_selection(self.selection, self.data_bytes, self.stage_bytes, self.recipe, self.policy, changed)

    def test_evidence_reuse_preserves_disagreements_and_requires_same_model_text_and_mp3(self):
        profile = {**self.profile, 'finalVerificationGitBlob': 'a' * 40}
        reused = F.reuse_baseline(self.baseline, self.selection, profile, self.stage_plan, self.plan, self.state)
        self.assertEqual(len(reused), 1)
        self.assertEqual(next(iter(reused.values()))['status'], 'review_required')
        for mutation in ('audio', 'text', 'model', 'decision'):
            state, baseline, current = copy.deepcopy(self.state), copy.deepcopy(self.baseline), copy.deepcopy(profile)
            key = next(iter(baseline['results']))
            if mutation == 'audio': state['clips'][key]['sha256'] = '0' * 64
            elif mutation == 'text': baseline['results'][key]['target'] += ' 변경'
            elif mutation == 'model': current['modelRevision'] = '0' * 40
            else: baseline['results'][key]['status'] = 'normalized_match'
            with self.subTest(mutation=mutation), self.assertRaises(R.ContractError):
                F.reuse_baseline(baseline, self.selection, current, self.stage_plan, self.plan, state)


if __name__ == '__main__':
    unittest.main()
