"""Synthetic tests; neither real audio verification nor human listening."""
import copy
import json
import unittest
import recovery_diagnostics as r


class RecoveryTests(unittest.TestCase):
    def receipt(self, status='blocked_requires_review', **changes):
        c = dict(natural_eos=True, cap_reached=False, signal={'blocked': False},
                 asr=dict(review_required=True, character_errors=1, missing_critical=[]))
        c.update(changes)
        return {'receipt': {'status': status, 'candidates': [c], 'review_flags': []}}

    def test_only_fixed_public_counters_leave_receipts(self):
        private = 'PRIVATE_MANUSCRIPT_NEVER_EXPORT'
        row = self.receipt()
        row['receipt'].update(source=private, recognized=private, review_flags=[private], review_file=private)
        row['receipt']['candidates'][0]['asr']['recognized'] = private
        row['receipt']['candidates'][0]['asr']['words'] = [{'text': private}]
        row['receipt']['candidates'][0]['error_code'] = private
        before = copy.deepcopy(row)
        out = r.summarize({7: row}, 10, 0)
        self.assertNotIn(private, json.dumps(out))
        self.assertEqual(row, before)
        self.assertEqual(out['counts']['blocked_receipts'], 1)
        self.assertEqual(out['candidate_error_codes']['other_error'], 1)
        self.assertFalse(out['production_eligible'])
        self.assertFalse(out['human_listening_verified'])
        self.assertFalse(out['synthesis_started'])

    def test_passed_receipt_not_promoted_to_course_completion(self):
        row = self.receipt('passed', asr=dict(review_required=False, character_errors=0, missing_critical=[]))
        out = r.summarize({0: row}, 100, 0)
        self.assertEqual(out['counts']['passed_receipts'], 1)
        self.assertEqual(out['missing_lesson_units'], 99)
        self.assertFalse(out['production_eligible'])
        self.assertEqual(out['status'], 'diagnostics_only_not_a_release')

    def test_failure_axes_are_not_conflated(self):
        out = r.summarize({0: self.receipt(natural_eos=False, cap_reached=True),
            1: self.receipt(signal={'blocked': True}, error_code='unit_tts_failed',
                            asr=dict(review_required=True))}, 3, 0)
        self.assertEqual(out['counts']['candidates_without_natural_eos'], 1)
        self.assertEqual(out['counts']['candidates_with_signal_failure'], 1)
        self.assertEqual(out['counts']['candidates_with_asr_review'], 2)
        self.assertEqual(out['candidate_character_error_bins']['one'], 1)
        self.assertEqual(out['candidate_character_error_bins']['not_measured'], 1)
        self.assertEqual(out['candidate_error_codes']['unit_tts_failed'], 1)

    def test_invalid_shape_counts_and_status_fail_closed(self):
        for rows, expected, reviews in [({}, 10, 0), ({-1:self.receipt()},10,0),
                ({True:self.receipt()},10,0), ({10:self.receipt()},10,0),
                ({0:self.receipt('unknown')},10,0), ({0:self.receipt()},True,0),
                ({0:self.receipt()},10,11),
                ({0:self.receipt(asr={'character_errors':True})},10,0)]:
            with self.subTest(rows=rows, expected=expected):
                with self.assertRaises(ValueError):r.summarize(rows,expected,reviews)

    def test_request_cannot_expand_scope_or_enable_synthesis(self):
        original = dict(schema='bora-recovery-diagnostic-request/v1',
            purpose='read-only-authenticated-diagnostics', incident=copy.deepcopy(r.INCIDENT))
        r.validate_request(original)
        for mutate in (lambda x:x.update(purpose='render'), lambda x:x.update(upload=True),
                       lambda x:x['incident'].update(lesson_id='TB-999'),
                       lambda x:x['incident'].update(asset_sha256='0'*64)):
            bad=copy.deepcopy(original);mutate(bad)
            with self.assertRaises(ValueError):r.validate_request(bad)

    def test_protected_workflow_context_cannot_be_spoofed_by_other_events(self):
        env=dict(GITHUB_REPOSITORY=r.REPO, GITHUB_REF='refs/heads/'+r.BRANCH,
                 GITHUB_EVENT_NAME='push', GITHUB_WORKFLOW_REF=r.WORKFLOW)
        r.validate_context(env,1001,1001)
        for key in env:
            bad=dict(env);bad[key]='wrong'
            with self.assertRaises(ValueError):r.validate_context(bad,1001,1001)
        for uid,euid in [(0,0),(1001,0),(1001,1002)]:
            with self.assertRaises(ValueError):r.validate_context(env,uid,euid)

    def test_disagreement_shape_never_exports_text(self):
        private='SYNTHETIC_PRIVATE_MARKER'
        out=r.disagreement_shape(private, private+'extra', lambda s:s)
        self.assertNotIn(private,json.dumps(out))
        self.assertTrue(all(type(v) is int for v in out.values()))
        self.assertEqual(out['insertion'],1)

    def test_disagreement_shapes_distinguish_omission_positions(self):
        for recognized,label in [('나 다','leading_omission'),('가 나','trailing_omission'),('가 다','internal_omission')]:
            out=r.disagreement_shape('가 나 다',recognized,lambda s:s.replace(' ',''))
            self.assertEqual(out[label],1)
            self.assertEqual(out['exact'],0)
        self.assertEqual(r.disagreement_shape('가 나 다','가 라 다',lambda s:s)['substitution'],1)

    def test_shape_bounds_and_empty_recognition(self):
        for expected,recognized in [('', 'x'),('x',None),('x','y'*10001),(None,'x')]:
            with self.assertRaises(ValueError):r.disagreement_shape(expected,recognized,lambda s:s)
        out=r.disagreement_shape('가나','',lambda s:s)
        self.assertEqual(out['empty_recognition'],1)
        self.assertEqual(out['exact'],0)

    def test_exact_shape_is_diagnostic_not_an_approval(self):
        out=r.disagreement_shape('같은 글','같은 글',lambda s:s.replace(' ',''))
        self.assertEqual(out['exact'],1)
        self.assertNotIn('passed',out)
        self.assertNotIn('production_eligible',out)

    def test_blocked_analysis_does_not_modify_authenticated_receipts(self):
        private='SYNTHETIC_CANARY_DO_NOT_PRINT'
        row=self.receipt(asr={'recognized':private,'character_errors':1,'review_required':True})
        row['receipt']['candidates'].append(copy.deepcopy(row['receipt']['candidates'][0]))
        before=copy.deepcopy(row)
        result=r.inspect_disagreements({0:row},{'units':[{'spoken':private+'X'}]})
        self.assertEqual(row,before)
        self.assertNotIn(private,json.dumps(result))
        self.assertEqual(result['blocked_units_with_identical_candidate_transcripts'],1)
        self.assertEqual(result['compared_blocked_candidates'],2)
        self.assertFalse(result['causal_attribution_confirmed'])
        self.assertFalse(result['production_eligible'])


if __name__ == '__main__':unittest.main()
