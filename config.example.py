"""
Пример конфига. Скопируй в config.py и заполни своими данными.
API: https://my.telegram.org/apps
"""

# Telegram API (получить на https://my.telegram.org/apps)
api_id = 0
api_hash = ""
session_name = "lead_bot"
phone = ""

# Фразы для поиска лидов в группах
KEYWORDS = [
    "ищу таргетолога",
    "нужен таргетолог",
    "кто настраивает рекламу",
]

MESSAGE_TEMPLATE = """Здравствуйте!
Увидел ваше сообщение в группе.
Вы искали таргетолога.
Могу подсказать по запуску рекламы ВКонтакте."""

MAX_MESSAGES_PER_DAY = 15
SEND_LOOP_IDLE_SECONDS = 5
SEND_LOOP_LIMIT_REACHED_SECONDS = 3600

DB_PATH = "leads.db"
EXCEL_PATH = "leads.xlsx"
EXCEL_SHEET = "Лиды"

# ИИ: Claude (Anthropic) для чата, Whisper (OpenAI) для голоса
ANTHROPIC_API_KEY = ""
CLAUDE_MODEL = "claude-haiku-4-5"
OPENAI_TEMPERATURE = 0.7
OPENAI_MAX_TOKENS = 4096
RAG_TOP_K = 5
OPENAI_API_KEY = ""

AI_MAX_HISTORY_MESSAGES = 80
REMINDER_CHECK_INTERVAL_SECONDS = 60
AI_SUMMARY_MIN_MESSAGES = 4

REPLY_DELAY_MIN_SECONDS = 1
REPLY_DELAY_MAX_SECONDS = 1
AI_DEBOUNCE_SECONDS = 4
SECONDS_PER_CHAR = 0.1

NOTIFY_USER_ID = 0

VOICE_FALLBACK_MESSAGE = (
    "Извините, я сейчас не могу прослушать голосовое сообщение. "
    "Можете, пожалуйста, написать текстом?"
)

LINK_THANKS_MESSAGE = "Благодарю, сейчас посмотрю)"
LINK_THANKS_DELAY_SECONDS = 30

PACK_DESIGNS_DIR = "pack_designs"
