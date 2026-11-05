import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import reader_assistance as R
import reader_assistance_asr as A
import reader_assistance_subset as S
import reader_assistance_verify as V
import reader_assistance_final_verify as F
import reader_assistance_revision_verify as M
from reader_assistance_subset_test import fixture, encoded, state_for


class RevisionEvidence(unittest.TestCase):
    def setUp(self):
        self.recipe = json.loads((ROOT / 'reader-assistance/recipe.json').read_text())
        self.old_data, self.policy = fixture()
        self.old_bytes = encoded(self.old_data)
        self.old_plan = S.build_subset_plan(self.old_bytes, self.recipe, self.policy)
        self.old_state = state_for(self.old_plan)
        self.old_catalog = S.assemble_subset(self.old_plan, self.old_state, self.policy)
        stage = copy.deepcopy(self.old_data)
        stage['entries'].pop()
        stage['coverage'].update(explainedRows=1, unexplainedBodyRows=2)
        self.stage_bytes = encoded(stage)
        stage_plan = V.plan_for(self.stage_bytes, self.recipe)
        self.baseline = V.evidence_report(V.make_selection(self.stage_bytes, self.recipe), {}, stage_plan,
                                         self.old_state, {}, 0, 0)
        selection = F.make_selection(self.old_bytes, self.stage_bytes, self.recipe, self.policy, self.baseline)
        self.old_results = {}
        for key, task in self.old_plan['tasks'].items():
            self.old_results[key] = {**A.assess(task['text'], task['text'].replace('합니다', '함니다')),
                                    'audioKey': key, 'mp3Sha256': self.old_state['clips'][key]['sha256'],
                                    'textSha256': task['textSha256'], 'durationMs': 3000, 'asrSeconds': 1}
        self.old_report = V.evidence_report(selection, {}, self.old_plan, self.old_state, self.old_results, 0, 0)
        self.data = copy.deepcopy(self.old_data)
        changed = self.data['entries'][1]
        changed['text'] = '정당한 사유가 있으면 제외할 수 있습니다.'
        changed['textSha256'] = changed['review']['textSha256'] = R.sha256(changed['text'])
        self.data_bytes = encoded(self.data)
        self.plan = S.build_subset_plan(self.data_bytes, self.recipe, self.policy)
        self.catalog = {'clips': {key: dict(self.old_state['clips'][key]) for key in self.plan['tasks'] if key in self.old_state['clips']}}
        self.selection = M.make_selection(self.data_bytes, self.stage_bytes, self.recipe, self.policy,
                                          self.baseline, self.old_plan, self.old_report)

    def test_all_remaining_previous_sample_and_every_changed_text_are_retained(self):
        keys = {entry['audioKey'] for entry in self.selection['entries']}
        previous = {entry['audioKey'] for entry in self.old_report['selection']['entries']}
        current = set(self.plan['tasks'])
        self.assertTrue(previous.intersection(current) <= keys)
        self.assertTrue(current - set(self.old_plan['tasks']) <= keys)
        self.assertEqual(set(self.selection['removedPriorSampleKeys']), previous - current)
        self.assertEqual(self.selection['newUniqueClips'], 1)
        self.assertEqual(self.selection, M.make_selection(self.data_bytes, self.stage_bytes, self.recipe, self.policy,
                                                        self.baseline, self.old_plan, self.old_report))

    def test_only_unchanged_media_reuses_results_and_disagreements_stay_disagreements(self):
        migrated = M.migrate_results(self.old_results, self.old_catalog, self.plan, self.catalog, self.selection)
        self.assertEqual(set(migrated), set(self.old_plan['tasks']) & set(self.plan['tasks']))
        self.assertTrue(all(result['status'] == 'review_required' for result in migrated.values()))
        self.assertTrue(all(result['reusedFromReportGitBlob'] == M.PRIOR['report.json'] for result in migrated.values()))

    def test_changed_text_or_media_digest_never_inherits_an_old_transcript(self):
        key = next(iter(self.catalog['clips']))
        for mutation in ('target', 'text-hash', 'audio'):
            prior, catalog = copy.deepcopy(self.old_results), copy.deepcopy(self.catalog)
            if mutation == 'target': prior[key]['target'] += ' 바뀜'
            elif mutation == 'text-hash': prior[key]['textSha256'] = '0' * 64
            else: catalog['clips'][key]['sha256'] = '0' * 64
            with self.subTest(mutation=mutation), self.assertRaises(R.ContractError):
                M.migrate_results(prior, self.old_catalog, self.plan, catalog, self.selection)

    def test_live_inventory_requires_every_declared_key_pack_and_identity(self):
        result = M.validate_live_catalog(self.old_plan, self.old_catalog, self.old_state)
        self.assertTrue(result['allEntryBindingsChecked'])
        self.assertFalse(result['allAudioBytesDownloaded'])
        self.assertFalse(result['allPronunciationsAssessed'])
        for mutation in ('missing-key', 'missing-pack', 'text', 'audio', 'length'):
            state = copy.deepcopy(self.old_state)
            key = next(iter(state['clips']))
            if mutation == 'missing-key': state['clips'].pop(key)
            elif mutation == 'missing-pack': state['packs'].clear()
            elif mutation == 'text': state['clips'][key]['textSha256'] = '0' * 64
            elif mutation == 'audio': state['clips'][key]['sha256'] = '0' * 64
            else: state['clips'][key]['length'] += 1
            with self.subTest(mutation=mutation), self.assertRaises(R.ContractError):
                M.validate_live_catalog(self.old_plan, self.old_catalog, state)

    def test_immutable_blob_fetch_never_trusts_a_matching_reported_sha_without_body_check(self):
        raw = b'{"evidence":true}\n'
        sha = R.git_blob(raw)
        class Client:
            def __init__(self, content): self.content, self.calls = content, 0
            def request(self, method, path):
                self.calls += 1
                assert method == 'GET'
                return {'sha': sha, 'encoding': 'base64', 'content': base64.b64encode(self.content).decode()}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            good = Client(raw)
            self.assertEqual(M.materialize_blob(good, sha, path), raw)
            self.assertEqual(M.materialize_blob(good, sha, path), raw)
            self.assertEqual(good.calls, 1)
            path.unlink()
            with self.assertRaisesRegex(R.ContractError, 'checksum'):
                M.materialize_blob(Client(b'{"evidence":false}\n'), sha, path)


if __name__ == '__main__':
    unittest.main()
