"""Fail closed until the parent records explicit extended-key-purpose approval."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def require_approval():
 a=json.loads((ROOT/'authorization.json').read_text())
 if a.get('version')!='integrated-lecture-authorization/v1' or a.get('approved') is not True or not isinstance(a.get('approval_reference'),str) or a['approval_reference']!='owner-approval-20261028T1036Z':raise ValueError('key-purpose approval pending')
 if a.get('repository')!='quddnr0107-lgtm/korean-voice' or a.get('environment')!='admin-law-premium-production' or a.get('branch')!='integrated-lectures-premium-20261028' or a.get('courses')!={'TB':25,'DR':78,'RL':34}:raise ValueError('approval scope')
 if a.get('source_key_name')!='ADMIN_LAW_PREMIUM_SOURCE_KEY_B64' or a.get('media_key_name')!='ADMIN_LAW_PREMIUM_MEDIA_KEY_B64':raise ValueError('key names')
 return a
if __name__=='__main__':
 require_approval();print('{"status":"scope_approved"}')
