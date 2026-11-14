"""Explicit compiled execution profile; original renderer and QA stay intact.

One inner predictor callable is retained per worker. Only numeric execution
evidence enters private receipts. Compiler caches are private temporary files.
"""
from __future__ import annotations
import contextlib
import copy
import functools
import importlib
import inspect
import os
from pathlib import Path
import tempfile
import threading
import time

import fp32_renderer as original

PROFILE_ID = 'bora-python-fp32-compiled-predictor/v1'
PROFILE_SCHEMA = 'bora-private-compiled-execution-profile/v1'
MODELING_SHA256 = '25c42656bcf810f06ef6bc1839bd7083f3c8cfedac3a147c4060b4262b1c96a0'
TARGET_PATH = 'model.model.talker.code_predictor.model.forward'
MASK_ALIASES = ('create_causal_mask', 'create_sliding_window_causal_mask')
MAX_GRAPHS = 64
PROFILE_SOURCES = ('bora-production/compiled_fp32_renderer.py',
                   'bora-production/execution_profiles.py')
_MASK_LOCK = threading.Lock()


def require(value, code):
    if not value:
        raise original.RenderFailure(code)


def prepare_torch():
    """Called only after the original offline guard, before model loading."""
    torch = importlib.import_module('torch')
    require(torch.__version__ == '2.8.0+cpu', 'compiled_torch_version_mismatch')
    if torch.get_num_interop_threads() != 1:
        torch.set_num_interop_threads(1)
    torch.set_num_threads(4)
    require(torch.get_num_threads() == 4 and torch.get_num_interop_threads() == 1,
            'compiled_native_threads_mismatch')
    return torch


def verified_predictor(model):
    predictor = model.model.talker.code_predictor
    target = predictor.model
    require(type(predictor).__name__ == 'Qwen3TTSTalkerCodePredictorModelForConditionalGeneration'
        and type(target).__name__ == 'Qwen3TTSTalkerCodePredictorModel',
        'compiled_predictor_type_mismatch')
    module = inspect.getmodule(type(predictor))
    path = inspect.getsourcefile(type(predictor))
    require(module is not None and path is not None
        and original.file_hash(path) == MODELING_SHA256,
        'compiled_predictor_source_mismatch')
    require(all(callable(getattr(module, name, None)) for name in MASK_ALIASES),
            'compiled_mask_alias_mismatch')
    return target, module


def compiler_backend(torch):
    torch._dynamo.config.suppress_errors = False
    torch._dynamo.config.recompile_limit = MAX_GRAPHS
    config = importlib.import_module('torch._inductor.config')
    config.compile_threads = 1
    registry = importlib.import_module('torch._dynamo.backends.registry')
    return registry.lookup_backend('inductor')


def profile_source_hashes():
    hashes = {}
    for name in PROFILE_SOURCES:
        path = original.ROOT/name
        require(path.is_file() and not path.is_symlink(), 'compiled_profile_source_missing')
        hashes[name] = original.file_hash(path)
    return hashes


def tracked_backend(inductor, counts):
    def backend(graph, inputs):
        counts['backend_compile_attempts'] += 1
        require(counts['backend_compile_attempts'] <= MAX_GRAPHS,
                'compiled_graph_budget_exceeded')
        compiled = inductor(graph, inputs)
        require(callable(compiled), 'compiled_backend_result_invalid')
        counts['backend_graphs_compiled'] += 1
        def execute(*args, **kwargs):
            counts['compiled_graph_invocations'] += 1
            return compiled(*args, **kwargs)
        # Copy only the boxed-call ABI flag. Dynamo's private unwrapping attrs
        # must not bypass the counter, as functools.wraps(compiled) could do.
        if hasattr(compiled, '_boxed_call'):
            execute._boxed_call = compiled._boxed_call
        return execute
    return backend


class PredictorController:
    def __init__(self, target, module, torch, backend, counts):
        self.target, self.module, self.counts = target, module, counts
        self.original = target.forward
        self.masks = {name: getattr(module, name) for name in MASK_ALIASES}
        self.disabled_masks = {name: torch.compiler.disable(fn, recursive=True)
            for name, fn in self.masks.items()}
        replacement = torch.compile(self.original, backend=tracked_backend(backend, counts),
                                    fullgraph=False, dynamic=True)
        @functools.wraps(self.original)
        def forward(*args, **kwargs):
            counts['predictor_forward_calls'] += 1
            return replacement(*args, **kwargs)
        self.compiled = forward
        self.closed = False

    def restore(self):
        self.target.forward = self.original
        for name, fn in self.masks.items():
            setattr(self.module, name, fn)

    @contextlib.contextmanager
    def activate(self):
        require(not self.closed, 'compiled_controller_closed')
        require(_MASK_LOCK.acquire(blocking=False), 'compiled_scope_busy')
        try:
            require(self.target.forward == self.original
                and all(getattr(self.module, name) is fn for name, fn in self.masks.items()),
                'compiled_scope_state_mismatch')
            self.target.forward = self.compiled
            for name, fn in self.disabled_masks.items():
                setattr(self.module, name, fn)
            yield
        finally:
            self.restore()
            _MASK_LOCK.release()

    def close(self):
        self.restore()
        self.closed = True


