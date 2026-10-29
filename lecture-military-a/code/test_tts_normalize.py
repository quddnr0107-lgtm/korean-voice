"""Synthetic pronunciation cases only; no production lecture text."""
import copy,unittest
import tts_normalize as n
class NormalizeTests(unittest.TestCase):
    def check(self,source,expected):
        spoken,edits,flags=n.normalize(source);self.assertEqual(spoken,expected);self.assertTrue(n.verify(source,spoken,edits));self.assertEqual(n.normalize(spoken)[0],spoken);self.assertEqual(n.normalize(spoken)[1],[]);return edits,flags
    def test_article_number_meaning(self):
        self.check('제3조의2 제10항 제12호 제34조부터 제40조까지','제삼조의이 제십항 제십이호 제삼십사조부터 제사십조까지')
    def test_reviewed_equipment_whole_tokens_only(self):
        self.check('C4I M16 M60 M1 M2 K-2','씨포아이 엠십육 엠육십 엠원 엠투 케이투')
        _,flags=self.check('M16A1 XM16 M16-1 C4IX','M16A1 XM16 M16-1 C4IX');self.assertEqual(len(flags),4)
        self.check('M16기관총과 M1, M2 나침의','엠십육기관총과 엠원, 엠투 나침의')
    def test_native_counters_and_longest_units(self):
        self.check('2명 3개 12개월 제1항 제1대 1시간','두 명 세 개 십이 개월 제일항 제일대 한 시간')
        self.check('1개 20명 21명 99마리 100명','한 개 스무 명 스물한 명 아흔아홉 마리 백 명')
        self.check('3번지 3번 2대대 2대','삼 번지 세 번 이 대대 두 대')
        self.check('제 2대 제3조의2 제12호','제 이대 제삼조의이 제십이호')
        self.check('1.5개','일 점 오 개')
    def test_dates_and_months(self):
        self.check('1996년 9월 18일, 6월 및 10월','천구백구십육년 구월 십팔일, 유월 및 시월')
        self.check('2024. 6. 10.','이천이십사년 유월 십일')
        _,flags=self.check('2024. 13. 40.','2024. 13. 40.');self.assertEqual(flags[0]['reason'],'invalid-date')
    def test_roman_types(self):self.check('Ⅰ형 Ⅱ형 Ⅲ형','일형 이형 삼형')
    def test_clock_preserves_24hours(self):
        self.check('03:30 18:30 3시 18시 24시','세 시 삼십 분 십팔 시 삼십 분 세 시 십팔 시 이십사 시')
        self.check('09:00:05','아홉 시 오 초')
    def test_duration_not_clock(self):self.check('3시간 12시간 20시간 28시간 48시간','세 시간 열두 시간 스무 시간 스물여덟 시간 마흔여덟 시간')
    def test_ambiguous_tokens_remain(self):
        _,flags=self.check('10～20, 1~2, C4I, K1A1, 100Km','십에서 이십, 일에서 이, 씨포아이, 케이원 에이원, 백 킬로미터');self.assertEqual(len(flags),0)
    def test_numeric_decimal_and_case(self):
        self.check('1,234.05 및 2020두12345','천이백삼십사 점 영오 및 이천이십 두 만 이천삼백사십오')
    def test_symbols(self):self.check('10% 1㎡ 2㎢ 3㎥','십퍼센트 일제곱미터 이제곱킬로미터 삼세제곱미터')
    def test_no_modification_of_plain_korean(self):self.check('숫자가 없는 문장은 그대로 둡니다.','숫자가 없는 문장은 그대로 둡니다.')
    def test_explicit_ranges_and_contextual_han(self):
        self.check('15～20% 1∼2H 1～2개','십오 퍼센트에서 이십 퍼센트 한 시간에서 두 시간 한 개에서 두 개')
        self.check('입사호(立射壕)','입사호(입사호)')
        _,flags=self.check('10-20 立射壕','10-20 立射壕');self.assertEqual(len(flags),2)
    def test_latin_and_han_review(self):
        _,flags=self.check('GPS A OX Body 立射壕','지피에스 에이 오엑스 Body 立射壕');self.assertEqual(len(flags),2)
    def test_reversible_and_tamper(self):
        source='제12조와 6월';spoken,edits,_=n.normalize(source);bad=copy.deepcopy(edits);bad[0]['replacement']='삼'
        with self.assertRaises(ValueError):n.verify(source,spoken,bad)
        with self.assertRaises(ValueError):n.verify(source,spoken+'추가',edits)
    def test_invalid_clock_and_number_are_flagged(self):
        _,flags=self.check('25:90 13월','25:90 13월');self.assertEqual(len(flags),2)
if __name__=='__main__':unittest.main()
