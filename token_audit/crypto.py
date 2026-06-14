import hashlib
import hmac
import os
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class EncryptedText:
    nonce: bytes
    ciphertext: bytes


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def preview_text(text: str, limit: int = 500) -> str:
    compact = " ".join(text.replace("\r", "\n").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def encrypt_text(text: str, key: bytes, associated_data: bytes = b"") -> EncryptedText:
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, text.encode("utf-8"), associated_data)
    return EncryptedText(nonce=nonce, ciphertext=ciphertext)


def decrypt_text(encrypted: EncryptedText, key: bytes, associated_data: bytes = b"") -> str:
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(encrypted.nonce, encrypted.ciphertext, associated_data)
    return plaintext.decode("utf-8")


def sign_payload(secret: str, timestamp: str, raw_body: bytes) -> str:
    signed = timestamp.encode("utf-8") + b"." + raw_body
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str, signature: str, raw_body: bytes, tolerance_seconds: int) -> tuple[bool, str]:
    if not timestamp or not signature:
        return False, "missing signature headers"
    try:
        ts = int(timestamp)
    except ValueError:
        return False, "invalid timestamp"
    if abs(int(time.time()) - ts) > tolerance_seconds:
        return False, "timestamp outside tolerance"
    expected = sign_payload(secret, timestamp, raw_body)
    if not hmac.compare_digest(expected, signature):
        return False, "invalid signature"
    return True, ""

