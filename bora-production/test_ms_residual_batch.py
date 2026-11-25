"""Synthetic tests for the separate parallel batch; no keys or private sources."""
import copy
import unittest
from unittest.mock import patch
import ms_residual_batch as batch


class ResidualBatchTests(unittest.TestCase):
    def test_exact_scope_excludes_active_ms018(self):
        self.assertEqual(len(batch.IDS), 17)
        self.assertNotIn('MS-018', batch.IDS)
        self.assertIn('MS-022', batch.IDS)

    def test_same_paragraph_rebind_preserves_operating_text(self):
        text = 'synthetic legal fragment'
        ident = 'MS-005'
        pid = ident + ':p0000'
        unit = dict(paragraph_id=pid, source=text, spoken='same spoken text',
                    review=[batch.FRAGMENT], mode='explain', reading_mode_verified=False,
                    reading_mode_verification={})
        plan = dict(lesson_id=ident, source=text, source_sha256=batch.sha(text),
                    paragraphs=[dict(id=pid,source=text,visible_start_utf16=0,visible_end_utf16=len(text))],
                    units=[unit], reading_spans=[], review_unit_count=1)
        pins = (set(),set(),{(ident,'a'*64,pid,batch.sha(text))},set(),'b'*64)
        result = batch.fragment_review(plan,pins)
        self.assertEqual(result['review_unit_count'],0)
        self.assertEqual(result['source'],text)
        self.assertEqual(result['units'][0]['spoken'],unit['spoken'])
        self.assertEqual(result['units'][0]['mode'],'reading')
        self.assertEqual(plan['units'][0]['review'],[batch.FRAGMENT])
        other = (set(),set(),{(ident,'a'*64,pid,'c'*64)},set(),'b'*64)
        self.assertEqual(batch.fragment_review(plan,other)['review_unit_count'],1)
        wrong = dict(plan,source_sha256='f'*64)
        with self.assertRaises(ValueError):
            batch.fragment_review(wrong,pins)

    def test_other_flags_never_cleared(self):
        text='example'
        pid='MS-006:p0000'
        unit=dict(paragraph_id=pid,source=text,spoken=text,
                  review=[batch.FRAGMENT,'unknown_pronunciation'],mode='explain',
                  reading_mode_verified=False,reading_mode_verification={})
        plan=dict(lesson_id='MS-006',source=text,source_sha256=batch.sha(text),
            paragraphs=[dict(id=pid,source=text,visible_start_utf16=0,visible_end_utf16=len(text))],
            units=[unit],reading_spans=[],review_unit_count=1)
        result=batch.fragment_review(plan,(set(),set(),{('MS-006','a'*64,pid,batch.sha(text))},set(),'b'*64))
        self.assertEqual(result['units'][0]['review'],['unknown_pronunciation'])
        self.assertEqual(result['review_unit_count'],1)

    def test_ms022_exact_failed_unit_only(self):
        import ms018_recovery as repair
        plan=dict(lesson_id='MS-022',units=[{} for _ in range(30)])
        settings=dict(seeds=[5192],engine='qwen-tts-python',dtype='float32')
        digest=batch.identity(settings)
        rows={i:dict(receipt=dict(status='passed')) for i in range(30)}
        rows[28]=dict(receipt=dict(status='blocked_requires_review',review_flags=[],natural_eos=False,
            render_settings=settings,render_settings_sha256=digest,
            candidates=[dict(seed=5192,natural_eos=False,cap_reached=True,eos_seen=False,
                             generated_sequence_tokens=1100,last_token=2,eos_token_id=3)]))
        with patch.multiple(batch, MS022_PLAN=batch.identity(plan),SETTINGS=digest), patch.object(repair,'SETTINGS',digest):
            found,new=batch.resolve_ms022(plan,batch.RECIPE,digest,rows,lambda _: {})
            self.assertEqual(set(found),set(rows)-{28})
            self.assertEqual(new['seeds'],[7319])
            self.assertEqual(settings['seeds'],[5192])
            broken=copy.deepcopy(rows)
            broken[1]['receipt']['status']='blocked_requires_review'
            with self.assertRaises(ValueError):
                batch.resolve_ms022(plan,batch.RECIPE,digest,broken,lambda _: {})


if __name__ == '__main__':
    unittest.main()
