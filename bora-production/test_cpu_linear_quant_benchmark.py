import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cpu_linear_quant_benchmark as b


class QuantTests(unittest.TestCase):
    def inventory(self):
        return [dict(name=n,**{'class':'torch.ao.nn.quantized.dynamic.modules.linear.Linear'},
            weight_dtype='torch.qint8') for n in sorted(b.expected_mlp_names())]
    def variants(self):
        hw={'cpu_models':['test']}
        return {v:dict(variant=v,device='cpu',threads=4,remaining_floating_parameter_dtypes=['float32'],
            model_revision=b.REVISION,fixtures=list(b.FIXTURES),cpu=hw,
            quantized_modules=[] if v==b.VARIANTS[0] else self.inventory(),
            quantized_module_count=0 if v==b.VARIANTS[0] else 84,
            memory={'after_synthesis':{'current_rss_kib':100,'peak_rss_kib':200}},
            generation_args=[b.generation_args('production_style',t) for t in b.FIXTURES],
            rows=[dict(variant=v,fixture=i,seed=5192+i,tts_seconds=20.0 if v==b.VARIANTS[0] else 10.0,
                audio_seconds=5.0,natural_eos=True,signal_valid=True) for i in range(2)]) for v in b.VARIANTS}
    def test_exact_84_mlp_modules_only(self):
        names=b.expected_mlp_names();self.assertEqual(len(names),84)
        self.assertTrue(all('.mlp.' in n and n.startswith('talker.model.layers.') for n in names))
        self.assertFalse(any('code_predictor' in n or 'codec_head' in n for n in names))
        self.assertEqual(b.validate_quant_inventory(b.VARIANTS[1],self.inventory()),84)
    def test_missing_wrong_dtype_or_extra_quantization_rejected(self):
        for change in (lambda x:x.pop(),lambda x:x[0].update(weight_dtype='torch.float32'),
                lambda x:x[0].update(name='talker.codec_head'),lambda x:x.append(dict(x[0]))):
            x=self.inventory();change(x)
            with self.assertRaises(ValueError):b.validate_quant_inventory(b.VARIANTS[1],x)
        with self.assertRaises(ValueError):b.validate_quant_inventory(b.VARIANTS[0],self.inventory())
    def test_comparison_measures_audio_and_preserves_inputs(self):
        v=self.variants();before=copy.deepcopy(v);r=b.compare_results(v,{'cpu_models':['test']})
        self.assertEqual(r['observed_rtf_speedup'],2)
        self.assertEqual(before,v);self.assertFalse(r['quality_equivalence_verified'])
        self.assertFalse(r['production_eligible'])
    def test_settings_hardware_and_applied_quantization_fail_closed(self):
        for change in (lambda x:x[b.VARIANTS[1]].update(cpu={}),
                lambda x:x[b.VARIANTS[1]].update(quantized_module_count=0),
                lambda x:x[b.VARIANTS[1]].update(generation_args=[]),
                lambda x:x[b.VARIANTS[1]]['rows'][0].update(tts_seconds=float('nan')),
                lambda x:x[b.VARIANTS[1]]['rows'].pop()):
            x=self.variants();change(x)
            with self.assertRaises(ValueError):b.compare_results(x,{'cpu_models':['test']})
    def test_eos_or_signal_failure_suppresses_speedup(self):
        for key in ('natural_eos','signal_valid'):
            x=self.variants();x[b.VARIANTS[1]]['rows'][0][key]=False
            self.assertIsNone(b.compare_results(x,{'cpu_models':['test']})['observed_rtf_speedup'])
    def test_rss_current_and_high_water_stay_distinct(self):
        self.assertEqual(b.rss_snapshot('VmRSS:\t100 kB\nVmHWM:\t250 kB'),
            {'current_rss_kib':100,'peak_rss_kib':250})
        with self.assertRaises(ValueError):b.rss_snapshot('VmRSS:\t0 kB')
    def test_timeout_writes_explicit_failure(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(b,'hardware',return_value={}),patch.object(b.subprocess,'run',side_effect=b.subprocess.TimeoutExpired('worker',600)):
            out=Path(tmp)/'out';self.assertEqual(b.run('/fake',out),1)
            self.assertIn('quant_worker_timeout',(out/'summary.json').read_text())
    def test_pinned_request_and_production_sampling(self):
        b.validate_request()
        args=b.generation_args('production_style',b.FIXTURES[0])
        self.assertIn('instruct',args);self.assertEqual(args['temperature'],0.8)
        self.assertEqual(args['non_streaming_mode'],True)
    def test_observed_model_manifest_covers_required_files_without_trust_claim(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'nested').mkdir()
            (root/'model.bin').write_bytes(b'weight');(root/'nested/config.json').write_bytes(b'{}')
            required={'model.bin':None,'nested/config.json':None}
            r=b.observed_model_manifest(root,required)
            self.assertEqual(r['required_file_count'],2)
            self.assertEqual({x['path'] for x in r['files']},set(required))
            self.assertFalse(r['independent_trust_anchor'])
            self.assertEqual(r['files'][0]['sha256'],hashlib.sha256(b'weight').hexdigest())
            with self.assertRaises(ValueError):b.observed_model_manifest(root,{'missing':None})
            with self.assertRaises(ValueError):b.observed_model_manifest(root,{'../escape':None})


if __name__=='__main__':unittest.main()