class CompiledFP32Renderer(original.FP32Renderer):
    """Original synthesis/ASR/DSP/selection with an explicit new fingerprint."""
    def __init__(self, *args, **kwargs):
        original.require_guard()
        self._controller = None
        self._cache = None
        self._closed = False
        self._counts = dict(backend_compile_attempts=0, backend_graphs_compiled=0,
            compiled_graph_invocations=0, predictor_forward_calls=0)
        self._setup_seconds = 0.0
        self._previous_cache = os.environ.get('TORCHINDUCTOR_CACHE_DIR')
        try:
            # Set before importing native libraries; preserve the parent's wait
            # policy, which is a separate measured execution choice.
            os.environ.update(OMP_NUM_THREADS='4', MKL_NUM_THREADS='4',
                OPENBLAS_NUM_THREADS='4', TORCHINDUCTOR_COMPILE_THREADS='1', MAX_JOBS='1')
            temporary = os.environ.get('RUNNER_TEMP')
            require(bool(temporary) and Path(temporary).is_dir(), 'compiled_private_temp_missing')
            self._cache = tempfile.TemporaryDirectory(prefix='bora-private-inductor-', dir=temporary)
            os.environ['TORCHINDUCTOR_CACHE_DIR'] = self._cache.name
            with original.quiet_native():
                torch = prepare_torch()
                super().__init__(*args, **kwargs)
                require(self.torch is torch, 'compiled_torch_instance_mismatch')
                self._target, self._modeling_module = verified_predictor(self.model)
            self.base_render_settings_sha256 = original.identity(self.settings)
            require(self.base_render_settings_sha256 == self.settings_sha256,
                    'compiled_base_settings_invalid')
            self.settings = copy.deepcopy(self.settings)
            source_hashes = profile_source_hashes()
            adapter_hash = source_hashes['bora-production/compiled_fp32_renderer.py']
            profile = dict(schema=PROFILE_SCHEMA, profile_id=PROFILE_ID,
                base_render_settings_sha256=self.base_render_settings_sha256,
                target=TARGET_PATH, model_source_sha256=MODELING_SHA256,
                backend='inductor', fullgraph=False, dynamic=True,
                suppress_errors=False, compile_threads=1,
                maximum_compiled_graphs=MAX_GRAPHS, interop_threads=1, intraop_threads=4,
                causal_mask_calculations='original-eager', eager_mask_aliases=list(MASK_ALIASES),
                dtype='float32', original_sampler=True, original_quality_gates=True,
                compiler_cache='private-ephemeral-no-export', adapter_sha256=adapter_hash,
                source_sha256=source_hashes)
            self.settings['execution_profile'] = profile
            self.settings['code_sha256'].update(source_hashes)
            self.execution_profile_sha256 = original.identity(profile)
            self.settings_sha256 = original.identity(self.settings)
            require(self.settings_sha256 != self.base_render_settings_sha256,
                    'compiled_settings_not_separate')
        except TimeoutError:
            self.close()
            raise
        except original.RenderFailure:
            self.close()
            raise
        except Exception:
            self.close()
            raise original.RenderFailure('compiled_renderer_initialization_failed') from None

    def _ensure_controller(self):
        require(not self._closed, 'compiled_renderer_closed')
        if self._controller is None:
            started = time.perf_counter()
            with original.quiet_native():
                self._controller = PredictorController(self._target, self._modeling_module,
                    self.torch, compiler_backend(self.torch), self._counts)
            self._setup_seconds += time.perf_counter()-started

    def execution_evidence(self):
        return dict(profile_id=PROFILE_ID, profile_sha256=self.execution_profile_sha256,
            counters=dict(self._counts), wrapper_setup_seconds=self._setup_seconds,
            cache_exported=False, fallback_used=False)

    def _candidate(self, unit, directory, seed):
        try:
            original.require_guard()
            require(original.identity(self.settings) == self.settings_sha256,
                    'compiled_settings_mutated')
            before = dict(self._counts)
            setup_before = self._setup_seconds
            self._ensure_controller()
            with self._controller.activate(), original.quiet_native():
                candidate = super()._candidate(unit, directory, seed)
            calls = self._counts['compiled_graph_invocations']-before['compiled_graph_invocations']
            require(calls > 0, 'compiled_execution_not_observed')
            candidate['execution_profile'] = dict(profile_id=PROFILE_ID,
                profile_sha256=self.execution_profile_sha256,
                compiled_graph_invocations=calls,
                new_graphs_compiled=self._counts['backend_graphs_compiled']-before['backend_graphs_compiled'],
                predictor_forward_calls=self._counts['predictor_forward_calls']-before['predictor_forward_calls'],
                wrapper_setup_seconds=self._setup_seconds-setup_before,
                fallback_used=False, cache_exported=False)
            return candidate
        except TimeoutError:
            raise
        except original.RenderFailure:
            raise
        except Exception:
            raise original.RenderFailure('compiled_candidate_failed') from None

    def close(self):
        self._closed = True
        if self._controller is not None:
            self._controller.close()
        if self._cache is not None:
            cache = self._cache
            name = cache.name
            self._cache = None
            if os.environ.get('TORCHINDUCTOR_CACHE_DIR') == name:
                if self._previous_cache is None:
                    os.environ.pop('TORCHINDUCTOR_CACHE_DIR', None)
                else:
                    os.environ['TORCHINDUCTOR_CACHE_DIR'] = self._previous_cache
            try:
                cache.cleanup()
            except OSError:
                raise original.RenderFailure('compiled_private_cache_cleanup_failed') from None


Renderer = CompiledFP32Renderer
