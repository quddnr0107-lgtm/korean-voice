import copy
import unittest
from unittest.mock import patch
import cpu_precision_benchmark as b


class PrecisionTests(unittest.TestCase):
    def hw(self):return b.hardware('model name : Test CPU\nflags : avx2 avx512_bf16 amx_bf16\n',4)
    def variants(self):
        return {d:dict(dtype=d,device='cpu',threads=4,parameter_dtypes=[d],
            model_revision=b.REVISION,fixtures=list(b.FIXTURES),cpu=self.hw(),
            rows=[dict(dtype=d,fixture=i,seed=5192+i,tts_seconds=20.0 if d=='float32' else 10.0,
                audio_seconds=5.0,natural_eos=True,signal_valid=True) for i in range(2)]) for d in b.DTYPES}
    def test_only_real_bf16_instruction_flags_count(self):
        self.assertFalse(b.hardware('flags : avx512f avx512_vnni amx_int8',4)['bf16_instruction_available'])
        self.assertTrue(self.hw()['bf16_instruction_available'])
        self.assertEqual(self.hw()['bf16_instruction_flags'],['amx_bf16','avx512_bf16'])
    def test_real_audio_duration_and_same_hardware_used(self):
        v=self.variants();before=copy.deepcopy(v);out=b.compare_results(v,self.hw())
        self.assertEqual(out['observed_rtf_speedup'],2)
        self.assertEqual(v,before)
        self.assertFalse(out['quality_equivalence_verified'])
        self.assertFalse(out['production_eligible'])
    def test_mismatched_hardware_precision_and_incomplete_coverage_rejected(self):
        for change in (lambda v:v.pop('bfloat16'),
                lambda v:v['bfloat16'].update(cpu={}),
                lambda v:v['bfloat16'].update(parameter_dtypes=['float32']),
                lambda v:v['bfloat16']['rows'].pop(),
                lambda v:v['float32']['rows'][0].update(tts_seconds=float('nan'))):
            v=self.variants();change(v)
            with self.assertRaises(ValueError):b.compare_results(v,self.hw())
    def test_truncation_and_signal_failure_suppress_speedup(self):
        for field in ('natural_eos','signal_valid'):
            v=self.variants();v['bfloat16']['rows'][0][field]=False
            out=b.compare_results(v,self.hw())
            self.assertIsNone(out['observed_rtf_speedup'])
    def test_unsupported_hardware_never_starts_subprocess(self):
        import tempfile
        from pathlib import Path
        hw=b.hardware('flags : avx2',4)
        with tempfile.TemporaryDirectory() as tmp,patch.object(b,'hardware',return_value=hw),patch.object(b.subprocess,'run') as call:
            self.assertEqual(b.run('/not-needed',Path(tmp)/'out'),0)
            call.assert_not_called()
    def test_request_pins_public_scope_and_default_args(self):
        b.validate_request()
        self.assertEqual(set(b.generation_args('plain_defaults',b.FIXTURES[0])),{'text','language','speaker','max_new_tokens'})
    def test_timed_out_worker_keeps_a_failure_summary(self):
        import tempfile,json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp,patch.object(b,'hardware',return_value=self.hw()),patch.object(b.subprocess,'run',side_effect=b.subprocess.TimeoutExpired('worker',600)):
            out=Path(tmp)/'out';self.assertEqual(b.run('/fake',out),1)
            result=json.loads((out/'summary.json').read_text())
            self.assertEqual(result['status'],'precision_worker_timeout')
            self.assertIsNone(result['observed_rtf_speedup'])


if __name__=='__main__':unittest.main()
