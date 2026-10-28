"""Protected public-job wrapper; default DISABLED. Only ciphertext is written.
Human recipient authorization is mandatory; header claims alone are not authentication.
"""
import hashlib,json,os,time
from pathlib import Path
from transfer_protocol import TransferError,validate_header,unique_object,canonical,unb,seal_packet
PUBLIC_REPO='quddnr0107-lgtm/korean-voice'
PUBLIC_REF='refs/heads/integrated-lectures-premium-20261028'
# Set only in a separately reviewed one-shot arming commit, before private receiver starts.
EXPECTED_RECEIVER_WORKFLOW_SHA='d67f72bd41551fa5fbe84f20aada4d0d0096e36a'
WINDOW_START=1793228220
WINDOW_END=1793229120

def prepare_packet(env,now=None):
    now=int(time.time()) if now is None else now
    if env.get('GITHUB_REPOSITORY')!=PUBLIC_REPO or env.get('GITHUB_REF')!=PUBLIC_REF or env.get('GITHUB_EVENT_NAME')!='workflow_dispatch' or env.get('GITHUB_ACTOR')!='quddnr0107-lgtm' or env.get('GITHUB_TRIGGERING_ACTOR')!='quddnr0107-lgtm' or env.get('GITHUB_RUN_ATTEMPT')!='1':raise TransferError('sealer identity')
    if env.get('GITHUB_SHA')!=env.get('GITHUB_WORKFLOW_SHA') or env.get('GITHUB_SHA')!=env.get('REVIEWED_SEALER_SHA'):raise TransferError('sealer commit')
    if not EXPECTED_RECEIVER_WORKFLOW_SHA or not 0<WINDOW_END-WINDOW_START<=900 or not WINDOW_START<=now<WINDOW_END:raise TransferError('sealer disabled/window')
    if env.get('OWNER_VERIFIED_PRIVATE_RUN')!='true':raise TransferError('recipient verification missing')
    raw=env.get('PUBLIC_RECEIVER_HEADER','')
    if not isinstance(raw,str) or len(raw.encode())>2048:raise TransferError('header size')
    try:h=json.loads(raw,object_pairs_hook=unique_object)
    except Exception:raise TransferError('header JSON') from None
    validate_header(h,now)
    if h['receiver_workflow_sha']!=EXPECTED_RECEIVER_WORKFLOW_SHA or h['issued_at']<WINDOW_START or h['expires_at']>WINDOW_END:raise TransferError('recipient workflow/window')
    if env.get('VERIFIED_RECEIVER_RUN_ID')!=h['receiver_run_id'] or env.get('VERIFIED_HEADER_SHA256')!=hashlib.sha256(canonical(h)).hexdigest():raise TransferError('recipient receipt mismatch')
    # All identity/window/receipt checks precede reading the operational secret.
    media=None
    try:
        media=bytearray(unb(env.pop('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64',''),32))
        packet=seal_packet(media,h,now)
    finally:
        if media is not None:media[:]=b'\0'*len(media)
    return 'receiver-'+h['receiver_run_id']+'-'+h['nonce']+'.json',packet

if __name__=='__main__':
    try:
        root=Path('.sealed-transfer')
        if root.exists() or root.is_symlink():raise TransferError('relay directory already exists')
        root.mkdir(mode=0o700)
        name,packet=prepare_packet(os.environ)
        with (root/name).open('xb') as f:f.write(packet)
        print('SEALED_PACKET_READY '+name,flush=True)
    except BaseException:
        print('SEALER_STOPPED: no secret output',flush=True)
        raise SystemExit(1)
