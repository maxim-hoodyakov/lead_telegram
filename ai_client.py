"""
Клиент ИИ: ответы лидам через Claude (Anthropic), напоминания, сводка о лиде.
Транскрипция голосовых — через OpenAI Whisper (если задан OPENAI_API_KEY).
"""

import logging
import os
import re
from typing import Optional

import anthropic
from openai import OpenAI

import config
import database

try:
    import rag
except ImportError:
    rag = None

try:
    from ai_config import build_system_prompt, SUMMARY_USER_PROMPT
except ImportError:
    build_system_prompt = None
    SUMMARY_USER_PROMPT = ""

logger = logging.getLogger(__name__)

# Маркер напоминания в ответе ИИ
REMINDER_PATTERN = re.compile(r"REMINDER:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)", re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Маркеры для спец-ответов (удаляются из текста, обрабатываются в telegram_client)
MARKER_ADS_FULL = "[ADS_FULL]"
MARKER_PACKAGING_FULL = "[PACKAGING_FULL]"
MARKER_CONVERSATION_ENDED = "CONVERSATION_ENDED"


def _get_anthropic_key() -> str:
    """Ключ Anthropic из config или переменной окружения (для Claude)."""
    return (getattr(config, "ANTHROPIC_API_KEY", "") or "").strip() or os.environ.get("ANTHROPIC_API_KEY", "")


def _get_openai_key() -> str:
    """Ключ OpenAI из config или окружения (только для Whisper)."""
    return (getattr(config, "OPENAI_API_KEY", "") or "").strip() or os.environ.get("OPENAI_API_KEY", "")


def _client() -> Optional[anthropic.Anthropic]:
    """Клиент Anthropic для Claude (чат)."""
    key = _get_anthropic_key()
    if not key:
        return None
    return anthropic.Anthropic(api_key=key)


def _openai_client() -> Optional[OpenAI]:
    """Клиент OpenAI только для транскрипции голоса (Whisper)."""
    key = _get_openai_key()
    if not key:
        return None
    return OpenAI(api_key=key)


# Для проверки «включён ли ИИ» в telegram_client, sender, reminder_loop
def _get_api_key() -> str:
    """Ключ Anthropic (Claude) — по нему решаем, отвечать ли лидам."""
    return _get_anthropic_key()


def _lead_name_instruction(lead_id: int) -> str:
    """
    Блок инструкций для ИИ про имя лида: только имя, без фамилии.
    Если в профиле «Имя Фамилия» — в промпт передаём только первое слово.
    """
    lead = database.get_lead_by_id(lead_id)
    if not lead:
        display_name = ""
    else:
        try:
            display_name = (lead["display_name"] or "").strip()
        except (KeyError, TypeError):
            display_name = ""

    first_name = display_name.split()[0] if display_name and display_name.split() else display_name or ""

    if first_name:
        return f"""
Имя клиента из профиля Telegram: {first_name} (используй только это имя, без фамилии).

Обращайся только по имени. Фамилию не используй. Если в профиле указано «Имя Фамилия» — используй только имя (например Максим Худяков → Максим, Ирина Морозова → Ирина).
В первом сообщении пиши «{first_name}, здравствуйте!». Если клиент спрашивает, как его зовут — отвечай: «Вас зовут {first_name}». Не проси клиента писать имя, если оно уже указано.
Если имя написано латиницей (Aleksey, Mihail) — в ответах используй русскую форму (Алексей, Михаил).
"""

    return """
Имя клиента в профиле Telegram не указано. Используй нейтральное обращение. В одном из первых сообщений вежливо спроси: «Подскажите, как к вам обращаться?» Не придумывай имя сам.
"""


def _rag_context(user_text: str) -> str:
    """
    По вопросу клиента получить релевантные фрагменты из базы знаний (RAG) и вернуть блок для системного промпта.
    """
    if not rag or not user_text or not user_text.strip():
        return ""
    chunks = rag.get_chunks()
    if not chunks:
        return ""
    relevant = rag.retrieve(user_text.strip(), chunks, top_k=getattr(config, "RAG_TOP_K", 5))
    if not relevant:
        return ""
    parts = [c["text"] for c in relevant]
    return "\n\n---\n\nБаза знаний (используй при ответе, если релевантно):\n\n" + "\n\n".join(parts)


def build_messages_for_lead(lead_id: int, new_user_text: Optional[str] = None) -> list[dict]:
    """
    Собрать список сообщений для API: system + история + новое сообщение пользователя.
    В системный промпт добавляются: имя лида, при новом вопросе — релевантные фрагменты из RAG.
    """
    system_prompt = build_system_prompt() if build_system_prompt else "Ты вежливый ассистент по рекламе."
    system_prompt = system_prompt.rstrip() + "\n" + _lead_name_instruction(lead_id)
    if new_user_text:
        rag_block = _rag_context(new_user_text)
        if rag_block:
            system_prompt = system_prompt.rstrip() + "\n" + rag_block
    messages = [{"role": "system", "content": system_prompt}]

    limit = getattr(config, "AI_MAX_HISTORY_MESSAGES", 20)
    history = database.get_conversation_history(lead_id, limit=limit)
    for row in history:
        role = row["role"]
        if role == "operator":
            role = "assistant"
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": row["content"]})

    if new_user_text:
        messages.append({"role": "user", "content": new_user_text})

    return messages


def call_chat_completion(messages: list[dict], extra_system: Optional[str] = None) -> str:
    """
    Вызвать Claude (Anthropic) Messages API. Вернуть содержимое ответа assistant.
    """
    client = _client()
    if not client:
        logger.warning("Anthropic API key не задан — ИИ отключён.")
        return ""

    model = getattr(config, "CLAUDE_MODEL", "claude-haiku-4-5")
    temperature = getattr(config, "OPENAI_TEMPERATURE", 0.7)
    max_tokens = getattr(config, "OPENAI_MAX_TOKENS", 150)
    if extra_system:
        messages = [{"role": "system", "content": extra_system}] + [
            m for m in messages if m["role"] != "system"
        ]

    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    system_text = "\n\n".join(system_parts) if system_parts else ""
    chat_messages = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_text if system_text else None,
            messages=chat_messages,
        )
        if resp.content and len(resp.content) > 0:
            block = resp.content[0]
            text = getattr(block, "text", None) or (block if isinstance(block, str) else "")
            return (text or "").strip()
    except Exception as e:
        logger.exception("Ошибка вызова Claude (Anthropic): %s", e)
    return ""


