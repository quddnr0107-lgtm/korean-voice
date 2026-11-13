import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
import benchmark_public as b

class PublicBenchmarkTests(unittest.TestCase):
    def rows(self):
        epoch=datetime(2026,11,13,tzinfo=timezone.utc)
        def at(n): return (epoch+timedelta(seconds=n)).isoformat()
        rows=[]
        for arm, ids in b.ARMS.items():
            length=80 if arm=='serial' else 20
            rows.append(dict(schema=b.SCHEMA,arm=arm,complete=True,request_sha256='a'*64,
                recipe_sha256='b'*64,fixture_sha256='c'*64,plan_sha256='d'*64,
                render_settings_sha256='e'*64,run_id='123',head_sha='f'*40,
                source_kind='original_public_synthetic_fixture',production_keys_used=False,
                production_inputs_read=False,hardware={'cpu_models':['same'],'logical_cpus':4},
                kernel_s=length,job_first_step_to_render_end_s=length+10,
                started_at=at(10),ended_at=at(10+length),job_first_step=at(0),passed=len(ids),
                units=[dict(index=i,seconds=10,status='passed',unit_key=str(i),
                    source_sha256=str(i),spoken_sha256=str(i),candidate_seeds=[5192],
                    selected_seed=5192,audio_sha256=str(i),character_errors=0,natural_eos=True) for i in ids]))
        return rows

    def test_actual_parallel_envelope_and_setup_penalty(self):
        out=b.summarize(self.rows())
        self.assertEqual(out['status'],'measured_comparable_pilot')
        self.assertEqual(out['observed_kernel_time_reduction_pct'],75)
        self.assertAlmostEqual(out['observed_setup_inclusive_time_reduction_pct'],100*(1-30/90))
        self.assertEqual(out['aggregate_worker_setup_inclusive_s'],210)

    def test_late_parallel_start_is_counted(self):
        rows=self.rows(); rows[-1]['started_at']='2026-11-13T00:00:40+00:00'
        rows[-1]['ended_at']='2026-11-13T00:01:00+00:00'
        self.assertEqual(b.summarize(rows)['parallel_observed_kernel_envelope_s'],50)

    def test_missing_or_duplicate_arms_rejected(self):
        for rows in (self.rows()[:-1],self.rows()[:-1]+[self.rows()[0]]):
            with self.assertRaises(ValueError):b.summarize(rows)

    def test_incomplete_or_missing_units_rejected(self):
        rows=self.rows();rows[0]['complete']=False
        with self.assertRaises(ValueError):b.summarize(rows)
        rows=self.rows();rows[0]['units'].pop()
        with self.assertRaises(ValueError):b.summarize(rows)

    def test_model_and_input_mismatch_rejected(self):
        for key in ('recipe_sha256','render_settings_sha256','fixture_sha256','head_sha'):
            rows=self.rows();rows[-1][key]='different'
            with self.assertRaises(ValueError):b.summarize(rows)
        rows=self.rows();rows[-1]['units'][0]['unit_key']='wrong'
        with self.assertRaises(ValueError):b.summarize(rows)

    def test_retry_difference_never_becomes_comparable_speedup(self):
        rows=self.rows();rows[1]['units'][0]['candidate_seeds']=[5192,7319]
        out=b.summarize(rows)
        self.assertIsNone(out['comparable_setup_inclusive_time_reduction_pct'])
        self.assertFalse(out['same_candidate_work'])

    def test_blocked_quality_is_not_hidden(self):
        rows=self.rows();rows[1]['units'][0]['status']='blocked_requires_review'
        out=b.summarize(rows)
        self.assertFalse(out['all_units_passed'])
        self.assertEqual(out['status'],'timing_with_caveats')

    def test_different_hardware_is_caveated(self):
        rows=self.rows();rows[1]['hardware']['cpu_models']=['different']
        out=b.summarize(rows)
        self.assertFalse(out['matching_hardware'])
        self.assertIsNone(out['comparable_setup_inclusive_time_reduction_pct'])

    def test_bad_durations_and_nonpublic_data_rejected(self):
        for value in (0,-1,float('nan'),float('inf')):
            rows=self.rows();rows[0]['units'][0]['seconds']=value
            with self.assertRaises(ValueError):b.summarize(rows)
        rows=self.rows();rows[0]['production_inputs_read']=True
        with self.assertRaises(ValueError):b.summarize(rows)

    def test_fixture_no_operating_manuscript_load(self):
        text=(Path(b.__file__).read_text())
        for prohibited in ('source_plan(', 'open_input(', 'protected_keys(', 'open_unit(', 'restore('):
            self.assertNotIn(prohibited,text)
        self.assertEqual(len(b.PARAGRAPHS),8)
        self.assertEqual(sorted(i for arm,ids in b.ARMS.items() if arm!='serial' for i in ids),list(range(8)))

if __name__=='__main__':unittest.main()
