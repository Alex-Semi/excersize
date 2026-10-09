"""
crypto_utils.py — слой шифрования для CLI-менеджера паролей.

Содержит:
  * генерацию и хранение ключа Fernet в файле .key (создаётся при первом запуске);
  * шифрование/дешифрование паролей (Fernet, симметричный AES-128-CBC + HMAC);
  * хеширование мастер-пароля (SHA-256 + соль) и его проверку.
"""

import hashlib
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

# Путь к файлу с ключом шифрования (рядом с исполняемым файлом проекта)
KEY_PATH = Path(__file__).resolve().parent / ".key"


def load_or_create_key(key_path: Path = KEY_PATH) -> bytes:
    """Загружает ключ Fernet из файла .key; если файла нет — генерирует и сохраняет."""
    if key_path.exists():
        key = key_path.read_bytes().strip()
        # Валидация: ключ должен быть корректным 32-байтовым base64-ключом Fernet
        Fernet(key)
        return key

    key = Fernet.generate_key()
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, 0o600)  # доступ только владельцу
    except OSError:
        pass  # на некоторых ОС права могли не примениться — не критично
    return key


def get_fernet(key_path: Path = KEY_PATH) -> Fernet:
    """Возвращает готовый объект Fernet для шифрования/дешифрования."""
    return Fernet(load_or_create_key(key_path))


def encrypt_password(fernet: Fernet, plaintext: str) -> str:
    """Шифрует пароль, возвращает base64-строку (токен Fernet)."""
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_password(fernet: Fernet, token: str) -> str:
    """Расшифровывает токен Fernet обратно в пароль.

    Бросает cryptography.fernet.InvalidToken, если данные повреждены
    или ключ не подходит.
    """
    return fernet.decrypt(token.encode("utf-8")).decode("utf-8")


def hash_master_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Хеширует мастер-пароль: SHA-256 с случайной солью.

    Возвращает пару (hex-хеш, hex-соль). Если соль не передана — генерируется.
    """
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.sha256(salt + password.encode("utf-8")).hexdigest()
    return digest, salt.hex()


def verify_master_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    """Проверяет введённый мастер-пароль против сохранённого хеша и соли."""
    digest, _ = hash_master_password(password, bytes.fromhex(stored_salt))
    return secrets.compare_digest(digest, stored_hash)


__all__ = [
    "KEY_PATH",
    "InvalidToken",
    "load_or_create_key",
    "get_fernet",
    "encrypt_password",
    "decrypt_password",
    "hash_master_password",
    "verify_master_password",
]
