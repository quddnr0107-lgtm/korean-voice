"""Synthetic projection/authorization tests, not actual voice verification."""
import copy
import json
from pathlib import Path
import unittest
import checkpoint_asr_recheck as c

class RecheckTests(unittest.TestCase):
    def rows(self):
        return [dict(unit_index=i,raw_matches_saved_recognition=True,checks=[dict(padding_ms=p,
            character_errors=0 if p else 1,missing_critical_count=0,seconds=1.5) for p in c.PADDING_MS]) for i in c.TARGETS]
    def test_exact_recognition_never_promotes_recording(self):
        rows=self.rows();before=copy.deepcopy(rows);out=c.summarize(rows)
        self.assertEqual(out['exact_in_both_padded_checks'],5)
        self.assertEqual(out['exact_by_padding_ms'],{'0':0,'500':5,'1500':5})
        self.assertFalse(out['production_eligible'])
        self.assertFalse(out['production_status_changed'])
        self.assertFalse(out['synthesis_started'])
        self.assertFalse(out['independent_asr_model_used'])
        self.assertEqual(rows,before)
    def test_missing_critical_terms_remain_mismatches(self):
        rows=self.rows();rows[0]['checks'][1]['missing_critical_count']=1
        self.assertEqual(c.summarize(rows)['exact_in_both_padded_checks'],4)
    def test_missing_duplicate_or_unexpected_units_fail(self):
        for rows in (self.rows()[:-1],self.rows()+self.rows()[:1],list(reversed(self.rows()))):
            with self.assertRaises(ValueError):c.summarize(rows)
    def test_private_fields_are_rejected_instead_of_exported(self):
        for level in ('row','check'):
            rows=self.rows()
            if level=='row':rows[0]['recognized']='PRIVATE_CANARY'
            else:rows[0]['checks'][0]['source']='PRIVATE_CANARY'
            with self.assertRaises(ValueError):c.summarize(rows)
    def test_invalid_numeric_counts_fail_closed(self):
        for key,values in [('character_errors',[-1,True,'0']),('missing_critical_count',[-1,True]),('seconds',[-1,float('nan'),float('inf'),True])]:
            for value in values:
                rows=self.rows();rows[0]['checks'][0][key]=value
                with self.assertRaises(ValueError):c.summarize(rows)
    def test_context_requires_original_repo_branch_workflow_and_nonroot(self):
        env=dict(GITHUB_REPOSITORY=c.d.REPO,GITHUB_REF='refs/heads/'+c.d.BRANCH,
            GITHUB_EVENT_NAME='push',GITHUB_WORKFLOW_REF=c.WORKFLOW)
        c.validate_context(env,1001,1001)
        for key in env:
            bad=dict(env);bad[key]='wrong'
            with self.assertRaises(ValueError):c.validate_context(bad,1001,1001)
        with self.assertRaises(ValueError):c.validate_context(env,0,0)
    def test_only_exact_blocked_review_targets_selected(self):
        receipt=dict(status='blocked_requires_review',review_file='review.wav',review_only=True,
            review_natural_eos=True,review_flags=[],review_sha256='a'*64,
            candidates=[dict(sha256='a'*64,natural_eos=True,signal={'blocked':False},asr={'recognized':'synthetic'})])
        rows={i:{'receipt':copy.deepcopy(receipt)} for i in c.TARGETS}
        self.assertEqual(c.validate_targets(rows),list(c.TARGETS))
        rows[c.TARGETS[0]]['receipt']['status']='passed'
        with self.assertRaises(ValueError):c.validate_targets(rows)
    def test_saved_request_and_source_prohibit_resynthesis(self):
        request=c.request()
        self.assertFalse(request['resynthesize']);self.assertFalse(request['promote_units'])
        text=Path(c.__file__).read_text()
        self.assertNotIn('FP32Renderer(',text)
        self.assertNotIn('render_unit(',text)
        self.assertNotIn('seal_unit(',text)
        self.assertNotIn('initial_prompt=',text)
        self.assertNotIn('hotwords=',text)

if __name__=='__main__':unittest.main()
