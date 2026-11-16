"""Recover the exact published DR-048 audio for this one owner edit."""
import base64
import io
import json
import os
from pathlib import Path
import socket
import sys
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import delivery
import lecture_crypto as crypto
import lecture_pack as pack
from namespace_guard import validate_snapshot

LESSON = "DR-048"
ASSETS = {
    "DR-048.audio.8cf26d233a4e89fa19c972f8feea0d66.bin": "930f9269e5cc2f5a06da9deed57187bbaa6e0990f898406cdaa3e5cc501980b2",
    "DR-048.content.addcca8c7c0e3389702a4b364fff99fe.bin": "94ed25382128dbfacaba24df2593be95b1a44a76103c345b14f4da659faf83c3",
    "DR-048.descriptor.json": "572cf250ccb54e1a4a32c015d39e6bf9af23b82dbcebb54392550737a5cf06c0"
}
RECIPIENT = "MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAzyA/NFFocmYzT3ug+vW3/V9qyr9z0C/upfK/lpR3eCjLMyMUpbTOe0QM6voFEd+iYvLB2+wAUhMcHnCZbu6Kb0EH2Q1EXLMLtOXkiKu31i+V/2Xk8ndvwufrRdfbZFjoLkpWEQ3RLBVgoObj3sjTJEa3w2VRUZU8TT2jmrS0D2lhLe7Rt+jROWgoIPks00qNRx2/qPi2oq2anvsaXkzfU8FN4DiDHAlwiTEp8PDXqIZI2LZrkmdDjZZ2gIKWLkJFfV/av8lQGPFjCkFL8Wt4RXeBzg+fw9R7J8ZvH6dhv4p7/gioL3+JGMTzTxELgm7798Dgwk0zfiqUiuV6Q6WrDXKB0XND5GiYSNIls2WL9Is9d0YliA0SVoc62COf+QbY+DyqyrQ7H3S2U8+QNJBPcbsPw9Ey3Ylh8cCjadLouFBm5FpGwY0DlDca9t7ksui3ZX+5HOZOJPugI/4o+i6w7ys1tolzTl23h5RGvRYI2wFnvY72aZU3BD+3SR1LX+w9AgMBAAE="
CONTEXT = b"owner-video-edit/dr-048/v1"

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
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("metadata.json", plain)
        archive.writestr("descriptor.json", json.dumps(descriptor))
        for part in audio["parts"]:
            index = part["part_index"]
            start = part["pack_offset"]
            end = start + part["cipher_bytes"]
            data = pack.open_part(
                key, part, blob[start:end], expected_id=LESSON,
                expected_part_index=index, expected_pack_id=audio["object_id"],
            )
            archive.writestr(f"part-{index:03d}.mp3", data)
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
    print("Published DR-048 media and timing verified and sealed for owner editing.")

if __name__ == "__main__":
    main()
