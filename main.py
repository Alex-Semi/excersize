"""
main.py — GUI (tkinter) и логика приложения-напоминалки для Windows 11.

Запуск:  python main.py

Возможности:
  * добавление / удаление напоминаний;
  * отметка "Готово" / "Отменено";
  * фильтр по статусу, просмотр всех напоминаний;
  * фоновый таймер: срабатывание уведомлений и автоперевод в "Просрочено";
  * уведомления работают даже при свёрнутом окне (winotify / MessageBoxW popup).

Архитектура:
  db.py        — работа с SQLite3
  notifier.py  — способы показа уведомлений
  main.py      — tkinter GUI + планировщик проверок
"""

import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta

from db import ReminderDB, ALL_STATUSES, STATUS_DONE, STATUS_CANCELED
from notifier import notify_async, format_reminder_message

# Как часто (в миллисекундах) проверять наступление времени напоминаний
CHECK_INTERVAL_MS = 15_000  # 15 секунд


class AddReminderDialog(tk.Toplevel):
    """Диалог создания нового напоминания."""

    def __init__(self, master: tk.Misc):
        super().__init__(master)
        self.title("Новое напоминание")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.result: dict | None = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Заголовок:").grid(row=0, column=0, sticky="w")
        self.e_title = ttk.Entry(frm, width=45)
        self.e_title.grid(row=0, column=1, pady=3, sticky="we")

        ttk.Label(frm, text="Описание:").grid(row=1, column=0, sticky="nw")
        self.e_desc = tk.Text(frm, width=45, height=4)
        self.e_desc.grid(row=1, column=1, pady=3, sticky="we")

        ttk.Label(frm, text="Дата (ГГГГ-ММ-ДД):").grid(row=2, column=0, sticky="w")
        self.e_date = ttk.Entry(frm, width=45)
        self.e_date.grid(row=2, column=1, pady=3, sticky="we")
        self.e_date.insert(0, datetime.now().strftime("%Y-%m-%d"))

        ttk.Label(frm, text="Время (ЧЧ:ММ):").grid(row=3, column=0, sticky="w")
        self.e_time = ttk.Entry(frm, width=45)
        self.e_time.grid(row=3, column=1, pady=3, sticky="we")
        self.e_time.insert(0, (datetime.now().replace(second=0, microsecond=0)
                               + timedelta(minutes=5)).strftime("%H:%M"))

        ttk.Label(
            frm, text="Формат: 2025-10-07  и  14:30",
            foreground="gray"
        ).grid(row=4, column=1, sticky="w")

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(btns, text="Сохранить", command=self._on_ok).pack(side="left", padx=6)
        ttk.Button(btns, text="Отмена", command=self.destroy).pack(side="left")

        frm.columnconfigure(1, weight=1)
        self.e_title.focus_set()
        self.bind("<Return>", lambda e: self._on_ok())
        self.bind("<Escape>", lambda e: self.destroy())

    def _on_ok(self) -> None:
        title = self.e_title.get().strip()
        if not title:
            messagebox.showwarning("Проверка", "Укажите заголовок напоминания.",
                                   parent=self)
            return
        try:
            due = datetime.strptime(
                f"{self.e_date.get().strip()} {self.e_time.get().strip()}",
                "%Y-%m-%d %H:%M",
            )
        except ValueError:
            messagebox.showwarning(
                "Проверка",
                "Неверный формат даты или времени.\n"
                "Пример: дата 2025-10-07, время 14:30",
                parent=self,
            )
            return
        self.result = {
            "title": title,
            "description": self.e_desc.get("1.0", "end").strip(),
            "due_dt": due,
        }
        self.destroy()


