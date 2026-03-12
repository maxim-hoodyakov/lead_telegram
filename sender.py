"""
Отправка личных сообщений лидам с учётом ограничений.
Первое сообщение генерирует ИИ по контексту группы; при отсутствии ключа — шаблон.
"""

import asyncio
import logging

from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserDeactivatedError

import config
import database
import excel_export
import telegram_client

try:
    import ai_client
except ImportError:
    ai_client = None

logger = logging.getLogger(__name__)


async def run_send_loop(client: TelegramClient) -> None:
    """
    Бесконечный цикл отправки сообщений лидам.
    Берёт первого необработанного лида и сразу отправляет ему сообщение, если:
    - не превышен дневной лимит;
    - пользователю ещё не писали раньше.
    """
    while True:
        try:
            # Проверяем дневной лимит
            sent_today = database.count_sent_today()
            if sent_today >= config.MAX_MESSAGES_PER_DAY:
                logger.info(
                    "Достигнут дневной лимит отправки (%s/%s). Жду %s секунд.",
                    sent_today,
                    config.MAX_MESSAGES_PER_DAY,
                    config.SEND_LOOP_LIMIT_REACHED_SECONDS,
                )
                await asyncio.sleep(config.SEND_LOOP_LIMIT_REACHED_SECONDS)
                continue

            leads = database.get_uncontacted_leads()
            if not leads:
                # Нет лидов — немного подождём и проверим ещё раз
                await asyncio.sleep(config.SEND_LOOP_IDLE_SECONDS)
                continue

            # Берём первого необработанного лида
            lead = leads[0]
            lead_id = lead["id"]
            user_id = lead["user_id"]

            # Дополнительная защита: не пишем, если уже есть contacted-лид с этим user_id
            if database.is_user_contacted(user_id):
                database.mark_contacted(lead_id)
                await asyncio.sleep(config.SEND_LOOP_IDLE_SECONDS)
                continue

            try:
                if ai_client and ai_client._get_api_key():
                    first_text = ai_client.generate_first_message(lead_id)
                    if not first_text:
                        first_text = config.MESSAGE_TEMPLATE
                else:
                    first_text = config.MESSAGE_TEMPLATE
                await telegram_client.send_with_typing_delay(client, user_id, first_text)
                database.add_conversation_message(lead_id, "assistant", first_text)
                database.mark_contacted(lead_id)
                excel_export.append_message(lead_id, f"{first_text} (я)", from_me=True)
                logger.info("Первое сообщение отправлено пользователю user_id=%s (lead_id=%s).", user_id, lead_id)
            except FloodWaitError as e:
                logger.warning("Ограничение по частоте (FloodWait): жду %s секунд.", e.seconds)
                await asyncio.sleep(e.seconds)
                # Повторим попытку на следующей итерации, лида не помечаем как contacted
            except UserDeactivatedError:
                logger.warning("Пользователь user_id=%s деактивирован. Помечаю лида как обработанного.", user_id)
                database.mark_contacted(lead_id)
            except Exception as e:
                logger.exception("Ошибка при отправке пользователю user_id=%s: %s", user_id, e)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Ошибка в цикле отправки сообщений: %s", e)
            await asyncio.sleep(config.SEND_LOOP_IDLE_SECONDS)
