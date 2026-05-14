import base64
import hashlib
import json

from cryptography.fernet import Fernet


class EncryptionService:
    def __init__(self, key: str) -> None:
        derived = hashlib.sha256(key.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(derived))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()

    def encrypt_json(self, data: dict) -> str:
        return self.encrypt(json.dumps(data))

    def decrypt_json(self, ciphertext: str) -> dict:
        return json.loads(self.decrypt(ciphertext))
