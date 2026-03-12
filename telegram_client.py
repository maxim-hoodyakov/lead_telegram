"""
Клиент Telegram на базе Telethon.
Подключается по сессии, слушает сообщения в группах, ищет лидов и сохраняет их.
В личке с лидами отвечает ИИ (ChatGPT), ведёт контекст и напоминания.
"""

import asyncio
import logging
import os
import random
import re
import tempfile
from datetime import datetime, timedelta

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat

import config
import content_blocks
import database
import excel_export
import parser

try:
    import ai_client
except ImportError:
    ai_client = None

logger = logging.getLogger(__name__)

# Длительность «печатает» обновляется в Telegram ~раз в 5 сек
TYPING_REFRESH_INTERVAL = 4


async def send_with_typing_delay(client: TelegramClient, user_id: int, text: str) -> None:
    """
    Показать «печатает» в Telegram на время len(text) * SECONDS_PER_CHAR, затем отправить сообщение.
    В Telethon action() — контекстный менеджер (async with), не корутина.
    """
    duration = len(text) * getattr(config, "SECONDS_PER_CHAR", 0.2727)
    if duration <= 0:
        await client.send_message(user_id, text)
        return
    async with client.action(user_id, "typing"):
        await asyncio.sleep(duration)
    await client.send_message(user_id, text)


