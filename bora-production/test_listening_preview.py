"""Synthetic previews only; no protected keys, saved lecture text or audio."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import listening_preview as p


def wav(samples):
    fmt = struct.pack('<HHIIHH', 3, 1, 24000, 96000, 4, 32)
    pcm = struct.pack('<f', .25)*samples
    chunks = b'fmt '+struct.pack('<I', len(fmt))+fmt+b'data'+struct.pack('<I', len(pcm))+pcm
    return b'RIFF'+struct.pack('<I', len(chunks)+4)+b'WAVE'+chunks


class ListeningPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)

    def request(self, key=None):
        der = (key or self.key).public_key().public_bytes(serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        return dict(schema=p.SCHEMA, request_id='synthetic-owner-review-01',
            purpose='owner-only-listening-review', pin=copy.deepcopy(p.full.PIN), max_clips=3,
            recipient_spki_der_b64=base64.b64encode(der).decode(),
            recipient_spki_sha256=hashlib.sha256(der).hexdigest(), synthesis=False,
            promotion=False, public_plaintext_export=False)

    def fixture(self, root):
        rows, units = {}, []
        for index in range(58):
            spoken = 'PRIVATE_EXPECTED_CANARY_'+str(index)
            unit = dict(id=f'TB-001:p{index:04d}:u000', spoken=spoken, mode='explain',
                source_sha256='a'*64, critical_terms=['PRIVATE_CRITICAL_CANARY'])
            units.append(unit)
            receipt = dict(status='passed')
            row = dict(receipt=receipt, path=root/f'passed-{index}.wav')
            if index >= 27:
                directory = root/f'u{index:05d}'; directory.mkdir()
                path = directory/'review.wav'; audio = wav(index+40); path.write_bytes(audio)
                sha = p.digest(audio)
                asr = dict(recognized='PRIVATE_RECOGNIZED_CANARY_'+str(index),
                    character_errors=1, missing_critical=['PRIVATE_CRITICAL_CANARY'] if index == 29 else [])
                candidate = dict(seed=5192, sha256=sha, natural_eos=True,
                    signal=dict(blocked=False, samples=index+40), asr=asr)
                receipt = dict(status='blocked_requires_review', blocked=True,
                    production_eligible=False, review_only=True, review_file='review.wav',
                    review_sample_rate=24000, review_sample_count=index+40,
                    review_natural_eos=True, review_sha256=sha,
                    spoken_sha256=p.digest(spoken.encode()), source_sha256=unit['source_sha256'],
                    unit_id=unit['id'], candidates=[candidate],
                    asr=dict(recognized='WRONG_RECEIPT_TRANSCRIPT_MUST_NOT_BE_EXPORTED'))
                row = dict(receipt=receipt, path=path)
            rows[index] = row
        return rows, dict(units=units)

    def test_recipient_request_pins_and_crypto_type_are_strict(self):
        request = self.request(); self.assertEqual(p.recipient(request).key_size, 3072)
        for mutate in (lambda x: x.update(max_clips=4),
                       lambda x: x.update(recipient_spki_sha256='0'*64),
                       lambda x: x.update(public_plaintext_export=True),
                       lambda x: x['pin'].update(units=57),
                       lambda x: x.update(recipient_spki_der_b64='!')):
            altered = copy.deepcopy(request); mutate(altered)
            with self.assertRaises((ValueError, TypeError)): p.recipient(altered)
        weak = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with self.assertRaises(ValueError): p.recipient(self.request(weak))

    def test_envelope_roundtrip_and_no_private_canary_or_original_keys(self):
        plain = b'PRIVATE_EXPECTED_CANARY PRIVATE_AUDIO_CANARY'
        request = self.request(); envelope = p.encrypt_payload(plain, request, clip_count=3)
        self.assertNotIn('PRIVATE_', json.dumps(envelope))
        aad = p.aad_for(request)
        unwrapped = self.key.decrypt(base64.b64decode(envelope['wrapped_key_b64']),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=aad))
        self.assertEqual(len(unwrapped), 32)
        nonce, cipher = base64.b64decode(envelope['nonce_b64']), base64.b64decode(envelope['ciphertext_b64'])
        self.assertEqual(len(nonce), 12)
        self.assertEqual(AESGCM(unwrapped).decrypt(nonce, cipher, aad), plain)
        self.assertEqual(envelope['encrypted_payload_sha256'], p.digest(cipher))
        self.assertFalse(envelope['production_eligible'])
        self.assertFalse(envelope['private_plaintext_in_public_artifact'])

    def test_wrong_recipient_changed_aad_and_ciphertext_fail(self):
        request = self.request(); envelope = p.encrypt_payload(b'PRIVATE', request, clip_count=1)
        aad = p.aad_for(request); wrapped = base64.b64decode(envelope['wrapped_key_b64'])
        oaep = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=aad)
        with self.assertRaises(ValueError): self.other_key.decrypt(wrapped, oaep)
        key = self.key.decrypt(wrapped, oaep)
        nonce, cipher = base64.b64decode(envelope['nonce_b64']), base64.b64decode(envelope['ciphertext_b64'])
        altered = dict(request, request_id='other-request')
        with self.assertRaises(InvalidTag): AESGCM(key).decrypt(nonce, cipher, p.aad_for(altered))
        with self.assertRaises(InvalidTag): AESGCM(key).decrypt(nonce, cipher[:-1]+bytes([cipher[-1]^1]), aad)

    def test_saved_candidate_matches_review_hash_not_receipt_transcript(self):
        with tempfile.TemporaryDirectory() as td:
            rows, plan = self.fixture(Path(td)); before = copy.deepcopy(rows)
            available = p.available_reviews(rows, plan)
            self.assertEqual(len(available), 31)
            self.assertEqual(available[0]['saved_recognized'], 'PRIVATE_RECOGNIZED_CANARY_27')
            self.assertEqual(rows, before)
            rows[27]['receipt']['candidates'][0]['sha256'] = '0'*64
            with self.assertRaises(ValueError): p.available_reviews(rows, plan)

    def test_selection_is_small_unique_and_keeps_passed_units_out(self):
        with tempfile.TemporaryDirectory() as td:
            rows, plan = self.fixture(Path(td)); available = p.available_reviews(rows, plan)
            chosen = p.select_reviews(available)
            self.assertEqual([r['index'] for r, _ in chosen], [27, 29, 28])
            self.assertEqual(chosen[1][1], 'critical_term_disagreement')
            self.assertTrue(all(rows[r['index']]['receipt']['status'] == 'blocked_requires_review' for r, _ in chosen))
            with self.assertRaises(ValueError): p.select_reviews(available[:-1])
            rows.pop(0)
            with self.assertRaises(ValueError): p.available_reviews(rows, plan)

    def test_private_zip_contains_only_fixed_names_and_unchanged_original_wavs(self):
        with tempfile.TemporaryDirectory() as td:
            rows, plan = self.fixture(Path(td)); before = copy.deepcopy(rows)
            chosen = p.select_reviews(p.available_reviews(rows, plan))
            plain = p.make_payload(chosen, self.request())
            with zipfile.ZipFile(io.BytesIO(plain)) as archive:
                self.assertEqual(archive.namelist(), ['clip-01.wav','clip-02.wav','clip-03.wav','preview.json'])
                manifest = json.loads(archive.read('preview.json'))
                self.assertFalse(manifest['production_status_changed'])
                for index, (row, _) in enumerate(chosen, 1):
                    self.assertEqual(archive.read(f'clip-{index:02d}.wav'), row['path'].read_bytes())
                    self.assertEqual(manifest['clips'][index-1]['saved_recognized'], row['saved_recognized'])
                    self.assertFalse(manifest['clips'][index-1]['approved'])
            self.assertEqual(rows, before)
            with patch.object(p, 'MAX_PLAIN_BYTES', 10):
                with self.assertRaises(ValueError): p.make_payload(chosen, self.request())

    def test_wav_integrity_count_and_source_identity_fail_closed(self):
        self.assertEqual(p.wav_info(wav(7))['sample_count'], 7)
        for payload in (b'BAD', wav(7)+b'EXTRA', b'RIFX'+wav(7)[4:]):
            with self.assertRaises(ValueError): p.wav_info(payload)
        for mutate in (lambda rows: rows[27]['receipt'].update(review_sample_count=99),
                       lambda rows: rows[27]['receipt'].update(spoken_sha256='0'*64),
                       lambda rows: rows[0]['receipt'].update(status='blocked_requires_review'),
                       lambda rows: rows[27]['receipt']['candidates'].append(copy.deepcopy(rows[27]['receipt']['candidates'][0]))):
            with tempfile.TemporaryDirectory() as td:
                rows, plan = self.fixture(Path(td)); mutate(rows)
                with self.assertRaises((ValueError, KeyError)): p.available_reviews(rows, plan)

    def test_context_and_source_have_no_render_promotion_or_key_export_interfaces(self):
        env = dict(GITHUB_REPOSITORY=p.full.d.REPO, GITHUB_REF='refs/heads/'+p.full.d.BRANCH,
            GITHUB_EVENT_NAME='push', GITHUB_WORKFLOW_REF=p.WORKFLOW)
        p.check_context(env, 1001, 1001)
        for field in env:
            changed = dict(env); changed[field] = 'wrong'
            with self.assertRaises(ValueError): p.check_context(changed, 1001, 1001)
        with self.assertRaises(ValueError): p.check_context(env, 0, 0)
        source = Path(p.__file__).read_text()
        for forbidden in ('FP32Renderer(', 'render_unit(', 'generate_custom_voice(',
                          'publish(', 'delete_asset(', 'private_bytes('):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
