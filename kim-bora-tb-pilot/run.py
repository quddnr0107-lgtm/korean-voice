"""TB-001 only: protected source -> offline PCM -> recipient-sealed preview.

Never uploads, activates media, reads media master keys, or writes plaintext to
the artifact directory. Model assets are downloaded in a separate keyless step.
"""
import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'lecture-premium'/'code'))
sys.path.insert(0, str(ROOT/'research'/'reading-quality'))

SCHEMA = 'kim-bora-tb-pilot/v1'
REPO = 'quddnr0107-lgtm/korean-voice'
REF = 'refs/heads/integrated-lectures-premium-20261028'
SOURCE_HASH = 'bceadf7509da807f9fd00c4bd7ca6cf8aa13e5797ccb078d853db8eaf2342915'
DOMAIN = b'kim-bora-tb-pilot-recipient/v1'
DIAGNOSTIC_DOMAIN = b'kim-bora-tb-pilot-source-diagnostic/v1'
MAX_ZIP = 64 * 1024 * 1024
MAX_AUDIO_FILE_BYTES = 16 * 1024 * 1024
MAX_SUBSET_METADATA_BYTES = 4 * 1024 * 1024


class PilotFailure(ValueError):
    """A bounded machine-readable failure code containing no manuscript text."""


class SourceMismatch(PilotFailure):
    def __init__(self, source):
        super().__init__('operating_source_hash_mismatch')
        self.source = source