async def _get_message_text(client: TelegramClient, event: events.NewMessage.Event) -> str:
    """
    Извлечь текст из сообщения: обычный текст или транскрипция голосового (Whisper).
    """
    text = (event.message.text or "").strip()
    if text:
        return text

    if not event.message.voice:
        return ""

    if not ai_client or not ai_client._get_api_key():
        logger.warning("Голосовое сообщение получено, но ИИ отключён — транскрипция невозможна.")
        return ""

    path_ogg = None
    path_wav = None
    try:
        path_ogg = await client.download_media(event.message.voice, file=tempfile.gettempdir())
        if not path_ogg or not os.path.isfile(path_ogg):
            return ""

        # Whisper принимает wav/mp3; в Telegram голос обычно .ogg — конвертируем
        try:
            from pydub import AudioSegment
            path_wav = path_ogg + ".wav"
            seg = AudioSegment.from_file(path_ogg)
            seg.export(path_wav, format="wav")
            path_to_transcribe = path_wav
        except ImportError:
            logger.warning(
                "Для голосовых сообщений установите pydub и ffmpeg: pip install pydub, затем ffmpeg в системе."
            )
            path_to_transcribe = path_ogg  # Whisper может не принять ogg

        transcribed = await asyncio.to_thread(
            ai_client.transcribe_audio,
            path_to_transcribe,
        )
        return transcribed or ""
    except Exception as e:
        logger.exception("Ошибка обработки голосового сообщения: %s", e)
        return ""
    finally:
        for p in (path_ogg, path_wav):
            if p and os.path.isfile(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


def create_client() -> TelegramClient:
    """Создать и вернуть клиент Telethon c использованием настроек из config.py."""
    return TelegramClient(
        config.session_name,
        config.api_id,
        config.api_hash,
    )


async def register_message_handler(client: TelegramClient) -> None:
    """
    Зарегистрировать обработчики сообщений:
    - сообщения в группах → поиск лидов, сохранение в БД и Excel;
    - личные входящие сообщения от лида → добавление в Excel;
    - личные исходящие сообщения вам → добавление в Excel.
    """

    @client.on(events.NewMessage())
    async def on_new_message(event: events.NewMessage.Event) -> None:
        """
        Обработчик новых сообщений.
        Здесь нас интересуют только группы и супергруппы: ищем ключевые фразы и
        сохраняем лидов.
        """
        chat = await event.get_chat()
        is_group = isinstance(chat, Chat)
        is_supergroup = isinstance(chat, Channel) and getattr(chat, "megagroup", False)
        if not (is_group or is_supergroup):
            return

        message = event.message
        text = message.text or ""
        if not parser.is_lead_message(text):
            return

        sender = await event.get_sender()
        user_id = sender.id if sender else event.sender_id
        username = getattr(sender, "username", None) if sender else None

        # Название группы, где нашли лида
        group_title = getattr(chat, "title", "") or ""

        # Строка с информацией о клиенте для Excel
        first_name = getattr(sender, "first_name", "") if sender else ""
        last_name = getattr(sender, "last_name", "") if sender else ""
        phone = getattr(sender, "phone", "") if sender else ""

        name_parts = [p for p in [first_name, last_name] if p]
        display_name = " ".join(name_parts).strip() if name_parts else ""
        user_info_parts = []
        if display_name:
            user_info_parts.append(display_name)
        if username:
            user_info_parts.append(f"@{username}")
        if phone:
            user_info_parts.append(phone)
        user_info = ", ".join(user_info_parts) if user_info_parts else str(user_id)

        lead_id = database.add_lead(user_id, username, text, group_title, display_name=display_name or None)
        if lead_id is None:
            logger.info(
                "Лид уже существует (user_id=%s, группа='%s'), пропускаю дубликат.",
                user_id,
                group_title,
            )
            return

        # Создаём строку в Excel для нового лида
        excel_export.ensure_lead_row(lead_id, user_info, group_title)
        # Сохраняем контекст группы для ИИ: первое «сообщение» в диалоге — что написал в группе
        database.add_conversation_message(
            lead_id,
            "user",
            f"Сообщение в группе «{group_title}»: {text}",
        )

        logger.info(
            "Новый лид: id=%s user_id=%s username=%s группа='%s'",
            lead_id,
            user_id,
            username,
            group_title,
        )

    # Буферы и задачи для дебаунса ответов ИИ по лидам
    lead_text_buffers: dict[int, str] = {}
    lead_reply_tasks: dict[int, asyncio.Task] = {}
    DEBOUNCE_SECONDS = getattr(config, "AI_DEBOUNCE_SECONDS", 3)
    URL_PATTERN = re.compile(
        r"https?://[^\s<>\"']+|t\.me/[^\s<>\"']+|vk\.(?:com|ru)/[^\s<>\"']+",
        re.IGNORECASE,
    )
    PACKAGING_KEYWORDS = ("упаковк", "дизайн сообщества", "обложк", "оформлен", "сообществ")
    # Вопрос про дизайн/упаковку — отправить блок услуги с картинками сразу
    PACKAGING_QUESTION = ("дизайн", "упаковк", "оформлен", "оформление")
    PACKAGING_QUESTION_CTX = ("сколько", "стоит", "что", "предложить", "можно", "стоимость", "входит")

    async def _debounced_ai_reply(lead_id: int, user_id: int) -> None:
        """
        Подождать паузу тишины от лида и затем одним ответом обработать накопленные сообщения.
        Если в сообщении лида есть ссылка — сначала отправить благодарность, пауза ~30 сек, затем основной ответ.
        При теме упаковки — после ответа отправить одну случайную картинку из pack_designs.
        """
        try:
            await asyncio.sleep(DEBOUNCE_SECONDS)
            # Если к этому моменту ИИ выключен или ключа нет — просто выходим
            if not (ai_client and ai_client._get_api_key()):
                return
            if not database.is_ai_enabled(lead_id):
                return

            user_text = lead_text_buffers.pop(lead_id, "").strip()
            if not user_text:
                return

            # Короткая задержка перед ответом (эффект «думает»)
            delay = random.uniform(
                getattr(config, "REPLY_DELAY_MIN_SECONDS", 1),
                getattr(config, "REPLY_DELAY_MAX_SECONDS", 1),
            )
            await asyncio.sleep(delay)

            # Если лид прислал ссылку — сначала короткая благодарность, затем пауза, потом основной ответ
            if URL_PATTERN.search(user_text):
                thanks_msg = getattr(config, "LINK_THANKS_MESSAGE", "Благодарю, сейчас посмотрю)")
                link_delay = getattr(config, "LINK_THANKS_DELAY_SECONDS", 30)
                await client.send_message(user_id, thanks_msg)
                database.add_conversation_message(lead_id, "assistant", thanks_msg)
                excel_export.append_message(lead_id, f"{thanks_msg} (я)", from_me=True)
                await asyncio.sleep(link_delay)

            user_lower = user_text.lower()
            is_packaging_question = (
                any(k in user_lower for k in PACKAGING_QUESTION)
                and any(k in user_lower for k in PACKAGING_QUESTION_CTX)
            )

            if is_packaging_question:
                # Вопрос про дизайн/упаковку — сразу один блок услуги с картинками (как services.md 34–48)
                pack_text = getattr(content_blocks, "PACKAGING_SERVICE_TEXT", "")
                pack_dir = getattr(config, "PACK_DESIGNS_DIR", "pack_designs")
                pngs = []
                if os.path.isdir(pack_dir):
                    pngs = [os.path.join(pack_dir, f) for f in os.listdir(pack_dir) if f.lower().endswith(".png")]
                chosen = random.sample(pngs, min(5, len(pngs))) if pngs else []
                try:
                    if chosen:
                        await client.send_file(user_id, chosen, caption=pack_text)
                    else:
                        await client.send_message(user_id, pack_text)
                    database.add_conversation_message(lead_id, "assistant", pack_text)
                    excel_export.append_message(lead_id, f"{pack_text} (я)", from_me=True)
                    logger.info("Отправлен блок «Упаковка» с %s картинками (вопрос лида) lead_id=%s.", len(chosen), lead_id)
                except Exception as e:
                    logger.warning("Не удалось отправить блок упаковки: %s", e)
                reminder_iso = None
                conversation_ended = False
                ads_full = False
                packaging_full = False
                reply_text = None
            else:
                reply_text, reminder_iso, conversation_ended, ads_full, packaging_full = ai_client.generate_ai_reply(lead_id, user_text)

            if ads_full:
                # Одно сообщение с полным текстом услуги «Таргетированная реклама»
                intro = (reply_text or "").strip()
                if intro:
                    await send_with_typing_delay(client, user_id, intro)
                    database.add_conversation_message(lead_id, "assistant", intro)
                    excel_export.append_message(lead_id, f"{intro} (я)", from_me=True)
                ads_text = getattr(content_blocks, "ADS_SERVICE_TEXT", "")
                if ads_text:
                    await client.send_message(user_id, ads_text)
                    database.add_conversation_message(lead_id, "assistant", ads_text)
                    excel_export.append_message(lead_id, f"{ads_text} (я)", from_me=True)
                    logger.info("Отправлен блок услуги «Реклама» лиду lead_id=%s.", lead_id)
            elif packaging_full:
                # Одно сообщение с текстом упаковки + 5 случайных картинок
                intro = (reply_text or "").strip()
                if intro:
                    await send_with_typing_delay(client, user_id, intro)
                    database.add_conversation_message(lead_id, "assistant", intro)
                    excel_export.append_message(lead_id, f"{intro} (я)", from_me=True)
                pack_text = getattr(content_blocks, "PACKAGING_SERVICE_TEXT", "")
                pack_dir = getattr(config, "PACK_DESIGNS_DIR", "pack_designs")
                pngs = []
                if os.path.isdir(pack_dir):
                    pngs = [os.path.join(pack_dir, f) for f in os.listdir(pack_dir) if f.lower().endswith(".png")]
                chosen = random.sample(pngs, min(5, len(pngs))) if pngs else []
                try:
                    if chosen:
                        await client.send_file(user_id, chosen, caption=pack_text)
                    else:
                        await client.send_message(user_id, pack_text)
                    database.add_conversation_message(lead_id, "assistant", pack_text)
                    excel_export.append_message(lead_id, f"{pack_text} (я)", from_me=True)
                    logger.info("Отправлен блок «Упаковка» с %s картинками лиду lead_id=%s.", len(chosen), lead_id)
                except Exception as e:
                    logger.warning("Не удалось отправить блок упаковки: %s", e)
            else:
                parts = ai_client.split_ai_reply(reply_text) if reply_text else []
                for part in parts:
                    await send_with_typing_delay(client, user_id, part)
                    database.add_conversation_message(lead_id, "assistant", part)
                    excel_export.append_message(lead_id, f"{part} (я)", from_me=True)
                    logger.info("ИИ ответил лиду (lead_id=%s) частью сообщения.", lead_id)

            if conversation_ended:
                disable_at = datetime.utcnow() + timedelta(hours=1)
                database.set_ai_disabled_at(lead_id, disable_at.strftime("%Y-%m-%dT%H:%M:%S"))
                lead_row = database.get_lead_by_id(lead_id)
                nickname = ""
                if lead_row:
                    try:
                        un = lead_row["username"]
                        dn = lead_row["display_name"]
                        nickname = (f"@{un}" if (un and str(un).strip()) else "") or (str(dn or "").strip()) or f"user_id={lead_row['user_id']}"
                    except (KeyError, TypeError):
                        nickname = f"lead_id={lead_id}"
                short_summary = ""
                if ai_client:
                    short_summary = ai_client.generate_lead_summary(lead_id) or ""
                body = (
                    "Разговор с лидом завершён.\n\n"
                    f"ID - {lead_id}\n"
                    f"Никнейм - {nickname}\n"
                    f"Коротко - {short_summary}\n"
                    "На какие услуги договорились - "
                )
                notify_user_id = getattr(config, "NOTIFY_USER_ID", None)
                if notify_user_id:
                    try:
                        await client.send_message(notify_user_id, body)
                        logger.info("Уведомление о завершении диалога отправлено на user_id=%s, lead_id=%s.", notify_user_id, lead_id)
                    except Exception as e:
                        logger.warning("Не удалось отправить уведомление о завершении диалога: %s", e)

            if reminder_iso:
                try:
                    run_at = datetime.strptime(reminder_iso, "%Y-%m-%dT%H:%M:%S")
                    database.create_reminder(lead_id, run_at, note="написать лиду")
                    logger.info("Напоминание создано на %s (lead_id=%s).", reminder_iso, lead_id)
                except ValueError:
                    logger.warning("Не удалось распарсить время напоминания: %s", reminder_iso)

            min_msgs = getattr(config, "AI_SUMMARY_MIN_MESSAGES", 4)
            history = database.get_conversation_history(lead_id, limit=min_msgs * 2)
            if len(history) >= min_msgs:
                summary = ai_client.generate_lead_summary(lead_id)
                if summary:
                    excel_export.update_lead_summary(lead_id, summary)
                    logger.info("Сводка о лиде обновлена в Excel (lead_id=%s).", lead_id)
        except asyncio.CancelledError:
            # Задача отменена, потому что пришло новое сообщение — просто выходим
            return
        finally:
            # Очистим ссылку на задачу
            lead_reply_tasks.pop(lead_id, None)

    @client.on(events.ChatAction)
    async def on_chat_action(event: events.ChatAction.Event) -> None:
        """
        Любая активность в личке (в первую очередь «печатает») перезапускает таймер ответа ИИ.
        """
        if not event.is_private:
            return

        sender = await event.get_user()
        user_id = sender.id if sender else None
        if user_id is None:
            return

        lead = database.get_lead_by_user_id(user_id)
        if not lead:
            return

        lead_id = lead["id"]

        # Если нет буфера текста — отвечать пока нечего, но можно держать прошлый таймер
        if lead_id not in lead_text_buffers:
            return

        if not (ai_client and ai_client._get_api_key()):
            return
        if not database.is_ai_enabled(lead_id):
            return

        existing_task = lead_reply_tasks.get(lead_id)
        if existing_task and not existing_task.done():
            existing_task.cancel()
        lead_reply_tasks[lead_id] = asyncio.create_task(_debounced_ai_reply(lead_id, user_id))

    @client.on(events.NewMessage(incoming=True))
    async def on_private_incoming(event: events.NewMessage.Event) -> None:
        """
        Входящие личные сообщения от лида: сохраняем в Excel и в контекст,
        вызываем ИИ для ответа, отправляем ответ, обрабатываем напоминания.
        """
        if not event.is_private:
            return

        sender = await event.get_sender()
        user_id = sender.id if sender else event.sender_id
        lead = database.get_lead_by_user_id(user_id)
        if not lead:
            return

        # Обновляем display_name, если в базе он пустой, а в профиле уже есть имя
        try:
            current_name = (lead["display_name"] or "").strip()
        except (KeyError, TypeError):
            current_name = ""
        if not current_name and sender:
            first_name = getattr(sender, "first_name", "") or ""
            last_name = getattr(sender, "last_name", "") or ""
            name_parts = [p for p in [first_name, last_name] if p]
            new_display_name = " ".join(name_parts).strip()
            if new_display_name:
                database.update_display_name(lead["id"], new_display_name)

        # Отметить сообщение как прочитанное (две галочки у отправителя)
        try:
            await client.send_read_acknowledge(event.chat_id, event.message)
        except Exception:
            pass

        text = await _get_message_text(client, event)
        if not text:
            # Голосовое пришло, но транскрипция не удалась — отправляем вежливый ответ
            if event.message.voice:
                fallback = getattr(
                    config,
                    "VOICE_FALLBACK_MESSAGE",
                    "Извините, я сейчас не могу прослушать голосовое, можете пожалуйста написать текстом?",
                )
                if fallback:
                    await client.send_message(user_id, fallback)
                    lead_id = lead["id"]
                    database.add_conversation_message(lead_id, "user", "[голосовое — не распознано]")
                    database.add_conversation_message(lead_id, "assistant", fallback)
                    excel_export.append_message(lead_id, "[голосовое — не распознано] (лид)", from_me=False)
                    excel_export.append_message(lead_id, f"{fallback} (я)", from_me=True)
                    logger.info("Голосовое не обработано, отправлен fallback (lead_id=%s).", lead_id)
            return

        lead_id = lead["id"]
        database.add_conversation_message(lead_id, "user", text)
        excel_export.append_message(lead_id, f"{text} (лид)", from_me=False)
        logger.info("Ответ лида сохранён в Excel (lead_id=%s).", lead_id)

        # Если лид прислал ссылку — отправить уведомление на твой аккаунт
        url_match = re.search(
            r"https?://[^\s<>\"']+|t\.me/[^\s<>\"']+|vk\.(?:com|ru)/[^\s<>\"']+",
            text,
            re.IGNORECASE,
        )
        if url_match:
            notify_user_id = getattr(config, "NOTIFY_USER_ID", None)
            if notify_user_id:
                link = url_match.group(0).rstrip(".,;:)")
                excel_info = excel_export.get_lead_excel_info(lead_id)
                if not excel_info:
                    lead_row = database.get_lead_by_id(lead_id)
                    if lead_row:
                        def _row(key, default=""):
                            try:
                                return lead_row[key] or default
                            except (KeyError, TypeError):
                                return default
                        excel_info = "ID: {id}\nКлиент: {client}\nГруппа: {group}".format(
                            id=_row("id", lead_id),
                            client=(_row("display_name") or _row("username") or str(_row("user_id", ""))).strip() or str(lead_id),
                            group=_row("group_title", ""),
                        )
                body = (excel_info or "Лид (данные из БД)") + "\n\nСсылка: " + link
                try:
                    await client.send_message(notify_user_id, body)
                    logger.info("Уведомление о ссылке от лида отправлено на user_id=%s.", notify_user_id)
                except Exception as e:
                    logger.warning("Не удалось отправить уведомление о ссылке: %s", e)

        # Проверяем, включён ли ИИ и есть ли ключ — логика ответа/дебаунса ниже
        if not (ai_client and ai_client._get_api_key()):
            logger.debug("ИИ отключён или ключ не задан — ответ не отправлен.")
            return
        if not database.is_ai_enabled(lead_id):
            logger.debug("ИИ выключен для lead_id=%s — входящее сообщение только сохраняется.", lead_id)
            return

        # Дебаунс: накапливаем текст и откладываем единый ответ
        prev = lead_text_buffers.get(lead_id, "")
        lead_text_buffers[lead_id] = (prev + "\n" + text).strip() if prev else text

        existing_task = lead_reply_tasks.get(lead_id)
        if existing_task and not existing_task.done():
            existing_task.cancel()
        lead_reply_tasks[lead_id] = asyncio.create_task(_debounced_ai_reply(lead_id, user_id))

    @client.on(events.NewMessage(outgoing=True))
    async def on_private_outgoing(event: events.NewMessage.Event) -> None:
        """
        Исходящие личные сообщения (от вас к лиду).
        Добавляем ваши сообщения в Excel.
        """
        if not event.is_private:
            return

        # Текст сообщения
        text = (event.message.text or "").strip()
        if not text:
            return

        # Команды управления ИИ (используются тобой, например в Избранном или любом чате)
        if text.startswith("/ai"):
            parts = text.split()
            if len(parts) >= 3 and parts[0] == "/ai":
                mode = parts[1].lower()
                target_raw = parts[2]
                try:
                    target_id = int(target_raw)
                except ValueError:
                    logger.warning("Неверный формат ID в команде /ai: %s", target_raw)
                    return

                lead_row = database.get_lead_by_user_id(target_id) or database.get_lead_by_id(target_id)
                if not lead_row:
                    await client.send_message(event.chat_id, f"Лид с user_id/lead_id {target_id} не найден.")
                    return

                lead_id = lead_row["id"]
                if mode == "off":
                    database.set_ai_enabled(lead_id, False)
                    await client.send_message(event.chat_id, f"ИИ отключён для lead_id={lead_id}.")
                elif mode == "on":
                    database.set_ai_enabled(lead_id, True)
                    await client.send_message(event.chat_id, f"ИИ включён для lead_id={lead_id}.")
                else:
                    await client.send_message(
                        event.chat_id,
                        "Использование: /ai on <user_id|lead_id> или /ai off <user_id|lead_id>",
                    )
                return

        # Избегаем дублирования первого авто-сообщения:
        if text == (config.MESSAGE_TEMPLATE or "").strip():
            return

        # Получатель сообщения — это peer (чат), у которого есть .id
        chat = await event.get_chat()
        user_id = getattr(chat, "id", None)
        if user_id is None:
            return

        lead = database.get_lead_by_user_id(user_id)
        if not lead:
            return

        lead_id = lead["id"]
        excel_export.append_message(lead_id, f"{text} (я)", from_me=True)
        database.add_conversation_message(lead_id, "operator", text)
        # При ручном сообщении оператора автоматически отключаем ИИ для этого лида
        database.set_ai_enabled(lead_id, False)
        logger.info("Исходящее сообщение сохранено в Excel и ИИ отключён (lead_id=%s).", lead_id)

    logger.info("Обработчики сообщений Telegram зарегистрированы.")
