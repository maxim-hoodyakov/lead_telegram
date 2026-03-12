"""
SQLite база данных для хранения лидов, контекста переписки и напоминаний.
Таблицы: leads, conversation_messages, reminders
"""

import sqlite3
from datetime import datetime
from typing import Optional

import config


def get_connection() -> sqlite3.Connection:
    """Вернуть подключение к базе данных SQLite."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row  # доступ к колонкам по имени
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """
    Убедиться, что у таблицы leads есть все нужные колонки.
    Если таблица уже существует без новых полей, добавить их через ALTER TABLE.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            message TEXT,
            group_title TEXT,
            display_name TEXT,
            ai_enabled INTEGER NOT NULL DEFAULT 1,
            contacted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            contacted_at TEXT
        )
        """
    )

    # Проверяем наличие новых колонок и добавляем при необходимости
    cur = conn.execute("PRAGMA table_info(leads)")
    columns = {row[1] for row in cur.fetchall()}

    if "group_title" not in columns:
        conn.execute("ALTER TABLE leads ADD COLUMN group_title TEXT")
    if "display_name" not in columns:
        conn.execute("ALTER TABLE leads ADD COLUMN display_name TEXT")
    if "ai_enabled" not in columns:
        conn.execute("ALTER TABLE leads ADD COLUMN ai_enabled INTEGER NOT NULL DEFAULT 1")
    if "ai_disabled_at" not in columns:
        conn.execute("ALTER TABLE leads ADD COLUMN ai_disabled_at TEXT")

    # Таблица сообщений диалога для контекста ИИ
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    # Таблица напоминаний (таймер «напишите вечером»)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            run_at TEXT NOT NULL,
            note TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )


def init_db() -> None:
    """Создать таблицу leads, если её ещё нет, и обновить схему при необходимости."""
    conn = get_connection()
    try:
        _ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()


def clear_all_leads() -> None:
    """Удалить все данные о лидах: таблицы leads, conversation_messages, reminders."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM conversation_messages")
        conn.execute("DELETE FROM reminders")
        conn.execute("DELETE FROM leads")
        conn.commit()
    finally:
        conn.close()


def add_lead(
    user_id: int,
    username: Optional[str],
    message: str,
    group_title: str,
    display_name: Optional[str] = None,
) -> Optional[int]:
    """
    Добавить нового лида.
    display_name — имя из профиля Telegram (first_name + last_name).
    Возвращает id лида или None, если уже есть необработанный лид с таким user_id.
    """
    conn = get_connection()
    try:
        # Избегаем дублирующихся необработанных лидов для одного пользователя
        cur = conn.execute(
            "SELECT id FROM leads WHERE user_id = ? AND contacted = 0",
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            return row["id"]

        now = datetime.utcnow().isoformat()
        cur = conn.execute(
            "INSERT INTO leads (user_id, username, message, group_title, display_name, contacted, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (user_id, username or "", message or "", group_title or "", (display_name or "").strip() or None, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_uncontacted_leads() -> list:
    """Вернуть список необработанных лидов (каждый ряд — sqlite3.Row)."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, user_id, username, message, group_title, display_name, ai_enabled, ai_disabled_at "
            "FROM leads WHERE contacted = 0 ORDER BY id ASC"
        )
        return cur.fetchall()
    finally:
        conn.close()


def mark_contacted(lead_id: int) -> None:
    """Отметить лида как контактированного и установить contacted_at на текущее время."""
    conn = get_connection()
    try:
        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE leads SET contacted = 1, contacted_at = ? WHERE id = ?",
            (now, lead_id),
        )
        conn.commit()
    finally:
        conn.close()


def count_sent_today() -> int:
    """Посчитать, сколько сообщений было отправлено сегодня (по дате contacted_at, UTC)."""
    conn = get_connection()
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        cur = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE contacted = 1 AND date(contacted_at) = ?",
            (today,),
        )
        return cur.fetchone()[0]
    finally:
        conn.close()


def is_user_contacted(user_id: int) -> bool:
    """True, если этому пользователю уже отправляли сообщение (contacted = 1)."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT 1 FROM leads WHERE user_id = ? AND contacted = 1 LIMIT 1",
            (user_id,),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def get_lead_by_id(lead_id: int):
    """Вернуть лида по id (или None)."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, user_id, username, message, group_title, display_name, ai_enabled, ai_disabled_at FROM leads WHERE id = ?",
            (lead_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def get_lead_by_user_id(user_id: int):
    """Вернуть последнего лида по user_id (или None, если его нет)."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, user_id, username, message, group_title, display_name, ai_enabled, ai_disabled_at "
            "FROM leads WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def add_conversation_message(lead_id: int, role: str, content: str) -> None:
    """Добавить сообщение в историю диалога (role: user, assistant, operator)."""
    conn = get_connection()
    try:
        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO conversation_messages (lead_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (lead_id, role, content, now),
        )
        conn.commit()
    finally:
        conn.close()


def clear_conversation(lead_id: int) -> None:
    """Удалить всю историю переписки для лида (для тестов проверочного ИИ)."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM conversation_messages WHERE lead_id = ?", (lead_id,))
        conn.commit()
    finally:
        conn.close()


def get_conversation_history(lead_id: int, limit: int) -> list:
    """Вернуть последние limit сообщений диалога по lead_id (по created_at)."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, lead_id, role, content, created_at FROM conversation_messages "
            "WHERE lead_id = ? ORDER BY created_at ASC",
            (lead_id,),
        )
        rows = cur.fetchall()
        return rows[-limit:] if len(rows) > limit else rows
    finally:
        conn.close()


def create_reminder(lead_id: int, run_at: datetime, note: str = "") -> None:
    """Создать напоминание (run_at в UTC)."""
    conn = get_connection()
    try:
        run_at_str = run_at.strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO reminders (lead_id, run_at, note, status) VALUES (?, ?, ?, 'pending')",
            (lead_id, run_at_str, note or ""),
        )
        conn.commit()
    finally:
        conn.close()


