#!/usr/bin/env python3
"""
passman.py — CLI-приложение для хранения паролей с шифрованием (SQLite3).

Запуск:  python passman.py <команда>

Команды:
  add    — добавить новую запись (название, логин, пароль -> шифруется Fernet)
  get    — получить пароль по названию (после ввода мастер-пароля)
  list   — показать список всех сохранённых записей (названия и логины)
  delete — удалить запись по названию
  new    — создать новый надёжный пароль (генератор secrets) и вывести его

Логика входа:
  * при ПЕРВОМ запуске программа запрашивает мастер-пароль (ввод дважды),
    сохраняет его хеш SHA-256 (+соль) в таблице settings базы данных;
  * при последующих запусках требует этот пароль для входа;
  * ключ Fernet хранится в файле .key и генерируется при первом запуске.

Архитектура:
  passdb.py       — работа с SQLite3 (таблицы, CRUD, мастер-пароль)
  crypto_utils.py — шифрование Fernet, хеширование SHA-256, файл .key
  passman.py      — основной CLI-обработчик (argparse + интерактив)
"""

import argparse
import secrets
import string
import sys

from cryptography.fernet import InvalidToken

from crypto_utils import (
    decrypt_password,
    encrypt_password,
    get_fernet,
    hash_master_password,
    verify_master_password,
)
from passdb import PasswordDB

# Символы для генератора паролей (команда «new»)
PWD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*()-_=+[]{}"


# ---------------------------------------------------------------------- #
# Вспомогательные функции ввода
# ---------------------------------------------------------------------- #
def _read_line(text: str, hidden: bool) -> str:
    """Низкоуровневое чтение строки: скрыто (termios/tty) либо через input().

    Использует tty-ввод только если stdin действительно терминал — иначе
    (pipes, перенаправление) безопасно читает из stdin.
    """
    import sys

    if hidden and sys.stdin.isatty():
        # Скрытый ввод только в реальном TTY: termios + tty (POSIX)
        try:
            import msvcrt  # Windows

            print(text, end="", flush=True)
            chars = []
            while True:
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    print()
                    break
                if ch == "\0" or ch == "\xe0":  # служебные клавиши (стрелки и т.п.)
                    msvcrt.getwch()
                    continue
                if ch == "\b":
                    if chars:
                        chars.pop()
                    continue
                chars.append(ch)
            return "".join(chars)
        except ImportError:
            import termios
            import tty

            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                sys.stdout.write(text)
                sys.stdout.flush()
                chars = []
                while True:
                    ch = sys.stdin.read(1)
                    if ch in ("\r", "\n"):
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        break
                    if ch == "\x7f":  # Backspace
                        if chars:
                            chars.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                        continue
                    if ch == "\x03":  # Ctrl+C
                        raise KeyboardInterrupt
                    chars.append(ch)
                return "".join(chars)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)

    # Не-TTY (pipes/файл) или обычный видимый ввод — читаем из stdin
    if hidden:
        sys.stdout.write(text)
        sys.stdout.flush()
    return sys.stdin.readline().rstrip("\r\n")


def prompt_hidden(text: str) -> str:
    """Запрашивает секретную строку без эха; корректно работает и в pipe."""
    try:
        line = _read_line(text, hidden=True)
    except (EOFError, KeyboardInterrupt):
        print("\nВход прерван.", file=sys.stderr)
        sys.exit(1)
    if line == "" and not sys.stdin.isatty():
        # EOF в pipe: считаем, что ввод закончился
        print("Неожиданный конец ввода (stdin закрыт).", file=sys.stderr)
        sys.exit(1)
    return line


def prompt_text(text: str) -> str:
    """Запрашивает обычную строку (название, логин — не секрет)."""
    try:
        return _read_line(text, hidden=False).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nДействие прервано.", file=sys.stderr)
        sys.exit(1)


