import unittest
from unittest.mock import patch

import rl027_candidate_text as check


class CandidateTextCheck(unittest.TestCase):
    # Synthetic clauses exercise the gates without publishing the manuscript.
    phrase = "제삼항 제십이조제이항을 확인하여 삼년 이하의 징역 또는 삼천만원 이하의 벌금."

    def score(self, transcript):
        expected = check.digest(check.canonical_words(self.phrase))
        with patch.object(check, "SPOKEN_DIGEST", expected):
            return check.score_spoken_words(transcript)

    def test_equivalent_numeric_spellings_and_spaces(self):
        for fine in ("3천만 원", "3,000만 원", "30,000,000 원", "삼천만 원"):
            variant = f"제3항, 제12조 제2항을 확인하여 3년 이하의 징역 또는 {fine} 이하의 벌금."
            self.assertTrue(self.score(variant)["accepted"])

    def test_lexical_errors_and_missing_marker_fail(self):
        for old, new in (("제삼항", ""), ("제삼항", "제이항"), ("제이항을", "제일항을"),
                         ("삼년", "이년"), ("삼천만원", "이천만원"), ("또는", "및"),
                         ("확인하여", "확인하지 아니하여"), ("삼년", "-3년"),
                         ("삼천만원", "3000.0000원"), ("삼천만원", "3000,0000원")):
            self.assertFalse(self.score(self.phrase.replace(old, new))["accepted"])

    def test_hashes_bind_the_two_source_positions(self):
        text = "「③ 합성 시험용 문장.」"
        record = {"id": "RL-027", "narration_text": text + text,
                  "segments": [None] * 98, "unit_measurements": [None] * 98}
        for pos, (i, uid) in enumerate(zip((check.TARGET, check.DONOR), check.UNIT_IDS)):
            record["segments"][i] = dict(unit_id=uid, mode="reading", start_cp=pos * len(text),
                                         end_cp=(pos + 1) * len(text), text=text)
            record["unit_measurements"][i] = dict(unit_id=uid, unit_index=i, input_sha256="a" * 64)
        with patch.object(check, "SOURCE_DIGEST", check.digest(text)):
            self.assertTrue(check.validate_donor(record))
            record["unit_measurements"][97]["input_sha256"] = "b" * 64
            self.assertFalse(check.validate_donor(record))
            record["segments"][97]["mode"] = "explain"
            with self.assertRaises(ValueError):
                check.validate_donor(record)
            record["segments"][97].update(mode="reading", text=text.replace("③", ""))
            with self.assertRaises(ValueError):
                check.validate_donor(record)
