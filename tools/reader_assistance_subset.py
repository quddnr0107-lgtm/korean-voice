#!/usr/bin/env python3
"""Release an explicitly reviewed subset while preserving the full-corpus gate.

The frozen renderer and complete-production pipeline are not modified. A separate
policy pins the single unresolved source row and the entire original row ledger.
Only the new reviewed-subset schema can be published by this entry point.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import tempfile

import reader_assistance as R

POLICY_PATH = R.ROOT / 'reader-assistance/reviewed-subset-policy.json'
POLICY_SCHEMA = 'reader-assistance-reviewed-subset-policy.v1'
CATALOG_SCHEMA = 'reader-assistance-reviewed-subset-audio.v1'
CATALOG_BRANCH = 'data/reader-assistance-f1-reviewed-subset-v1'
PACK_BASE = 'https://github.com/' + R.REPOSITORY + '/releases/download/' + R.RECIPE_ID + '-packs/'


def validate_policy(policy):
    R.require(isinstance(policy, dict) and policy.get('schema') == POLICY_SCHEMA and
              policy.get('kind') == 'reviewed-subset', 'Explicit reviewed-subset policy is required')
    for field in ('sourceCorpusSha256', 'sourceRowsSha256'):
        R.require(isinstance(policy.get(field), str) and R.HEX64.fullmatch(policy[field]),
                  'Missing pinned source identity: ' + field)
    body, approved, excluded = policy.get('bodyRows'), policy.get('approvedEntries'), policy.get('excluded')
    R.require(type(body) is int and type(approved) is int and approved > 0 and body == approved + 1,
              'This policy allows exactly one unresolved body row')
    R.require(isinstance(excluded, list) and len(excluded) == 1 and isinstance(excluded[0], dict),
              'Exactly one explicitly reviewed exclusion is required')
    row = excluded[0]
    for field in ('id', 'originalSha256', 'textSha256', 'reviewRecordSha256'):
        R.require(isinstance(row.get(field), str) and R.HEX64.fullmatch(row[field]),
                  'Invalid excluded source identity: ' + field)
    R.require(type(row.get('sourceIndex')) is int and row['sourceIndex'] >= 0,
              'Excluded source index is required')
    R.require(isinstance(row.get('reason'), str) and 1 <= len(row['reason'].strip()) <= 1000,
              'The exact source exclusion must have a review reason')
    return policy


def source_ledger_digest(rows):
    return R.sha256(R.canonical_json(sorted(rows, key=lambda row: row[0])))


def validate_excluded_record(data, policy):
    supplied = data.get('exclusions')
    R.require(isinstance(supplied, list) and len(supplied) == 1 and isinstance(supplied[0], dict),
              'The canonical compiler output must retain the one blocked review')
    entry, pinned = supplied[0], policy['excluded'][0]
    R.require(entry.get('sourceId') == pinned['id'] and 'text' not in entry and 'audio' not in entry,
              'Excluded source must not carry an explanation or audio')
    for field in ('sourceIndex', 'originalSha256', 'textSha256', 'reviewRecordSha256', 'reason'):
        R.require(entry.get(field) == pinned[field], 'Blocked source differs from its fixed policy: ' + field)
    review, issues = entry.get('review'), entry.get('unresolvedIssues')
    R.require(entry.get('status') == 'blocked' and isinstance(review, dict) and review.get('status') == 'blocked',
              'The unresolved source must remain blocked')
    R.require(isinstance(issues, list) and issues and all(isinstance(issue, str) and issue.strip() for issue in issues) and
              entry['reason'] == '\n'.join(issues) and review.get('unresolvedIssues') == issues,
              'The exact unresolved issues must remain visible')
    for field, expected in (('sourceId', pinned['id']), ('sourceIndex', pinned['sourceIndex']),
                            ('sourceDataSha256', policy['sourceCorpusSha256']),
                            ('originalSha256', pinned['originalSha256']), ('textSha256', pinned['textSha256'])):
        R.require(review.get(field) == expected, 'Excluded review identity changed: ' + field)
    R.require(isinstance(entry.get('original'), str) and R.sha256(entry['original']) == pinned['originalSha256'],
              'Excluded original source changed')
    # Compiler pins JSON.stringify(review), which preserves this object's insertion
    # order. Never sort its fields or replace this with a newly created review.
    review_bytes = json.dumps(review, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    R.require(R.sha256(review_bytes) == pinned['reviewRecordSha256'], 'Blocked review record changed')


def build_subset_plan(data_bytes, recipe, policy):
    validate_policy(policy)
    R.check_recipe(recipe, verify_sources=True)
    data = json.loads(data_bytes)
    R.require(isinstance(data, dict) and data.get('partial') is True and 'releaseScope' not in data and 'audio' not in data,
              'Use the unchanged partial compiler output; release scope belongs to the attached result')
    validate_excluded_record(data, policy)
    coverage = data.get('coverage', {})
    R.require(isinstance(coverage, dict) and coverage == {**coverage, 'bodyRows': policy['bodyRows'],
                          'explainedRows': policy['approvedEntries'], 'unexplainedBodyRows': 1},
              'Subset source coverage does not match its fixed policy')
    for name in ('bodyRows', 'explainedRows', 'unexplainedBodyRows'):
        R.require(type(coverage.get(name)) is int, 'Subset coverage must use integer counts')
    R.require(data.get('sourceBasis', {}).get('sourceDataSha256') == policy['sourceCorpusSha256'],
              'Subset source does not bind the pinned original corpus')
    plan = R.build_plan(data_bytes, recipe, allow_partial=True)
    R.require(plan['expectedEntries'] == policy['approvedEntries'] and plan['completeEligible'] is False,
              'Subset cannot pass the full-corpus completeness gate')
    excluded = policy['excluded'][0]
    ledger = [[excluded['sourceIndex'], excluded['id'], excluded['originalSha256']]]
    indices = {excluded['sourceIndex']}
    for entry, binding in zip(data['entries'], plan['entries']):
        entry_id, index, review = entry['id'], entry.get('sourceIndex'), entry.get('review', {})
        R.require(R.HEX64.fullmatch(entry_id) and entry_id == entry.get('sourceId') == review.get('sourceId'),
                  'Entry ID does not match its reviewed original source')
        R.require(entry_id != excluded['id'] and type(index) is int and index >= 0 and index not in indices and
                  index == review.get('sourceIndex'), 'Excluded, duplicate or mismatched source index')
        R.require(review.get('sourceDataSha256') == policy['sourceCorpusSha256'] and
                  review.get('unresolvedIssues') == [] and isinstance(review.get('originalSha256'), str) and
                  R.HEX64.fullmatch(review['originalSha256']), 'Review does not bind a resolved original source')
        R.require(all(isinstance(review.get(name), str) and review[name].strip() for name in ('author', 'reviewer')),
                  'Author and reviewer records must remain attributable')
        R.require(review['author'] != review['reviewer'], 'An author cannot approve their own explanation')
        R.require(isinstance(entry.get('original'), str) and R.sha256(entry['original']) == review['originalSha256'] and
                  entry.get('originalSha256') == review['originalSha256'] and entry.get('textSha256') == review['textSha256'],
                  'Compiler text and original provenance disagree with their reviewed hashes')
        indices.add(index)
        ledger.append([index, entry_id, review['originalSha256']])
        # Preserve enough provenance for the deployed Worker to recheck the same
        # whole-source ledger without carrying explanation/review prose there.
        binding.update(sourceIndex=index, originalSha256=review['originalSha256'])
    R.require(len(ledger) == policy['bodyRows'] and source_ledger_digest(ledger) == policy['sourceRowsSha256'],
              'Approved rows plus the explicit exclusion do not match the whole pinned source ledger')
    plan['releaseScope'] = policy
    return plan


def validate_subset_catalog(catalog, plan, policy):
    validate_policy(policy)
    R.require(isinstance(catalog, dict) and catalog.get('schema') == CATALOG_SCHEMA and catalog.get('complete') is False and
              catalog.get('reviewedScopeReady') is True and catalog.get('releaseScope') == policy,
              'Only the explicitly ready reviewed-subset catalog can be attached')
    for field in ('voice', 'recipe', 'sourceDataSha256', 'recipeSha256', 'expectedEntries'):
        R.require(catalog.get(field) == plan[field], 'Subset catalog provenance mismatch: ' + field)
    R.require(catalog.get('readyEntries') == policy['approvedEntries'] and catalog.get('entries') == plan['entries'],
              'Subset catalog entry coverage mismatch')
    clips, packs = catalog.get('clips'), catalog.get('packs')
    R.require(isinstance(clips, dict) and isinstance(packs, dict) and set(clips) == set(plan['tasks']),
              'Subset catalog clip set mismatch')
    used = set()
    for key, pack in packs.items():
        R.require(isinstance(key, str) and R.HEX64.fullmatch(key) and isinstance(pack, dict) and pack.get('sha256') == key and
                  type(pack.get('size')) is int and 0 < pack['size'] <= R.MAX_PACK_BYTES and
                  pack.get('url') == PACK_BASE + key + '.bin', 'Invalid subset audio pack')
    for key, clip in clips.items():
        R.require(isinstance(clip, dict) and clip.get('pack') in packs, 'Missing subset audio pack')
        R.checked_clip(key, clip, packs[clip['pack']]['size'])
        R.require(clip['textSha256'] == plan['tasks'][key]['textSha256'], 'Subset audio text identity changed')
        used.add(clip['pack'])
    R.require(used == set(packs), 'Subset catalog includes an unreferenced pack')
    return catalog


def assemble_subset(plan, state, policy):
    R.require(plan.get('releaseScope') == policy and plan.get('completeEligible') is False,
              'A verified subset plan is required')
    catalog = R.assemble(plan, state)
    catalog.update(schema=CATALOG_SCHEMA, complete=False, reviewedScopeReady=True, releaseScope=policy)
    return validate_subset_catalog(catalog, plan, policy)


def attach_subset(data_bytes, recipe, catalog, policy):
    plan = build_subset_plan(data_bytes, recipe, policy)
    validate_subset_catalog(catalog, plan, policy)
    output = json.loads(data_bytes)
    output['releaseScope'] = policy
    for entry, binding in zip(output['entries'], plan['entries']):
        key = binding['audioKey']
        clip = catalog['clips'][key]
        entry['audio'] = {'voice': R.VOICE, 'recipe': R.RECIPE_ID, 'url': R.MEDIA_BASE + key + '.mp3',
                          'durationMs': clip['durationMs'], 'sha256': clip['sha256'],
                          'audioKey': key, 'textSha256': clip['textSha256']}
    output['audio'] = {'schema': 'reader-assistance-reviewed-subset-binding.v1',
                       'voice': R.VOICE, 'recipe': R.RECIPE_ID, 'complete': False,
                       'reviewedScopeReady': True, 'releaseScope': policy,
                       'recipeSha256': plan['recipeSha256'], 'sourceDataSha256': plan['sourceDataSha256'],
                       'readyEntries': policy['approvedEntries']}
    return output


def publish_subset_catalog(client, catalog, policy):
    R.require(catalog.get('schema') == CATALOG_SCHEMA and catalog.get('complete') is False and
              catalog.get('reviewedScopeReady') is True and catalog.get('releaseScope') == validate_policy(policy),
              'Only explicitly reviewed-subset evidence can use this separate catalog branch')
    data = (json.dumps(catalog, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    prefix = '/repos/' + R.REPOSITORY
    ref_path = prefix + '/git/ref/heads/' + R.quote(CATALOG_BRANCH, safe='/')
    ref = client.request('GET', ref_path, allow404=True)
    parent_sha = ref['object']['sha'] if ref else os.environ.get('GITHUB_SHA', '')
    R.require(re.fullmatch('[a-f0-9]{40}', parent_sha) is not None, 'Pinned subset catalog parent required')
    parent = client.request('GET', prefix + '/git/commits/' + parent_sha)
    blob = client.request('POST', prefix + '/git/blobs', {'content': base64.b64encode(data).decode(), 'encoding': 'base64'})
    R.require(blob['sha'] == R.git_blob(data), 'Subset catalog Git checksum mismatch')
    tree = client.request('POST', prefix + '/git/trees', {'base_tree': parent['tree']['sha'], 'tree': [
        {'path': 'reader-assistance/audio-manifest.json', 'mode': '100644', 'type': 'blob', 'sha': blob['sha']}]})
    if tree['sha'] == parent['tree']['sha']:
        return {'branch': CATALOG_BRANCH, 'commit': parent_sha, 'changed': False}
    commit = client.request('POST', prefix + '/git/commits', {'message': 'Publish explicitly reviewed F1 subset ' + catalog['sourceDataSha256'][:16],
                                                           'tree': tree['sha'], 'parents': [parent_sha]})
    if ref:
        current = client.request('GET', ref_path)
        R.require(current['object']['sha'] == parent_sha, 'Subset catalog branch changed; refusing overwrite')
        client.request('PATCH', prefix + '/git/refs/heads/' + R.quote(CATALOG_BRANCH, safe='/'), {'sha': commit['sha'], 'force': False})
    else:
        client.request('POST', prefix + '/git/refs', {'ref': 'refs/heads/' + CATALOG_BRANCH, 'sha': commit['sha']})
    return {'branch': CATALOG_BRANCH, 'commit': commit['sha'], 'changed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'inventory', 'bake', 'assemble', 'attach'])
    parser.add_argument('--data', default='reader-assistance/data.json')
    parser.add_argument('--recipe', default='reader-assistance/recipe.json')
    parser.add_argument('--assets', default='supertonic3')
    parser.add_argument('--bucket')
    parser.add_argument('--catalog')
    parser.add_argument('--out')
    parser.add_argument('--publish-catalog', action='store_true')
    args = parser.parse_args()
    R.require(not args.publish_catalog or args.command == 'assemble', 'Catalog publication requires full subset assembly')
    policy = json.loads(POLICY_PATH.read_text())
    recipe = json.loads(Path(args.recipe).read_text())
    data_bytes = Path(args.data).read_bytes()
    plan = build_subset_plan(data_bytes, recipe, policy)
    if args.command == 'plan':
        result = {'schema': 'reader-assistance-reviewed-subset-plan.v1', 'complete': False,
                  'sourceDataSha256': plan['sourceDataSha256'], 'recipeSha256': plan['recipeSha256'],
                  'approvedEntries': plan['expectedEntries'], 'uniqueAudioClips': len(plan['tasks']),
                  'releaseScope': policy}
    elif args.command == 'attach':
        R.require(args.catalog, 'An explicit reviewed-subset catalog is required')
        result = attach_subset(data_bytes, recipe, json.loads(Path(args.catalog).read_text()), policy)
    else:
        R.require(os.environ.get('GITHUB_REPOSITORY', R.REPOSITORY) == R.REPOSITORY, 'Unexpected repository')
        client = R.GitHub()
        cache = Path(os.environ.get('RUNNER_TEMP', tempfile.gettempdir())) / 'reader-assistance-pack-indexes'
        if args.command == 'bake':
            result = R.bake(client, plan, recipe, args.bucket, args.assets, cache_dir=cache)
        else:
            state = R.inventory(client, plan, cache_dir=cache)
            if args.command == 'inventory':
                counts = {bucket: sum(task['bucket'] == bucket and key not in state['clips']
                                      for key, task in plan['tasks'].items()) for bucket in '0123456789abcdef'}
                result = {'missing': sum(counts.values()), 'buckets': [key for key, value in counts.items() if value],
                          'readyUniqueClips': len(set(plan['tasks']) & set(state['clips']))}
                if os.environ.get('GITHUB_OUTPUT'):
                    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                        output.write('missing=' + str(result['missing']) + '\n')
                        output.write('buckets=' + json.dumps(result['buckets'] or ['0']) + '\n')
            else:
                result = assemble_subset(plan, state, policy)
                if args.publish_catalog:
                    print(json.dumps(publish_subset_catalog(client, result, policy)), flush=True)
    if args.out:
        destination = Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    if args.command in ('assemble', 'attach'):
        print(json.dumps({'command': args.command, 'complete': False, 'reviewedScopeReady': True,
                          'approvedEntries': policy['approvedEntries'], 'excludedRows': 1,
                          'sourceDataSha256': plan['sourceDataSha256']}), flush=True)
    else:
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (R.ContractError, json.JSONDecodeError, OSError) as error:
        raise SystemExit('reader-assistance-subset: ' + str(error))