def _parse_reminder_from_reply(reply: str) -> tuple[str, Optional[str]]:
    """
    Выделить из ответа основной текст и маркер REMINDER: ISO_DATETIME.
    Возвращает (текст_для_клиента, reminder_iso или None).
    """
    text = reply
    reminder_iso = None
    match = REMINDER_PATTERN.search(reply)
    if match:
        reminder_iso = match.group(1).strip()
        if reminder_iso.count(":") == 1:
            reminder_iso += ":00"
        text = REMINDER_PATTERN.sub("", reply).strip()
        text = re.sub(r"\n\s*\n\s*\n", "\n\n", text).strip()
    return text, reminder_iso


def _parse_special_markers(text: str) -> tuple[str, bool, bool, bool]:
    """
    Удалить из текста маркеры [ADS_FULL], [PACKAGING_FULL], CONVERSATION_ENDED.
    Возвращает (очищенный_текст, conversation_ended, ads_full, packaging_full).
    """
    t = (text or "").strip()
    conversation_ended = False
    ads_full = False
    packaging_full = False
    if MARKER_CONVERSATION_ENDED in t:
        conversation_ended = True
        t = re.sub(r"\s*CONVERSATION_ENDED\s*", "\n", t, flags=re.IGNORECASE).strip()
    if MARKER_ADS_FULL in t:
        ads_full = True
        t = t.replace(MARKER_ADS_FULL, "").strip()
    if MARKER_PACKAGING_FULL in t:
        packaging_full = True
        t = t.replace(MARKER_PACKAGING_FULL, "").strip()
    t = re.sub(r"\n\s*\n\s*\n", "\n\n", t).strip()
    return t, conversation_ended, ads_full, packaging_full


