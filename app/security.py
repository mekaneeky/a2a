from __future__ import annotations

import base64
import binascii
import hashlib
from nacl.exceptions import BadSignatureError
from nacl.public import PrivateKey, PublicKey, SealedBox
from nacl.signing import SigningKey, VerifyKey


def generate_signing_keypair() -> tuple[str, str]:
    signing_key = SigningKey.generate()
    verify_key = signing_key.verify_key
    return (
        base64.b64encode(bytes(signing_key)).decode("ascii"),
        base64.b64encode(bytes(verify_key)).decode("ascii"),
    )


def canonical_request_message(
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{body_hash}".encode("utf-8")


def sign_message(private_key_b64: str, message: bytes) -> str:
    signing_key = SigningKey(base64.b64decode(private_key_b64.encode("ascii")))
    signed = signing_key.sign(message)
    return base64.b64encode(signed.signature).decode("ascii")


def verify_signature(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    try:
        verify_key = VerifyKey(base64.b64decode(public_key_b64.encode("ascii")))
        signature = base64.b64decode(signature_b64.encode("ascii"))
        verify_key.verify(message, signature)
        return True
    except (BadSignatureError, ValueError, binascii.Error):
        return False


def is_fresh_timestamp(now_ts: int, ts: int, ttl_seconds: int = 300) -> bool:
    return abs(now_ts - ts) <= ttl_seconds


def generate_encryption_keypair() -> tuple[str, str]:
    private_key = PrivateKey.generate()
    public_key = private_key.public_key
    return (
        base64.b64encode(bytes(private_key)).decode("ascii"),
        base64.b64encode(bytes(public_key)).decode("ascii"),
    )


def encrypt_for_recipient(public_key_b64: str, plaintext: bytes) -> bytes:
    public_key = PublicKey(base64.b64decode(public_key_b64.encode("ascii")))
    return SealedBox(public_key).encrypt(plaintext)


def decrypt_with_private_key(private_key_b64: str, ciphertext: bytes) -> bytes:
    private_key = PrivateKey(base64.b64decode(private_key_b64.encode("ascii")))
    return SealedBox(private_key).decrypt(ciphertext)
