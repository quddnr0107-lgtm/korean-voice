"""One-lesson, owner-approved delivery to a dedicated local recipient.

Reuse the reviewed MS-034 exact-asset and AEAD checks. This is not a
production release or a general export endpoint. No plaintext leaves
the offline namespace, and the existing owner recipient is unchanged.
"""
import base64
import hashlib
import os
from pathlib import Path
import socket
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ms034_owner_export import recover, seal_for_owner
from namespace_guard import validate_snapshot

PUBLIC_DER_B64 = 'MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAyUyrV3VZBLDA7/MmSznfzo3qGo0AbNEz1DrDG5WplXFYFS4v5sQvX5vH8SEEvNb/us/Wv2AJbazfH1tUmgC7VNVSfvh1xMYluMy3sqSxhfffGTWcDptV1O2ws+xXTe6c61KfBVSIxpaP3RkQVDB3DVeayGf2qsoxqpJNIu58Y3pLOWbcvy7qr3d0s67YwLYZvDTBdQyE2iHIOpXfRs+XQk10YIFhhoXYfEUI9rfy5bHOM+urG+sXccl0xRQ/Wv5ddVrRb8bBz+tUCfbQ1596b1Aqexw/LEfRYlp4voa4jLtrjDMsh5MLK0ST2SVSWNptVyortP2+7hkyOqj5R2hbJOQrt4HZPxMX0sD6d+ybgQgGyK32QjpuI2k9NF5ekg3ck6xqnWLJPR/0906OKHi0NHRd+ATP1dogScgLuUpXuSlSAZMYu1JuWiO1q1xWRGGlj1bxvvUSMvAZaK7wGQIy1HWyq0FsYnY51b0NNKDzNqjQfqxYn7Cvq8gKTuQapu7zAgMBAAE='
PUBLIC_DER_SHA256 = '5cca87194a50ea4f6f162bfac96fc0c805a8ae36322fbee0dee274fe2d41f064'


def recipient():
    raw = base64.b64decode(PUBLIC_DER_B64, validate=True)
    if hashlib.sha256(raw).hexdigest() != PUBLIC_DER_SHA256:
        raise ValueError('dedicated recipient fingerprint mismatch')
    public = serialization.load_der_public_key(raw)
    if not isinstance(public, rsa.RSAPublicKey) or public.key_size != 3072 or public.public_numbers().e != 65537:
        raise ValueError('dedicated recipient type')
    return public


def main():
    directory, target, uid, host_namespace = sys.argv[1:]
    validate_snapshot(os.getuid(), int(uid), socket.if_nameindex(),
                      Path('/proc/net/route').read_text(), os.readlink('/proc/self/ns/net'),
                      host_namespace, Path('/proc/net/ipv6_route').read_text())
    public = recipient()
    media_key = base64.b64decode(os.environ.pop('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64'), validate=True)
    ciphertext = seal_for_owner(public, recover(directory, media_key))
    os.umask(0o077)
    with Path(target).open('xb') as handle:
        handle.write(ciphertext)
    print('MS-034: original audio authenticated and sealed to the dedicated recipient.')


if __name__ == '__main__':
    main()
