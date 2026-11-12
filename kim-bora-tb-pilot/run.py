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
REF = 'refs/heads/codex/reading-quality-20261112'
SOURCE_HASH = 'bceadf7509da807f9fd00c4bd7ca6cf8aa13e5797ccb078d853db8eaf2342915'
DOMAIN = b'kim-bora-tb-pilot-recipient/v1'
MAX_ZIP = 64 * 1024 * 1024


class PilotFailure(ValueError):
    """A bounded machine-readable failure code containing no manuscript text."""


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
    return cfg


def validate_request(request, cfg):
    if (request.get('schema') != 'kim-bora-tb-pilot-request/v1'
            or request.get('purpose') != 'one-lesson-private-preview'
            or request.get('config_sha256') != digest(enc(cfg))):
        raise PilotFailure('request_configuration_mismatch')
    return request


def require_context(env=os.environ):
    if (env.get('GITHUB_REPOSITORY') != REPO or env.get('GITHUB_REF') != REF
            or env.get('GITHUB_EVENT_NAME') != 'push'):
        raise RuntimeError('unapproved workflow context')
    original = env.get('BORA_HOST_NETNS')
    if not original or os.readlink('/proc/self/ns/net') == original:
        raise RuntimeError('offline namespace required')


def build_reviewed_plan(source, cfg):
    from paragraph_plan import build_plan
    if digest(source.encode('utf-8')) != cfg['source_sha256']:
        raise PilotFailure('operating_source_hash_mismatch')
    plan = build_plan(source, 'TB-001', cfg['max_chars'], cfg.get('reading_spans', []))
    for unit in plan['units']:
        for old, new in cfg.get('spoken_replacements', []):
            unit['spoken'] = unit['spoken'].replace(old, new)
        unit['critical_terms'] = [t for t in cfg.get('critical_terms', []) if t in unit['spoken']]
    if len(plan['units']) != cfg['unit_count'] or len(plan['paragraphs']) != cfg['paragraph_count']:
        raise ValueError('plan shape')
    if digest(enc(plan)) != cfg['expected_plan_sha256']:
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
    directory = Path(directory).resolve()
    names = ['subset.json']
    for row in manifest['units']:
        if row.get('blocked'):
            continue
        name = row.get('relative_file', '')
        if name != f'units/{row["unit_index"]:04d}/final.wav':
            raise ValueError('unexpected subset file')
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError('duplicate subset file')
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name in names:
            p = directory/name
            if p.is_symlink() or not p.is_file() or directory not in p.resolve().parents:
                raise ValueError('unsafe subset file')
            z.write(p, name)
    value = result.getvalue()
    if len(value) > MAX_ZIP:
        raise ValueError('subset too large')
    return value


def seal_result(plain, cfg, group, run_id, attempt, quality_status='private_pcm_subset_complete'):
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
        run_id=str(run_id), attempt=str(attempt), plain_sha256=digest(plain),
        recipient_public=cfg['receiver_public'],
        sender_public=b64(sender.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
        salt=b64(salt), nonce=b64(nonce))
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
            or header.get('recipient_public') != cfg['receiver_public']):
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=HERE/'config.json')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--group', type=int)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    cfg = validate_config(json.loads(args.config.read_text()))
    validate_request(json.loads((HERE/'request.json').read_text()), cfg)
    if not args.check_only and (args.group is None or not 0 <= args.group < len(cfg['groups'])):
        raise ValueError('group selection')
    plan = open_plan(cfg)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.output.is_symlink():
        raise ValueError('output symlink')
    if args.check_only:
        result = dict(schema='kim-bora-tb-pilot-public-check/v1', status='source_and_plan_match',
            source_sha256=plan['source_sha256'], plan_sha256=cfg['expected_plan_sha256'],
            groups=len(cfg['groups']), unit_count=len(plan['units']),
            matrix=dict(group=list(range(len(cfg['groups'])))))
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
                             os.environ['GITHUB_RUN_ATTEMPT'], manifest['status'])
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
