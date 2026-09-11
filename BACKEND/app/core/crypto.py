import base64
import hashlib
import os
from cryptography.fernet import Fernet
from app.core.security import SECRET_KEY

# Derive standard 32-byte URL-safe base64 key from SECRET_KEY
def _get_fernet() -> Fernet:
    key_bytes = hashlib.sha256(SECRET_KEY.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)

def encrypt_secret(plain_text: str) -> str:
    """Encrypts a plaintext secret into a base64 Fernet string."""
    if not plain_text:
        return ""
    f = _get_fernet()
    return f.encrypt(plain_text.encode("utf-8")).decode("utf-8")

def decrypt_secret(cipher_text: str) -> str:
    """Decrypts a base64 Fernet string back into plaintext."""
    if not cipher_text:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
    except Exception:
        # Fallback if unencrypted string was saved or legacy
        return cipher_text
