import pytest
from app.core.crypto import encrypt_secret, decrypt_secret, is_fernet_token
from app.core.security import FALLBACK_SECRET

def test_encrypt_decrypt_roundtrip():
    secret = "my_super_secret_password_123"
    enc = encrypt_secret(secret)
    assert enc.startswith("gAAAAA")
    assert is_fernet_token(enc) is True
    dec = decrypt_secret(enc)
    assert dec == secret

def test_fallback_secret_decryption():
    secret = "test_password_with_fallback"
    # Encrypt explicitly with FALLBACK_SECRET
    from app.core.crypto import _get_fernet
    f_fallback = _get_fernet(FALLBACK_SECRET)
    enc = f_fallback.encrypt(secret.encode("utf-8")).decode("utf-8")
    
    # decrypt_secret should transparently decrypt tokens encrypted under FALLBACK_SECRET
    dec = decrypt_secret(enc)
    assert dec == secret

def test_legacy_plaintext_starting_with_gAAAAA():
    # A legacy plaintext password that happens to start with "gAAAAA" but is not a Fernet token
    legacy_pass = "gAAAAA_my_legacy_plain_password"
    assert is_fernet_token(legacy_pass) is False
    # decrypt_secret must return it unchanged as legacy plaintext
    dec = decrypt_secret(legacy_pass)
    assert dec == legacy_pass

def test_normal_plaintext():
    plain = "simple_plain_text"
    assert is_fernet_token(plain) is False
    assert decrypt_secret(plain) == plain

def test_invalid_fernet_token_returns_empty_string():
    # A valid Fernet token structure encrypted with an unknown secret key
    from cryptography.fernet import Fernet
    unknown_key = Fernet.generate_key()
    f_unknown = Fernet(unknown_key)
    token = f_unknown.encrypt(b"secret").decode("utf-8")
    
    assert is_fernet_token(token) is True
    # Because neither SECRET_KEY nor FALLBACK_SECRET can decrypt this token,
    # it must return empty string to prevent sending raw ciphertext to SMTP
    assert decrypt_secret(token) == ""
