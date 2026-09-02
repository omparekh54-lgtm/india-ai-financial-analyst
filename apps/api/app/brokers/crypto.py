from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class BrokerTokenCipherError(RuntimeError):
    pass


class BrokerTokenCipher:
    """Encrypts broker credentials before persistence using an environment-owned Fernet key."""

    def __init__(self, key: str) -> None:
        candidate = key.strip().encode()
        if not candidate:
            raise BrokerTokenCipherError("Broker token encryption key is not configured")
        try:
            self._fernet = Fernet(candidate)
        except (TypeError, ValueError) as exc:
            raise BrokerTokenCipherError("Broker token encryption key is invalid") from exc

    def encrypt(self, plaintext: str) -> str:
        value = plaintext.strip()
        if not value:
            raise BrokerTokenCipherError("Cannot encrypt an empty broker token")
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        value = ciphertext.strip()
        if not value:
            raise BrokerTokenCipherError("Stored broker token is empty")
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except (InvalidToken, ValueError) as exc:
            raise BrokerTokenCipherError("Stored broker token could not be decrypted") from exc
