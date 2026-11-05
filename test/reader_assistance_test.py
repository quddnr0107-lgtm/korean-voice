import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('reader_assistance', ROOT / 'tools/reader_assistance.py')
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def recipe():
    return json.loads((ROOT / 'reader-assistance/recipe.json').read_text())


def document(texts=('원문에 적힌 조건을 갖춘 경우에만 적용합니다.', '허가를 받아야 한다는 뜻입니다.'), partial=False):
    entries = [{'id': 'line-' + str(i), 'text': text, 'review': {'status': 'approved', 'textSha256': R.sha256(text)}} for i, text in enumerate(texts)]
    count = len(entries)
    data = {'schema': 'reader-assistance.v1', 'partial': partial,
            'coverage': {'bodyRows': count + int(partial), 'explainedRows': count, 'unexplainedBodyRows': int(partial)}, 'entries': entries}
    return json.dumps(data, ensure_ascii=False).encode()


def fake_clip(task, fill=1):
    data = b'ID3' + bytes([fill]) * 512
    return data, {'sha256': R.sha256(data), 'textSha256': task['textSha256'], 'durationMs': 3000}


def indexed_state(plan):
    clips = []
    for i, task in enumerate(plan['tasks'].values(), 1):
        data, metadata = fake_clip(task, i)
        clips.append((task['audioKey'], metadata, data))
    packed, index, digest = R.build_pack(clips, plan['recipeSha256'], plan['sourceDataSha256'], plan['fixture'])
    url = f'https://github.com/{R.REPOSITORY}/releases/download/{R.RECIPE_ID}-packs/{digest}.bin'
    state = {'packs': {digest: {'url': url, 'size': len(packed), 'sha256': digest}},
             'clips': {key: {**clip, 'pack': digest} for key, clip in index['clips'].items()}}
    return packed, index, digest, state


class Identity(unittest.TestCase):
    def test_canonical_identity_matches_node_and_exact_text(self):
        data, rec = document(), recipe()
        text = json.loads(data)['entries'][0]['text']
        request = json.dumps({'recipe': rec, 'text': text}, ensure_ascii=False)
        code = "import{canonicalJson,audioKey,textSha256}from './lib/reader-assistance.mjs';let s='';for await(const c of process.stdin)s+=c;const x=JSON.parse(s);process.stdout.write(JSON.stringify({canonical:canonicalJson(x.recipe),key:audioKey(x.recipe,x.text),text:textSha256(x.text)}));"
        out = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', code], input=request.encode(), cwd=ROOT))
        self.assertEqual(out, {'canonical': R.canonical_json(rec), 'key': R.audio_key(rec, text), 'text': R.sha256(text)})
        self.assertNotEqual(R.audio_key(rec, text), R.audio_key(rec, text + ' '))
        changed = copy.deepcopy(rec); changed['id'] += '-revision'
        self.assertNotEqual(R.audio_key(rec, text), R.audio_key(changed, text))

    def test_reviewed_text_must_match_current_hash(self):
        obj = json.loads(document()); obj['entries'][0]['text'] += ' 바뀜'
        with self.assertRaisesRegex(R.ContractError, 'not approved'):
            R.build_plan(json.dumps(obj).encode(), recipe())
        obj['entries'][0]['review']['status'] = 'draft'
        with self.assertRaises(R.ContractError):
            R.build_plan(json.dumps(obj).encode(), recipe())

    def test_partial_gate_and_pilot_never_becomes_complete(self):
        with self.assertRaisesRegex(R.ContractError, 'complete reviewed coverage'):
            R.build_plan(document(partial=True), recipe())
        plan = R.build_plan(document(partial=True), recipe(), allow_partial=True)
        _, _, _, state = indexed_state(plan)
        catalog = R.assemble(plan, state)
        self.assertFalse(catalog['complete'])
        with self.assertRaises(R.ContractError):
            R.attach(document(partial=True), recipe(), catalog)
        with self.assertRaisesRegex(R.ContractError, 'production data branch'):
            R.publish_catalog(None, catalog)

    def test_duplicate_ids_rejected_and_identical_text_is_baked_once(self):
        data = document(('같은 문장입니다.', '같은 문장입니다.'))
        plan = R.build_plan(data, recipe())
        self.assertEqual(len(plan['tasks']), 1)
        self.assertEqual(len(plan['entries']), 2)
        obj = json.loads(data); obj['entries'][1]['id'] = obj['entries'][0]['id']
        with self.assertRaisesRegex(R.ContractError, 'duplicate entry ID'):
            R.build_plan(json.dumps(obj).encode(), recipe())

    def test_only_f1_preview_recipe_is_accepted(self):
        for change in ({'voice': 'female'}, {'style': 'F2'}, {'settings': {**recipe()['settings'], 'steps': 8}}):
            rec = {**recipe(), **change}
            with self.assertRaises(R.ContractError):
                R.build_plan(document(), rec)

    def test_checked_in_renderer_sources_match_sealed_recipe(self):
        R.check_recipe(recipe(), verify_sources=True)


