import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import compiled_fp32_renderer as compiled


class FakeTorch:
    __version__ = '2.8.0+cpu'
    def __init__(self, execute_backend=True):
        self.interop, self.intraop = 2, 2
        self.compile_calls = 0
        self.execute_backend = execute_backend
        self.compiler = SimpleNamespace(disable=self.disable)
    def get_num_interop_threads(self): return self.interop
    def set_num_interop_threads(self, value): self.interop = value
    def get_num_threads(self): return self.intraop
    def set_num_threads(self, value): self.intraop = value
    def disable(self, fn, recursive):
        assert recursive is True
        return lambda *args, **kwargs: fn(*args, **kwargs)
    def compile(self, fn, **kwargs):
        self.compile_calls += 1
        assert kwargs['fullgraph'] is False and kwargs['dynamic'] is True
        retained = None
        def call(*args, **options):
            nonlocal retained
            if retained is None:
                retained = kwargs['backend'](fn, []) if self.execute_backend else fn
            return retained(*args, **options)
        return call


class CompiledTests(unittest.TestCase):
    def settings(self):
        return dict(schema='bora-python-fp32-production/v1', engine='qwen-tts-python',
            dtype='float32', threads=4, attention='sdpa', device='cpu',
            styles=copy.deepcopy(compiled.original.STYLES),
            sampling=dict(compiled.original.SAMPLING, max_new_tokens=1100),
            seeds=[5192, 7319], filters=copy.deepcopy(compiled.original.FILTERS),
            code_sha256={'bora-production/fp32_renderer.py': 'a'*64})

    @contextlib.contextmanager
    def renderer(self, *, execute_backend=True, error=None):
        torch = FakeTorch(execute_backend)
        target = SimpleNamespace(forward=lambda value: value+1)
        original_forward = target.forward
        masks = {name: (lambda value: value) for name in compiled.MASK_ALIASES}
        module = SimpleNamespace(**masks)
        settings = self.settings(); original_settings = copy.deepcopy(settings)
        model = SimpleNamespace(model=SimpleNamespace(talker=SimpleNamespace(
            code_predictor=SimpleNamespace(model=target))))
        calls = []
        def initialize(renderer, *args, **kwargs):
            self.assertEqual((torch.interop, torch.intraop), (1, 4))
            renderer.settings = copy.deepcopy(settings)
            renderer.settings_sha256 = compiled.original.identity(renderer.settings)
            renderer.torch, renderer.model = torch, model
            renderer.seeds = (5192, 7319); renderer.max_new_tokens = 1100
        def candidate(renderer, unit, directory, seed):
            calls.append((copy.deepcopy(unit), seed))
            value = target.forward(10)
            if error is not None: raise error
            return dict(seed=seed, original_value=value, tts_seconds=1.0,
                natural_eos=True, signal=dict(blocked=False),
                asr=dict(blocked=False, review_required=False,
                         character_errors=0, missing_critical=[]))
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {'RUNNER_TEMP': temporary, 'TORCHINDUCTOR_CACHE_DIR': 'inherited-cache'}), \
             patch.object(compiled.original, 'require_guard') as guard, \
             patch.object(compiled.original, 'quiet_native', contextlib.nullcontext), \
             patch.object(compiled.importlib, 'import_module', return_value=torch), \
             patch.object(compiled.original.FP32Renderer, '__init__', initialize), \
             patch.object(compiled.original.FP32Renderer, '_candidate', candidate), \
             patch.object(compiled, 'verified_predictor', return_value=(target, module)), \
             patch.object(compiled, 'compiler_backend', return_value=lambda graph, inputs: graph), \
             patch.object(compiled, 'profile_source_hashes', return_value={
                 'bora-production/compiled_fp32_renderer.py': 'b'*64,
                 'bora-production/execution_profiles.py': 'c'*64}):
            renderer = compiled.CompiledFP32Renderer('/model', '/asr')
            try:
                yield SimpleNamespace(renderer=renderer, torch=torch, target=target,
                    original_forward=original_forward, module=module, masks=masks,
                    calls=calls, settings=settings, original_settings=original_settings,
                    guard=guard, temporary=temporary)
            finally:
                renderer.close()

    def test_offline_guard_runs_before_torch_or_original_model_constructor(self):
        with patch.object(compiled.original, 'require_guard',
                          side_effect=compiled.original.RenderFailure('offline_guard_required')), \
             patch.object(compiled.importlib, 'import_module') as imports, \
             patch.object(compiled.original.FP32Renderer, '__init__') as initialize:
            with self.assertRaisesRegex(compiled.original.RenderFailure, '^offline_guard_required$'):
                compiled.CompiledFP32Renderer('/model', '/asr')
            imports.assert_not_called(); initialize.assert_not_called()

    def test_single_retained_compiler_for_both_modes_and_both_original_seeds(self):
        with self.renderer() as fake:
            renderer = fake.renderer
            before_hash = renderer.settings_sha256
            self.assertEqual(fake.torch.compile_calls, 0)
            for mode, seed in [('explain', 5192), ('reading', 7319), ('explain', 7319)]:
                result = renderer._candidate({'mode': mode}, '/private-output', seed)
                self.assertEqual(result['original_value'], 11)
                self.assertGreater(result['execution_profile']['compiled_graph_invocations'], 0)
                self.assertFalse(result['execution_profile']['fallback_used'])
                self.assertIs(fake.target.forward, fake.original_forward)
                for name, fn in fake.masks.items(): self.assertIs(getattr(fake.module, name), fn)
            self.assertEqual(fake.torch.compile_calls, 1)
            self.assertEqual(fake.calls, [({'mode': 'explain'}, 5192),
                ({'mode': 'reading'}, 7319), ({'mode': 'explain'}, 7319)])
            self.assertEqual(renderer.execution_evidence()['counters']['compiled_graph_invocations'], 3)
            self.assertEqual(renderer.settings_sha256, before_hash)
            self.assertEqual(compiled.original.identity(renderer.settings), before_hash)

    def test_new_profile_never_alters_original_settings_or_quality_controls(self):
        styles, sampler = copy.deepcopy(compiled.original.STYLES), dict(compiled.original.SAMPLING)
        with self.renderer() as fake:
            renderer = fake.renderer
            expected_old = compiled.original.identity(fake.original_settings)
            self.assertEqual(renderer.base_render_settings_sha256, expected_old)
            self.assertNotEqual(renderer.settings_sha256, expected_old)
            self.assertEqual(fake.settings, fake.original_settings)
            for field in ('styles', 'sampling', 'seeds', 'filters', 'dtype', 'attention', 'engine'):
                self.assertEqual(renderer.settings[field], fake.original_settings[field])
            self.assertEqual(renderer.settings['code_sha256']['bora-production/fp32_renderer.py'], 'a'*64)
            profile = renderer.settings['execution_profile']
            self.assertEqual(profile['profile_id'], 'bora-python-fp32-compiled-predictor/v1')
            self.assertEqual(profile['model_source_sha256'], compiled.MODELING_SHA256)
            self.assertEqual(profile['source_sha256']['bora-production/execution_profiles.py'], 'c'*64)
            self.assertNotIn('counters', json.dumps(renderer.settings))
            self.assertNotIn('bora-private-inductor-', json.dumps(renderer.settings))
        self.assertEqual(compiled.original.STYLES, styles)
        self.assertEqual(compiled.original.SAMPLING, sampler)

    def test_errors_and_timeout_restore_original_forward_and_masks(self):
        for error, expected in [(ValueError('PRIVATE_INPUT'), compiled.original.RenderFailure),
                                (TimeoutError('deadline'), TimeoutError)]:
            with self.renderer(error=error) as fake:
                with self.assertRaises(expected) as caught:
                    fake.renderer._candidate({'mode': 'reading'}, '/private', 7319)
                if expected is compiled.original.RenderFailure:
                    self.assertEqual(str(caught.exception), 'compiled_candidate_failed')
                self.assertIs(fake.target.forward, fake.original_forward)
                for name, fn in fake.masks.items(): self.assertIs(getattr(fake.module, name), fn)

    def test_eager_fallback_cannot_be_accepted_as_compiled_execution(self):
        with self.renderer(execute_backend=False) as fake:
            with self.assertRaisesRegex(compiled.original.RenderFailure, '^compiled_execution_not_observed$'):
                fake.renderer._candidate({'mode': 'explain'}, '/private', 5192)
            self.assertEqual(fake.renderer.execution_evidence()['counters']['compiled_graph_invocations'], 0)
            self.assertIs(fake.target.forward, fake.original_forward)

    def test_close_removes_private_cache_and_restores_environment_without_export(self):
        with self.renderer() as fake:
            fake.renderer._candidate({'mode': 'explain'}, '/private', 5192)
            cache = Path(os.environ['TORCHINDUCTOR_CACHE_DIR'])
            self.assertTrue(cache.is_dir())
            self.assertEqual(cache.stat().st_mode & 0o777, 0o700)
            fake.renderer.close(); fake.renderer.close()
            self.assertFalse(cache.exists())
            self.assertEqual(os.environ['TORCHINDUCTOR_CACHE_DIR'], 'inherited-cache')
            with self.assertRaisesRegex(compiled.original.RenderFailure, '^compiled_renderer_closed$'):
                fake.renderer._candidate({'mode': 'explain'}, '/private', 5192)
            self.assertFalse(fake.renderer.execution_evidence()['cache_exported'])

    def test_source_identity_and_required_profile_files_are_enforced(self):
        predictor_type = type('Qwen3TTSTalkerCodePredictorModelForConditionalGeneration', (), {})
        target_type = type('Qwen3TTSTalkerCodePredictorModel', (), {})
        predictor = predictor_type(); predictor.model = target_type()
        model = SimpleNamespace(model=SimpleNamespace(talker=SimpleNamespace(code_predictor=predictor)))
        module = SimpleNamespace(**{name: lambda: None for name in compiled.MASK_ALIASES})
        with patch.object(compiled.inspect, 'getmodule', return_value=module), \
             patch.object(compiled.inspect, 'getsourcefile', return_value='/pinned-qwen.py'), \
             patch.object(compiled.original, 'file_hash', return_value=compiled.MODELING_SHA256):
            self.assertEqual(compiled.verified_predictor(model), (predictor.model, module))
            with patch.object(compiled.original, 'file_hash', return_value='0'*64):
                with self.assertRaisesRegex(compiled.original.RenderFailure, '^compiled_predictor_source_mismatch$'):
                    compiled.verified_predictor(model)
        with tempfile.TemporaryDirectory() as directory, patch.object(compiled.original, 'ROOT', Path(directory)):
            for name in compiled.PROFILE_SOURCES:
                path = Path(directory)/name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            hashes = compiled.profile_source_hashes()
            self.assertEqual(set(hashes), set(compiled.PROFILE_SOURCES))
            for name, value in hashes.items(): self.assertEqual(value, hashlib.sha256(name.encode()).hexdigest())
            (Path(directory)/'bora-production/execution_profiles.py').unlink()
            with self.assertRaisesRegex(compiled.original.RenderFailure, '^compiled_profile_source_missing$'):
                compiled.profile_source_hashes()

    def test_counter_wrapper_cannot_be_bypassed_by_dynamo_unwrapping_attributes(self):
        counts = dict(backend_compile_attempts=0, backend_graphs_compiled=0,
                      compiled_graph_invocations=0, predictor_forward_calls=0)
        def inner(value): return value+1
        inner._torchdynamo_orig_callable = lambda value: value+2
        inner._torchdynamo_disable = True; inner._boxed_call = True
        result = compiled.tracked_backend(lambda graph, args: inner, counts)(None, [])
        self.assertFalse(hasattr(result, '_torchdynamo_orig_callable'))
        self.assertFalse(hasattr(result, '_torchdynamo_disable'))
        self.assertFalse(hasattr(result, '__wrapped__'))
        self.assertTrue(result._boxed_call)
        self.assertEqual(result(3), 4)
        self.assertEqual(counts['compiled_graph_invocations'], 1)

    def test_original_source_review_gate_skips_tts_and_compile(self):
        with self.renderer() as fake, tempfile.TemporaryDirectory() as directory, \
             patch.object(compiled.original, 'validate_unit', return_value=['input_review_required']):
            unit = dict(id='TB-002:p0000:u000', paragraph_id='TB-002:p0000',
                start_utf16=0, end_utf16=2, source_sha256='a'*64, spoken='공개', mode='reading')
            receipt = fake.renderer.render_unit(unit, Path(directory)/'unit')
            self.assertEqual(receipt['status'], 'blocked_requires_review')
            self.assertEqual(receipt['synthesis_skipped'], 'unresolved_input_review')
            self.assertEqual(fake.calls, [])
            self.assertEqual(fake.torch.compile_calls, 0)


if __name__ == '__main__':
    unittest.main()