def enc(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def unb(value):
    return base64.b64decode(value, validate=True)


def b64(value):
    return base64.b64encode(value).decode('ascii')


def validate_config(cfg):
    if (cfg.get('schema') != SCHEMA or cfg.get('lesson_id') != 'TB-001'
            or cfg.get('source_sha256') != SOURCE_HASH
            or cfg.get('publication') != 'encrypted_requester_preview_only'
            or cfg.get('replace_existing') is not False):
        raise ValueError('pilot scope')
    if not re.fullmatch('[a-f0-9]{64}', cfg.get('expected_plan_sha256', '')):
        raise ValueError('plan identity')
    count = cfg.get('unit_count')
    groups = cfg.get('groups')
    if type(count) is not int or not 1 <= count <= 200:
        raise ValueError('unit count')
    if not isinstance(groups, list) or not 1 <= len(groups) <= 40:
        raise ValueError('group count')
    cursor = 0
    for group in groups:
        if (not isinstance(group, list) or len(group) != 2
                or any(type(x) is not int for x in group)
                or group[0] != cursor or not cursor < group[1] <= count
                or group[1] - cursor > 5):
            raise ValueError('group coverage')
        cursor = group[1]
    if cursor != count:
        raise ValueError('group coverage')
    if len(unb(cfg.get('receiver_public', ''))) != 32:
        raise ValueError('recipient key')
    if type(cfg.get('max_chars')) is not int or not 40 <= cfg['max_chars'] <= 220:
        raise ValueError('chunk bound')
    replacements = cfg.get('spoken_replacements', [])
    if not isinstance(replacements, list) or len(replacements) > 50:
        raise ValueError('pronunciation configuration')
    for pair in replacements:
        if (not isinstance(pair, list) or len(pair) != 2 or not pair[0]
                or any(not isinstance(x, str) or len(x) > 100 for x in pair)):
            raise ValueError('pronunciation configuration')
    terms = cfg.get('critical_terms', [])
    if not isinstance(terms, list) or any(not isinstance(x, str) or not x or len(x) > 100 for x in terms):
        raise ValueError('critical terms')
    prefixes = cfg.get('delivery_prefixes', [])
    if not isinstance(prefixes, list) or len(prefixes) > 4:
        raise ValueError('delivery prefix bounds')
    prefix_indices = set()
    for item in prefixes:
        if (not isinstance(item, dict) or set(item) != {'unit_index', 'prefix'}
                or type(item.get('unit_index')) is not int
                or not 0 <= item['unit_index'] < count
                or item.get('prefix') not in ('자, ', '음, ')
                or item['unit_index'] in prefix_indices):
            raise ValueError('delivery prefix configuration')
        prefix_indices.add(item['unit_index'])
    accepted = cfg.get('accepted_sources', [])
    if not isinstance(accepted, list) or len(accepted) > 12:
        raise ValueError('accepted source bounds')
    seen = set()
    for spec in accepted:
        if (not isinstance(spec, dict) or not re.fullmatch('[a-f0-9]{64}', spec.get('source_sha256', ''))
                or not re.fullmatch('[a-f0-9]{64}', spec.get('expected_plan_sha256', ''))
                or spec.get('unit_count') != count or spec.get('paragraph_count') != cfg.get('paragraph_count')
                or not isinstance(spec.get('reading_spans'), list)
                or spec['source_sha256'] in seen):
            raise ValueError('accepted source identity')
        seen.add(spec['source_sha256'])
    return cfg


def validate_request(request, cfg):
    if (not isinstance(request,dict) or request.get('schema') != 'kim-bora-tb-pilot-request/v1'
            or request.get('purpose') != 'one-lesson-private-preview'
            or request.get('config_sha256') != digest(enc(cfg))):
        raise PilotFailure('request_configuration_mismatch')
    if 'render_groups' in request:
        groups=request['render_groups']
        if (not isinstance(groups,list) or not 1 <= len(groups) <= len(cfg['groups'])
                or any(type(group) is not int or not 0 <= group < len(cfg['groups']) for group in groups)
                or len(set(groups)) != len(groups)):
            raise PilotFailure('request_render_groups_invalid')
    return request


def request_scope(request,cfg):
    validate_request(request,cfg)
    all_groups=list(range(len(cfg['groups'])))
    groups=sorted(request.get('render_groups',all_groups))
    full_run=groups==all_groups
    return dict(matrix=dict(group=groups),full_run=full_run,
                render_timeout_minutes=90 if full_run else 180)


def require_context(env=os.environ):
    if (env.get('GITHUB_REPOSITORY') != REPO or env.get('GITHUB_REF') != REF
            or env.get('GITHUB_EVENT_NAME') != 'push'):
        raise RuntimeError('unapproved workflow context')
    original = env.get('BORA_HOST_NETNS')
    if not original or os.readlink('/proc/self/ns/net') == original:
        raise RuntimeError('offline namespace required')


def source_spec(source, cfg):
    actual = digest(source.encode('utf-8'))
    candidates = [dict(source_sha256=cfg['source_sha256'], expected_plan_sha256=cfg['expected_plan_sha256'],
        reading_spans=cfg.get('reading_spans', []), unit_count=cfg['unit_count'], paragraph_count=cfg['paragraph_count'])]
    candidates += cfg.get('accepted_sources', [])
    matches = [s for s in candidates if s['source_sha256'] == actual]
    if not matches:
        raise SourceMismatch(source)
    identities = {(s['expected_plan_sha256'], enc(s['reading_spans'])) for s in matches}
    if len(identities) != 1:
        raise PilotFailure('conflicting_source_variant')
    return matches[0]


def accepted_identities(cfg):
    return {(cfg['source_sha256'],cfg['expected_plan_sha256'])} | {
        (s['source_sha256'],s['expected_plan_sha256']) for s in cfg.get('accepted_sources',[])}


def build_reviewed_plan(source, cfg):
    from paragraph_plan import build_plan
    spec = source_spec(source, cfg)
    plan = build_plan(source, 'TB-001', cfg['max_chars'], spec['reading_spans'])
    for unit in plan['units']:
        for old, new in cfg.get('spoken_replacements', []):
            unit['spoken'] = unit['spoken'].replace(old, new)
        unit['critical_terms'] = [t for t in cfg.get('critical_terms', []) if t in unit['spoken']]
    prefixes = cfg.get('delivery_prefixes', [])
    if not isinstance(prefixes, list) or len(prefixes) > 4:
        raise ValueError('delivery prefix bounds')
    used = set()
    for item in prefixes:
        if (not isinstance(item, dict) or set(item) != {'unit_index', 'prefix'}
                or type(item.get('unit_index')) is not int
                or not 0 <= item['unit_index'] < len(plan['units'])
                or item.get('prefix') not in ('자, ', '음, ')
                or item['unit_index'] in used):
            raise ValueError('delivery prefix configuration')
        index = item['unit_index']
        unit = plan['units'][index]
        if (unit['mode'] != 'explain'
                or (index and plan['units'][index-1]['paragraph_id'] == unit['paragraph_id'])
                or re.match(r'^(?:자|음)(?:[,，…\.\s]|$)', unit['spoken'].lstrip())):
            raise ValueError('delivery prefix placement')
        unit['source_spoken'] = unit['spoken']
        unit['delivery_prefix'] = item['prefix']
        unit['spoken'] = item['prefix'] + unit['source_spoken']
        used.add(index)
    if len(plan['units']) != spec['unit_count'] or len(plan['paragraphs']) != spec['paragraph_count']:
        raise ValueError('plan shape')
    if digest(enc(plan)) != spec['expected_plan_sha256']:
        raise PilotFailure('reviewed_plan_hash_mismatch')
    return plan


def open_plan(cfg):
    # Namespace/ref guards run BEFORE source-key access. Pop it before model imports.
    require_context()
    value = os.environ.pop('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', '')
    key = unb(value)
    del value
    if len(key) != 32:
        raise ValueError('protected source key unavailable')
    import lecture_crypto as crypto
    public = json.loads((HERE/'receiver-public.json').read_text())
    wrapped = json.loads((HERE/'receiver-wrapped.json').read_text())
    record = json.loads((ROOT/'lecture-premium'/'inputs'/'TB-001.sealed.json').read_text())
    compressed = crypto.open_input(key, public, wrapped, record['record'], unb(record['cipher_b64']), 'TB-001')
    del key, wrapped, record
    inflater = zlib.decompressobj()
    plain = inflater.decompress(compressed, 8 * 1024 * 1024 + 1)
    if len(plain) > 8 * 1024 * 1024 or not inflater.eof or inflater.unused_data:
        raise ValueError('source payload bounds')
    payload = json.loads(plain)
    del plain, compressed
    if payload.get('version') != 'integrated-lecture-input/v1' or payload.get('course') != 'tongbang':
        raise ValueError('source payload schema')
    lesson = payload['lesson']
    if lesson.get('id') != 'TB-001':
        raise ValueError('source lesson scope')
    source = ''.join(c['text'] for c in lesson['chunks'])
    if digest(source.encode()) != lesson['source_sha256']:
        raise ValueError('source internal integrity')
    del payload, lesson
    return build_reviewed_plan(source, cfg)


def archive_subset(directory, manifest):
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('unsafe subset directory')
    directory = directory.resolve()
    members = [('subset.json', None, MAX_SUBSET_METADATA_BYTES, None)]
    for row in manifest['units']:
        if row.get('blocked'):
            if 'relative_file' in row or 'file' in row:
                raise ValueError('blocked audio cannot be an accepted file')
            if 'review_relative_file' not in row:
                if row.get('review_file') is not None:
                    raise ValueError('missing review file reference')
                continue
            index = row.get('unit_index')
            name = row['review_relative_file']
            size = row.get('review_size_bytes')
            if (type(index) is not int or index < 0 or name != f'units/{index:04d}/review.wav'
                    or row.get('review_file') != 'review.wav' or row.get('review_only') is not True
                    or row.get('production_eligible') is not False
                    or row.get('review_kind') != 'raw_candidate_for_listening_only'
                    or row.get('review_signal',{}).get('blocked') is not False
                    or type(size) is not int or not 0 < size <= MAX_AUDIO_FILE_BYTES):
                raise ValueError('unexpected quarantined review file')
            members.append((name,row.get('review_sha256'),MAX_AUDIO_FILE_BYTES,size))
        else:
            index = row.get('unit_index');name = row.get('relative_file', '')
            if (type(index) is not int or index < 0 or name != f'units/{index:04d}/final.wav'
                    or 'review_relative_file' in row or row.get('review_file') is not None):
                raise ValueError('unexpected subset file')
            members.append((name,row.get('sha256'),MAX_AUDIO_FILE_BYTES,None))
    if len(members) != len({name for name,_,_,_ in members}):
        raise ValueError('duplicate subset file')
    result = io.BytesIO()
    total_bytes = 0
    with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, expected_hash, maximum, expected_size in members:
            p = directory
            for part in Path(name).parts:
                p /= part
                if p.is_symlink():
                    raise ValueError('unsafe subset symlink')
            if directory not in p.resolve().parents:
                raise ValueError('unsafe subset file')
            fd = os.open(p, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as file:
                info = os.fstat(file.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
                    raise ValueError('subset file size')
                raw = file.read(maximum + 1)
            if len(raw) != info.st_size or (expected_size is not None and len(raw) != expected_size):
                raise ValueError('subset file size changed')
            if name == 'subset.json':
                if enc(json.loads(raw)) != enc(manifest):
                    raise ValueError('subset metadata changed')
            elif not isinstance(expected_hash,str) or not re.fullmatch('[a-f0-9]{64}',expected_hash) or digest(raw) != expected_hash:
                raise ValueError('subset file hash mismatch')
            total_bytes += len(raw)
            if total_bytes > MAX_ZIP:
                raise ValueError('subset uncompressed size')
            z.writestr(name, raw)
    value = result.getvalue()
    if len(value) > MAX_ZIP:
        raise ValueError('subset too large')
    return value


def seal_result(plain, cfg, group, run_id, attempt, quality_status='private_pcm_subset_complete',
                source_sha256=None, plan_sha256=None):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if (type(group) is not int or not 0 <= group < len(cfg['groups']) or len(plain) > MAX_ZIP
            or quality_status not in ('private_pcm_subset_complete','blocked_requires_review')):
        raise ValueError('seal context')
    sender = X25519PrivateKey.generate()
    salt, nonce = os.urandom(32), os.urandom(12)
    header = dict(schema='kim-bora-tb-pilot-envelope/v1', lesson_id='TB-001',
        config_sha256=digest(enc(cfg)), group=group, unit_range=cfg['groups'][group],
        quality_status=quality_status,
        source_sha256=source_sha256 or cfg['source_sha256'],
        plan_sha256=plan_sha256 or cfg['expected_plan_sha256'],
        run_id=str(run_id), attempt=str(attempt), plain_sha256=digest(plain),
        recipient_public=cfg['receiver_public'],
        sender_public=b64(sender.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
        salt=b64(salt), nonce=b64(nonce))
    if (header['source_sha256'],header['plan_sha256']) not in accepted_identities(cfg):
        raise ValueError('unreviewed source identity')
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=DOMAIN).derive(
        sender.exchange(X25519PublicKey.from_public_bytes(unb(cfg['receiver_public']))))
    cipher = AESGCM(key).encrypt(nonce, plain, enc(header))
    return dict(header=header, cipher_sha256=digest(cipher), cipher=b64(cipher))


def open_result(envelope, cfg, group, recipient_private):
    """Recipient helper. This private key is generated locally, never a master key."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    header = envelope['header']
    if (header.get('schema') != 'kim-bora-tb-pilot-envelope/v1' or header.get('lesson_id') != 'TB-001'
            or header.get('config_sha256') != digest(enc(cfg)) or header.get('group') != group
            or header.get('unit_range') != cfg['groups'][group]
            or header.get('recipient_public') != cfg['receiver_public']
            or (header.get('source_sha256'),header.get('plan_sha256')) not in accepted_identities(cfg)):
        raise ValueError('recipient context')
    private = X25519PrivateKey.from_private_bytes(recipient_private)
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if b64(public) != cfg['receiver_public']:
        raise ValueError('recipient key mismatch')
    cipher = unb(envelope['cipher'])
    if len(cipher) > MAX_ZIP + 16 or digest(cipher) != envelope['cipher_sha256']:
        raise ValueError('cipher integrity')
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=unb(header['salt']), info=DOMAIN).derive(
        private.exchange(X25519PublicKey.from_public_bytes(unb(header['sender_public']))))
    plain = AESGCM(key).decrypt(unb(header['nonce']), cipher, enc(header))
    if digest(plain) != header['plain_sha256']:
        raise ValueError('plain integrity')
    return plain


def seal_source_diagnostic(source, cfg, run_id, attempt):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    actual_hash = digest(source.encode())
    payload = dict(schema='kim-bora-tb-source-diagnostic/v1', lesson_id='TB-001', actual_source=source,
        actual_source_sha256=actual_hash, actual_utf8_bytes=len(source.encode()),
        actual_utf16_length=len(source.encode('utf-16-le'))//2,
        expected_source_sha256=cfg['source_sha256'],
        source_repository=cfg.get('source_repository'), source_revision=cfg.get('source_revision'),
        source_blob=cfg.get('source_blob'), config_sha256=digest(enc(cfg)),
        status='source_mismatch_render_not_started')
    plain = enc(payload)
    if len(plain) > MAX_ZIP:
        raise ValueError('source diagnostic bounds')
    sender = X25519PrivateKey.generate(); salt,nonce = os.urandom(32),os.urandom(12)
    header = dict(schema='kim-bora-tb-source-diagnostic-envelope/v1', lesson_id='TB-001',
        config_sha256=digest(enc(cfg)), run_id=str(run_id), attempt=str(attempt),
        actual_source_sha256=actual_hash, recipient_public=cfg['receiver_public'], plain_sha256=digest(plain),
        sender_public=b64(sender.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)),
        salt=b64(salt),nonce=b64(nonce))
    key=HKDF(algorithm=hashes.SHA256(),length=32,salt=salt,info=DIAGNOSTIC_DOMAIN).derive(
        sender.exchange(X25519PublicKey.from_public_bytes(unb(cfg['receiver_public']))))
    cipher=AESGCM(key).encrypt(nonce,plain,enc(header))
    return dict(header=header,cipher_sha256=digest(cipher),cipher=b64(cipher))


def open_source_diagnostic(envelope,cfg,recipient_private):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    h=envelope['header']
    if (h.get('schema')!='kim-bora-tb-source-diagnostic-envelope/v1' or h.get('lesson_id')!='TB-001'
            or h.get('config_sha256')!=digest(enc(cfg)) or h.get('recipient_public')!=cfg['receiver_public']):
        raise ValueError('diagnostic context')
    private=X25519PrivateKey.from_private_bytes(recipient_private)
    if b64(private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw))!=cfg['receiver_public']:
        raise ValueError('diagnostic recipient')
    cipher=unb(envelope['cipher'])
    if len(cipher)>MAX_ZIP+16 or digest(cipher)!=envelope['cipher_sha256']:
        raise ValueError('diagnostic cipher integrity')
    key=HKDF(algorithm=hashes.SHA256(),length=32,salt=unb(h['salt']),info=DIAGNOSTIC_DOMAIN).derive(
        private.exchange(X25519PublicKey.from_public_bytes(unb(h['sender_public']))))
    plain=AESGCM(key).decrypt(unb(h['nonce']),cipher,enc(h))
    if digest(plain)!=h['plain_sha256']:
        raise ValueError('diagnostic plaintext integrity')
    data=json.loads(plain)
    if digest(data['actual_source'].encode())!=h['actual_source_sha256']:
        raise ValueError('diagnostic source integrity')
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=HERE/'config.json')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--group', type=int)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    cfg = validate_config(json.loads(args.config.read_text()))
    scope=request_scope(json.loads((HERE/'request.json').read_text()),cfg)
    if not args.check_only and args.group not in scope['matrix']['group']:
        raise ValueError('group selection')
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.output.is_symlink():
        raise ValueError('output symlink')
    try:
        plan = open_plan(cfg)
    except SourceMismatch as exc:
        if not args.check_only:
            raise
        sealed = seal_source_diagnostic(exc.source,cfg,os.environ['GITHUB_RUN_ID'],os.environ['GITHUB_RUN_ATTEMPT'])
        (args.output/'source-diagnostic.sealed.json').write_bytes(enc(sealed))
        result=dict(schema='kim-bora-tb-pilot-public-check/v1',status='source_mismatch_render_not_started',
                    actual_source_sha256=sealed['header']['actual_source_sha256'])
        (args.output/'plan-public.json').write_bytes(enc(result))
        print(json.dumps(dict(status=result['status'])))
        return
    if args.check_only:
        result = dict(schema='kim-bora-tb-pilot-public-check/v1', status='source_and_plan_match',
            source_sha256=plan['source_sha256'], plan_sha256=digest(enc(plan)),
            groups=len(scope['matrix']['group']), unit_count=len(plan['units']),**scope)
        (args.output/'plan-public.json').write_bytes(enc(result))
        print(json.dumps(dict(status=result['status'], units=result['unit_count'], groups=result['groups'])))
        return
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      DO_NOT_TRACK='1', ORT_DISABLE_TELEMETRY='1')
    # No source key remains in the process environment. Native code has no network.
    from pilot_render import render_subset
    parent = Path(os.environ['RUNNER_TEMP'])
    directory = Path(tempfile.mkdtemp(prefix='bora-private-', dir=parent))
    try:
        start, stop = cfg['groups'][args.group]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            manifest = render_subset(plan, directory, os.environ['BORA_MODEL'], os.environ['BORA_ASR'],
                list(range(start, stop)), threads=2, max_new_tokens=900)
        archive = archive_subset(directory, manifest)
        result = seal_result(archive, cfg, args.group, os.environ['GITHUB_RUN_ID'],
                             os.environ['GITHUB_RUN_ATTEMPT'], manifest['status'],
                             plan['source_sha256'],digest(enc(plan)))
        path = args.output/f'group_{args.group:03d}.sealed.json'
        with path.open('x') as file:
            json.dump(result, file, separators=(',', ':'))
        print(json.dumps(dict(group=args.group, units=stop-start, status='encrypted_result_ready',
                              quality_status=manifest['status'])))
        # Review failures are delivered encrypted, so they can be inspected/repaired.
    finally:
        shutil.rmtree(directory)


if __name__ == '__main__':
    try:
        main()
    except PilotFailure as exc:
        print('TB_PILOT_FAILED:' + str(exc), file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print('TB_PILOT_FAILED:' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
