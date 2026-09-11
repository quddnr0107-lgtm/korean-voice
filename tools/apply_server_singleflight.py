from pathlib import Path

p = Path('server/server.py')
s = p.read_text()

old = """sys.path.insert(0, ROOT)\nimport voice_shape as VS  # noqa: E402  — 지금 조합(기본)"""
new = """sys.path.insert(0, ROOT)\nfrom singleflight import KeyedSingleflight  # noqa: E402\nimport voice_shape as VS  # noqa: E402  — 지금 조합(기본)"""
assert old in s, 'singleflight import target not found'
s = s.replace(old, new, 1)

old = """_stats = {'synth': 0, 'hit': 0, 'synth_s': 0.0}"""
new = """_stats = {'synth': 0, 'hit': 0, 'dedup': 0, 'synth_s': 0.0}\n_singleflight = KeyedSingleflight()"""
assert old in s, 'stats target not found'
s = s.replace(old, new, 1)

old = """        try:\n            path = os.path.join(CACHE, voice, cache_key(voice, text, steps, r, tag) + '.mp3')\n            if not os.path.exists(path):\n                synthesize(voice, text, steps, r, tag)\n        except Exception as e:"""
new = """        try:\n            cached_or_synthesize(voice, text, steps, r, tag)\n        except Exception as e:"""
assert old in s, 'warm worker target not found'
s = s.replace(old, new, 1)

old = """def cache_key(voice, text, steps, r=1.0, tag=None):\n    # 🔴 worker.mjs 의 키와 같은 꼴(sha1(\"voice|steps|r|tag|text\")) — r 은 소수 둘째 자리까지\n    return hashlib.sha1(f'{voice}|{steps}|{fmt_r(r)}|{RC.get(tag).RECIPE_TAG}|{text}'.encode('utf-8')).hexdigest()\n\n\ndef fmt_r(r):"""
new = """def cache_key(voice, text, steps, r=1.0, tag=None):\n    # 🔴 worker.mjs 의 키와 같은 꼴(sha1(\"voice|steps|r|tag|text\")) — r 은 소수 둘째 자리까지\n    return hashlib.sha1(f'{voice}|{steps}|{fmt_r(r)}|{RC.get(tag).RECIPE_TAG}|{text}'.encode('utf-8')).hexdigest()\n\n\ndef cached_or_synthesize(voice, text, steps, r=1.0, tag=None):\n    \"\"\"Disk cache + keyed singleflight shared by live and warm requests.\"\"\"\n    tag = RC.get(tag).RECIPE_TAG\n    key = cache_key(voice, text, steps, r, tag)\n    path = os.path.join(CACHE, voice, key + '.mp3')\n\n    def ready():\n        return path if os.path.exists(path) else None\n\n    def produce():\n        return synthesize(voice, text, steps, r, tag)\n\n    out, state = _singleflight.run(key, ready, produce)\n    if state != 'owner':\n        _stats['hit'] += 1\n    if state == 'waiter':\n        _stats['dedup'] += 1\n    return out\n\n\ndef fmt_r(r):"""
assert old in s, 'cache helper insertion target not found'
s = s.replace(old, new, 1)

old = """        path = os.path.join(CACHE, voice, cache_key(voice, text, steps, r, tag) + '.mp3')\n        if os.path.exists(path):\n            _stats['hit'] += 1\n        else:\n            try:\n                path = synthesize(voice, text, steps, r, tag)\n            except Exception as e:\n                return self._json({'ok': False, 'error': 'synthesis_failed', 'reason': str(e)[:300]}, 502)\n        self._send_file(path, tag)"""
new = """        try:\n            path = cached_or_synthesize(voice, text, steps, r, tag)\n        except Exception as e:\n            return self._json({'ok': False, 'error': 'synthesis_failed', 'reason': str(e)[:300]}, 502)\n        self._send_file(path, tag)"""
assert old in s, 'live tts target not found'
s = s.replace(old, new, 1)

p.write_text(s)
