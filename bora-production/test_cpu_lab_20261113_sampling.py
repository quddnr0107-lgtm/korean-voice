"""Probability tests compile the actual patched sampler, without model weights.

Set CPU_LAB_SOURCE to the unmodified pinned C checkout. No downloads occur here.
The header and matvec stub only remove model/kernel dependencies from the test;
the sampler and experimental CP selection function are extracted unchanged.
"""
import ctypes
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import cpu_lab_20261113_sampling as lab
import cbackend_sohee_benchmark as backend


def probabilities(logits, temperature, top_k, top_p, penalty, previous, *, legacy=False):
    """Independent double-precision reference of HF filtering/penalty rules."""
    values = list(logits)
    tokens = previous if legacy else set(previous)
    for token in tokens:
        if 0 <= token < len(values):
            values[token] = values[token] / penalty if values[token] > 0 else values[token] * penalty
    maximum = max(values)
    values = [math.exp((value - maximum) / temperature) for value in values]
    total = sum(values); values = [value / total for value in values]
    if 0 < top_k < len(values):
        cutoff = sorted(values, reverse=True)[top_k - 1]
        values = [value if value >= cutoff else 0.0 for value in values]
    if not legacy:
        total = sum(values); values = [value / total for value in values]
    if 0 < top_p < 1:
        if legacy:
            order = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
            accumulated = 0.0; keep = set()
            for index in order:
                keep.add(index); accumulated += values[index]
                if accumulated > top_p: break
            values = [value if i in keep else 0.0 for i, value in enumerate(values)]
        else:
            # HF sorts ascending and removes low-mass tokens with cumulative
            # probability <= 1-p, retaining at least the highest token.
            order = sorted(range(len(values)), key=lambda i: values[i])
            accumulated = 0.0
            for index in order[:-1]:
                accumulated += values[index]
                if accumulated <= 1.0 - top_p: values[index] = 0.0
    total = sum(values)
    return [value / total for value in values]


class RunnerContractTests(unittest.TestCase):
    def test_public_two_fixtures_same_recipe_and_explicit_switches(self):
        recipe = lab.bora.production_recipe()
        self.assertEqual(len(backend.FIXTURES), 2)
        for index in range(2):
            command = lab.generation_command('bin', 'model', 'public.wav', index, recipe)
            for flag, value in {'--text':backend.FIXTURES[index], '-j':'4', '-s':'sohee',
                    '--instruct':recipe['styles']['explain'], '--seed':str(5192 + index),
                    '--temperature':'0.8', '--top-p':'0.9', '--rep-penalty':'1.05',
                    '--eos-strategy':'off', '--max-tokens':'1100'}.items():
                self.assertEqual(command[command.index(flag) + 1], value)
        for case, flags in lab.FLAGS.items():
            env = lab.child_environment(case)
            self.assertEqual((env[lab.HF_FLAG], env[lab.CP_FLAG]), tuple(map(str, flags)))
            self.assertNotIn('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', env)
            self.assertNotIn('QWEN_CP_PREC', env)

    def test_active_options_need_exact_native_evidence(self):
        for case, (hf, cp) in lab.FLAGS.items():
            log = ('\n' + lab.HF_MARKER) * hf + ('\n' + lab.CP_MARKER) * cp
            self.assertTrue(lab.option_evidence(log, case)['native_option_markers_verified'])
            self.assertFalse(lab.option_evidence(log + lab.CP_MARKER, case)['native_option_markers_verified'])

    def rows(self):
        return [dict(case=case, fixture=i, seed=5192+i, process_wall_seconds=10.0,
            audio_seconds=4.0, natural_eos_observed=True, signal_valid=True,
            decoded_pcm_frame_coverage_verified=True, native_option_markers_verified=True)
            for case in lab.CASES for i in range(2)]

    def test_summary_never_promotes_or_claims_random_identity(self):
        recipe = lab.bora.production_recipe(); rows = self.rows()
        summary = lab.summarize(rows, recipe)
        self.assertTrue(summary['complete_natural_eos_and_signal'])
        self.assertFalse(summary['production_eligible'])
        self.assertFalse(summary['human_listening_verified'])
        self.assertEqual(len(summary['sampling_rule_changes']), 3)
        self.assertTrue(any('random draws' in s for s in summary['limitations']))
        rows[0]['native_option_markers_verified'] = False
        summary = lab.summarize(rows, recipe)
        self.assertFalse(summary['complete_natural_eos_and_signal'])
        self.assertIsNone(summary['totals']['baseline']['process_seconds_per_audio_second'])
        self.assertIsNone(lab.summarize([], recipe)['totals']['both']['process_seconds_per_audio_second'])
        rows = self.rows(); rows[0]['audio_seconds'] = float('nan')
        with self.assertRaises(ValueError): lab.summarize(rows, recipe)
        rows = self.rows(); rows.append(dict(rows[0]))
        with self.assertRaises(ValueError): lab.summarize(rows, recipe)


class NativeProbabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('CPU_LAB_SOURCE')
        if not supplied:
            raise unittest.SkipTest('CPU_LAB_SOURCE must point to the pinned source for actual native tests')
        source = Path(supplied)
        lab.validate_source_files(source)
        if not shutil.which('gcc'):
            raise unittest.SkipTest('gcc is required for the standalone native probability harness')
        cls.temporary = tempfile.TemporaryDirectory(prefix='cpu-lab-probability-')
        cls.root = Path(cls.temporary.name)
        for name in lab.BEFORE:
            shutil.copyfile(source / name, cls.root / name)
        subprocess.run(['git', 'apply', '--check', str(lab.PATCH)], cwd=cls.root,
            check=True, capture_output=True)
        subprocess.run(['git', 'apply', str(lab.PATCH)], cwd=cls.root,
            check=True, capture_output=True)
        lab.validate_source_files(cls.root, patched=True)
        # Upstream files were read/copied, not modified by the test.
        lab.validate_source_files(source)
        (cls.root / 'qwen_tts.h').write_text('#include <stdint.h>\n')
        cp = (cls.root / 'qwen_tts_code_predictor.c').read_text()
        begin = cp.index('/* Isolated CPU single-request experiment;')
        end = cp.index('int qwen_cp_predict(', begin)
        selection = cp[begin:end]
        cls.patched_cp = cp
        harness = r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#define QWEN_TTS_CODEBOOK_SIZE 2048
