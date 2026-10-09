"""
db.py — слой работы с базой данных SQLite3 для приложения-напоминалки.

Содержит:
  * инициализацию БД (создание таблиц при старте);
  * CRUD-операции над напоминаниями;
  * автоматический перевод просроченных напоминаний в статус "Просрочено".

Статусы напоминаний: "Ожидает", "Готово", "Просрочено", "Отменено".
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Путь к файлу базы данных (рядом с исполняемым файлом проекта)
DB_PATH = Path(__file__).resolve().parent / "reminders.db"

# Возможные статусы напоминания
STATUS_PENDING = "Ожидает"
STATUS_DONE = "Готово"
STATUS_OVERDUE = "Просрочено"
STATUS_CANCELED = "Отменено"
ALL_STATUSES = [STATUS_PENDING, STATUS_DONE, STATUS_OVERDUE, STATUS_CANCELED]

# Формат хранения даты/времени в БД
DT_FORMAT = "%Y-%m-%d %H:%M:%S"


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Преобразует строку результата в словарь."""
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "due_dt": datetime.strptime(row["due_dt"], DT_FORMAT),
        "status": row["status"],
        "created_at": datetime.strptime(row["created_at"], DT_FORMAT),
        "notified": bool(row["notified"]),
    }


class ReminderDB:
    """Класс-обёртка над SQLite3 для хранения напоминаний."""

    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = str(db_path)
        self._init_db()

    # ------------------------------------------------------------------ #
    #                       Инициализация схемы                            #
    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Проверяет и создаёт таблицы, если их нет (вызывается при старте)."""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    title       TEXT    NOT NULL,
                    description TEXT    NOT NULL DEFAULT '',
                    due_dt      TEXT    NOT NULL,               -- 'YYYY-MM-DD HH:MM:SS'
                    status      TEXT    NOT NULL DEFAULT 'Ожидает',
                    created_at  TEXT    NOT NULL,
                    notified    INTEGER NOT NULL DEFAULT 0      -- уведомление отправлено?
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_dt)"
            )

    # ------------------------------------------------------------------ #
    #                          Основные операции                           #
    # ------------------------------------------------------------------ #
    def add_reminder(
        self,
        title: str,
        description: str,
        due_dt: datetime,
    ) -> int:
        """Добавляет новое напоминание, возвращает его id."""
        now = datetime.now().strftime(DT_FORMAT)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO reminders (title, description, due_dt, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    title.strip(),
                    description.strip(),
                    due_dt.strftime(DT_FORMAT),
                    STATUS_PENDING,
                    now,
                ),
            )
            return cur.lastrowid

    def delete_reminder(self, reminder_id: int) -> None:
        """Удаляет напоминание по id."""
        with self._connect() as conn:
            conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))

    def set_status(self, reminder_id: int, status: str) -> None:
        """Меняет статус напоминания (например, 'Готово' или 'Отменено')."""
        if status not in ALL_STATUSES:
            raise ValueError(f"Недопустимый статус: {status}")
        with self._connect() as conn:
            conn.execute(
                "UPDATE reminders SET status = ? WHERE id = ?",
                (status, reminder_id),
            )

    def mark_notified(self, reminder_id: int) -> None:
        """Помечает напоминание как уже уведомлённое (чтобы не спамить)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE reminders SET notified = 1 WHERE id = ?",
                (reminder_id,),
            )

    def get_reminder(self, reminder_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
            return _row_to_dict(row) if row else None

    def list_reminders(self, status: Optional[str] = None) -> List[dict]:
        """
        Возвращает список напоминаний.
        status=None — все; иначе фильтр по статусу.
        Сортировка: сначала ближайшие активные, затем остальные по дате.
        """
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM reminders WHERE status = ? ORDER BY due_dt ASC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM reminders
                    ORDER BY CASE status
                                 WHEN 'Ожидает'   THEN 0
                                 WHEN 'Просрочено' THEN 1
                                 ELSE 2 END,
                             due_dt ASC
                    """
                ).fetchall()
            return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    #                    Автопроверка просроченных / срабатываний           #
    # ------------------------------------------------------------------ #
    def get_due_reminders(self, now: Optional[datetime] = None) -> List[dict]:
        """Возвращает напоминания со статусом 'Ожидает', у которых наступило время."""
        now = now or datetime.now()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM reminders
                WHERE status = ? AND due_dt <= ? AND notified = 0
                ORDER BY due_dt ASC
                """,
                (STATUS_PENDING, now.strftime(DT_FORMAT)),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]

    def auto_expire_overdue(self, now: Optional[datetime] = None) -> int:
        """
        Автоматически переводит просроченные напоминания в статус 'Просрочено'.
        Просроченным считается 'Ожидает', у которого время вышло более N минут назад
        (и при этом оно уже было проуведомлено либо время сильно прошло).
        Возвращает количество обновлённых записей.
        """
        now = now or datetime.now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE reminders SET status = ?
                WHERE status = ? AND due_dt < ? AND notified = 1
                """,
                (STATUS_OVERDUE, STATUS_PENDING, now.strftime(DT_FORMAT)),
            )
            return cur.rowcount
