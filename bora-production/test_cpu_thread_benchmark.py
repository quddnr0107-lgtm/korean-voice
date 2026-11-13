import copy
import unittest
import cpu_thread_benchmark as b


class ThreadTests(unittest.TestCase):
    def info(self,n):return f'omp_get_max_threads() : {n}\nmkl_get_max_threads() : {n}\n'
    def rows(self):
        return [dict(fixture=i,seed=5192+i,threads_requested=n,threads_observed=n,interop_threads=1,
            dtype='float32',device='cpu',parallel_info=self.info(n),tts_seconds=10.0*n,audio_seconds=5.0,
            natural_eos=True,signal_valid=True,wav_sha256='a'*64,
            talker_sequence_sha256='b'*64,
            generation_args=b.generation_args('production_style',b.FIXTURES[i]))
            for i,order in enumerate(b.ORDERS) for n in order]
    def test_settings_keep_production_sampler_and_exact_trial_order(self):
        b.validate_request();self.assertEqual(b.ORDERS,((4,2,1),(1,2,4)))
        args=b.generation_args('production_style',b.FIXTURES[0])
        self.assertIn('instruct',args);self.assertEqual(args['temperature'],0.8)
        self.assertTrue(args['non_streaming_mode'])
    def test_actual_audio_duration_and_inputs_preserved(self):
        rows=self.rows();before=copy.deepcopy(rows);out=b.summarize(rows)
        self.assertEqual(out['totals']['4']['tts_seconds'],80)
        self.assertEqual(out['totals']['4']['audio_seconds'],10)
        self.assertEqual(out['observed_rtf_speedups_vs_4']['1'],4)
        self.assertTrue(out['identical_talker_sequence_hashes_by_fixture']['0'])
        self.assertIn('not full-codebook parity',out['talker_sequence_hash_scope'])
        self.assertEqual(rows,before);self.assertFalse(out['production_eligible'])
    def test_missing_actual_thread_mismatch_or_invalid_timing_rejected(self):
        for change in (lambda r:r.pop(),lambda r:r[0].update(threads_observed=2),
                lambda r:r[0].update(tts_seconds=float('nan')),
                lambda r:r[0].update(generation_args={}),lambda r:r[0].update(audio_seconds=0)):
            rows=self.rows();change(rows)
            with self.assertRaises(ValueError):b.summarize(rows)
    def test_eos_or_signal_failure_suppresses_speed_comparison(self):
        for key in ('natural_eos','signal_valid'):
            rows=self.rows();rows[0][key]=False;out=b.summarize(rows)
            self.assertFalse(out['complete_natural_eos_and_signal'])
            self.assertTrue(all(x is None for x in out['observed_rtf_speedups_vs_4'].values()))
    def test_native_mkl_and_openmp_must_match_requested_threads(self):
        self.assertEqual(b.validate_native_threads(2,2,1,self.info(2))['mkl_get_max_threads'],2)
        for requested,actual,interop,info in [(2,2,1,self.info(4)),(1,1,2,self.info(1)),(2,2,1,''),(True,1,1,self.info(1))]:
            with self.assertRaises(ValueError):b.validate_native_threads(requested,actual,interop,info)


if __name__=='__main__':unittest.main()
