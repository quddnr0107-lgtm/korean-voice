#!/usr/bin/env python3
"""Prebake only exact approved explanations without authorizing production activation.

The frozen F1 renderer and recipe remain unchanged, so the production pass reuses
these immutable clips by audioKey. This entry point has no catalog publication,
attachment or approval command. Missing or blocked explanations stay missing.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

import reader_assistance as R


def build_stage_plan(data_bytes, recipe):
    R.check_recipe(recipe, verify_sources=True)
    data = json.loads(data_bytes)
    R.require(isinstance(data, dict) and type(data.get('partial')) is bool,
              'An explicit source completeness flag is required')
    coverage = data.get('coverage', {})
    R.require(isinstance(coverage, dict), 'Source coverage is required')
    for key in ('bodyRows', 'explainedRows', 'unexplainedBodyRows'):
        R.require(type(coverage.get(key)) is int and coverage[key] >= 0,
                  'Invalid source coverage count: ' + key)
    R.require(data['partial'] == (coverage['unexplainedBodyRows'] > 0),
              'Source completeness flag disagrees with its coverage')
    # This validates every supplied entry, including exact text approval hashes.
    # It does not filter or silently rewrite an unapproved entry.
    plan = R.build_plan(data_bytes, recipe, allow_partial=True)
    plan['completeEligible'] = False
    plan['staged'] = True
    plan['corpusBodyRows'] = coverage['bodyRows']
    plan['unexplainedBodyRows'] = coverage['unexplainedBodyRows']
    return plan


def missing_summary(plan, state):
    counts = {bucket: sum(task['bucket'] == bucket and key not in state['clips']
                          for key, task in plan['tasks'].items())
              for bucket in '0123456789abcdef'}
    return {'stageOnly': True, 'productionActivationAllowed': False,
            'sourceDataSha256': plan['sourceDataSha256'],
            'approvedEntries': plan['expectedEntries'],
            'corpusBodyRows': plan['corpusBodyRows'],
            'unexplainedBodyRows': plan['unexplainedBodyRows'],
            'uniqueAudioClips': len(plan['tasks']),
            'readyUniqueClips': len(set(plan['tasks']) & set(state['clips'])),
            'missing': sum(counts.values()),
            'buckets': [bucket for bucket, count in counts.items() if count]}


def stage_report(plan, state):
    R.require(plan.get('staged') is True and plan.get('completeEligible') is False,
              'Only an explicitly non-activating stage can be reported')
    # Assembly verifies all staged audio identities. Its result stays incomplete
    # even when this staging input happens to contain every corpus entry.
    catalog = R.assemble(plan, state)
    R.require(catalog['complete'] is False, 'A staged pass cannot become complete')
    return {'schema': 'reader-assistance-stage-report.v1',
            **missing_summary(plan, state),
            'allStagedClipsReady': True,
            'recipeSha256': plan['recipeSha256'],
            'usedPacks': len(catalog['packs']),
            'storedBytes': sum(pack['size'] for pack in catalog['packs'].values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'inventory', 'bake', 'report'])
    parser.add_argument('--data', default='reader-assistance/staged.json')
    parser.add_argument('--recipe', default='reader-assistance/recipe.json')
    parser.add_argument('--assets', default='supertonic3')
    parser.add_argument('--bucket')
    parser.add_argument('--out', help='Write a non-activating plan or evidence report')
    args = parser.parse_args()
    R.require(os.environ.get('GITHUB_REPOSITORY', R.REPOSITORY) == R.REPOSITORY,
              'Unexpected repository')
    recipe = json.loads(Path(args.recipe).read_text())
    plan = build_stage_plan(Path(args.data).read_bytes(), recipe)
    if args.command == 'plan':
        result = missing_summary(plan, {'clips': {}})
        result['recipeSha256'] = plan['recipeSha256']
        result['textCharacters'] = sum(len(task['text']) for task in plan['tasks'].values())
    else:
        client = R.GitHub()
        cache = Path(os.environ.get('RUNNER_TEMP', tempfile.gettempdir())) / 'reader-assistance-pack-indexes'
        if args.command == 'bake':
            result = R.bake(client, plan, recipe, args.bucket, args.assets, cache_dir=cache)
            result.update(stageOnly=True, productionActivationAllowed=False)
        else:
            state = R.inventory(client, plan, cache_dir=cache)
            if args.command == 'inventory':
                result = missing_summary(plan, state)
                if os.environ.get('GITHUB_OUTPUT'):
                    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                        output.write('missing=' + str(result['missing']) + '\n')
                        output.write('buckets=' + json.dumps(result['buckets'] or ['0']) + '\n')
            else:
                result = stage_report(plan, state)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.out:
        destination = Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    if args.command == 'report' and os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write('## Approved F1 prebake complete\n\n')
            output.write(f"{result['approvedEntries']} exact approved entries; {result['uniqueAudioClips']} unique clips ready.\n\n")
            output.write(f"{result['unexplainedBodyRows']} corpus rows still lack an approved explanation in this source.\n\n")
            output.write('No production catalog, Worker, or study-site activation was performed. Final activation still requires the separate complete-source gate.\n')


if __name__ == '__main__':
    try:
        main()
    except (R.ContractError, json.JSONDecodeError, OSError) as error:
        raise SystemExit('reader-assistance-stage: ' + str(error))
