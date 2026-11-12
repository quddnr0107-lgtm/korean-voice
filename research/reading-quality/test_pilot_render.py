import json
import tempfile
import unittest
from pathlib import Path

from paragraph_plan import build_plan
from pilot_render import (canonical, check_plan, choose_candidate, file_hash,
                          digest_bytes, export_subset, identity, reusable_record, transcript_score, unit_key)


class RenderGateTests(unittest.TestCase):
    def test_plan_hash_matches_protected_wrapper_serialization(self):
        p=build_plan('통합방위법 제1조입니다.')
        expected=digest_bytes(json.dumps(p,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode('ascii'))
        self.assertEqual(identity(p),expected)

    def test_missing_number_is_not_ignored(self):
        score=transcript_score('제이호 군간부후보생입니다.', '군간부후보생입니다.', ['제이호'])
        self.assertTrue(score['blocked'])
        self.assertEqual(score['missing_critical'],['제이호'])

    def test_numeric_asr_and_spacing_normalize(self):
        self.assertEqual(canonical('제24조 국가 방위 요소'),canonical('제이십사조 국가방위요소'))

    def test_inserted_repetition_blocks(self):
        expected='법문을 또박또박 읽습니다.'
        self.assertTrue(transcript_score(expected,expected*2)['blocked'])

    def test_small_disagreement_is_review_not_claim_of_error(self):
        row=transcript_score('가나다라마바사아자차카타파하','가나다라마바사아자차카타파가')
        self.assertTrue(row['review_required'])
        self.assertFalse(row['blocked'])
        self.assertTrue(row['perceptual_review_required'])

    def test_selector_rejects_clipped_exact_transcript(self):
        bad=dict(seed=5192,signal={'blocked':True},asr=transcript_score('첫째입니다','첫째입니다'))
        good=dict(seed=7319,signal={'blocked':False},asr=transcript_score('첫째입니다','첫째입니다'))
        self.assertEqual(choose_candidate([bad,good])['seed'],7319)

    def test_checkpoint_rejects_changed_audio_or_settings(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);audio=root/'final.wav';audio.write_bytes(b'original')
            row=dict(key='a',kind='selected',file=audio.name,sha256=file_hash(audio))
            self.assertTrue(reusable_record(row,'a',root,'selected'))
            self.assertFalse(reusable_record(row,'b',root,'selected'))
            audio.write_bytes(b'changed')
            self.assertFalse(reusable_record(row,'a',root,'selected'))

    def test_checkpoint_rejects_outside_path(self):
        self.assertFalse(reusable_record(dict(key='a',kind='selected',file='../private.wav'),'a',Path('.'),'selected'))

    def test_source_and_spoken_change_invalidate_checkpoint(self):
        p=build_plan('제1조 목적입니다.');u=p['units'][0]
        before=unit_key(p,u,{})
        u['spoken']='다른 낭독입니다.'
        self.assertNotEqual(before,unit_key(p,u,{}))

    def test_plan_refuses_cross_paragraph_unit(self):
        p=build_plan('첫째입니다.\n둘째입니다.')
        p['units'][0]['end_utf16']=p['source_utf16_length']
        with self.assertRaises(ValueError):check_plan(p)

    def test_plan_refuses_irrelevant_critical_term(self):
        p=build_plan('목적입니다.')
        p['units'][0]['critical_terms']=['존재하지 않는 말']
        with self.assertRaises(ValueError):check_plan(p)

    def test_subset_keeps_original_indices_and_source_ranges(self):
        p=build_plan('첫째입니다.\n둘째입니다.\n셋째입니다.')
        rows=[dict(unit_id=p['units'][i]['id'],blocked=False,file='final.wav') for i in (0,2)]
        with tempfile.TemporaryDirectory() as name:
            settings={'speaker':'Sohee','seeds':[5192,7319]}
            m=export_subset(p,rows,0,3,Path(name),[0,2],settings)
            self.assertEqual(m['unit_indices'],[0,2])
            self.assertEqual(m['units'][1]['relative_file'],'units/0002/final.wav')
            self.assertEqual(m['units'][1]['start_utf16'],p['units'][2]['start_utf16'])
            self.assertFalse(m['sentence_alignment_verified'])
            self.assertEqual(m['render_settings_sha256'],identity(settings))
            self.assertEqual(m['units'][1]['spoken_sha256'],digest_bytes(p['units'][2]['spoken'].encode()))

    def test_blocked_subset_has_no_playable_file_claim(self):
        p=build_plan('첫째입니다.')
        with tempfile.TemporaryDirectory() as name:
            m=export_subset(p,[dict(blocked=True)],0,1,Path(name))
            self.assertEqual(m['status'],'blocked_requires_review')
            self.assertNotIn('relative_file',m['units'][0])


if __name__=='__main__':unittest.main()
