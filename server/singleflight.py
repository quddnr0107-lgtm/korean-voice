#!/usr/bin/env python3
"""Small keyed singleflight primitive for the TTS container.

Only callers for the same cache key are coalesced. Different keys may continue
through to the server's existing model lock independently.
"""
import argparse
import os
import tempfile
import threading
import time


class KeyedSingleflight:
    def __init__(self):
        self._guard = threading.Lock()
        self._events = {}

    def run(self, key, ready, produce, timeout=120.0):
        """Return (value, state), where state is hit|owner|waiter.

        ready() returns a cached value or None. Exactly one owner runs produce()
        for a key at a time. Waiters re-check ready() after the owner finishes.
        If an owner fails, its event is always released and a waiter may retry.
        """
        waited = False
        while True:
            cached = ready()
            if cached is not None:
                return cached, 'waiter' if waited else 'hit'

            with self._guard:
                cached = ready()
                if cached is not None:
                    return cached, 'waiter' if waited else 'hit'
                event = self._events.get(key)
                if event is None:
                    event = threading.Event()
                    self._events[key] = event
                    owner = True
                else:
                    owner = False

            if owner:
                try:
                    return produce(), 'owner'
                finally:
                    with self._guard:
                        if self._events.get(key) is event:
                            self._events.pop(key, None)
                    event.set()

            waited = True
            event.wait(timeout)


def _selftest():
    sf = KeyedSingleflight()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'same.mp3')
        calls = [0]
        calls_lock = threading.Lock()
        barrier = threading.Barrier(8)
        results = []
        errors = []

        def ready():
            return path if os.path.exists(path) else None

        def produce():
            with calls_lock:
                calls[0] += 1
            time.sleep(0.05)
            with open(path, 'wb') as f:
                f.write(b'ok')
            return path

        def worker():
            try:
                barrier.wait()
                results.append(sf.run('same', ready, produce, timeout=1.0))
            except Exception as exc:  # pragma: no cover - only for diagnostics
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(2.0)

        assert not errors, errors
        assert all(not t.is_alive() for t in threads), 'waiter deadlock'
        assert calls[0] == 1, f'producer ran {calls[0]} times'
        assert len(results) == 8
        assert sum(1 for _, state in results if state == 'owner') == 1
        assert all(value == path for value, _ in results)
        assert sf.run('same', ready, produce, timeout=1.0)[1] == 'hit'

    # Owner failure must release waiters / leave no stale flight.
    attempts = [0]
    def never_ready():
        return None
    def flaky():
        attempts[0] += 1
        if attempts[0] == 1:
            raise RuntimeError('boom')
        return 'recovered'
    try:
        sf.run('flaky', never_ready, flaky, timeout=0.1)
        raise AssertionError('first owner should fail')
    except RuntimeError:
        pass
    value, state = sf.run('flaky', never_ready, flaky, timeout=0.1)
    assert (value, state, attempts[0]) == ('recovered', 'owner', 2)
    print('singleflight selftest: ok')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--selftest', action='store_true')
    args = p.parse_args()
    if args.selftest:
        _selftest()
