"""Read two pinned, encrypted DR assembly receipts. Only the fetch subcommand uses the network."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sys


RELEASE = 445842493
RECIPE = 'd3d35666712d276c0fa60bb5c5b1f7ab7d11c7042d490c95bb6fb8846682502d'
SETTINGS = '2fabab4a10c67164403f759fc12b9af489a4eeb948599d4657e014ec75c96d13'
PINS = {
    'DR-052': (753495268, '83503e5fe924c66c72834c5941b9b4a85c58a5aaac842b6ff2f0aac684089381',
               '09fa7f04a92f047298b141b2659a4bf4ebfb5260a8e6f9fffcb3606ec610763e',
               74795364, 74853720, 110),
    'DR-054': (753483138, 'd12c22d4148e1f3cd57db0551c0b58c239633aac2aa3805b672445364e3de747',
               '1a3289c6e64971170d302817facad9a9d3c64bc8b87e885983454280ee1f579f',
               74777282, 74833785, 119),
}
from batch3.config import MEDIA
PINS.update(MEDIA)
# This original receipt is also used by the isolated DR-038 pronunciation supplement.
from batch2.old_media import PINS as reviewed_second_batch_receipts
PINS['DR-038'] = reviewed_second_batch_receipts['DR-038']


PRODUCER_BLOBS = {
    'transport': 'c66fab15082ca4022e954b0d14572f72615a2194',
    'lecture_crypto': '1ca4d6da5b9272b3f5fae171d533bc2447716b7d',
    'namespace_guard': '10d7578f25c05cf40133645c2eb7a0a8b37428b8',
}
PARAGRAPH = re.compile(r'(?:[^\r\n]+(?:\r?\n(?![ \t]*\r?\n)[^\r\n]+)*)')


def require(condition, category):
    if not condition:
        raise ValueError(category)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def dependency(directory, name):
    file = Path(directory).resolve() / (name + '.py')
    data = file.read_bytes()
    require(hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest() == PRODUCER_BLOBS[name],
            'receipt_reader_code_pin')
    # Only the network fetch process imports transport and its sibling dependencies.
    if str(file.parent) not in sys.path:
        sys.path.append(str(file.parent))
    spec = importlib.util.spec_from_file_location('_exam80_original_' + name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch(producer_dir, folder, lesson):
    tr = dependency(producer_dir, 'transport')
    gh = tr.GitHubBackend()
    asset_id, descriptor_sha = PINS[lesson][:2]
    listed = [row for row in gh.assets({'id': RELEASE}) if row['state'] == 'uploaded']
    assets = {v['name']: v for v in listed}
    require(len(assets) == len(listed), 'asset_inventory')
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    descriptor = folder / (lesson + '.descriptor.json')
    asset = assets[descriptor.name]
    require(asset['id'] == asset_id and asset['digest'] == 'sha256:' + descriptor_sha, 'original_descriptor_asset')
    tr._fetch_final_object(gh, asset, descriptor, tr.DESCRIPTOR_CAP)
    require(digest(descriptor.read_bytes()) == descriptor_sha, 'original_descriptor_digest')
    _, names = tr._read_descriptor(descriptor, lesson)
    content = assets[names['content']]
    require(re.fullmatch('sha256:[0-9a-f]{64}', content['digest']), 'original_object_digest_missing')
    tr._fetch_final_object(gh, content, folder / names['content'], tr.FINAL_OBJECT_CAPS['content'])


def clock(lesson, rec):
    _, descriptor_sha, private_source, samples, private_samples, num_units = PINS[lesson]
    source = rec['narration_text']
    clean = source.strip('\r\n') + '\n'
    require(digest(source.encode()) == rec['source_sha256'] and digest(clean.encode()) == private_source,
            'original_manuscript_identity')
    require(rec['id'] == lesson and rec['recipe_sha256'] == RECIPE and rec['render_settings_sha256'] == SETTINGS
            and rec['sample_rate'] == 44100 and rec['total_samples'] == samples, 'original_recipe_or_timing_identity')
    segs = rec['segments']
    require(len(segs) == num_units and segs[0]['start_sample'] == 0 and segs[-1]['end_sample'] == samples,
            'original_timing_coverage')
    visible = []
    start_padding = len(source) - len(source.lstrip('\r\n'))
    cp = pos = 0
    for s in segs:
        a, b = s['start_cp'], s['end_cp']
        x, y = s['start_sample'], s['end_sample']
        text = s['text']
        require(a == cp and a < b and source[a:b] == text and pos <= x < y <= samples, 'original_timing_sequence')
        va = a + len(text) - len(text.lstrip()) - start_padding
        vb = b - len(text) + len(text.rstrip()) - start_padding
        require(va < vb, 'empty_original_unit')
        visible.append((va, vb, x, y))
        cp, pos = b, y
    require(cp == len(source), 'original_timing_script_coverage')
    rows = []
    consumed = 0
    for i, match in enumerate(PARAGRAPH.finditer(clean)):
        a, b = match.span()
        units = [s for s in visible if a <= s[0] and s[1] <= b]
        require(units and not any(not ch.isspace() for ch in clean[a:units[0][0]] + clean[units[-1][1]:b]),
                'original_paragraph_edges')
        require(all(clean[left[1]:right[0]].strip() == '' for left, right in zip(units, units[1:])),
                'original_paragraph_units')
        consumed += len(units)
        rows.append({'paragraph': i, 'start_cp': a, 'end_cp': b,
                     'start_sample': units[0][2], 'end_sample': units[-1][3]})
    require(consumed == len(visible), 'original_paragraph_coverage')
    return {'lesson_id': lesson, 'descriptor_sha256': descriptor_sha, 'source_sha256': private_source,
            'recipe_sha256': RECIPE, 'render_settings_sha256': SETTINGS, 'sample_rate': 44100,
            'total_samples': samples, 'private_audio_samples': private_samples,
            'timebase': 'original_segmented_player_44100', 'paragraphs': rows}


def open_receipt(producer_dir, folder, ident, media_key):
    """Return source-bound evidence for the original *segmented* player clock, never private-file offsets."""
    guard = dependency(producer_dir, 'namespace_guard')
    guard.validate_snapshot(os.getuid(), os.getuid(), socket.if_nameindex(), Path('/proc/net/route').read_text(),
                            os.readlink('/proc/self/ns/net'), os.environ['BORA_HOST_NETNS'],
                            Path('/proc/net/ipv6_route').read_text())
    require(os.getuid() != 0 and os.getuid() == os.geteuid(), 'original_receipt_unprivileged')
    crypto = dependency(producer_dir, 'lecture_crypto')
    folder = Path(folder)
    raw = (folder / (ident + '.descriptor.json')).read_bytes()
    require(digest(raw) == PINS[ident][1], 'original_descriptor_pin')
    desc = json.loads(raw)
    require(desc['lesson_id'] == ident and desc['version'] == 'integrated-lecture-output/v2', 'original_id')
    m = desc['content']
    require(re.fullmatch('[0-9a-f]{32}', m['object_id']), 'original_content_id')
    data = (folder / f'{ident}.content.{m["object_id"]}.bin').read_bytes()
    raw, _ = crypto.open_range(media_key, m, data, expected_id=ident, expected_kind='private-metadata',
                               expected_key_id='integrated-lectures-media-v1')
    rec = json.loads(raw)
    require(rec['audio_pack_sha256'] == desc['audio']['pack_sha256'] and
            rec['total_samples'] == desc['audio']['total_samples'], 'original_audio_metadata_binding')
    return clock(ident, rec)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['fetch'])
    p.add_argument('--producer', type=Path, required=True)
    p.add_argument('--lesson', choices=PINS, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    fetch(args.producer, args.output, args.lesson)