class Pack(unittest.TestCase):
    def setUp(self):
        self.plan = R.build_plan(document(), recipe())
        self.packed, self.index, self.digest, self.state = indexed_state(self.plan)

    def test_pack_preserves_exact_mp3_bytes_and_index_ranges(self):
        for i, (key, clip) in enumerate(self.index['clips'].items(), 1):
            expected, _ = fake_clip(self.plan['tasks'][key], i)
            actual = self.packed[clip['offset']:clip['offset'] + clip['length']]
            self.assertEqual(actual, expected)
            self.assertEqual(R.sha256(actual), clip['sha256'])
        start, length, digest = R.parse_footer(self.packed[-R.FOOTER_SIZE:], len(self.packed))
        result = R.parse_index(self.packed[start:start + length], digest, start, len(self.packed), self.plan['recipeSha256'])
        self.assertEqual(result, self.index)

    def test_corrupted_footer_index_or_range_is_rejected(self):
        bad = bytearray(self.packed[-R.FOOTER_SIZE:]); bad[0] ^= 1
        with self.assertRaises(R.ContractError): R.parse_footer(bytes(bad), len(self.packed))
        start, length, digest = R.parse_footer(self.packed[-R.FOOTER_SIZE:], len(self.packed))
        bad = bytearray(self.packed[start:start + length]); bad[0] ^= 1
        with self.assertRaisesRegex(R.ContractError, 'checksum'): R.parse_index(bytes(bad), digest, start, len(self.packed), self.plan['recipeSha256'])
        obj = copy.deepcopy(self.index); next(iter(obj['clips'].values()))['length'] += 1
        payload = R.canonical_json(obj).encode()
        with self.assertRaisesRegex(R.ContractError, 'overlap|coverage'):
            R.parse_index(payload, R.sha256(payload), start, len(self.packed), self.plan['recipeSha256'])

    def test_different_recipe_or_fixture_is_rejected(self):
        start, length, digest = R.parse_footer(self.packed[-R.FOOTER_SIZE:], len(self.packed))
        for rec, fixture in (('0' * 64, False), (self.plan['recipeSha256'], True)):
            with self.assertRaisesRegex(R.ContractError, 'recipe or fixture'):
                R.parse_index(self.packed[start:start + length], digest, start, len(self.packed), rec, fixture)

    def test_assembly_fails_with_a_missing_clip_and_attaches_exact_audio(self):
        missing = copy.deepcopy(self.state); missing['clips'].pop(next(iter(missing['clips'])))
        with self.assertRaisesRegex(R.ContractError, 'missing'):
            R.assemble(self.plan, missing)
        catalog = R.assemble(self.plan, self.state)
        out = R.attach(document(), recipe(), catalog)
        self.assertTrue(out['audio']['complete'])
        for entry in out['entries']:
            self.assertEqual(entry['audio']['voice'], 'f1')
            self.assertEqual(entry['audio']['textSha256'], R.sha256(entry['text']))
            self.assertEqual(entry['audio']['url'], R.MEDIA_BASE + R.audio_key(recipe(), entry['text']) + '.mp3')
        altered = copy.deepcopy(catalog); altered['sourceDataSha256'] = '0' * 64
        with self.assertRaisesRegex(R.ContractError, 'provenance'):
            R.attach(document(), recipe(), altered)

    def test_remote_probe_reads_only_footer_and_index_exact_ranges(self):
        calls = []
        def fetch(url, start, length, total):
            calls.append((start, length, total)); return self.packed[start:start + length]
        asset = {'size': len(self.packed), 'browser_download_url': 'not-used'}
        result = R.read_remote_index(asset, self.plan['recipeSha256'], fetch_range=fetch)
        self.assertEqual(result, self.index)
        self.assertEqual(len(calls), 2)
        self.assertLess(sum(call[1] for call in calls), len(self.packed))

    def test_asset_identity_rejects_missing_digest_renamed_or_wrong_host(self):
        tag = R.RECIPE_ID + '-packs'
        base = {'name': self.digest + '.bin', 'state': 'uploaded', 'digest': 'sha256:' + self.digest,
                'label': 'reader-assistance-pack.v1/0', 'size': len(self.packed),
                'browser_download_url': f'https://github.com/{R.REPOSITORY}/releases/download/{tag}/{self.digest}.bin'}
        self.assertEqual(R.validate_asset(base, tag), self.digest)
        for change in ({'digest': None}, {'name': 'other.bin'}, {'browser_download_url': 'https://evil.test/a.bin'}, {'state': 'starter'}, {'label': None}):
            with self.assertRaises(R.ContractError): R.validate_asset({**base, **change}, tag)


