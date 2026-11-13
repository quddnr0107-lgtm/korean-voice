import copy
from pathlib import Path
import tempfile
import unittest

import cbackend_bora_sample as sample
import cbackend_sohee_benchmark as backend


class BoraPublicSampleTests(unittest.TestCase):
    def fixtures(self):
        return dict(schema=sample.FIXTURE_SCHEMA, units=[
            dict(id='public-explain', mode='explain', text='원리를 차분하게 설명합니다.'),
            dict(id='public-reading', mode='reading', text='제일조 목적을 읽습니다.')])

    def test_manifest_is_bounded_and_cannot_become_output_path(self):
        self.assertEqual(len(sample.validate_fixtures(self.fixtures())), 2)
        for mutation in (lambda x:x.update(schema='production'),
                lambda x:x['units'].append(copy.deepcopy(x['units'][0])),
                lambda x:x['units'][0].update(id='../escape'),
                lambda x:x['units'][0].update(mode='other'),
                lambda x:x['units'][0].update(text=' '),
                lambda x:x.update(units=x['units'] * 3)):
            manifest = self.fixtures(); mutation(manifest)
            with self.assertRaises(ValueError): sample.validate_fixtures(manifest)

    def test_exact_production_styles_filters_and_supported_talker_flags(self):
        from fp32_renderer import SAMPLING, SEEDS
        from pilot_render import STYLES, FILTERS
        recipe = sample.production_recipe()
        self.assertEqual(recipe['styles'], STYLES)
        self.assertEqual(recipe['filters'], FILTERS)
        self.assertEqual(recipe['sampling'], SAMPLING)
        self.assertEqual(recipe['seed'], SEEDS[0])
        for unit in self.fixtures()['units']:
            args = sample.native_command('bin', 'model', 'raw.wav', unit, recipe)
            for flag, value in {'--temperature':'0.8', '--top-p':'0.9', '--top-k':'50',
                    '--rep-penalty':'1.05', '--eos-strategy':'off', '--max-tokens':'1100',
                    '-j':'4', '-s':'sohee', '-l':'Korean', '--seed':'5192',
                    '--instruct':STYLES[unit['mode']]}.items():
                self.assertEqual(args[args.index(flag) + 1], value)
            self.assertNotIn('--int8', args)
            self.assertNotIn('--rate', args)
            dsp = sample.filter_command('ffmpeg', 'raw.wav', 'processed.wav', unit['mode'], recipe)
            self.assertEqual(dsp[dsp.index('-af') + 1], FILTERS[unit['mode']])

    def test_subtalker_difference_and_rng_are_explicit(self):
        result = sample.sampling_comparison(sample.production_recipe())
        self.assertEqual(result['subtalker_actual'], 'greedy_argmax')
        self.assertIn('subtalker_dosample', result['unsupported_production_controls'])
        self.assertFalse(result['sampling_equivalence_claimed'])
        self.assertFalse(result['fp32_equivalence_claimed'])

    def rows(self):
        return [dict(id=u['id'], mode=u['mode'], tts_process_wall_seconds=6.0,
            raw_audio_seconds=2.0, processed_audio_seconds=1.9,
            natural_eos_observed=True, eos_assistance_enabled=False,
            decoded_pcm_frame_coverage_verified=True,
            raw_signal={'blocked':False}, processed_signal={'blocked':False},
            postprocessing_complete=True) for u in self.fixtures()['units']]

    def test_incomplete_and_invalid_audio_suppress_speed_claim(self):
        units = self.fixtures()['units']; recipe = sample.production_recipe()
        valid = sample.summarize(units, self.rows(), recipe)
        self.assertEqual(valid['tts_seconds_per_raw_audio_second'], 3.0)
        self.assertFalse(valid['production_eligible'])
        self.assertTrue(valid['process_time_includes_each_model_load'])
        for key in ('natural_eos_observed', 'decoded_pcm_frame_coverage_verified', 'postprocessing_complete'):
            rows = self.rows(); rows[0][key] = False
            self.assertIsNone(sample.summarize(units, rows, recipe)['tts_seconds_per_raw_audio_second'])
        self.assertIsNone(sample.summarize(units, self.rows()[:1], recipe)['tts_seconds_per_raw_audio_second'])
        rows = self.rows(); rows[0]['tts_process_wall_seconds'] = float('nan')
        with self.assertRaises(ValueError): sample.summarize(units, rows, recipe)

    def test_only_fresh_directory_outside_production_source_and_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'source'; model = root / 'model'
            source.mkdir(); model.mkdir()
            self.assertEqual(sample.public_output_path(root / 'public-out', source, model), root / 'public-out')
            for path in (source / 'out', model / 'out', sample.ROOT / 'public-out', source):
                with self.assertRaises(ValueError): sample.public_output_path(path, source, model)

    def test_shared_eos_parser_rejects_cap_and_missing_off_evidence(self):
        for text in ('EOS at frame 20\nGenerated 20 frames (1.6s audio)\n',
                'EOS strategy: off (suppress=2)\nEOS at frame 1100\nGenerated 1100 frames (88.0s audio)\n'):
            self.assertFalse(backend.eos_evidence(text)['natural_eos_observed'])


if __name__ == '__main__': unittest.main()
