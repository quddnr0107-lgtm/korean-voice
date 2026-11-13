import copy
import types
import unittest
import cp_compile_bench as c

class CompileBenchTests(unittest.TestCase):
    def modules(self):
        originals=[types.SimpleNamespace(parameters=lambda:[]) for i in range(5)]
        compiled=[types.SimpleNamespace(_orig_mod=old,parameters=old.parameters) for old in originals]
        layers=[types.SimpleNamespace(mlp=old) for old in originals]
        return layers,originals,compiled
    def test_only_five_mlp_references_are_switched_and_restored(self):
        layers,old,new=self.modules();other=object()
        for layer in layers:layer.self_attn=other
        c.verify_compiled_modules(old,new);c.select_modules(layers,old,new,'cp_mlp_compiled')
        self.assertTrue(all(layer.mlp is new[i] and layer.self_attn is other for i,layer in enumerate(layers)))
        c.select_modules(layers,old,new,'baseline');self.assertTrue(all(layer.mlp is old[i] for i,layer in enumerate(layers)))
    def test_wrong_module_count_or_original_identity_is_rejected(self):
        layers,old,new=self.modules()
        with self.assertRaises(ValueError):c.select_modules(layers[:-1],old,new,'baseline')
        new[0]._orig_mod=object()
        with self.assertRaises(ValueError):c.verify_compiled_modules(old,new)
    def test_compile_proof_requires_graph_kernel_and_all_five_calls(self):
        before={'dynamo':{'stats':{'unique_graphs':0}},'inductor':{'generated_kernel_count':0}}
        after={'dynamo':{'stats':{'unique_graphs':2}},'inductor':{'generated_kernel_count':3}}
        self.assertTrue(c.proof_from_snapshots(before,after,[10]*5)['actual_compilation_verified'])
        self.assertFalse(c.proof_from_snapshots(before,before,[10]*5)['actual_compilation_verified'])
        self.assertFalse(c.proof_from_snapshots(before,after,[10,10,10,10,0])['actual_compilation_verified'])
    def rows(self):
        return [dict(repeat=r,fixture=f,profile=p,status='ok',natural_eos=True,signal_valid=True,
            codec_sha256='code'+str(f),codec_shape=[50,16],pcm_f32_sha256='pcm'+str(f),samples=100,
            sample_rate=24000,generate_custom_voice_seconds=10 if p=='baseline' else 8,
            compiler_events_before=(2,3),compiler_events_after=(2,3)) for r,f,p in c.trial_order(2)]
    def test_exact_output_and_actual_compile_proof_allow_timing_and_amortization(self):
        result=c.summarize(self.rows(),2,{'actual_compilation_verified':True},40)
        self.assertTrue(result['exact_pcm_and_codec_equal']);self.assertAlmostEqual(result['observed_time_reduction_pct'],20)
        self.assertEqual(result['warmup_break_even_two_fixture_batches'],10);self.assertFalse(result['production_eligible'])
    def test_output_change_missing_proof_recompile_or_weight_change_blocks_speed_claim(self):
        for case in ('pcm','codec','eos','recompile','proof','weights'):
            rows=self.rows();proof={'actual_compilation_verified':True};weights=True
            if case=='pcm':rows[1]['pcm_f32_sha256']='different'
            if case=='codec':rows[1]['codec_sha256']='different'
            if case=='eos':rows[1]['natural_eos']=False
            if case=='recompile':rows[1]['compiler_events_after']=(3,4)
            if case=='proof':proof['actual_compilation_verified']=False
            if case=='weights':weights=False
            result=c.summarize(rows,2,proof,40,weights)
            self.assertIsNone(result['observed_time_reduction_pct'],case)
    def test_incomplete_or_duplicate_trials_do_not_pass(self):
        rows=self.rows();self.assertFalse(c.summarize(rows[:-1],2,{'actual_compilation_verified':True},40)['complete'])
        with self.assertRaises(ValueError):c.summarize(rows+[rows[0]],2,{'actual_compilation_verified':True},40)
    def test_balanced_order_and_no_gain_has_no_break_even(self):
        rows=self.rows()
        self.assertEqual(c.trial_order(2)[0:4],[(0,0,'baseline'),(0,0,'cp_mlp_compiled'),(0,1,'cp_mlp_compiled'),(0,1,'baseline')])
        for row in rows:row['generate_custom_voice_seconds']=10
        self.assertIsNone(c.summarize(rows,2,{'actual_compilation_verified':True},40)['warmup_break_even_two_fixture_batches'])

if __name__=='__main__':unittest.main()
