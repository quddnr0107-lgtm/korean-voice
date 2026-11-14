"""Synthetic audio/text only; no operational keys or protected lecture files."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import tb001_remaining_listen as remaining


def wav(samples):
    fmt = struct.pack('<HHIIHH', 3, 1, 24000, 96000, 4, 32)
    pcm = struct.pack('<f', .25)*samples
    chunks = b'fmt '+struct.pack('<I', len(fmt))+fmt+b'data'+struct.pack('<I', len(pcm))+pcm
    return b'RIFF'+struct.pack('<I', len(chunks)+4)+b'WAVE'+chunks


class RemainingListeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)

    def request(self):
        der = self.key.public_key().public_bytes(serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        return dict(schema=remaining.SCHEMA, request_id='synthetic-remaining-listen-01',
            purpose='owner-only-remaining-listening-review', pin=copy.deepcopy(remaining.full.PIN),
            diagnostic_summary_sha256=remaining.DIAGNOSTIC_SUMMARY_SHA256,
            unit_indices=list(remaining.TARGETS),
            recipient_spki_der_b64=base64.b64encode(der).decode(),
            recipient_spki_sha256=hashlib.sha256(der).hexdigest(),
            synthesis=False, promotion=False, public_plaintext_export=False)

    def reviews(self, root):
        others = [i for i in range(58) if i not in remaining.TARGETS][:16]
        rows = []
        for index in sorted([*remaining.TARGETS, *others]):
            audio = wav(index+50)
            path = root/f'unit-{index}.wav'
            path.write_bytes(audio)
            rows.append(dict(index=index, path=path, wav_sha256=remaining.preview.digest(audio),
                info=remaining.preview.wav_info(audio), unit_id=f'TB-001:p{index:04d}:u000',
                mode='explain', expected_spoken='PRIVATE_EXPECTED_CANARY_'+str(index),
                saved_recognized='PRIVATE_RECOGNIZED_CANARY_'+str(index), saved_seed=5192,
                critical_terms=[], missing_critical=[], character_errors=1))
        pins = {row['index']: row['wav_sha256'] for row in rows if row['index'] in remaining.TARGETS}
        return rows, pins

    def test_request_pins_scope_and_public_key(self):
        original = self.request()
        remaining.validate_request(original)
        changes = [('promotion', True), ('synthesis', True), ('public_plaintext_export', True),
            ('unit_indices', list(remaining.TARGETS[:-1])), ('diagnostic_summary_sha256', 'a'*64),
            ('recipient_spki_sha256', 'b'*64), ('request_id', '../not-a-request')]
        for field, value in changes:
            altered = copy.deepcopy(original)
            altered[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                remaining.validate_request(altered)

    def test_exact_context_nonroot_and_workflow(self):
        env = dict(GITHUB_REPOSITORY=remaining.full.d.REPO,
            GITHUB_REF='refs/heads/'+remaining.full.d.BRANCH,
            GITHUB_EVENT_NAME='push', GITHUB_WORKFLOW_REF=remaining.WORKFLOW)
        remaining.check_context(env, 1001, 1001)
        for uid, euid in [(0, 0), (1001, 0), (1001, 1002)]:
            with self.assertRaises(ValueError):
                remaining.check_context(env, uid, euid)
        env['GITHUB_WORKFLOW_REF'] += '-other'
        with self.assertRaises(ValueError):
            remaining.check_context(env, 1001, 1001)

    def test_actual_numeric_evidence_is_immutable_and_exact_15(self):
        pins = remaining.read_evidence()
        self.assertEqual(set(pins), set(remaining.TARGETS))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'modified-evidence.json'
            path.write_bytes(remaining.EVIDENCE.read_bytes()+b' ')
            with self.assertRaises(ValueError):
                remaining.read_evidence(path)

    def test_select_rejects_changed_scope_or_wave_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            reviews, pins = self.reviews(Path(directory))
            chosen = remaining.select_remaining(reviews, pins)
            self.assertEqual([row['index'] for row in chosen], list(remaining.TARGETS))
            with self.assertRaises(ValueError):
                remaining.select_remaining(reviews[:-1], pins)
            wrong = copy.deepcopy(pins)
            wrong[remaining.TARGETS[0]] = '0'*64
            with self.assertRaises(ValueError):
                remaining.select_remaining(reviews, wrong)
            wrong = copy.deepcopy(pins)
            wrong[2] = 'a'*64
            with self.assertRaises(ValueError):
                remaining.select_remaining(reviews, wrong)

    def test_five_envelopes_decrypt_to_exact_15_unmodified_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            reviews, pins = self.reviews(Path(directory))
            before = copy.deepcopy(reviews)
            request = self.request()
            outputs = remaining.make_envelopes(reviews, pins, request)
            self.assertEqual(reviews, before)
            self.assertEqual([name for name, _ in outputs],
                [f'owner-remaining-{i:02d}.json' for i in range(1, 6)])
            exported = []
            for group, (_, envelope) in enumerate(outputs, 1):
                public = json.dumps(envelope)
                self.assertNotIn('PRIVATE_EXPECTED_CANARY', public)
                self.assertNotIn('PRIVATE_RECOGNIZED_CANARY', public)
                self.assertFalse(envelope['production_eligible'])
                self.assertFalse(envelope['private_plaintext_in_public_artifact'])
                self.assertEqual(envelope['clip_count'], 3)
                child = remaining.subrequest(request, group)
                aad = remaining.preview.aad_for(child)
                self.assertEqual(base64.b64decode(envelope['aad_b64']), aad)
                key = self.key.decrypt(base64.b64decode(envelope['wrapped_key_b64']),
                    padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=aad))
                plain = AESGCM(key).decrypt(base64.b64decode(envelope['nonce_b64']),
                    base64.b64decode(envelope['ciphertext_b64']), aad)
                with zipfile.ZipFile(io.BytesIO(plain)) as archive:
                    self.assertEqual(set(archive.namelist()),
                        {'clip-01.wav', 'clip-02.wav', 'clip-03.wav', 'preview.json'})
                    manifest = json.loads(archive.read('preview.json'))
                    self.assertEqual(manifest['request_id'], child['request_id'])
                    self.assertEqual(manifest['source_pin'], remaining.full.PIN)
                    self.assertFalse(manifest['production_status_changed'])
                    for clip in manifest['clips']:
                        index = clip['unit_index']
                        original = next(row for row in reviews if row['index'] == index)
                        self.assertEqual(archive.read(clip['wav_file']), original['path'].read_bytes())
                        self.assertEqual(clip['wav_sha256'], pins[index])
                        self.assertFalse(clip['approved'])
                        exported.append(index)
            self.assertEqual(exported, list(remaining.TARGETS))

    def test_tampered_raw_wave_cannot_be_encrypted(self):
        with tempfile.TemporaryDirectory() as directory:
            reviews, pins = self.reviews(Path(directory))
            next(row for row in reviews if row['index'] == remaining.TARGETS[0])['path'].write_bytes(b'changed')
            with self.assertRaises(ValueError):
                remaining.make_envelopes(reviews, pins, self.request())

    def test_legacy_three_clip_limit_remains_unchanged(self):
        self.assertEqual(remaining.preview.MAX_CLIPS, 3)
        with self.assertRaises(ValueError):
            remaining.preview.encrypt_payload(b'example', remaining.subrequest(self.request(), 1), clip_count=4)
        for group in [0, 6, True]:
            with self.assertRaises(ValueError):
                remaining.subrequest(self.request(), group)


if __name__ == '__main__':
    unittest.main()
