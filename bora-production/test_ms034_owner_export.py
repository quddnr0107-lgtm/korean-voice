"""Synthetic data only: complete audio, wrong keys, tamper and recipient."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import lecture_crypto as crypto
import lecture_pack as pack
import ms034_owner_export as exporter
import transport


class OwnerExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.key = bytes(range(32))
        lesson = exporter.LESSON
        pack_id = 'a' * 32
        self.expected = [b'synthetic mp3 bytes A', b'synthetic mp3 bytes B']
        parts, blobs = [], []
        for i, payload in enumerate(self.expected):
            part, blob = pack.seal_part(self.key, payload, lesson, pack_id, i,
                                       i * 44100, (i + 1) * 44100, sum(map(len, blobs)))
            parts.append(part)
            blobs.append(blob)
        audio = b''.join(blobs)
        manifest = pack.make_pack_manifest(lesson, pack_id, parts, audio)
        record = dict(id=lesson, audio_pack_sha256=manifest['pack_sha256'], total_samples=88200,
                      sample_rate=44100, narration_text='synthetic', segments=[{'text': 'synthetic'}])
        content, blob = crypto.seal(self.key, json.dumps(record).encode(), lesson,
                                    'private-metadata', transport.KEY_ID)
        descriptor = dict(version='integrated-lecture-output/v2', lesson_id=lesson,
                          audio=manifest, content=content)
        files = {
            f'{lesson}.audio.{pack_id}.bin': audio,
            f'{lesson}.content.{content["object_id"]}.bin': blob,
            f'{lesson}.descriptor.json': json.dumps(descriptor).encode(),
        }
        self.pins = {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in files.items()}
        for name, data in files.items():
            (self.directory / name).write_bytes(data)

    def test_complete_original_parts_and_recipient_roundtrip(self):
        original = exporter.recover(self.directory, self.key, pins=self.pins)
        with zipfile.ZipFile(io.BytesIO(original)) as z:
            self.assertEqual(z.namelist(), ['metadata.json', 'descriptor.json', 'part-000.mp3', 'part-001.mp3'])
            self.assertEqual([z.read(f'part-{i:03d}.mp3') for i in range(2)], self.expected)
        owner = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        wrapped = exporter.seal_for_owner(owner.public_key(), original)
        key = owner.decrypt(wrapped[:384], padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                       algorithm=hashes.SHA256(), label=exporter.CONTEXT))
        self.assertEqual(AESGCM(key).decrypt(wrapped[384:396], wrapped[396:], exporter.CONTEXT), original)

    def test_reject_wrong_media_key(self):
        with self.assertRaises(InvalidTag):
            exporter.recover(self.directory, bytes(32), pins=self.pins)

    def test_reject_extra_file(self):
        (self.directory / 'unexpected.bin').write_bytes(b'no')
        with self.assertRaises(ValueError):
            exporter.recover(self.directory, self.key, pins=self.pins)

    def test_reject_changed_asset(self):
        path = next(self.directory.glob('*.audio.*.bin'))
        raw = path.read_bytes()
        path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        with self.assertRaises(ValueError):
            exporter.recover(self.directory, self.key, pins=self.pins)

    def test_existing_recipient_pin(self):
        self.assertEqual(exporter.recipient().key_size, 3072)


if __name__ == '__main__':
    unittest.main()
