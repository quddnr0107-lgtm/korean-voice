import math
from pathlib import Path
from struct import pack
import subprocess
import tempfile
import unittest

from mp3_gapless import crc16, repair


class Mp3GaplessTests(unittest.TestCase):
    def test_padding_repair_preserves_audio_and_exact_decoded_length(self):
        # A remainder of 1 in a 1152-sample frame exposes 46 surplus samples.
        expected = 10 * 1152 + 1
        pcm = b''.join(pack('<f', .17 * math.sin(2 * math.pi * 220 * n / 44100))
                       for n in range(expected))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'padding-edge.mp3'
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'f32le', '-ar', '44100',
                            '-ac', '1', '-i', 'pipe:0', '-map_metadata', '-1', '-c:a', 'libmp3lame',
                            '-b:a', '128k', '-write_xing', '1', str(path)], input=pcm, check=True)

            def decode():
                return subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
                                       '-f', 'f32le', '-ac', '1', '-ar', '44100', 'pipe:1'],
                                      check=True, capture_output=True).stdout

            original = path.read_bytes()
            decoded = decode()
            decoded_samples = len(decoded) // 4
            self.assertEqual(decoded_samples, expected + 46)
            header = 10 + sum(original[6 + i] << (7 * (3 - i)) for i in range(4))
            xing = header + 21

            def retag(encoder):
                variant = bytearray(original)
                variant[xing + 120:xing + 129] = encoder
                variant[xing + 154:xing + 156] = b'\0\0'
                variant[xing + 154:xing + 156] = crc16(variant[header:header + 190]).to_bytes(2, 'big')
                return bytes(variant)

            for encoder in (original[xing + 120:xing + 129], b'Lavc60.31', b'Lavc61.3.', b'Lavc61.19'):
                with self.subTest(encoder=encoder):
                    variant = retag(encoder)
                    repaired = repair(variant, expected, decoded_samples)
                    self.assertEqual(repaired[xing + 156:], variant[xing + 156:])
                    self.assertEqual(repaired[:xing + 141], variant[:xing + 141])
                    self.assertEqual(repaired[xing + 144:xing + 154], variant[xing + 144:xing + 154])
                    path.write_bytes(repaired)
                    self.assertEqual(decode(), decoded[:expected * 4])

            with self.assertRaisesRegex(ValueError, 'encoder scope'):
                repair(retag(b'Lavc99.99'), expected, decoded_samples)
            with self.assertRaisesRegex(ValueError, 'not known padding edge'):
                repair(retag(b'Lavc60.31'), expected, decoded_samples - 1)
            damaged = bytearray(retag(b'Lavc60.31'))
            damaged[-1] ^= 1
            with self.assertRaisesRegex(ValueError, 'audio CRC'):
                repair(bytes(damaged), expected, decoded_samples)
