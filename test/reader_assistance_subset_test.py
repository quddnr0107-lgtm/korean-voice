import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import reader_assistance as R
import reader_assistance_subset as S


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def fixture():
    corpus = R.sha256('원본 명부')
    held = {'id': R.sha256('held'), 'sourceIndex': 6384, 'originalSha256': R.sha256('각 2회 10발'),
            'reason': '공식 원문 자체의 수치 관계가 모호하여 보조설명을 승인하지 않음.'}
    held_review = {'status': 'blocked', 'sourceId': held['id'], 'sourceIndex': held['sourceIndex'],
                   'originalSha256': held['originalSha256'], 'textSha256': R.sha256('승인하지 않은 초안'),
                   'sourceDataSha256': corpus, 'author': 'author', 'reviewer': 'reviewer',
                   'unresolvedIssues': [held['reason']], 'resolutions': []}
    held['textSha256'] = held_review['textSha256']
    held['reviewRecordSha256'] = R.sha256(json.dumps(held_review, ensure_ascii=False, separators=(',', ':')))
    excluded = {**{key: value for key, value in held.items() if key != 'id'}, 'sourceId': held['id'],
                'original': '각 2회 10발', 'status': 'blocked', 'unresolvedIssues': [held['reason']], 'review': held_review}
    entries = []
    for index, text in ((1, '정해진 기한 안에 신고해야 합니다.'), (9, '정당한 사유가 있으면 제외합니다.')):
        identity, original = R.sha256('source-' + str(index)), R.sha256('original-' + str(index))
        entries.append({'id': identity, 'sourceId': identity, 'sourceIndex': index, 'text': text, 'textSha256': R.sha256(text),
                        'original': 'original-' + str(index), 'originalSha256': original,
                        'review': {'status': 'approved', 'textSha256': R.sha256(text),
                                   'sourceId': identity, 'sourceIndex': index, 'originalSha256': original,
                                   'sourceDataSha256': corpus, 'unresolvedIssues': [], 'author': 'author', 'reviewer': 'reviewer'}})
    ledger = [[e['sourceIndex'], e['id'], e['review']['originalSha256']] for e in entries]
    ledger.append([held['sourceIndex'], held['id'], held['originalSha256']])
    policy = {'schema': S.POLICY_SCHEMA, 'kind': 'reviewed-subset', 'sourceCorpusSha256': corpus,
              'sourceRowsSha256': S.source_ledger_digest(ledger), 'bodyRows': 3, 'approvedEntries': 2,
              'excluded': [held]}
    document = {'schema': 'reader-assistance.v1', 'partial': True, 'exclusions': [excluded],
                'sourceBasis': {'sourceDataSha256': corpus}, 'coverage': {'bodyRows': 3, 'explainedRows': 2,
                'unexplainedBodyRows': 1}, 'entries': entries}
    return document, policy


def state_for(plan):
    rows = []
    for number, (key, task) in enumerate(plan['tasks'].items()):
        mp3 = b'ID3' + bytes([number]) * 512
        rows.append((key, {'sha256': R.sha256(mp3), 'textSha256': task['textSha256'], 'durationMs': 3000}, mp3))
    packed, index, pack = R.build_pack(rows, plan['recipeSha256'], plan['sourceDataSha256'])
    return {'packs': {pack: {'sha256': pack, 'size': len(packed), 'url': S.PACK_BASE + pack + '.bin'}},
            'clips': {key: {**clip, 'pack': pack} for key, clip in index['clips'].items()}}


