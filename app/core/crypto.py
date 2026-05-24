from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretCipher:
    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("Encryption key is required")
        self._fernet = Fernet(key.encode("utf-8"))

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Cannot decrypt stored secret") from exc


def generate_encryption_key() -> str:
    return Fernet.generate_key().decode("utf-8")