def generate_password(length: int = 16) -> str:
    """Криптостойкий генератор паролей (secrets.choice)."""
    return "".join(secrets.choice(PWD_ALPHABET) for _ in range(length))


# ---------------------------------------------------------------------- #
# Авторизация (мастер-пароль)
# ---------------------------------------------------------------------- #
def ensure_login(db: PasswordDB) -> None:
    """Проверяет мастер-пароль. При первом запуске создаёт его.

    Бросает SystemExit(-1) при неверном пароле.
    """
    stored = db.get_master_password_data()

    if stored is None:
        # -------- Первый запуск: создание мастер-пароля --------
        print("Первый запуск — создайте мастер-пароль для входа в менеджер.")
        while True:
            p1 = prompt_hidden("Придумайте мастер-пароль: ")
            if len(p1) < 4:
                print("Пароль слишком короткий (минимум 4 символа), повторите.")
                continue
            p2 = prompt_hidden("Повторите мастер-пароль: ")
            if p1 != p2:
                print("Пароли не совпадают, начните заново.")
                continue
            break
        password_hash, salt_hex = hash_master_password(p1)
        db.set_master_password(password_hash, salt_hex)
        print("Мастер-пароль сохранён в базе в виде хеша SHA-256.")
        return

    # -------- Обычный запуск: проверка ввода --------
    stored_hash, stored_salt = stored
    attempt = prompt_hidden("Введите мастер-пароль для входа: ")
    if not verify_master_password(attempt, stored_hash, stored_salt):
        print("Неверный мастер-пароль. Доступ запрещён.", file=sys.stderr)
        sys.exit(1)
    print("Вход выполнен.")


# ---------------------------------------------------------------------- #
# Команды CLI
# ---------------------------------------------------------------------- #
def cmd_add(db: PasswordDB, fernet, args: argparse.Namespace) -> int:
    """add — добавить новую запись (пароль шифруется перед сохранением)."""
    name = args.name or prompt_text("Название/откуда (например, Google): ")
    login = args.login or prompt_text("Логин: ")
    if args.password is not None:
        password = args.password
    else:
        password = prompt_hidden("Пароль (ввод скрыт): ")
    if not name or not login or not password:
        print("Все поля обязательны — запись не добавлена.", file=sys.stderr)
        return 1

    encrypted = encrypt_password(fernet, password)
    try:
        if args.update and db.get_entry(name) is not None:
            db.update_entry(name, login, encrypted)
            print(f"Запись «{name}» обновлена.")
        else:
            db.add_entry(name, login, encrypted)
            print(f"Запись «{name}» добавлена (пароль зашифрован Fernet).")
    except ValueError as exc:
        print(f"{exc}. Используйте флаг --update для перезаписи.", file=sys.stderr)
        return 1
    return 0


def cmd_get(db: PasswordDB, fernet, args: argparse.Namespace) -> int:
    """get — получить расшифрованный пароль по названию."""
    name = args.name or prompt_text("Название записи: ")
    row = db.get_entry(name)
    if row is None:
        print(f"Запись «{name}» не найдена.", file=sys.stderr)
        return 1
    try:
        password = decrypt_password(fernet, row["encrypted_pass"])
    except InvalidToken:
        print("Не удалось расшифровать: файл .key не соответствует базе.",
              file=sys.stderr)
        return 1
    print(f"Название: {row['name']}")
    print(f"Логин:    {row['login']}")
    print(f"Пароль:   {password}")
    return 0


def cmd_list(db: PasswordDB, fernet, args: argparse.Namespace) -> int:
    """list — показать названия и логины всех записей (без паролей)."""
    rows = db.list_entries()
    if not rows:
        print("База пуста — добавьте первую командой: python passman.py add")
        return 0
    print(f"{'НАЗВАНИЕ':<28}{'ЛОГИН':<28}{'ОБНОВЛЕНО'}")
    print("-" * 76)
    for r in rows:
        print(f"{r['name']:<28}{r['login']:<28}{r['updated_at']}")
    print(f"\nВсего записей: {len(rows)}")
    return 0


