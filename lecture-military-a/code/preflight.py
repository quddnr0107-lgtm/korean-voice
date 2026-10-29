import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
EXPECTED={'version': 'military-service-authorization/v1', 'approved': True, 'approval_reference': 'owner-approval-20261029T012349Z', 'repository': 'quddnr0107-lgtm/korean-voice', 'environment': 'admin-law-premium-production', 'branch': 'integrated-lectures-premium-20261028', 'courses': {'MS': 166}, 'batch': 'a', 'ids_start': 1, 'ids_end': 83, 'source_key_name': 'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'media_key_name': 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64'}
def require_approval():
 a=json.loads((ROOT/"authorization.json").read_text())
 if a!=EXPECTED:raise ValueError("approval scope")
 return a
if __name__=="__main__":require_approval();print('{"status":"scope_approved"}')