#include "qwen_tts_sampling.c"
/* Tiny, independent BF16 matrix multiplication for selection-path testing. */
static void qwen_matvec_bf16(float *out, const uint16_t *weights,
                            const float *x, int rows, int cols) {
    for (int r = 0; r < rows; r++) {
        float sum = 0;
        for (int c = 0; c < cols; c++) {
            uint32_t bits = (uint32_t)weights[r*cols+c] << 16;
            float value; memcpy(&value, &bits, sizeof value);
            sum += value * x[c];
        }
        out[r] = sum;
    }
}
''' + selection + r'''
int lab_distribution(float *logits, int n, float temperature, int top_k,
                     float top_p, float penalty, int *past, int count,
                     unsigned seed, int hf_enabled) {
    setenv("QWEN_CPU_LAB_HF_SAMPLER", hf_enabled ? "1" : "0", 1);
    qwen_set_seed(seed);
    return qwen_tts_sample(logits, n, temperature, top_k, top_p, penalty, past, count);
}
int lab_cp_select(const uint16_t *head, const float *x, int hidden, unsigned seed) {
    qwen_set_seed(seed);
    return cpu_lab_cp_sample(x, head, hidden, QWEN_TTS_CODEBOOK_SIZE);
}
unsigned lab_seed(void) { return qwen_get_seed(); }
int lab_topp_rule(float *probs, int n, float p, int hf) {
    ensure_work_buffers(n);
    return topp_filter(probs, n, p, hf);
}
'''
        (cls.root / 'harness.c').write_text(harness)
        subprocess.run(['gcc', '-std=gnu11', '-O3', '-ffast-math', '-shared', '-fPIC',
            str(cls.root / 'harness.c'), '-lm', '-o', str(cls.root / 'harness.so')],
            check=True, capture_output=True, timeout=30)
        cls.native = ctypes.CDLL(str(cls.root / 'harness.so'))
        cls.native.lab_distribution.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_int,
            ctypes.c_float, ctypes.c_int, ctypes.c_float, ctypes.c_float,
            ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.c_uint, ctypes.c_int]
        cls.native.lab_distribution.restype = ctypes.c_int
        cls.native.lab_cp_select.argtypes = [ctypes.POINTER(ctypes.c_uint16),
            ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_uint]
        cls.native.lab_cp_select.restype = ctypes.c_int
        cls.native.lab_seed.restype = ctypes.c_uint
        cls.native.lab_topp_rule.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_int,
            ctypes.c_float, ctypes.c_int]
        cls.native.lab_topp_rule.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'temporary'): cls.temporary.cleanup()

    def distribution(self, values, *, temperature=.8, top_k=0, top_p=1.0,
                     penalty=1.0, previous=(), seed=5192, hf=True):
        vector = (ctypes.c_float * len(values))(*values)
        history = (ctypes.c_int * len(previous))(*previous)
        selected = self.native.lab_distribution(vector, len(values), temperature, top_k,
            top_p, penalty, history, len(previous), seed, int(hf))
        return selected, list(vector)

    def assert_distribution(self, actual, expected):
        self.assertAlmostEqual(sum(actual), 1.0, places=5)
        for a, b in zip(actual, expected, strict=True):
            self.assertAlmostEqual(a, b, delta=2e-5)

    def test_repeated_history_penalty_matches_once_per_token_including_negative_logits(self):
        values = [3.0, -2.0, .5, -.7]; previous = [0, 0, 1, 1, -1, 5000]
        _, actual = self.distribution(values, penalty=1.2, previous=previous)
        expected = probabilities(values, .8, 0, 1.0, 1.2, previous)
        self.assert_distribution(actual, expected)
        _, legacy = self.distribution(values, penalty=1.2, previous=previous, hf=False)
        self.assert_distribution(legacy, probabilities(values, .8, 0, 1.0, 1.2, previous, legacy=True))
        self.assertGreater(abs(actual[0] - legacy[0]), .01)

    def test_top_p_recomputes_probability_mass_after_top_k(self):
        values = [math.log(.86), math.log(.09), math.log(.05)]
        _, aligned = self.distribution(values, temperature=1.0, top_k=2, top_p=.9)
        _, legacy = self.distribution(values, temperature=1.0, top_k=2, top_p=.9, hf=False)
        self.assert_distribution(aligned, probabilities(values, 1.0, 2, .9, 1.0, []))
        self.assert_distribution(legacy, probabilities(values, 1.0, 2, .9, 1.0, [], legacy=True))
        self.assertAlmostEqual(aligned[0], 1.0, places=6)
        self.assertGreater(legacy[1], .09)

    def test_exact_top_p_boundary_keeps_minimal_mass_instead_of_an_extra_token(self):
        aligned = (ctypes.c_float * 3)(.5, .3, .2)
        legacy = (ctypes.c_float * 3)(.5, .3, .2)
        self.assertEqual(self.native.lab_topp_rule(aligned, 3, .5, 1), 1)
        self.assertEqual(self.native.lab_topp_rule(legacy, 3, .5, 0), 2)
        self.assertEqual(list(aligned), [.5, 0.0, 0.0])

    def test_seeded_sampler_uses_reference_cdf_without_claiming_torch_rng_identity(self):
        values = [2.0, 1.0, .2, -.3, -1.1]
        expected = probabilities(values, .9, 3, 1.0, 1.0, [])
        for seed in (1, 2, 5192, 7319, 0xFFFFFFFF):
            selected, actual = self.distribution(values, temperature=.9, top_k=3, seed=seed)
            state = (seed * 1103515245 + 12345) & 0xFFFFFFFF
            uniform = ((state >> 16) & 0x7FFF) / 32768
            accumulated = 0.0
            for index, probability in enumerate(expected):
                accumulated += probability
                if uniform < accumulated: break
            self.assertEqual(selected, index)
            self.assertEqual(self.native.lab_seed(), state)
            self.assert_distribution(actual, expected)

    def test_code_predictor_uses_full_logits_and_sampled_head_in_both_single_branches(self):
        self.assertEqual(self.patched_cp.count('= cpu_lab_cp_sample(cp_normed,'), 2)
        self.assertIn('if (ctx->use_int8 || ctx->use_int4 || ctx->cp_roughness > 0.0f) abort();', self.patched_cp)
        head = (ctypes.c_uint16 * 2048)(*[0, 0] + [0xC2C8] * 2046)  # 0, 0, then BF16 -100
        x = (ctypes.c_float * 1)(1.0)
        observed = set()
        for seed in range(1, 65):
            selected = self.native.lab_cp_select(head, x, 1, seed)
            state = (seed * 1103515245 + 12345) & 0xFFFFFFFF
            self.assertEqual(selected, int(((state >> 16) & 0x7FFF) >= 16384))
            self.assertEqual(self.native.lab_seed(), state)
            observed.add(selected)
        self.assertEqual(observed, {0, 1})


if __name__ == '__main__': unittest.main()
