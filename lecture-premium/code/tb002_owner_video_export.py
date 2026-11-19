"""Recover the exact published TB-002 audio for this one owner edit."""
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

LESSON = "TB-002"
ASSETS = {
    "TB-002.audio.f1ae278b978512ab2ad0da1e656b57f8.bin": "84d749a291774cd1997c56910caa7a622dddf19d6406f94f670f4bc418dd4bf1",
    "TB-002.content.3afa0ea646aa21de8c8a350eda5a75c3.bin": "ffea944d242d1a591f65936949a1b9f44d8f69ea3a12111a3285f4c14e92033b",
    "TB-002.descriptor.json": "33f1531b82656e9a79ed92eedaf0de306e794d040f0c3f6d0a0bf466c094f6ab"
}
RECIPIENT = 'MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEApdLIGPB6EeH2Kj63lPvgrPY4/3pyqeK8m7SXELVuvlj70asFBFpkhHFtG+l4D0zJ2+eHopZ+t+rWLd0lcjJJYTPGOzsZEi96PFlxrf8E5SDMLYmShrF+Juxp588gjIVxg6/zj5tnUafCDyuXlP5uBJN8YJtN+2OAC4Ttpf59PAMZ8vDQC/KZAlIV0IWms/ctcA2OpvnHfVhPjsQBTT3Z9aPXzpWXvHdNX/EhrW1LOStL8D3mIV5X8HRtBbBPdCGtCfzrBTMRlf0JtiYQzOyA8J8h76PTO6ZolKr4jYveoPdxLjSIU7sY5TaGIy3eqzaQgGS0a3nf72xyoz2Xn0JujkMKC2oCJqTXQyO6LZV1DTgluJBGfQvj2FjzrNWhWMYDzS6LkOUhYvTc7fvoUI6rrg8WipqhC3z9FgPHkK+23vA4thCy038Alm+xKzrxWu3IlEw7m49cig2X359NHeY1e+ACiq9Rc05OhRtnD+r+E08qS5fzauYmtGWolJrjLbfTAgMBAAE='
CONTEXT = b"owner-video-edit/tb-002/v1"

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
    segments = record["segments"]
    if not segments or "".join(s["text"] for s in segments) != record["narration_text"]:
        raise ValueError("incomplete published timed source")
    bundle = io.BytesIO()
    # Preserve the published gapless MP3 parts without resynthesis or recompression.
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as archive:
        blocks = []
        for part in audio["parts"]:
            index = part["part_index"]
            start = part["pack_offset"]
            end = start + part["cipher_bytes"]
            data = pack.open_part(key, part, blob[start:end], expected_id=LESSON,
                                  expected_part_index=index, expected_pack_id=audio["object_id"])
            filename = f"parts/{index:04}.mp3"
            archive.writestr(filename, data)
            blocks.append(dict(file=filename, start=part["start_sample"], end=part["end_sample"]))
        metadata = {"id": LESSON, "sample_rate": rate, "parts": blocks, "segments": segments}
        archive.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False))
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
    print("Published TB-002 media and timing verified and sealed for owner editing.")

if __name__ == "__main__":
    main()
