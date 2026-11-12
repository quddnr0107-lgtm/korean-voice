import unittest
from paragraph_plan import build_plan,measured_timeline,utf16

class ParagraphTests(unittest.TestCase):
    def test_single_newline_is_a_site_paragraph(self):
        p=build_plan('첫 문단입니다.\n둘째 문단입니다.')
        self.assertEqual(len(p['paragraphs']),2)
        self.assertEqual([u['spoken'] for u in p['units']],['첫 문단입니다.','둘째 문단입니다.'])
    def test_crlf_blank_lines_and_whitespace_preserved(self):
        s='  \r\n 첫 문장입니다.\r\n\r\n둘째 문장입니다.  \n'
        p=build_plan(s)
        self.assertEqual(len(p['paragraphs']),2)
        self.assertEqual(''.join(u['source'] for u in p['units']),s)
        self.assertEqual(''.join(x['source'] for x in p['paragraphs']),s)
    def test_nonbmp_utf16(self):
        p=build_plan('😀 안내입니다.\n다음입니다.')
        self.assertEqual(p['units'][1]['start_utf16'],10)
        self.assertEqual(p['source_utf16_length'],16)
    def test_reading_mode_has_precise_source_spans(self):
        s='설명입니다. 법문을 읽습니다. 여기까지입니다.'
        a=s.index('법문');b=s.index(' 여기')
        p=build_plan(s,reading_spans=[(utf16(s[:a]),utf16(s[:b]))])
        self.assertEqual([u['mode'] for u in p['units']],['explain','reading','explain'])
        self.assertEqual(p['units'][1]['source'],'법문을 읽습니다.')
        self.assertEqual(''.join(u['source'] for u in p['units']),s)
    def test_cross_paragraph_reading_rejected(self):
        with self.assertRaises(ValueError):build_plan('첫째입니다.\n둘째입니다.',reading_spans=[(0,14)])
    def test_long_paragraph_can_split_but_never_merge(self):
        s=('이 문장은 설명을 위한 합성 문장입니다. '*5)+'\n다른 문단입니다.'
        p=build_plan(s,max_chars=40)
        self.assertGreater(len(p['units']),len(p['paragraphs']))
        for u in p['units']:
            owner=next(x for x in p['paragraphs'] if x['id']==u['paragraph_id'])
            self.assertLessEqual(u['end_utf16'],owner['end_utf16'])
            self.assertGreaterEqual(u['start_utf16'],owner['start_utf16'])
        self.assertEqual(''.join(u['source'] for u in p['units']),s)
    def test_unbroken_token_is_not_cut_into_syllables(self):
        p=build_plan('가'*80,max_chars=40)
        self.assertEqual(len(p['units']),1)
        self.assertIn('long_unbroken_span_review',p['units'][0]['review'])
    def test_source_numbering_stays_original(self):
        p=build_plan('2. 공·사 단체입니다.')
        self.assertEqual(p['source'],'2. 공·사 단체입니다.')
        self.assertEqual(p['units'][0]['spoken'],'제이호, 공사 단체입니다.')

class TimingTests(unittest.TestCase):
    def setUp(self):
        self.plan=build_plan('첫째입니다.\n둘째입니다.')
        self.m=dict(source_sha256=self.plan['source_sha256'],units=[dict(id=u['id'],source_sha256=u['source_sha256'],
           measurement_kind='final_decoded_pcm',sample_rate=24000,sample_count=24000*(i+1),gap_before_samples=2400 if i else 0)
           for i,u in enumerate(self.plan['units'])])
    def test_actual_samples_and_gap_drive_times(self):
        r=measured_timeline(self.plan,self.m)
        self.assertEqual(r['segments'][0]['end_s'],1)
        self.assertEqual(r['segments'][1]['start_s'],1.1)
        self.assertEqual(r['duration_s'],3.1)
    def test_reject_old_source(self):
        self.m['source_sha256']='0'*64
        with self.assertRaises(ValueError):measured_timeline(self.plan,self.m)
    def test_reject_duplicate_or_missing_audio(self):
        self.m['units'][1]=self.m['units'][0]
        with self.assertRaises(ValueError):measured_timeline(self.plan,self.m)
    def test_reject_guessed_time(self):
        self.m['units'][0]['measurement_kind']='text_length_estimate'
        with self.assertRaises(ValueError):measured_timeline(self.plan,self.m)
    def test_reject_stale_unit_audio(self):
        self.m['units'][0]['source_sha256']='0'*64
        with self.assertRaises(ValueError):measured_timeline(self.plan,self.m)
    def test_reject_mixed_sample_rates(self):
        self.m['units'][1]['sample_rate']=44100
        with self.assertRaises(ValueError):measured_timeline(self.plan,self.m)
    def test_reject_shifted_character_offsets(self):
        self.plan['units'][1]['start_utf16']+=1
        with self.assertRaises(ValueError):measured_timeline(self.plan,self.m)
    def test_reject_substituted_source_unit(self):
        self.plan['units'][0]['source']='다른 문장입니다.'
        with self.assertRaises(ValueError):measured_timeline(self.plan,self.m)

if __name__=='__main__':unittest.main()
