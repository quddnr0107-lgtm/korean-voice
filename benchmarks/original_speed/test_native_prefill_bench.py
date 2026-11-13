import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import wave

SPEC = importlib.util.spec_from_file_location('native_prefill_bench', Path(__file__).with_name('native_prefill_bench.py'))
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


class NativeScreenEvidenceTests(unittest.TestCase):
    def census(self, *triples, dropped=0):
        return dict(dropped_ops=dropped, rows=[dict(path=p, B=b, calls=c, comp='talker') for p,b,c in triples])

    def test_requested_rowpack_cannot_be_credited_for_fallback(self):
        old_path = self.census(('prefill_bf16_native',120,10), ('matmat_bf16',16,100))
        self.assertFalse(bench.validate_proof('rowpack_qkv', old_path)['path_verified'])
        new_path = self.census(('matmat_bf16_rows',16,100), ('bf16_rowpack_shared',16,28))
        self.assertTrue(bench.validate_proof('rowpack_qkv', new_path)['path_verified'])

    def test_linux_bf16_feature_spelling_and_wrong_compile_target(self):
        self.assertTrue(bench.avx512bf16_supported('build SIMD=avx512bf16', 'avx2 avx512f avx512_bf16'))
        self.assertTrue(bench.avx512bf16_supported('build SIMD=avx512bf16', 'avx2 avx512bf16'))
        self.assertFalse(bench.avx512bf16_supported('build SIMD=avx512bf16', 'avx2 avx512f'))
        self.assertFalse(bench.avx512bf16_supported('build SIMD=amx', 'avx2 avx512_bf16'))

    def test_combined_requires_observed_wide_kernel_not_only_requested_env(self):
        narrow = self.census(('matmat_bf16_rows',16,100), ('bf16_rowpack_shared',16,28))
        self.assertFalse(bench.validate_proof('combined', narrow)['path_verified'])
        wide = self.census(('matmat_bf16_rows',32,100), ('bf16_rowpack_shared',16,28))
        self.assertTrue(bench.validate_proof('combined', wide)['path_verified'])
        self.assertFalse(bench.validate_proof('combined', dict(wide, dropped_ops=1))['path_verified'])

    def test_rowpack_chunk32_is_not_credited_with_shared_qkv(self):
        evidence = self.census(('matmat_bf16_rows',32,100))
        self.assertTrue(bench.validate_proof('rowpack_chunk32', evidence)['path_verified'])
        self.assertEqual(bench.validate_proof('rowpack_chunk32', evidence)['shared_qkv_pack_calls'], 0)

    def test_inline_progress_eos_and_frame_limit_are_distinguished(self):
        valid = 'EOS strategy: off (suppress=2)\n  Frame 50/1100 (4.0s audio)...  EOS at frame 72\n  Generated 72 frames (5.8s audio)\n  Prefill: 3033 ms\n'
        r = bench.log_evidence(valid)
        self.assertTrue(r['natural_eos_observed'])
        self.assertEqual(r['prefill_seconds'], 3.033)
        self.assertFalse(bench.log_evidence(valid.replace('frame 72', 'frame 1100').replace('72 frames', '1100 frames'))['natural_eos_observed'])
        self.assertFalse(bench.log_evidence(valid.replace('off (', 'v1 ('))['natural_eos_observed'])

    def test_wav_hash_and_pcm_hash_are_separate_and_truncation_rejected(self):
        import struct
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'audio.wav'
            pcm = struct.pack('<hh', 3000, -3000)*20
            with wave.open(str(path),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(pcm)
            r = bench.audio_evidence(path)
            self.assertEqual(r['pcm_sha256'], hashlib.sha256(pcm).hexdigest())
            self.assertEqual(r['sample_count'],40)
            path.write_bytes(path.read_bytes()[:-2])
            with self.assertRaises(ValueError):bench.audio_evidence(path)

    def rows(self, changed=False):
        return [dict(profile=p,fixture=f,repeat=r,valid_complete=True,process_wall_seconds=10 if p=='baseline' else 5,
                     prefill_seconds=2,sample_count=24000,sample_rate=24000,
                     pcm_sha256=('different' if changed and p=='combined' and f==1 and r==1 else f'pcm-{f}'))
                for p in bench.PROFILES for f in range(2) for r in range(2)]

    def test_faster_changed_audio_never_receives_approved_speedup(self):
        profiles={p:dict(proof=dict(path_verified=True)) for p in bench.PROFILES}
        s=bench.summary(self.rows(changed=True), profiles, {})
        c=next(x for x in s['comparisons'] if x['profile']=='combined')
        self.assertEqual(c['status'],'changed_audio_no_approval')
        self.assertIsNone(c['process_time_reduction_fraction_if_exact'])

    def test_exact_outputs_still_need_path_proof(self):
        profiles={p:dict(proof=dict(path_verified=False)) for p in bench.PROFILES}
        c=bench.summary(self.rows(),profiles,{})['comparisons'][0]
        self.assertTrue(c['exact_pcm_parity'])
        self.assertFalse(c['requested_path_verified'])
        self.assertIsNone(c['process_time_reduction_fraction_if_exact'])

    def test_unstable_baseline_cannot_approve_matching_per_repeat_candidates(self):
        rows=self.rows()
        # Every candidate still matches its corresponding baseline, but the
        # same input/seed yields different baseline PCM on the second repeat.
        for r in rows:
            if r['fixture']==0 and r['repeat']==1:r['pcm_sha256']='changed-every-profile'
        profiles={p:dict(proof=dict(path_verified=True)) for p in bench.PROFILES}
        s=bench.summary(rows,profiles,{})
        self.assertFalse(s['baseline_repeats_stable'])
        self.assertTrue(s['comparisons'][0]['exact_pcm_parity'])
        self.assertEqual(s['comparisons'][0]['status'],'unstable_baseline_no_speed_claim')
        self.assertIsNone(s['comparisons'][0]['process_time_reduction_fraction_if_exact'])


if __name__=='__main__':unittest.main()
