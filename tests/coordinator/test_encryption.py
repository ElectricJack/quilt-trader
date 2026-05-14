import json
import pytest
from coordinator.services.encryption import EncryptionService


def test_encrypt_decrypt_roundtrip():
    svc = EncryptionService("test-key-that-is-32-bytes-long!!")
    plaintext = "super-secret-api-key"
    encrypted = svc.encrypt(plaintext)
    assert encrypted != plaintext
    assert svc.decrypt(encrypted) == plaintext


def test_encrypt_produces_different_ciphertexts():
    svc = EncryptionService("test-key-that-is-32-bytes-long!!")
    enc1 = svc.encrypt("same-input")
    enc2 = svc.encrypt("same-input")
    assert enc1 != enc2


def test_decrypt_wrong_key_fails():
    svc1 = EncryptionService("key-one-that-is-32-bytes-long!!")
    svc2 = EncryptionService("key-two-that-is-32-bytes-long!!")
    encrypted = svc1.encrypt("secret")
    with pytest.raises(Exception):
        svc2.decrypt(encrypted)


def test_encrypt_json_credentials():
    svc = EncryptionService("test-key-that-is-32-bytes-long!!")
    creds = {"api_key": "pk_123", "api_secret": "sk_456"}
    encrypted = svc.encrypt_json(creds)
    decrypted = svc.decrypt_json(encrypted)
    assert decrypted == creds


def test_encrypt_empty_string():
    svc = EncryptionService("test-key-that-is-32-bytes-long!!")
    encrypted = svc.encrypt("")
    assert svc.decrypt(encrypted) == ""