def _shorten_reply(text: str, max_sentences: int = 2) -> str:
    """
    Оставить в ответе только первые max_sentences предложений, чтобы не было «воды».
    """
    text = (text or "").strip()
    if not text:
        return ""
    sentences = SENTENCE_SPLIT_RE.split(text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return ""
    short = " ".join(sentences[:max_sentences])
    return short.strip()


# Нумерация 1., 2., 3. или **1 — не разбивать, одним сообщением
NUMBERING_RE = re.compile(r"(?:^|\s)\d+[.)]\s|\*\*\d+", re.MULTILINE)
# Блок услуги (Что входит / Стоимость / упоминание услуг с руб) — одним сообщением
SERVICE_BLOCK_MIN_LEN = 80


def _strip_trailing_dot(s: str) -> str:
    """Убрать одну точку в конце; «?» и «!» не трогать."""
    s = (s or "").strip()
    if s.endswith(".") and not s.endswith("...") and "?" not in s[-2:] and "!" not in s[-2:]:
        return s[:-1].strip()
    return s


def split_ai_reply(text: str) -> list[str]:
    """
    Разбить ответ ИИ на сообщения. Если в тексте нумерация (1., 2., 3.) или блок услуги — одним сообщением.
    Иначе — по предложениям. В конце сообщений точку убираем (кроме ? и !).
    """
    text = (text or "").strip()
    if not text:
        return []

    if NUMBERING_RE.search(text):
        return [_strip_trailing_dot(text)]

    # Блок услуги: списки, цены, перечисление услуг — не разбивать
    if len(text) >= SERVICE_BLOCK_MIN_LEN and (
        ("Что входит" in text or "Стоимость" in text or "руб" in text) and ("\n-" in text or "—" in text)
        or "Упаковка сообщества" in text
        or "Таргетированная реклама" in text
    ):
        return [_strip_trailing_dot(text)]

    short = _shorten_reply(text, max_sentences=2)
    if not short:
        return []
    parts: list[str] = []
    for line in short.splitlines():
        line = line.strip()
        if not line:
            continue
        subs = SENTENCE_SPLIT_RE.split(line)
        for s in subs:
            s = s.strip()
            if s:
                parts.append(_strip_trailing_dot(s))
    return parts


def generate_ai_reply(lead_id: int, user_text: str) -> tuple[str, Optional[str], bool, bool, bool]:
    """
    Сгенерировать ответ ИИ на сообщение лида.
    Возвращает (текст_ответа_для_Telegram, reminder_iso или None, conversation_ended, ads_full, packaging_full).
    """
    messages = build_messages_for_lead(lead_id, new_user_text=user_text)
    raw = call_chat_completion(messages)
    reply_text, reminder_iso = _parse_reminder_from_reply(raw)
    reply_text, conversation_ended, ads_full, packaging_full = _parse_special_markers(reply_text)
    return reply_text, reminder_iso, conversation_ended, ads_full, packaging_full


def generate_reminder_follow_up(lead_id: int) -> str:
    """
    Сгенерировать короткое сообщение для сработавшего напоминания.
    """
    messages = build_messages_for_lead(lead_id, new_user_text=None)
    messages.append({
        "role": "user",
        "content": "Сейчас наступило запланированное время напомнить клиенту. Напиши одно-два коротких вежливых предложения, что выходишь на связь как договаривались. Не добавляй REMINDER в ответ.",
    })
    return call_chat_completion(messages)


FIRST_MESSAGE_USER_PROMPT = """Напиши первое личное сообщение этому клиенту. Оно должно быть вовлекающим, не шаблонным — вызывать интерес и желание ответить. Используй его сообщение в группе (ниша, бюджет, запрос). Если в инструкциях указано имя клиента — начни с «Имя, здравствуйте!». Коротко, без воды: одно-два предложения. Не добавляй в конец REMINDER."""


def generate_first_message(lead_id: int) -> str:
    """
    Сгенерировать первое сообщение лиду на основе контекста группы.
    В conversation_messages уже должно быть сообщение «Сообщение в группе ...».
    """
    messages = build_messages_for_lead(lead_id, new_user_text=None)
    messages.append({"role": "user", "content": FIRST_MESSAGE_USER_PROMPT})
    raw = call_chat_completion(messages)
    reply_text, _ = _parse_reminder_from_reply(raw)
    return reply_text


def transcribe_audio(file_path: str) -> str:
    """
    Транскрибировать аудиофайл через OpenAI Whisper.
    Поддерживаются форматы: mp3, mp4, mpeg, mpga, m4a, wav, webm.
    Возвращает текст или пустую строку при ошибке. Требуется OPENAI_API_KEY.
    """
    client = _openai_client()
    if not client:
        return ""
    try:
        with open(file_path, "rb") as f:
            resp = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
            )
        return (resp.text or "").strip()
    except Exception as e:
        logger.exception("Ошибка транскрипции Whisper: %s", e)
        return ""


def generate_lead_summary(lead_id: int) -> str:
    """
    Сформировать краткую сводку о лиде по истории переписки (для Excel).
    """
    history = database.get_conversation_history(lead_id, limit=50)
    if not history:
        return ""

    client = _client()
    if not client:
        return ""

    # Формируем текст диалога для контекста (Claude)
    lines = []
    for row in history:
        role = "Клиент" if row["role"] == "user" else "Ассистент"
        lines.append(f"{role}: {row['content']}")

    user_content = SUMMARY_USER_PROMPT + "\n\n---\n" + "\n".join(lines)
    messages = [
        {"role": "system", "content": "Ты помогаешь свести переписку к одной строке о клиенте. Отвечай только одной строкой в заданном формате."},
        {"role": "user", "content": user_content},
    ]
    return call_chat_completion(messages).strip()
