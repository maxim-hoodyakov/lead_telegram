"""
Configuration for the Telegram lead generation system.
Fill in api_id and api_hash from https://my.telegram.org/apps
"""

# Telegram API credentials (get from https://my.telegram.org/apps)
api_id = 35236077
api_hash = "71affa841c91cddb43cd4b803d05c275"

# Session file name (Telethon will create e.g. lead_bot.session)
session_name = "lead_bot"

# Phone number for login (optional). If set, Telethon will only ask for the code.
phone = "+79880880429"


# Keywords to detect in group messages (case insensitive)
# Список фраз, по которым будем искать лидов в группах (без учёта регистра).
KEYWORDS = [
    "ищу таргетолога",
    "нужен таргетолог",
    "кто настраивает рекламу",
]

# Message sent to the user in private
# Первое сообщение, которое бот отправляет лиду в личку.
MESSAGE_TEMPLATE = """Здравствуйте!
Увидел ваше сообщение в группе.
Вы искали таргетолога.
Могу подсказать по запуску рекламы ВКонтакте."""

# Safety limits / Ограничения
# Максимальное количество контактов (отправленных сообщений) за одни сутки.
MAX_MESSAGES_PER_DAY = 15

# Пауза цикла отправки, когда нет лидов (в секундах).
SEND_LOOP_IDLE_SECONDS = 5

# Пауза цикла отправки, когда достигнут лимит на сегодня (в секундах).
SEND_LOOP_LIMIT_REACHED_SECONDS = 3600

# SQLite database file path
# Путь к файлу базы данных SQLite.
DB_PATH = "leads.db"

# Excel export
# Путь к Excel-файлу, где будут храниться лиды и переписка.
EXCEL_PATH = "leads.xlsx"
# Имя листа внутри Excel-файла.
EXCEL_SHEET = "Лиды"

# --- Настройки ИИ: чат — Claude (Anthropic), голос — Whisper (OpenAI) ---
# Claude 3.5 Sonnet. Ключ можно задать через ANTHROPIC_API_KEY в окружении.
ANTHROPIC_API_KEY = "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"
# Модель Claude (Haiku 4.5 — быстрая и дешёвая)
CLAUDE_MODEL = "claude-haiku-4-5"
# Температура и макс. токенов ответа для Claude (без жёсткого лимита — большой лимит, чтобы не обрывать ответы)
OPENAI_TEMPERATURE = 0.7
OPENAI_MAX_TOKENS = 4096
# RAG: сколько релевантных чанков из папки RAG подмешивать в контекст
RAG_TOP_K = 5
# OpenAI — транскрипция голосовых (Whisper) и проверочный ИИ (checker_ai)
OPENAI_API_KEY = "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"
# Сколько последних сообщений диалога передавать в контекст (полная память переписки)
AI_MAX_HISTORY_MESSAGES = 80
# Как часто проверять напоминания (секунды)
REMINDER_CHECK_INTERVAL_SECONDS = 60
# Минимум сообщений в диалоге перед обновлением сводки о лиде в Excel
AI_SUMMARY_MIN_MESSAGES = 4

# Задержка перед ответом ИИ: фиксированная пауза (секунды) после сообщения лида
REPLY_DELAY_MIN_SECONDS = 1
REPLY_DELAY_MAX_SECONDS = 1
# Пауза тишины (секунды) после последней активности лида (сообщения/печатает),
# по истечении которой ИИ формирует ответ на накопленные сообщения
AI_DEBOUNCE_SECONDS = 4
# Длительность «печатает» в Telegram: секунд на один символ ответа
SECONDS_PER_CHAR = 0.1

# Куда слать уведомление, когда лид присылает ссылку (user_id твоего аккаунта)
NOTIFY_USER_ID = 638551033  # username: max_hoodyakov

# Ответ лиду, если не удалось обработать голосовое сообщение (транскрипция недоступна или не сработала)
VOICE_FALLBACK_MESSAGE = (
    "Извините, я сейчас не могу прослушать голосовое сообщение. "
    "Можете, пожалуйста, написать текстом?"
)

# Если лид прислал ссылку — сначала отправить это сообщение, через LINK_THANKS_DELAY_SECONDS — основной ответ
LINK_THANKS_MESSAGE = "Благодарю, сейчас посмотрю)"
LINK_THANKS_DELAY_SECONDS = 30

# Папка с картинками упаковок сообществ (.png); при обсуждении упаковки бот может отправить пример
PACK_DESIGNS_DIR = "pack_designs"

# OPENAI_API_KEY используется и проверочным ИИ (папка checker_ai/) для сценариев и анализа
