"""Recover the exact published TB-005 audio for this one owner edit."""
import base64
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import wave
import sys
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import delivery
import lecture_crypto as crypto
import lecture_pack as pack
from namespace_guard import validate_snapshot

LESSON = "TB-005"
ASSETS = {
    "TB-005.audio.5837334b4ddde1843527b28e2954e11d.bin": "5fe42114dea352642f64c3779cabe4b9c86a71bc3a8dde4ed10b27646a8effd4",
    "TB-005.content.01d748af392cb49ccf3ca24ba057b80f.bin": "f940ee939ed74a21f6a0d797b22120c8232805fcde1a8bf274050ae6dd6e349f",
    "TB-005.descriptor.json": "6dfa21fa2eaa2f9cfec5559ba1b87ef586302ffb70a78e606003ecf2484009e3"
}
RECIPIENT = "MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAoOjQD1Tr20EbIw4VMJDpKEKmDa6sK7z/VcAi0uThuSW3dElbDIpFskvEnV/qRdM8S0HlORXVzoHgXRgdF9xhJSnWSa/4iRd64he4Qu7ejfhbQJ7JBWe1+KBTaYl1LN8iA1kJeIR2BQxcJ+88ICMkNU3B9l3+HL2ZsAkww882ygmXPXFhNvUH4KIIZvrFSWnVPBwcKvjsfAVutAaF7XOlGQYsBRJpeIozIosiXA1ZpWdtQ0LIVB3wBVGgjD74niBOJ9fn3inyRc63UbqM47Ezs2PXpWiks+nEsOYC0rYmEIXeeJeQs+GwGhWtPddQz1JVAGwITsKZJPgB8uszFcuO1Vh6sfnPRJJycGFQZGST7xtWbp6v/ld/4NWZJr2Dzsv6UZmqlxxq4sDY2kV+d2UOiD5HSxTOrJgLVHMNAu4BKuNdZRP0lEDLcWj0UwrfA24pe0kLE7uDPh20Y8Bx1iVnWmPT+QL8n1BtXb4Q42TrNhWQ/Vwj2x8wGgvLusIeLtWRAgMBAAE="
CONTEXT = b"owner-video-edit/tb-005/v1"

def recover(directory, key):
    for name, digest in ASSETS.items():
        if crypto.digest((directory / name).read_bytes()) != digest:
            raise ValueError("published asset pin mismatch")
    descriptor = delivery.checked_complete(directory, LESSON)
    audio = descriptor["audio"]
    content = descriptor["content"]
    blob = (directory / delivery.media_name(LESSON, "audio", audio)).read_bytes()
    metadata = (directory / delivery.media_name(LESSON, "content", content)).read_bytes()
    plain, _ = crypto.open_range(
        key, content, metadata, expected_id=LESSON,
        expected_kind="private-metadata", expected_key_id=delivery.KEY_ID,
    )
    record = json.loads(plain)
    if record["id"] != LESSON or record["audio_pack_sha256"] != audio["pack_sha256"]:
        raise ValueError("audio and timing identity mismatch")
    rate = audio["sample_rate"]
    if rate != 44100:
        raise ValueError("unexpected audio sample rate")
    target_samples = rate * 600
    timeline = bytearray()
    for part in audio["parts"]:
        if len(timeline) == 2 * target_samples:
            break
        index = part["part_index"]
        start = part["pack_offset"]
        end = start + part["cipher_bytes"]
        data = pack.open_part(
            key, part, blob[start:end], expected_id=LESSON,
            expected_part_index=index, expected_pack_id=audio["object_id"],
        )
        pcm = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "mp3", "-i", "pipe:0", "-ac", "1",
             "-ar", str(rate), "-f", "s16le", "pipe:1"],
            input=data, check=True, capture_output=True,
        ).stdout
        expected = part["end_sample"] - part["start_sample"]
        if abs(len(pcm) // 2 - expected) > 1152:
            raise ValueError("part length differs from verified manifest")
        rest = min(expected, target_samples - len(timeline) // 2)
        timeline.extend(pcm[:2 * rest].ljust(2 * rest, b"\0"))
    if len(timeline) != 2 * target_samples:
        raise ValueError("incomplete first ten-minute timeline")
    wave_file = io.BytesIO()
    with wave.open(wave_file, "wb") as audio_out:
        audio_out.setparams((1, 2, rate, 0, "NONE", ""))
        audio_out.writeframes(timeline)
    segments = [s for s in record["segments"] if s["end_s"] <= 600]
    if not segments or "".join(s["text"] for s in segments) != record["narration_text"][:segments[-1]["end_cp"]]:
        raise ValueError("invalid timed caption coverage")
    edit_meta = {"id": LESSON, "duration_s": 600, "segments": segments}
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("metadata.json", json.dumps(edit_meta, ensure_ascii=False))
        archive.writestr("narration.wav", wave_file.getvalue())
    return bundle.getvalue()

def main():
    directory, target, uid, host_namespace = sys.argv[1:]
    validate_snapshot(
        os.getuid(), int(uid), socket.if_nameindex(),
        Path("/proc/net/route").read_text(),
        os.readlink("/proc/self/ns/net"), host_namespace,
        Path("/proc/net/ipv6_route").read_text(),
    )
    key = base64.b64decode(os.environ.pop("ADMIN_LAW_PREMIUM_MEDIA_KEY_B64"), validate=True)
    plain = recover(Path(directory), key)
    recipient = serialization.load_der_public_key(base64.b64decode(RECIPIENT, validate=True))
    envelope_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    wrapped = recipient.encrypt(
        envelope_key,
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=CONTEXT),
    )
    encrypted = AESGCM(envelope_key).encrypt(nonce, plain, CONTEXT)
    Path(target).write_bytes(wrapped + nonce + encrypted)
    print("Published TB-005 media and timing verified and sealed for owner editing.")

if __name__ == "__main__":
    main()
