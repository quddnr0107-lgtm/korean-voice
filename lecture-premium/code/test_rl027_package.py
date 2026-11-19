"""Synthetic encrypted release: prove unchanged audio, shifted time, and rejection after encoding."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import rl027_package as mod
from rl027_package import LESSON, RATE, assemble, candidate, crypto, delivery, pack, set_times


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = bytes(range(32))
        self.pcm = lambda n, frequency: (np.sin(np.arange(n) * frequency * 2 * np.pi / RATE) * 0.12).astype('<f4')
        n = 3 * RATE
        pack_id = '12' * 16
        headers, parts, cipher, self.original_bytes = [], [], bytearray(), []
        for i in range(3):
            f = self.root / f'origin-{i}.mp3'
            p = assemble._encode_part(self.pcm(n, 220 + 45*i).tobytes(), f, n, 'ffmpeg')
            set_times(p, i*n, (i+1)*n); p.update(index=i, sample_rate=RATE)
            self.original_bytes.append(f.read_bytes())
            h, c = pack.seal_part(self.key, f.read_bytes(), LESSON, pack_id, i, i*n, (i+1)*n, len(cipher))
            headers.append(h); parts.append(p); cipher.extend(c)
        audio = pack.make_pack_manifest(LESSON, pack_id, headers, bytes(cipher))
        cuts = [0, n, n+RATE, 2*n, 3*n]
        self.target = 2
        segs, measures = [], []
        for i, (a,b) in enumerate(zip(cuts,cuts[1:])):
            s = dict(unit_id=f'u{i}', start_utf16=i, end_utf16=i+1, text='예')
            set_times(s,a,b); segs.append(s)
            measures.append(dict(unit_index=i,unit_id=s['unit_id'],sample_count=b-a,gap_before_samples=0))
        self.record = dict(id=LESSON, segments=segs, unit_measurements=measures, audio_parts=parts,
                           total_samples=3*n, narration_text='예'*4, audio_pack_sha256=audio['pack_sha256'])
        self.original = SimpleNamespace(audio=audio,record=self.record,cipher=bytes(cipher))
        self.evidence = dict(pcm_sha256='34'*32,source_descriptor_sha256=candidate.check.DESCRIPTOR_DIGEST)

    def build(self, samples, accepted=True):
        pcm = (self.pcm(samples, 377) * 32768).astype('<i2').tobytes()
        ev = dict(self.evidence, pcm_sha256=crypto.digest(pcm))
        def verify(decoded):
            self.assertEqual(len(decoded),len(pcm))
            return accepted, [dict(speed=1.0, accepted=accepted), dict(speed=1.2, accepted=accepted)]
        return mod.build_pack(self.original, self.key, ev, pcm, self.root, verify, target=self.target)

    def unpack(self, d, cipher, private):
        record = json.loads(crypto.open_range(self.key,d['content'],private,expected_id=LESSON,
                             expected_kind='private-metadata',expected_key_id=delivery.KEY_ID)[0])
        pack.validate_pack(d['audio'],cipher)
        parts = []
        for h in d['audio']['parts']:
            start=h['pack_offset']; data=cipher[start:start+h['cipher_bytes']]
            parts.append(pack.open_part(self.key,h,data,expected_id=LESSON,expected_part_index=h['part_index'],
                                        expected_pack_id=d['audio']['object_id']))
        return record, parts

    def test_index_contains_only_the_new_lesson_and_digest_of_its_exact_descriptor(self):
        descriptor = b'test descriptor bytes'
        index = json.loads(mod.one_lesson_index(descriptor))
        tag = 'bora-fp32-rl027-art15p3-v2-20261119'
        self.assertEqual(index, {'version':'integrated-lecture-index/v2','release_tag':tag,'count':1,
            'lessons':[{'id':LESSON,'sha256':crypto.digest(descriptor),
                        'descriptor_url':f'https://github.com/quddnr0107-lgtm/korean-voice/releases/download/{tag}/RL-027.descriptor.json'}]})

    def test_preservation_and_exact_or_changed_timing(self):
        for samples in (2 * RATE, 2 * RATE - 2031, 2 * RATE + 1825):
            with self.subTest(samples=samples):
                d, audio, private = self.build(samples)
                self.assertNotEqual(d['audio']['object_id'],self.original.audio['object_id'])
                record,parts = self.unpack(d,audio,private)
                self.assertEqual(parts[0], self.original_bytes[0]); self.assertEqual(parts[2], self.original_bytes[2])
                self.assertNotEqual(parts[1], self.original_bytes[1])
                delta = samples - 2 * RATE
                self.assertEqual(record['total_samples'],self.record['total_samples']+delta)
                self.assertEqual(record['segments'][3]['start_sample'],6*RATE+delta)
                self.assertEqual(record['unit_measurements'][2]['sample_count'],samples)
                self.assertEqual(record['segments'][2]['start_utf16'],self.record['segments'][2]['start_utf16'])
                self.assertEqual(record['audio_pack_sha256'],crypto.digest(audio))
                self.assertEqual(self.record['segments'][3]['start_sample'],6*RATE)

    def test_repartition_reuses_untouched_bytes(self):
        with patch.object(pack,'MAX_PART_SAMPLES',3 * RATE):
            d,audio,private=self.build(2*RATE+900)
            record, parts=self.unpack(d,audio,private)
            self.assertEqual(len(parts),4)
            self.assertEqual(parts[0],self.original_bytes[0]);self.assertEqual(parts[3],self.original_bytes[2])
            self.assertEqual(record['audio_parts'][2]['start_sample'],4*RATE)
            self.assertTrue(all(p['end_sample']-p['start_sample']<=3*RATE for p in d['audio']['parts']))

    def test_reject_failed_final_quality_and_tampered_original(self):
        with self.assertRaisesRegex(ValueError,'encoded_audio_quality'):
            self.build(2*RATE,False)
        self.original.cipher=b'X'+self.original.cipher[1:]
        with self.assertRaises(ValueError):
            self.build(2*RATE)

    def test_two_repairs_shift_both_occurrences_and_keep_other_mp3_bytes(self):
        pcm = (self.pcm(2 * RATE + 502, 333) * 32768).astype('<i2').tobytes()
        ev = dict(self.evidence, pcm_sha256=crypto.digest(pcm))
        verified = []
        def verify(clip):
            verified.append(len(clip))
            return True, [{'speed': speed, 'accepted': True} for speed in (1, 1.2)]
        d, audio, private, spans = mod.replace_occurrences(
            self.original, self.key, ev, pcm, self.root, verify, (0, 2))
        record, parts = self.unpack(d, audio, private)
        self.assertEqual(verified, [len(pcm), len(pcm)])
        self.assertEqual([r['target_index'] for r in record['unit_repairs']], [0, 2])
        self.assertEqual(parts[-1], self.original_bytes[-1])
        for s in spans:
            self.assertEqual(s['new_end_sample'] - s['new_start_sample'], len(pcm)//2)
            self.assertEqual(s['old_start_sample'], self.record['segments'][s['unit']]['start_sample'])
        delta = sum((s['new_end_sample']-s['new_start_sample']) -
                    (s['old_end_sample']-s['old_start_sample']) for s in spans)
        self.assertEqual(record['total_samples'], self.record['total_samples'] + delta)
        with self.assertRaisesRegex(ValueError, 'repair_part_overlap'):
            mod.replace_occurrences(self.original, self.key, ev, pcm, self.root, verify, (1, 2))

    def test_sealed_candidate_must_match_release_and_pcm(self):
        samples=2*RATE; raw=bytes(2*samples)
        segment=dict(start_sample=3*RATE,end_sample=5*RATE)
        e=dict(schema=candidate.SCHEMA,lesson_id=LESSON,unit_index=candidate.TARGET,speaker=candidate.SPEAKER,
               source_descriptor_sha256=candidate.check.DESCRIPTOR_DIGEST,spoken_sha256=candidate.SPOKEN_SHA,
               rate=RATE,channels=1,pcm_format='s16le',original_start_sample=3*RATE,original_end_sample=5*RATE,
               pcm_sha256=crypto.digest(raw),candidate=dict(candidate_samples=samples, original_samples=samples,
               accepted=True,checks=[dict(speed=v,accepted=True,words=dict(accepted=True)) for v in (1.,1.2)]))
        for i,evidence in enumerate((e,dict(e, pcm_sha256='f'*64),dict(e,original_start_sample=0))):
            dest=self.root/f'candidate-{i}'
            candidate.seal_candidate(dest,self.key,raw,evidence)
            if i==0:self.assertEqual(mod.load_candidate(dest,self.key,segment),(e,raw))
            else:
                with self.assertRaises(ValueError):mod.load_candidate(dest,self.key,segment)

if __name__ == '__main__':
    unittest.main()