def cmd_delete(db: PasswordDB, fernet, args: argparse.Namespace) -> int:
    """delete — удалить запись по названию."""
    name = args.name or prompt_text("Название записи для удаления: ")
    row = db.get_entry(name)
    if row is None:
        print(f"Запись «{name}» не найдена.", file=sys.stderr)
        return 1
    if not args.yes:
        answer = prompt_text(f"Удалить «{row['name']}»? [y/N]: ").lower()
        if answer not in ("y", "yes", "д", "да"):
            print("Отменено.")
            return 0
    db.delete_entry(row["name"])
    print(f"Запись «{row['name']}» удалена.")
    return 0


def cmd_new(db: PasswordDB, fernet, args: argparse.Namespace) -> int:
    """new — сгенерировать новый надёжный пароль (и опционально сохранить)."""
    length = max(8, args.length)
    password = generate_password(length)
    print(f"Сгенерирован новый пароль ({length} символов):\n\n    {password}\n")

    if args.save_to is None:
        print("Чтобы сохранить в базу: python passman.py add "
              "(или используйте флаг --save-to НАЗВАНИЕ)")
        return 0

    name = args.save_to
    login = args.login or prompt_text("Логин для записи: ")
    encrypted = encrypt_password(fernet, password)
    if db.get_entry(name) is not None:
        db.update_entry(name, login, encrypted)
        print(f"Запись «{name}» обновлена новым паролем.")
    else:
        db.add_entry(name, login, encrypted)
        print(f"Создана запись «{name}» с новым паролем.")
    return 0


# ---------------------------------------------------------------------- #
# Основной CLI-обработчик
# ---------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="passman",
        description="CLI-менеджер паролей: SQLite3 + шифрование Fernet + "
                    "мастер-пароль (SHA-256).",
        epilog="Примеры: %(prog)s add | %(prog)s get Google | "
               "%(prog)s list | %(prog)s delete Google | %(prog)s new --length 20",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="добавить новую запись")
    p_add.add_argument("--name", help="название/откуда (например, Google)")
    p_add.add_argument("--login", help="логин")
    p_add.add_argument("--password", help="пароль (если не указан — запросит скрыто)")
    p_add.add_argument("--update", action="store_true",
                       help="перезаписать запись, если название уже существует")

    p_get = sub.add_parser("get", help="получить пароль по названию")
    p_get.add_argument("name", nargs="?", help="название записи")

    sub.add_parser("list", help="показать список записей (названия и логины)")

    p_del = sub.add_parser("delete", help="удалить запись по названию")
    p_del.add_argument("name", nargs="?", help="название записи")
    p_del.add_argument("-y", "--yes", action="store_true",
                       help="не спрашивать подтверждение")

    p_new = sub.add_parser("new", help="создать новый надёжный пароль")
    p_new.add_argument("--length", type=int, default=16,
                       help="длина пароля (по умолчанию 16, минимум 8)")
    p_new.add_argument("--save-to", metavar="НАЗВАНИЕ",
                       help="сразу сохранить пароль в запись с таким названием")
    p_new.add_argument("--login", help="логин (вместе с --save-to)")

    return parser


COMMANDS = {
    "add": cmd_add,
    "get": cmd_get,
    "list": cmd_list,
    "delete": cmd_delete,
    "new": cmd_new,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Инициализация БД: таблицы проверяются/создаются при старте.
    # Ключ .key: если файла нет — генерируется при первом запуске.
    with PasswordDB() as db:
        ensure_login(db)                 # вход по мастер-паролю
        fernet = get_fernet()            # ключ шифрования из .key
        handler = COMMANDS[args.command]
        return handler(db, fernet, args)


if __name__ == "__main__":
    sys.exit(main())
