import unittest

import numpy as np

from rl027_audio_check import ANALYSIS_RATE, RATE, measurements, playback_signal


class SignalCheck(unittest.TestCase):
    def test_stable_tone_and_dropout_have_correct_lesson_times_at_each_speed(self):
        t = np.arange(RATE * 5) / RATE
        rng = np.random.default_rng(27)
        data = rng.normal(0, .07, size=len(t))
        tone = (t >= 1) & (t < 2.5)
        data[tone] = .12 * np.sin(2 * np.pi * 490 * t[tone])
        data[(t >= 3.1) & (t < 4.3)] = 0
        for speed in (1.0, 1.2):
            signal = playback_signal((data * 32767).astype("<i2").tobytes(), speed)
            result = measurements(signal, 705, speed)
            self.assertEqual(len(result["tone_runs"]), 1)
            self.assertEqual(len(result["quiet_runs"]), 1)
            self.assertAlmostEqual(result["tone_runs"][0]["dominant_hz"], 490, delta=10)
            self.assertAlmostEqual(result["tone_runs"][0]["from_s"], 706, delta=.15)
            self.assertAlmostEqual(result["quiet_runs"][0]["to_s"], 709.3, delta=.15)

    def test_a_pitch_glide_is_not_a_stable_tone(self):
        t = np.arange(2 * ANALYSIS_RATE) / ANALYSIS_RATE
        chirp = .12 * np.sin(2 * np.pi * (180 * t + 275 * t ** 2))
        result = measurements(chirp, 700, 1)
        self.assertEqual(result["tone_runs"], [])
        self.assertEqual(result["quiet_runs"], [])

    def test_decode_authenticated_parts_and_reject_tampering(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import delivery
        import lecture_crypto as crypto
        import lecture_pack as pack
        import rl027_audio_check as check
        # Two separately encoded parts across a boundary use an ephemeral key.
        key = bytes(range(32))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parts = []
            blob = b""
            for index in (0, 1):
                dst = root / f"{index}.mp3"
                import subprocess
                subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi",
                                "-i", f"sine=frequency={330 + 220 * index}:duration=2",
                                "-ar", str(RATE), "-ac", "1", str(dst)], check=True)
                part, data = pack.seal_part(key, dst.read_bytes(), check.LESSON, "2" * 32, index,
                                            2 * index * RATE, 2 * (index + 1) * RATE, len(blob))
                parts.append(part)
                blob += data
            audio = pack.make_pack_manifest(check.LESSON, "2" * 32, parts, blob)
            audio_path = root / delivery.media_name(check.LESSON, "audio", audio)
            audio_path.write_bytes(blob)
            plain = json.dumps({"id": check.LESSON, "audio_pack_sha256": audio["pack_sha256"],
                                "segments": []}).encode()
            metadata, data = crypto.seal(key, plain, check.LESSON, "private-metadata", delivery.KEY_ID)
            (root / delivery.media_name(check.LESSON, "content", metadata)).write_bytes(data)
            descriptor = json.dumps({"lesson_id": check.LESSON, "version": "integrated-lecture-output/v2",
                                     "audio": audio, "content": metadata}).encode()
            (root / f"{check.LESSON}.descriptor.json").write_bytes(descriptor)
            with patch.multiple(check, START=1, END=3, DESCRIPTOR_DIGEST=crypto.digest(descriptor)):
                pcm, segments, bounds = check.recover(root, key)
                self.assertEqual(len(pcm), 2 * 2 * RATE)
                self.assertEqual([x["decoded_sample_delta"] for x in bounds], [0, 0])
                self.assertEqual(segments, [])
                audio_path.write_bytes(blob[:-1] + bytes([blob[-1] ^ 1]))
                with self.assertRaisesRegex(ValueError, "cipher chunk digest"):
                    check.recover(root, key)