class ReviewedSubset(unittest.TestCase):
    def setUp(self):
        self.recipe = json.loads((ROOT / 'reader-assistance/recipe.json').read_text())
        self.data, self.policy = fixture()
        self.bytes = encoded(self.data)
        self.plan = S.build_subset_plan(self.bytes, self.recipe, self.policy)
        self.state = state_for(self.plan)

    def test_exact_scope_and_whole_original_ledger_are_bound_without_completeness_claim(self):
        catalog = S.assemble_subset(self.plan, self.state, self.policy)
        result = S.attach_subset(self.bytes, self.recipe, catalog, self.policy)
        self.assertFalse(catalog['complete'])
        self.assertFalse(result['audio']['complete'])
        self.assertTrue(result['partial'])
        self.assertTrue(result['audio']['reviewedScopeReady'])
        self.assertEqual(result['releaseScope'], self.policy)
        self.assertEqual(result['audio']['releaseScope'], self.policy)
        self.assertEqual(result['coverage']['unexplainedBodyRows'], 1)
        self.assertEqual(result['audio']['sourceDataSha256'], R.sha256(self.bytes))
        self.assertEqual(result['audio']['readyEntries'], 2)
        self.assertNotIn(self.policy['excluded'][0]['id'], {entry['id'] for entry in catalog['entries']})
        for entry in result['entries']:
            self.assertEqual(entry['audio']['textSha256'], R.sha256(entry['text']))
            self.assertEqual(entry['audio']['audioKey'], R.audio_key(self.recipe, entry['text']))

    def test_legacy_full_corpus_gate_still_refuses_subset_source_and_catalog(self):
        catalog = S.assemble_subset(self.plan, self.state, self.policy)
        with self.assertRaisesRegex(R.ContractError, 'complete reviewed coverage'):
            R.build_plan(self.bytes, self.recipe)
        with self.assertRaisesRegex(R.ContractError, 'complete reviewed coverage'):
            R.attach(self.bytes, self.recipe, catalog)
        with self.assertRaisesRegex(R.ContractError, 'production data branch'):
            R.publish_catalog(None, catalog)
        ordinary = R.assemble(self.plan, self.state)
        with self.assertRaisesRegex(R.ContractError, 'explicitly ready reviewed-subset'):
            S.attach_subset(self.bytes, self.recipe, ordinary, self.policy)

    def test_missing_substituted_duplicate_or_unresolved_source_cannot_expand_the_exclusion(self):
        for mutation in ('missing', 'replace-id', 'original', 'duplicate-index', 'excluded-index', 'excluded-id',
                         'review-corpus', 'unresolved', 'author', 'unapproved', 'changed-text'):
            data = copy.deepcopy(self.data)
            entry = data['entries'][0]
            if mutation == 'missing':
                data['entries'].pop()
            elif mutation == 'replace-id':
                entry['id'] = entry['sourceId'] = entry['review']['sourceId'] = R.sha256('different-source')
            elif mutation == 'original':
                entry['review']['originalSha256'] = R.sha256('modified original')
            elif mutation == 'duplicate-index':
                entry['sourceIndex'] = entry['review']['sourceIndex'] = data['entries'][1]['sourceIndex']
            elif mutation == 'excluded-index':
                entry['sourceIndex'] = entry['review']['sourceIndex'] = self.policy['excluded'][0]['sourceIndex']
            elif mutation == 'excluded-id':
                entry['id'] = entry['sourceId'] = entry['review']['sourceId'] = self.policy['excluded'][0]['id']
            elif mutation == 'review-corpus':
                entry['review']['sourceDataSha256'] = R.sha256('other corpus')
            elif mutation == 'unresolved':
                entry['review']['unresolvedIssues'] = ['unresolved condition']
            elif mutation == 'author':
                entry['review']['author'] = ''
            elif mutation == 'unapproved':
                entry['review']['status'] = 'blocked'
            else:
                entry['text'] += ' 바뀜'
            with self.subTest(mutation=mutation), self.assertRaises(R.ContractError):
                S.build_subset_plan(encoded(data), self.recipe, self.policy)

    def test_extra_hold_boolean_counts_or_unpinned_scope_cannot_pass(self):
        for mutation in ('extra-hold', 'reason', 'partial', 'count', 'source-corpus', 'ledger-digest'):
            data = copy.deepcopy(self.data)
            if mutation == 'extra-hold':
                data['exclusions'].append(copy.deepcopy(data['exclusions'][0]))
            elif mutation == 'reason':
                data['exclusions'][0]['reason'] = '다른 이유'
            elif mutation == 'partial':
                data['partial'] = False
            elif mutation == 'count':
                data['coverage']['unexplainedBodyRows'] = True
            elif mutation == 'source-corpus':
                data['sourceBasis']['sourceDataSha256'] = '0' * 64
            else:
                data['releaseScope'] = {**self.policy, 'sourceRowsSha256': '0' * 64}
            with self.subTest(mutation=mutation), self.assertRaises(R.ContractError):
                S.build_subset_plan(encoded(data), self.recipe, self.policy)

    def test_blocked_review_cannot_be_rewritten_or_given_explanation_audio(self):
        for mutation in ('record', 'status', 'text', 'audio', 'issue', 'text-hash'):
            data = copy.deepcopy(self.data)
            held = data['exclusions'][0]
            if mutation == 'record': held['review']['author'] = 'different author'
            elif mutation == 'status': held['review']['status'] = 'approved'
            elif mutation == 'text': held['text'] = '승인되지 않은 설명'
            elif mutation == 'audio': held['audio'] = {'voice': 'f1'}
            elif mutation == 'issue': held['review']['unresolvedIssues'] = []
            else: held['review']['textSha256'] = '0' * 64
            with self.subTest(mutation=mutation), self.assertRaises(R.ContractError):
                S.build_subset_plan(encoded(data), self.recipe, self.policy)

    def test_incomplete_or_rebound_audio_cannot_be_marked_ready(self):
        missing = copy.deepcopy(self.state)
        missing['clips'].pop(next(iter(missing['clips'])))
        with self.assertRaisesRegex(R.ContractError, 'Audio remains missing'):
            S.assemble_subset(self.plan, missing, self.policy)
        good = S.assemble_subset(self.plan, self.state, self.policy)
        for mutation in ('complete', 'ready', 'voice', 'recipe', 'source', 'entry', 'text', 'range', 'pack-url', 'spare-pack'):
            bad = copy.deepcopy(good)
            key, clip = next(iter(bad['clips'].items()))
            if mutation == 'complete': bad['complete'] = True
            elif mutation == 'ready': bad['readyEntries'] -= 1
            elif mutation == 'voice': bad['voice'] = 'f2'
            elif mutation == 'recipe': bad['recipeSha256'] = '0' * 64
            elif mutation == 'source': bad['sourceDataSha256'] = '0' * 64
            elif mutation == 'entry': bad['entries'][0]['audioKey'] = bad['entries'][1]['audioKey']
            elif mutation == 'text': clip['textSha256'] = '0' * 64
            elif mutation == 'range': clip['offset'] = bad['packs'][clip['pack']]['size']
            elif mutation == 'pack-url': bad['packs'][clip['pack']]['url'] = 'https://example.test/audio.bin'
            else:
                unused = R.sha256('unused pack')
                bad['packs'][unused] = {'sha256': unused, 'size': 1000, 'url': S.PACK_BASE + unused + '.bin'}
            with self.subTest(mutation=mutation), self.assertRaises(R.ContractError):
                S.attach_subset(self.bytes, self.recipe, bad, self.policy)

    def test_identical_text_recipe_reuses_the_staged_audio_identity_after_source_migration(self):
        migrated, policy = copy.deepcopy(self.data), copy.deepcopy(self.policy)
        policy['sourceCorpusSha256'] = R.sha256('updated official corpus')
        migrated['sourceBasis']['sourceDataSha256'] = policy['sourceCorpusSha256']
        held = migrated['exclusions'][0]
        held['review']['sourceDataSha256'] = policy['sourceCorpusSha256']
        held['reviewRecordSha256'] = R.sha256(json.dumps(held['review'], ensure_ascii=False, separators=(',', ':')))
        policy['excluded'][0]['reviewRecordSha256'] = held['reviewRecordSha256']
        for entry in migrated['entries']:
            entry['review']['sourceDataSha256'] = policy['sourceCorpusSha256']
        new_plan = S.build_subset_plan(encoded(migrated), self.recipe, policy)
        self.assertEqual(new_plan['tasks'], self.plan['tasks'])
        self.assertNotEqual(new_plan['sourceDataSha256'], self.plan['sourceDataSha256'])
        catalog = S.assemble_subset(new_plan, self.state, policy)
        self.assertEqual(set(catalog['clips']), set(self.plan['tasks']))
        self.assertEqual(catalog['sourceDataSha256'], new_plan['sourceDataSha256'])

    def test_catalog_publication_isolated_from_full_branch_and_checked_before_fast_forward(self):
        catalog = S.assemble_subset(self.plan, self.state, self.policy)
        calls = []
        class Client:
            def request(self, method, path, value=None, **_):
                calls.append((method, path, value))
                if '/git/ref/heads/' in path: return {'object': {'sha': 'a' * 40}}
                if method == 'GET' and '/git/commits/' in path: return {'tree': {'sha': 'b' * 40}}
                if path.endswith('/git/blobs'):
                    import base64
                    return {'sha': R.git_blob(base64.b64decode(value['content']))}
                if path.endswith('/git/trees'): return {'sha': 'c' * 40}
                if path.endswith('/git/commits'): return {'sha': 'd' * 40}
                if method == 'PATCH': return {'object': {'sha': value['sha']}}
                raise AssertionError(path)
        result = S.publish_subset_catalog(Client(), catalog, self.policy)
        self.assertEqual(result['branch'], S.CATALOG_BRANCH)
        self.assertEqual(calls[-2][0], 'GET')
        self.assertTrue(calls[-1][1].endswith('/git/refs/heads/' + S.CATALOG_BRANCH))
        self.assertFalse(calls[-1][2]['force'])
        self.assertNotEqual(S.CATALOG_BRANCH, R.CATALOG_BRANCH)


if __name__ == '__main__':
    unittest.main()
