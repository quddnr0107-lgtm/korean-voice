"""Narrow trigger routing. No key access; separate activation-only commit."""
import json,os,re,subprocess
from pathlib import Path
TRIGGER='bora-production/tb13-silence-diagnostic-once.json'
REQUEST={'schema':'tb13-diagnostic-once/v1','request_id':'tb13-silence-43565543458-once',
         'source_run_id':43565543458,'source_commit':'836500cd9250f38698235c729ae5553f41e7c262'}
def mode(changes,request=None):
    if not any(line.split('\t')[-1]==TRIGGER for line in changes): return 'production'
    if changes!=['A\t'+TRIGGER] or request!=REQUEST: raise ValueError('diagnostic_activation_scope')
    return 'diagnostic'
def main():
    before=os.environ['EVENT_BEFORE'];head=os.environ['GITHUB_SHA']
    if not all(re.fullmatch('[a-f0-9]{40}',s) and s!='0'*40 for s in (before,head)):
        raise ValueError('event_revision')
    # Full history checkout lets this bind the actual push range, not HEAD^.
    changes=subprocess.check_output(['git','diff','--name-status','--no-renames',before,head,'--'],text=True).splitlines()
    request=json.loads(Path(TRIGGER).read_text()) if any(x.endswith('\t'+TRIGGER) for x in changes) else None
    chosen=mode(changes,request)
    if chosen=='diagnostic':
        # Reject ordinary replay/deletion/re-addition in branch history.
        history=subprocess.check_output(['git','log','--format=%H',before,'--',TRIGGER],text=True).strip()
        if history: raise ValueError('diagnostic_already_activated')
        if os.environ.get('GITHUB_RUN_ATTEMPT')!='1': raise ValueError('explicit_retry_review_required')
    with open(os.environ['GITHUB_OUTPUT'],'a') as f: f.write('mode='+chosen+'\n')
if __name__=='__main__': main()
