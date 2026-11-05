#!/usr/bin/env python3
"""Check saved review integrity. This never performs or grants semantic approval."""
import argparse
import collections
import hashlib
import json
from pathlib import Path


class CheckpointError(ValueError):
    pass


def require(value, message):
    if not value:
        raise CheckpointError(message)


def digest(value):
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def records(directory):
    result = []
    for path in sorted(directory.glob('*.jsonl')):
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if line.strip():
                row = json.loads(line)
                require(isinstance(row, dict), f'{path.name}:{number}: object required')
                result.append((path.name, row))
    return result


def audit(source_bytes, inventory, draft_records, review_records):
    source_sha = digest(source_bytes)
    require(source_sha == inventory['source']['sha256'], 'Original snapshot SHA-256 mismatch')
    expected_blob = inventory['source'].get('gitBlobSha')
    if expected_blob:
        actual = hashlib.sha1(b'blob '+str(len(source_bytes)).encode()+b'\0'+source_bytes).hexdigest()
        require(actual == expected_blob, 'Original Git blob mismatch')
    source = json.loads(source_bytes)['blanks']
    occurrences = collections.Counter()
    bodies = {}
    for index, row in enumerate(source):
        if row.get('isHeader'):
            continue
        identity = [row['law'], row['period'], row['type'], str(row['articleNum']), row['original']]
        key = compact(identity)
        occurrence = occurrences[key]
        occurrences[key] += 1
        source_id = digest(compact(identity+[occurrence]))
        require(source_id not in bodies, 'Duplicate source identity')
        bodies[source_id] = dict(sourceIndex=index, originalSha256=digest(row['original']))
    require(len(bodies) == inventory['coverage']['bodyRows'], 'Original body count mismatch')
    drafts, files = {}, {}
    for filename, draft in draft_records:
        sid = draft.get('sourceId')
        require(sid in bodies and sid not in drafts, 'Unknown or duplicate draft identity')
        require(draft.get('sourceIndex') == bodies[sid]['sourceIndex'], 'Draft source index mismatch')
        text = draft.get('text')
        require(isinstance(text, str) and 1 <= len(text.strip()) <= 1600, 'Invalid draft text')
        refs = draft.get('referenceSourceIndices', [])
        require(isinstance(refs, list) and all(type(i) is int and 0 <= i < len(source) for i in refs),
                'Invalid reference source index')
        drafts[sid], files[sid] = draft, filename
    require(drafts.keys() == bodies.keys(), 'Draft coverage mismatch')
    reviews, blocked = {}, []
    for filename, review in review_records:
        sid = review.get('sourceId')
        require(sid in drafts and sid not in reviews, 'Unknown or duplicate review identity')
        draft, original = drafts[sid], bodies[sid]
        for field, expected in [('sourceIndex', original['sourceIndex']),
                ('originalSha256', original['originalSha256']), ('sourceDataSha256', source_sha),
                ('textSha256', digest(draft['text']))]:
            require(review.get(field) == expected, f'{original["sourceIndex"]}: stale {field}')
        author, reviewer = review.get('author'), review.get('reviewer')
        require(isinstance(author, str) and author and isinstance(reviewer, str) and reviewer and author != reviewer,
                'Independent author/reviewer identity required')
        if draft.get('author'):
            require(draft['author'] == author, 'Original draft author changed')
        status, issues = review.get('status'), review.get('unresolvedIssues')
        require(status in ('approved', 'blocked') and isinstance(issues, list), 'Invalid review state')
        require((status == 'approved' and not issues) or (status == 'blocked' and issues),
                'Review approval and unresolved issues disagree')
        reviews[sid] = review
        if status == 'blocked':
            blocked.append(dict(sourceIndex=original['sourceIndex'], shard=files[sid], issues=issues))
    missing = sorted(bodies[sid]['sourceIndex'] for sid in drafts.keys()-reviews.keys())
    return dict(schema='reader-assistance-review-integrity.v1', sourceDataSha256=source_sha,
        bodyRows=len(bodies), draftRows=len(drafts), reviewRecords=len(reviews),
        approvedRows=len(reviews)-len(blocked), blockedRows=len(blocked), missingReviewRows=len(missing),
        missingSourceIndices=missing, blocked=sorted(blocked, key=lambda x: x['sourceIndex']),
        allTextReviewsCurrentAndApproved=not blocked and not missing,
        productionReady=False,
        limitation='Integrity checks do not replace reading, source/display correction checks, actual listening, or UI verification.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--work', type=Path, default=Path('assistance-work'))
    parser.add_argument('--out', type=Path)
    parser.add_argument('--allow-incomplete', action='store_true', help='Report progress without granting approval')
    args = parser.parse_args()
    result = audit(args.source.read_bytes(), json.loads((args.work/'inventory.json').read_text()),
                   records(args.work/'drafts'), records(args.work/'reviews'))
    encoded = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
    if args.out:
        args.out.write_text(encoded, encoding='utf-8')
    print(encoded, end='')
    if not result['allTextReviewsCurrentAndApproved'] and not args.allow_incomplete:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
