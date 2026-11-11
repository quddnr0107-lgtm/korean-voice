import unittest
from candidate_eval import content_metrics, distance

class ComparisonTest(unittest.TestCase):
    def test_edit_distance(self):
        self.assertEqual(distance('abc','axbc'),1)
        self.assertEqual(distance('abc','ac'),1)
        self.assertEqual(distance('abc','axc'),1)
    def test_number_equivalence(self):
        self.assertEqual(content_metrics('제2호, 군간부후보생','제이호 군간부후보생')['cer'],0)
    def test_real_omission(self):
        m=content_metrics('자격을 가진 사람으로서 국가공무원법','자격으로 서국가공무원법',('자격을 가진 사람으로서',))
        self.assertEqual(m['status'],'review')
        self.assertTrue(m['missing_critical'])
        self.assertGreater(m['cer'],0)
    def test_repetition(self):
        self.assertGreater(content_metrics('병역증','병역증 병역증')['cer'],0)
    def test_punctuation_is_not_omission(self):
        self.assertEqual(content_metrics('사람으로서, 국가공무원법.','사람으로서 국가공무원법')['cer'],0)

if __name__=='__main__':unittest.main()