class ReminderApp(tk.Tk):
    """Главное окно приложения."""

    def __init__(self):
        super().__init__()
        self.title("Напоминалка — Windows 11")
        self.geometry("900x520")
        self.minsize(700, 400)

        self.store = ReminderDB()          # при старте таблицы создаются, если их нет

        self._build_ui()
        self.refresh_list()

        # Фоновый цикл проверки (работает и при свёрнутом окне)
        self.after(CHECK_INTERVAL_MS, self._tick)

    # ------------------------------------------------------------------ #
    #                                UI                                    #
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        # --- верхняя панель инструментов ---
        toolbar = ttk.Frame(self, padding=(8, 6))
        toolbar.pack(fill="x")

        ttk.Button(toolbar, text="➕ Добавить",
                   command=self.add_reminder).pack(side="left", padx=3)
        ttk.Button(toolbar, text="✔ Готово",
                   command=lambda: self._set_status(STATUS_DONE)).pack(side="left", padx=3)
        ttk.Button(toolbar, text="✖ Отменено",
                   command=lambda: self._set_status(STATUS_CANCELED)).pack(side="left", padx=3)
        ttk.Button(toolbar, text="🗑 Удалить",
                   command=self.delete_selected).pack(side="left", padx=3)
        ttk.Button(toolbar, text="⟳ Обновить",
                   command=self.refresh_list).pack(side="left", padx=3)

        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Label(toolbar, text="Фильтр по статусу:").pack(side="left")
        self.var_filter = tk.StringVar(value="Все")
        filter_combo = ttk.Combobox(
            toolbar, textvariable=self.var_filter, state="readonly", width=12,
            values=["Все"] + ALL_STATUSES,
        )
        filter_combo.pack(side="left", padx=4)
        filter_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_list())

        # --- таблица напоминаний ---
        table_frame = ttk.Frame(self, padding=(8, 0, 8, 4))
        table_frame.pack(fill="both", expand=True)

        columns = ("id", "title", "desc", "due", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings",
                                 selectmode="browse")
        headings = {
            "id": ("№", 45),
            "title": ("Заголовок", 220),
            "desc": ("Описание", 280),
            "due": ("Дата и время", 150),
            "status": ("Статус", 110),
        }
        for col, (text, width) in headings.items():
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # цветовая подсветка статусов
        self.tree.tag_configure("pending", foreground="#1a1a1a")
        self.tree.tag_configure("done", foreground="#2e7d32")
        self.tree.tag_configure("overdue", foreground="#c62828")
        self.tree.tag_configure("canceled", foreground="#9e9e9e")

        self.tree.bind("<Double-1>", lambda e: self._set_status(STATUS_DONE))

        # --- статусная строка ---
        self.var_status = tk.StringVar()
        ttk.Label(self, textvariable=self.var_status, padding=(8, 4),
                  relief="groove").pack(fill="x")

    # ------------------------------------------------------------------ #
    #                          Работа со списком                           #
    # ------------------------------------------------------------------ #
    def refresh_list(self) -> None:
        filt = self.var_filter.get()
        items = self.store.list_reminders(None if filt == "Все" else filt)
        self.tree.delete(*self.tree.get_children())
        tag_map = {
            "Ожидает": "pending",
            "Готово": "done",
            "Просрочено": "overdue",
            "Отменено": "canceled",
        }
        for r in items:
            self.tree.insert(
                "", "end", iid=str(r["id"]),
                values=(
                    r["id"],
                    r["title"],
                    r["description"],
                    r["due_dt"].strftime("%d.%m.%Y %H:%M"),
                    r["status"],
                ),
                tags=(tag_map.get(r["status"], ""),),
            )
        counts = {s: len(self.store.list_reminders(s)) for s in ALL_STATUSES}
        self.var_status.set(
            f"Всего: {sum(counts.values())}   |   "
            + "   |   ".join(f"{s}: {c}" for s, c in counts.items())
        )

    def _selected_id(self) -> int | None:
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    # ------------------------------------------------------------------ #
    #                              Действия                                #
    # ------------------------------------------------------------------ #
    def add_reminder(self) -> None:
        dlg = AddReminderDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            self.store.add_reminder(**dlg.result)
            self.refresh_list()

    def delete_selected(self) -> None:
        rid = self._selected_id()
        if rid is None:
            messagebox.showinfo("Удаление", "Выберите напоминание в списке.")
            return
        r = self.store.get_reminder(rid)
        if messagebox.askyesno("Удаление",
                               f"Удалить напоминание «{r['title']}»?"):
            self.store.delete_reminder(rid)
            self.refresh_list()

    def _set_status(self, status: str) -> None:
        rid = self._selected_id()
        if rid is None:
            messagebox.showinfo("Статус", "Выберите напоминание в списке.")
            return
        self.store.set_status(rid, status)
        self.refresh_list()

    # ------------------------------------------------------------------ #
    #                     Фоновая проверка / уведомления                    #
    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        """Вызывается каждые CHECK_INTERVAL_MS через Tk-цикл событий."""
        now = datetime.now()

        # 1) Наступившие напоминания → уведомление (popup/toast)
        for r in self.store.get_due_reminders(now):
            title, body = format_reminder_message(r)
            # помечаем сразу, чтобы не дублировать уведомления
            self.store.mark_notified(r["id"])
            notify_async(title, body)

        # 2) Автоперевод уже уведомлённых и прошедших время в "Просрочено"
        if self.store.auto_expire_overdue(now):
            self.refresh_list()

        # продолжаем цикл
        self.after(CHECK_INTERVAL_MS, self._tick)


if __name__ == "__main__":
    app = ReminderApp()
    app.mainloop()
