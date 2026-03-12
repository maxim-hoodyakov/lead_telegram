"""
Фоновый цикл напоминаний: по таймеру проверяет, пора ли написать лиду,
и отправляет короткое сообщение от ИИ.
"""

import asyncio
import logging
from datetime import datetime

from telethon import TelegramClient

import config
import database
import excel_export
import telegram_client

try:
    import ai_client
except ImportError:
    ai_client = None

logger = logging.getLogger(__name__)


async def run_reminder_loop(client: TelegramClient) -> None:
    """
    Раз в REMINDER_CHECK_INTERVAL_SECONDS проверяем напоминания.
    Для каждого сработавшего: генерируем сообщение от ИИ, отправляем лиду,
    пишем в контекст и Excel, помечаем напоминание выполненным.
    """
    interval = getattr(config, "REMINDER_CHECK_INTERVAL_SECONDS", 60)
    while True:
        try:
            now = datetime.now()
            due = database.get_due_reminders(now)
            for rem in due:
                reminder_id = rem["id"]
                lead_id = rem["lead_id"]
                lead = database.get_lead_by_id(lead_id)
                if not lead:
                    database.mark_reminder_done(reminder_id)
                    continue
                user_id = lead["user_id"]
                if not ai_client or not ai_client._get_api_key():
                    continue
                text = ai_client.generate_reminder_follow_up(lead_id)
                if not text:
                    continue
                try:
                    await telegram_client.send_with_typing_delay(client, user_id, text)
                    database.add_conversation_message(lead_id, "assistant", text)
                    excel_export.append_message(lead_id, f"{text} (я)", from_me=True)
                    logger.info("Напоминание отправлено лиду (lead_id=%s).", lead_id)
                except Exception as e:
                    logger.exception("Ошибка отправки напоминания lead_id=%s: %s", lead_id, e)
                database.mark_reminder_done(reminder_id)
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Ошибка в цикле напоминаний: %s", e)
            await asyncio.sleep(interval)
