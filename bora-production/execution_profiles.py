"""Explicit execution identities; no environment or workflow impersonation."""
from __future__ import annotations
import re

PROFILE_ID = 'bora-python-fp32-compiled-predictor/v1'
SINGLE_PASS_PROFILE_ID = 'bora-python-fp32-single-pass/v1'
COMPILED_REQUEST_ID = 'tb002-compiled-predictor-pilot-owner-20261114-01'
CAMPAIGN_SHA256 = '60e9763ab344e8a59264ec634a47ee60321667bc426108100f2973a04cfdca76'
REQUEST_FILENAMES = {
    'eager': 'request.json',
    'compiled': 'compiled-profile-request.json',
    'single_pass': 'single-pass-request.json',
}
WORKFLOW_FILENAMES = {
    'eager': 'bora-fp32-production.yml',
    'compiled': 'bora-fp32-compiled-pilot.yml',
    'single_pass': 'bora-fp32-single-pass.yml',
}


def validate_profile(profile='eager'):
    if profile not in ('eager', 'compiled', 'single_pass'):
        raise ValueError('execution_profile')
    return profile


def request_filename(profile='eager'):
    return REQUEST_FILENAMES[validate_profile(profile)]


def workflow_filename(profile='eager'):
    return WORKFLOW_FILENAMES[validate_profile(profile)]


def validate_single_pass_request(request):
    required = dict(schema='bora-fp32-request/v1', campaign_sha256=CAMPAIGN_SHA256,
                    retry_blocked=False, execution_profile=SINGLE_PASS_PROFILE_ID)
    fields = set(required) | {'request_id', 'course'}
    if (not isinstance(request, dict) or not fields <= request.keys()
            or set(request) - fields - {'lesson_ids', 'execution_shard_chars', 'selected_shards', 'overlap_tb'}
            or any(request[name] != value for name, value in required.items())
            or ('overlap_tb' in request and
                (request['overlap_tb'] is not True or request['course'] != 'RL'
                 or not isinstance(request.get('lesson_ids'), list)
                 or not 1 <= len(request['lesson_ids']) <= 8
                 or 'selected_shards' in request))
            or request['retry_blocked'] is not False
            or not isinstance(request['request_id'], str)
            or not re.fullmatch('[a-z0-9][a-z0-9-]{0,127}', request['request_id'])
            or request['course'] not in ('TB', 'RL', 'DR', 'MS')):
        raise ValueError('single_pass_request_scope')


def validate_request(request, profile='eager'):
    """Validate new profiles without changing the legacy request contract."""
    validate_profile(profile)
    if profile == 'eager':
        return
    if profile == 'single_pass':
        validate_single_pass_request(request)
        return
    expected = dict(schema='bora-fp32-request/v1', campaign_sha256=CAMPAIGN_SHA256,
        course='TB', lesson_ids=['TB-002'], request_id=COMPILED_REQUEST_ID,
        retry_blocked=False, execution_shard_chars=2000, execution_profile=PROFILE_ID)
    if (not isinstance(request, dict) or request != expected
            or type(request.get('retry_blocked')) is not bool
            or type(request.get('execution_shard_chars')) is not int):
        raise ValueError('compiled_request_scope')
