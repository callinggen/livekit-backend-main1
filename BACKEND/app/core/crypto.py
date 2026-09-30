import base64
import hashlib
import os
from cryptography.fernet import Fernet
from app.core.security import SECRET_KEY, FALLBACK_SECRET

# Derive standard 32-byte URL-safe base64 key from SECRET_KEY
def _get_fernet(secret: str = SECRET_KEY) -> Fernet:
    key_bytes = hashlib.sha256(secret.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)

def encrypt_secret(plain_text: str) -> str:
    """Encrypts a plaintext secret into a base64 Fernet string using active SECRET_KEY."""
    if not plain_text:
        return ""
    f = _get_fernet(SECRET_KEY)
    return f.encrypt(plain_text.encode("utf-8")).decode("utf-8")

def is_fernet_token(val: str) -> bool:
    """Checks whether a string has a valid Fernet token structure.
    Fernet token format: Version (1B, 0x80) + Timestamp (8B) + IV (16B) + Ciphertext (min 16B) + HMAC (32B) = min 73B
    """
    if not val or not isinstance(val, str) or not val.startswith("gAAAAA"):
        return False
    try:
        decoded = base64.urlsafe_b64decode(val.encode("utf-8"))
        return len(decoded) >= 73 and decoded[0] == 0x80
    except Exception:
        return False

def decrypt_secret(cipher_text: str) -> str:
    """Decrypts a base64 Fernet string back into plaintext.
    Supports current SECRET_KEY, FALLBACK_SECRET, and legacy unencrypted passwords.
    """
    if not cipher_text:
        return ""

    # 1. Try active SECRET_KEY
    try:
        f = _get_fernet(SECRET_KEY)
        return f.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
    except Exception:
        pass

    # 2. Try FALLBACK_SECRET if different from active SECRET_KEY
    if SECRET_KEY != FALLBACK_SECRET:
        try:
            f_fallback = _get_fernet(FALLBACK_SECRET)
            return f_fallback.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
        except Exception:
            pass

    # 3. If cipher_text has a valid Fernet token structure, but decryption failed,
    # do NOT return the raw ciphertext as password (which causes SMTP auth failure).
    if is_fernet_token(cipher_text):
        import logging
        logging.getLogger("crypto").error("Failed to decrypt Fernet secret with all known keys.")
        return ""

    # 4. Fallback if unencrypted string was saved or legacy plaintext (including strings starting with 'gAAAAA')
    return cipher_text
