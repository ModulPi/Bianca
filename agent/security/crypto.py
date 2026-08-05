from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from agent.config import Settings, get_settings


def _derive_fernet_key(raw: str) -> bytes:
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_fernet(*, settings: Settings | None = None) -> Fernet | None:
    cfg = settings or get_settings()
    key = cfg.encryption_key.strip()
    if not key:
        return None
    return Fernet(_derive_fernet_key(key))


def encrypt_secret(plaintext: str, *, settings: Settings | None = None) -> str:
    fernet = get_fernet(settings=settings)
    if fernet is None:
        raise ValueError("ENCRYPTION_KEY 未配置，无法加密存储密钥")
    return fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str, *, settings: Settings | None = None) -> str:
    fernet = get_fernet(settings=settings)
    if fernet is None:
        raise ValueError("ENCRYPTION_KEY 未配置，无法解密密钥")
    try:
        return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("密钥解密失败，请检查 ENCRYPTION_KEY") from exc