def get_due_reminders(now: datetime) -> list:
    """Вернуть напоминания со статусом pending и run_at <= now."""
    conn = get_connection()
    try:
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S")
        cur = conn.execute(
            "SELECT id, lead_id, run_at, note FROM reminders WHERE status = 'pending' AND run_at <= ? ORDER BY run_at ASC",
            (now_str,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def mark_reminder_done(reminder_id: int) -> None:
    """Отметить напоминание как выполненное."""
    conn = get_connection()
    try:
        conn.execute("UPDATE reminders SET status = 'done' WHERE id = ?", (reminder_id,))
        conn.commit()
    finally:
        conn.close()


def set_ai_enabled(lead_id: int, enabled: bool) -> None:
    """Включить или выключить ИИ для лида."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE leads SET ai_enabled = ?, ai_disabled_at = NULL WHERE id = ?",
            (1 if enabled else 0, lead_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_ai_disabled_at(lead_id: int, at_iso: str) -> None:
    """Запланировать отключение ИИ для лида в момент at_iso (ISO datetime). ИИ будет отключён после этого времени."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE leads SET ai_disabled_at = ? WHERE id = ?",
            (at_iso, lead_id),
        )
        conn.commit()
    finally:
        conn.close()


def is_ai_enabled(lead_id: int) -> bool:
    """True, если для данного лида включён ИИ (ai_enabled = 1 и время отключения ещё не наступило)."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT ai_enabled, ai_disabled_at FROM leads WHERE id = ?",
            (lead_id,),
        )
        row = cur.fetchone()
        if not row:
            return True
        try:
            if not row["ai_enabled"]:
                return False
            disabled_at = row["ai_disabled_at"]
            if not disabled_at:
                return True
            from datetime import datetime
            try:
                at = datetime.strptime(disabled_at[:19], "%Y-%m-%dT%H:%M:%S")
            except (ValueError, TypeError):
                return True
            now = datetime.utcnow()
            if now >= at:
                conn.execute(
                    "UPDATE leads SET ai_enabled = 0, ai_disabled_at = NULL WHERE id = ?",
                    (lead_id,),
                )
                conn.commit()
                return False
            return True
        except (KeyError, TypeError):
            return True
    finally:
        conn.close()


def update_display_name(lead_id: int, display_name: str) -> None:
    """Обновить display_name лида (если появилось или изменилось имя в профиле)."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE leads SET display_name = ? WHERE id = ?",
            ((display_name or "").strip() or None, lead_id),
        )
        conn.commit()
    finally:
        conn.close()

