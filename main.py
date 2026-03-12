"""
Точка входа для системы генерации лидов в Telegram.
Запуск: python3 main.py
Авторизация: по QR-коду (отсканируйте с телефона).
"""

import asyncio
import logging
import sys

import qrcode
from telethon import TelegramClient, errors

import config
import database
import excel_export
import reminder_loop
import sender
import telegram_client

# Логируем всё на русском: новые лиды, отправленные сообщения, ошибки.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _print_qr(url: str) -> None:
    """Печатает QR-код в терминал по ссылке для входа."""
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make()
    for row in qr.modules:
        print("".join("\033[40m  \033[0m" if cell else "  " for cell in row))


async def _login_via_qr(client: TelegramClient) -> None:
    """Авторизация по QR-коду: показываем QR, ждём сканирования (при 2FA запрашиваем пароль)."""
    qr = await client.qr_login()
    while True:
        print("\nОткройте Telegram → Настройки → Устройства → Подключить рабочий стол")
        print("Отсканируйте QR-код ниже:\n")
        _print_qr(qr.url)
        print()
        try:
            await qr.wait(timeout=60)
            break
        except asyncio.TimeoutError:
            logger.info("QR истёк, генерирую новый...")
            qr = await qr.recreate()
        except errors.SessionPasswordNeededError:
            from getpass import getpass

            password = getpass("Введите пароль двухэтапной аутентификации: ")
            await client.sign_in(password=password)
            break


async def main() -> None:
    """Основная корутина: инициализация БД, Excel, Telegram-клиента и запуск бота."""
    # Проверяем, что в config.py указаны api_id и api_hash
    if not config.api_id or not config.api_hash:
        logger.error(
            "Укажите api_id и api_hash в config.py (получить можно на https://my.telegram.org/apps)."
        )
        return

    # Инициализация SQLite и Excel
    database.init_db()
    excel_export.init_excel_file()
    logger.info("База данных и Excel-файл инициализированы.")

    # Создаём клиент Telethon и подключаемся (авторизация по QR или по сохранённой сессии)
    client = telegram_client.create_client()
    await client.connect()
    if not await client.is_user_authorized():
        await _login_via_qr(client)
    logger.info("Клиент Telegram подключен и авторизован.")

    # Регистрируем обработчики сообщений
    await telegram_client.register_message_handler(client)

    # Фоновые циклы: отправка первым лидам, напоминания от ИИ
    send_task = asyncio.create_task(sender.run_send_loop(client))
    reminder_task = asyncio.create_task(reminder_loop.run_reminder_loop(client))

    # Ждём, пока клиент не отключится
    await client.run_until_disconnected()
    send_task.cancel()
    reminder_task.cancel()
    for t in (send_task, reminder_task):
        try:
            await t
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем.")
