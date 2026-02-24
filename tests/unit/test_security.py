import pytest

from app import security


def test_sign_and_verify_roundtrip() -> None:
    private_key_b64, public_key_b64 = security.generate_signing_keypair()
    body = b'{"hello":"world"}'
    msg = security.canonical_request_message(
        method="POST",
        path="/contracts/123/accept",
        timestamp="1700000000",
        nonce="nonce-1",
        body=body,
    )

    signature = security.sign_message(private_key_b64, msg)

    assert security.verify_signature(public_key_b64, msg, signature)


def test_signature_verification_fails_for_tampered_payload() -> None:
    private_key_b64, public_key_b64 = security.generate_signing_keypair()
    msg = security.canonical_request_message(
        method="POST",
        path="/contracts/123/accept",
        timestamp="1700000000",
        nonce="nonce-1",
        body=b'{"hello":"world"}',
    )
    tampered = security.canonical_request_message(
        method="POST",
        path="/contracts/123/accept",
        timestamp="1700000000",
        nonce="nonce-1",
        body=b'{"hello":"evil"}',
    )

    signature = security.sign_message(private_key_b64, msg)

    assert not security.verify_signature(public_key_b64, tampered, signature)


def test_verify_signature_rejects_invalid_signature_format() -> None:
    _, public_key_b64 = security.generate_signing_keypair()
    msg = security.canonical_request_message(
        method="GET",
        path="/health",
        timestamp="1700000000",
        nonce="nonce-1",
        body=b"",
    )
    assert not security.verify_signature(public_key_b64, msg, "!!!not-base64!!!")


def test_encryption_roundtrip() -> None:
    private_key_b64, public_key_b64 = security.generate_encryption_keypair()
    plaintext = b"secret payload"

    ciphertext = security.encrypt_for_recipient(public_key_b64, plaintext)
    decrypted = security.decrypt_with_private_key(private_key_b64, ciphertext)

    assert decrypted == plaintext


def test_timestamp_freshness_window() -> None:
    assert security.is_fresh_timestamp(now_ts=1_700_000_000, ts=1_700_000_000, ttl_seconds=300)
    assert not security.is_fresh_timestamp(now_ts=1_700_000_000, ts=1_700_000_301, ttl_seconds=300)
    assert not security.is_fresh_timestamp(now_ts=1_700_000_000, ts=1_699_999_699, ttl_seconds=300)


def test_sign_message_rejects_invalid_private_key() -> None:
    with pytest.raises(ValueError):
        security.sign_message("bad-key", b"message")
