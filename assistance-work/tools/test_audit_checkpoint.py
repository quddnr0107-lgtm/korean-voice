import copy
import json
import unittest
from audit_checkpoint import audit, CheckpointError, compact, digest


def fixture():
    original = '대상자는 3일 이내에 신청할 수 있다.'
    row = dict(law='예비군법', period='27년 전반기', type='본법', articleNum='1', original=original)
    source = compact({'blanks': [row]}).encode()
    sid = digest(compact(['예비군법', '27년 전반기', '본법', '1', original, 0]))
    draft = dict(sourceId=sid, sourceIndex=0, text='대상자는 사유가 생기면 3일 안에 신청할 수 있습니다.', author='writer', referenceSourceIndices=[0])
    review = dict(sourceId=sid, sourceIndex=0, author='writer', reviewer='checker', status='approved',
                  originalSha256=digest(original), sourceDataSha256=digest(source), textSha256=digest(draft['text']), unresolvedIssues=[])
    return source, {'source': {'sha256': digest(source)}, 'coverage': {'bodyRows': 1}}, [('one.jsonl', draft)], [('one.jsonl', review)]


class Integrity(unittest.TestCase):
    def test_current_records_do_not_claim_production_ready(self):
        report = audit(*fixture())
        self.assertTrue(report['allTextReviewsCurrentAndApproved'])
        self.assertFalse(report['productionReady'])

    def test_changed_original_bytes_rejected(self):
        source, inventory, drafts, reviews = fixture()
        with self.assertRaises(CheckpointError): audit(source+b' ', inventory, drafts, reviews)

    def test_changed_text_rejected_even_if_count_unchanged(self):
        args = fixture(); args[2][0][1]['text'] = '대상자는 3일 안에 반드시 신청해야 합니다.'
        with self.assertRaises(CheckpointError): audit(*args)

    def test_stale_original_review_rejected(self):
        args = fixture(); args[3][0][1]['originalSha256'] = '0'*64
        with self.assertRaises(CheckpointError): audit(*args)

    def test_duplicate_review_rejected(self):
        args = fixture(); args[3].append(copy.deepcopy(args[3][0]))
        with self.assertRaises(CheckpointError): audit(*args)

    def test_missing_review_is_not_approval(self):
        source, inventory, drafts, _ = fixture(); report = audit(source, inventory, drafts, [])
        self.assertEqual(report['missingReviewRows'], 1)
        self.assertFalse(report['allTextReviewsCurrentAndApproved'])

    def test_blocked_is_preserved_and_never_counted_approved(self):
        args = fixture(); args[3][0][1].update(status='blocked', unresolvedIssues=['Figure not inspected'])
        report = audit(*args)
        self.assertEqual(report['approvedRows'], 0)
        self.assertEqual(report['blockedRows'], 1)
        self.assertFalse(report['allTextReviewsCurrentAndApproved'])

    def test_approved_with_unresolved_issue_rejected(self):
        args = fixture(); args[3][0][1]['unresolvedIssues'] = ['Unresolved source conflict']
        with self.assertRaises(CheckpointError): audit(*args)

    def test_author_cannot_review_or_be_silently_replaced(self):
        for change in [{'reviewer': 'writer'}, {'author': 'other'}]:
            args = fixture(); args[3][0][1].update(change)
            with self.assertRaises(CheckpointError): audit(*args)

    def test_out_of_range_reference_rejected(self):
        args = fixture(); args[2][0][1]['referenceSourceIndices'] = [1]
        with self.assertRaises(CheckpointError): audit(*args)

    def test_missing_or_duplicate_draft_rejected(self):
        for drafts in [[], fixture()[2]*2]:
            source, inventory, _, reviews = fixture()
            with self.assertRaises(CheckpointError): audit(source, inventory, drafts, reviews)


if __name__ == '__main__': unittest.main()
