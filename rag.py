"""
База знаний (RAG): загрузка .md из папки RAG, разбиение на чанки и поиск релевантных фрагментов по вопросу клиента.
"""

import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Папка с .md файлами базы знаний (относительно корня проекта)
RAG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "RAG")
# Количество релевантных чанков, подмешиваемых в контекст
RAG_TOP_K = 5
# Минимальная длина чанка (слишком короткие не считаем отдельными)
MIN_CHUNK_LEN = 40


def _tokenize(text: str) -> set[str]:
    """Привести текст к множеству слов (латиница + кириллица)."""
    text = (text or "").lower()
    words = re.findall(r"[a-zа-яё0-9]+", text)
    return set(w for w in words if len(w) > 1)


def _chunk_text(content: str, source: str) -> list[dict]:
    """
    Разбить текст на чанки: по двойному переносу и по заголовкам ##.
    Каждый чанк: {"text": str, "source": str}.
    """
    chunks: list[dict] = []
    # Сначала режем по ##
    parts = re.split(r"\n##\s+", content)
    for i, part in enumerate(parts):
        part = part.strip()
        if not part or len(part) < MIN_CHUNK_LEN:
            continue
        # Внутри части режем по двойному переносу
        for block in re.split(r"\n\s*\n", part):
            block = block.strip()
            if not block or len(block) < MIN_CHUNK_LEN:
                continue
            chunks.append({"text": block, "source": source})
    # Если не было ##, режем по \n\n
    if not chunks:
        for block in re.split(r"\n\s*\n", content):
            block = block.strip()
            if not block or len(block) < MIN_CHUNK_LEN:
                continue
            chunks.append({"text": block, "source": source})
    return chunks


def load_rag_chunks() -> list[dict]:
    """
    Загрузить все .md из RAG/ и разбить на чанки.
    Возвращает список {"text": str, "source": str}.
    """
    chunks: list[dict] = []
    if not os.path.isdir(RAG_DIR):
        logger.warning("Папка RAG не найдена: %s", RAG_DIR)
        return chunks
    for name in sorted(os.listdir(RAG_DIR)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(RAG_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.warning("Не удалось прочитать %s: %s", path, e)
            continue
        chunks.extend(_chunk_text(content, name))
    logger.info("RAG: загружено %s чанков из %s", len(chunks), RAG_DIR)
    return chunks


def retrieve(user_text: str, chunks: list[dict], top_k: int = RAG_TOP_K) -> list[dict]:
    """
    Найти наиболее релевантные чанки по вопросу пользователя (простой подсчёт совпадающих слов).
    user_text — последнее сообщение клиента; chunks — результат load_rag_chunks().
    """
    if not user_text or not chunks:
        return []
    user_words = _tokenize(user_text)
    if not user_words:
        return chunks[:top_k]
    scored: list[tuple[float, dict]] = []
    for c in chunks:
        chunk_words = _tokenize(c["text"])
        if not chunk_words:
            scored.append((0.0, c))
            continue
        overlap = len(user_words & chunk_words)
        # Нормализуем по длине чанка, чтобы короткие с одним совпадением не вылетали вверх
        score = overlap / (1 + len(chunk_words) ** 0.5)
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_k]]


# Кэш чанков (загружаем один раз при первом запросе)
_cached_chunks: Optional[list[dict]] = None


def get_chunks() -> list[dict]:
    """Вернуть чанки базы знаний (с кэшем)."""
    global _cached_chunks
    if _cached_chunks is None:
        _cached_chunks = load_rag_chunks()
    return _cached_chunks
