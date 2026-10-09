"""
passdb.py — слой работы с базой данных SQLite3 для CLI-менеджера паролей.

Содержит:
  * инициализацию БД (проверка наличия таблиц и создание при необходимости);
  * хранение мастер-пароля в виде хеша SHA-256 (плюс соль);
  * CRUD-операции над записями «название / логин / зашифрованный пароль».
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Путь к файлу базы данных (рядом с исполняемым файлом проекта)
DB_PATH = Path(__file__).resolve().parent / "passwords.db"

# SQL: таблица настроек (мастер-пароль) и таблица записей
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    id            INTEGER PRIMARY KEY CHECK (id = 1),  -- всегда одна строка
    master_hash   TEXT NOT NULL,                       -- SHA-256(соль + пароль)
    master_salt   TEXT NOT NULL,                       -- гекс-соль
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,              -- название/откуда (Google)
    login           TEXT NOT NULL,                     -- логин
    encrypted_pass  TEXT NOT NULL,                     -- Fernet-токен пароля
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class PasswordDB:
    """Обёртка над SQLite3 для хранения мастер-пароля и записей."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    # ------------------------------------------------------------------ #
    # Инициализация
    # ------------------------------------------------------------------ #
    def _init_schema(self) -> None:
        """При старте проверяет наличие таблиц и создаёт их при необходимости."""
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "PasswordDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Мастер-пароль
    # ------------------------------------------------------------------ #
    def has_master_password(self) -> bool:
        """Есть ли в БД сохранённый мастер-пароль (первый ли это запуск)."""
        row = self.conn.execute("SELECT COUNT(*) AS c FROM settings").fetchone()
        return row["c"] > 0

    def set_master_password(self, password_hash: str, salt_hex: str) -> None:
        """Сохраняет хеш SHA-256 и соль мастер-пароля (первый запуск)."""
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (id, master_hash, master_salt, created_at)"
            " VALUES (1, ?, ?, ?)",
            (password_hash, salt_hex, _now()),
        )
        self.conn.commit()

    def get_master_password_data(self) -> Optional[tuple[str, str]]:
        """Возвращает (hash, salt) мастер-пароля либо None."""
        row = self.conn.execute(
            "SELECT master_hash, master_salt FROM settings WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return row["master_hash"], row["master_salt"]

    # ------------------------------------------------------------------ #
    # Записи (CRUD)
    # ------------------------------------------------------------------ #
    def add_entry(self, name: str, login: str, encrypted_pass: str) -> None:
        """Добавляет новую запись; если название занято — ValueError."""
        now = _now()
        try:
            self.conn.execute(
                "INSERT INTO entries (name, login, encrypted_pass, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (name, login, encrypted_pass, now, now),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Запись «{name}» уже существует")
        self.conn.commit()

    def update_entry(self, name: str, login: str, encrypted_pass: str) -> bool:
        """Обновляет существующую запись. Возвращает True, если запись найдена."""
        cur = self.conn.execute(
            "UPDATE entries SET login = ?, encrypted_pass = ?, updated_at = ?"
            " WHERE name = ?",
            (login, encrypted_pass, _now(), name),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_entry(self, name: str) -> Optional[sqlite3.Row]:
        """Возвращает запись по названию (точное или нечувствительное к регистру)."""
        row = self.conn.execute(
            "SELECT * FROM entries WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT * FROM entries WHERE name LIKE ? COLLATE NOCASE", (name,)
            ).fetchone()
        return row

    def list_entries(self) -> List[sqlite3.Row]:
        """Список всех записей (без паролей) в алфавитном порядке."""
        return self.conn.execute(
            "SELECT name, login, updated_at FROM entries ORDER BY name COLLATE NOCASE"
        ).fetchall()

    def delete_entry(self, name: str) -> bool:
        """Удаляет запись по названию. Возвращает True, если запись была удалена."""
        cur = self.conn.execute("DELETE FROM entries WHERE name = ?", (name,))
        if cur.rowcount == 0:
            cur = self.conn.execute(
                "DELETE FROM entries WHERE name LIKE ? COLLATE NOCASE", (name,)
            )
        self.conn.commit()
        return cur.rowcount > 0

    def count_entries(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM entries").fetchone()
        return row["c"]


__all__ = ["DB_PATH", "PasswordDB"]
