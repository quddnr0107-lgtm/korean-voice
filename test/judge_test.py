import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ko_voice_judge', ROOT / 'research' / 'judge.py')
JUDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(JUDGE)


class JudgeTest(unittest.TestCase):
    def test_cer_identity(self):
        self.assertEqual(JUDGE.cer('제삼 조의 이', '제삼조의이'), 0.0)

    def test_cer_detects_error(self):
        self.assertGreater(JUDGE.cer('케이 투', '케이 구'), 0.0)

    def test_empty_reference(self):
        self.assertEqual(JUDGE.cer('', ''), 0.0)
        self.assertEqual(JUDGE.cer('', '오독'), 1.0)

    def test_ko_voice_canonicalizer_normalizes_military_model(self):
        self.assertEqual(JUDGE.canonicalize_text('K2 전차를 점검한다.'), '케이투 전차를 점검한다.')

    def test_canonical_cer_does_not_penalize_asr_symbol_spelling(self):
        target = '케이투 전차를 점검한다.'
        asr = 'K2 전차를 점검한다.'
        self.assertGreater(JUDGE.cer(target, asr), 0.0)
        self.assertEqual(JUDGE.canonical_cer(target, asr), 0.0)

    def test_canonical_cer_still_detects_semantic_failure(self):
        self.assertGreater(JUDGE.canonical_cer('케이투 전차를 점검한다.', 'KR'), 0.0)

    def test_new_aircraft_gates_canonicalize(self):
        self.assertEqual(JUDGE.canonicalize_text('KF-21 비행시험을 실시한다.'), '케이에프 이십일 비행시험을 실시한다.')
        self.assertEqual(JUDGE.canonicalize_text('FA-50을 운용한다.'), '에프에이 오십을 운용한다.')

    def test_load_jobs_accepts_dict_and_tuple(self):
        data = [
            {'name': 'a', 'path': '/tmp/a.wav', 'target': '제일 항'},
            ['b', '/tmp/b.wav', '케이 투'],
        ]
        with tempfile.NamedTemporaryFile('w+', suffix='.json', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            jobs = JUDGE.load_jobs(f.name)
        self.assertEqual(jobs[0], ('a', '/tmp/a.wav', '제일 항'))
        self.assertEqual(jobs[1], ('b', '/tmp/b.wav', '케이 투'))


if __name__ == '__main__':
    unittest.main()
