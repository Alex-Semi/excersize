"""
notifier.py — модуль уведомлений для Windows 11.

Стратегия (по убыванию приоритета):
  1. Системные уведомления Windows через winotify (современная замена win10toast,
     работает даже когда окно свёрнуто/скрыто).
  2. Если winotify не установлен — ctypes-вызов MessageBoxW с флагом
     MB_TOPMOST (всплывает поверх всех окон).
  3. На случай, если ни то ни другое недоступно (например, запуск не на Windows) —
     tkinter popup поверх всех окон.

Все варианты синхронно блокируют только свой поток; из GUI вызывается
через отдельный тред, чтобы не подвешивать интерфейс.
"""

import threading
from datetime import datetime
from typing import Optional

APP_NAME = "Напоминалка"


def _notify_winotify(title: str, message: str) -> bool:
    """Системное уведомление Windows 11 через winotify."""
    try:
        from winotify import Notification, audio
    except ImportError:
        return False
    toast = Notification(
        app_id=APP_NAME,
        title=title,
        msg=message,
        duration="long",
    )
    toast.set_audio(audio.Reminder, loop=False)
    toast.show()
    return True


def _notify_messagebox(title: str, message: str) -> bool:
    """Popup поверх всех окон через нативный MessageBoxW (Windows)."""
    try:
        import ctypes
        MB_TOPMOST = 0x00040000   # окно поверх всех
        MB_ICONINFORMATION = 0x00000040
        MB_SETFOREGROUND = 0x00010000
        MB_OK = 0x0
        ctypes.windll.user32.MessageBoxW(
            None, message, f"{APP_NAME}: {title}",
            MB_TOPMOST | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_OK,
        )
        return True
    except Exception:
        return False


def _notify_tkinter_popup(title: str, message: str) -> bool:
    """Резервный popup на tkinter (работает и вне Windows)."""
    try:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title(f"{APP_NAME}: {title}")
        root.attributes("-topmost", True)      # поверх всех окон
        root.resizable(False, False)
        w, h = 420, 200
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 3
        root.geometry(f"{w}x{h}+{x}+{y}")

        frm = ttk.Frame(root, padding=15)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=title, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frm, text=message, justify="left", wraplength=380).pack(
            anchor="w", pady=(6, 12)
        )
        ttk.Button(frm, text="Понятно", command=root.destroy).pack(side="bottom")
        root.mainloop()
        return True
    except Exception:
        return False


def show_notification(title: str, message: str) -> None:
    """
    Показывает уведомление, выбирая доступный способ автоматически.
    Безопасно вызывать из любого потока.
    """
    for strategy in (_notify_winotify, _notify_messagebox, _notify_tkinter_popup):
        if strategy(title, message):
            return
    # Совсем крайний случай — вывод в консоль
    print(f"[НАПОМИНАНИЕ] {title}\n{message}")


def notify_async(title: str, message: str, on_done=None) -> None:
    """Показывает уведомление в отдельном потоке (не блокирует GUI)."""
    def _run():
        show_notification(title, message)
        if on_done:
            on_done()
    t = threading.Thread(target=_run, daemon=True)
    t.start()


def format_reminder_message(reminder: dict) -> tuple[str, str]:
    """Готовит (title, body) для уведомления по записи напоминания."""
    due: datetime = reminder["due_dt"]
    body_lines = [
        f"Время: {due.strftime('%d.%m.%Y %H:%M')}",
    ]
    desc = reminder.get("description") or ""
    if desc.strip():
        body_lines.append(f"Описание: {desc}")
    return reminder["title"], "\n".join(body_lines)
