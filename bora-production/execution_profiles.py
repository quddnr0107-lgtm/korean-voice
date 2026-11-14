"""Explicit execution identities; no environment or workflow impersonation."""
from __future__ import annotations

PROFILE_ID = 'bora-python-fp32-compiled-predictor/v1'
COMPILED_REQUEST_ID = 'tb002-compiled-predictor-pilot-owner-20261114-01'
CAMPAIGN_SHA256 = '60e9763ab344e8a59264ec634a47ee60321667bc426108100f2973a04cfdca76'


def validate_profile(profile='eager'):
    if profile not in ('eager', 'compiled'):
        raise ValueError('execution_profile')
    return profile


def request_filename(profile='eager'):
    return {'eager': 'request.json', 'compiled': 'compiled-profile-request.json'}[validate_profile(profile)]


def workflow_filename(profile='eager'):
    return {'eager': 'bora-fp32-production.yml', 'compiled': 'bora-fp32-compiled-pilot.yml'}[validate_profile(profile)]


def validate_request(request, profile='eager'):
    """Constrain the compiled pilot without changing the legacy request contract."""
    if validate_profile(profile) == 'eager':
        return
    expected = dict(schema='bora-fp32-request/v1', campaign_sha256=CAMPAIGN_SHA256,
        course='TB', lesson_ids=['TB-002'], request_id=COMPILED_REQUEST_ID,
        retry_blocked=False, execution_shard_chars=2000, execution_profile=PROFILE_ID)
    if (not isinstance(request, dict) or request != expected
            or type(request.get('retry_blocked')) is not bool
            or type(request.get('execution_shard_chars')) is not int):
        raise ValueError('compiled_request_scope')