class Resume(unittest.TestCase):
    def test_existing_content_does_not_load_model_or_upload(self):
        plan = R.build_plan(document(('하나의 문장입니다.',)), recipe())
        key = next(iter(plan['tasks'])); _, _, _, state = indexed_state(plan)
        state.update({'release': {'id': 1}, 'assetCount': 1})
        with patch.object(R, 'inventory', return_value=state):
            def forbidden(*_): raise AssertionError('Unexpected model load')
            result = R.bake(None, plan, recipe(), key[0], 'unused', renderer_factory=forbidden)
        self.assertEqual(result, {'baked': 0, 'packs': 0})

    def test_failed_upload_stops_without_catalog_or_fallback(self):
        plan = R.build_plan(document(('하나의 문장입니다.',)), recipe())
        key = next(iter(plan['tasks']))
        class Client:
            def assets(self, _): return []
            def upload(self, *_): raise R.ContractError('upload failed')
        class FakeRenderer:
            def __init__(self, *_): pass
            def render(self, task): return fake_clip(task)
        state = {'release': {'id': 1, 'tag_name': R.RECIPE_ID + '-packs'}, 'packs': {}, 'clips': {}, 'assetCount': 0}
        with patch.object(R, 'inventory', return_value=state):
            with self.assertRaisesRegex(R.ContractError, 'upload failed'):
                R.bake(Client(), plan, recipe(), key[0], 'unused', renderer_factory=FakeRenderer)

    def test_publish_never_uses_main_and_preserves_compare_parent(self):
        plan = R.build_plan(document(), recipe()); _, _, _, state = indexed_state(plan)
        catalog = R.assemble(plan, state)
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
                if path.endswith('/git/commits'):
                    self.parents = value['parents']; return {'sha': 'd' * 40}
                if method == 'PATCH': return {'object': {'sha': value['sha']}}
                raise AssertionError(path)
        result = R.publish_catalog(Client(), catalog)
        mutation = calls[-1]
        self.assertTrue(mutation[1].endswith('/git/refs/heads/' + R.CATALOG_BRANCH))
        self.assertFalse(mutation[2]['force'])
        self.assertEqual(result['commit'], 'd' * 40)


try:
    import numpy as np
except ImportError:
    np = None


@unittest.skipIf(np is None, 'Audio metric tests require numpy; they run after inference dependencies are installed')
class Wave(unittest.TestCase):
    def test_wave_checks_silence_clipping_duration_and_nonfinite(self):
        sr = 44100
        normal = (0.2 * np.sin(np.arange(sr * 2) * 2 * np.pi * 180 / sr)).astype(np.float32)
        out = R.validate_wave(normal, sr, '정상 발음 확인 문장입니다.')
        self.assertEqual(out['durationMs'], 2000)
        bad = [normal * 0, normal * 10, np.full(sr, np.nan, dtype=np.float32), np.concatenate([normal, np.zeros(sr * 5), normal])]
        for wave in bad:
            with self.assertRaises(R.ContractError): R.validate_wave(wave, sr, '검사 문장입니다.')


if __name__ == '__main__':
    unittest.main()
