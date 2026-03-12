# Telegram Lead Bot

Бот для сбора лидов из групп Telegram и ведения диалога с помощью ИИ (Claude). Таргетированная реклама ВКонтакте, упаковка сообществ.

## Установка

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Для голосовых сообщений нужен **ffmpeg** в системе.

## Настройка

1. Скопируй конфиг и заполни данные:
   ```bash
   cp config.example.py config.py
   ```
2. В `config.py`: **api_id**, **api_hash** с [my.telegram.org/apps](https://my.telegram.org/apps), номер телефона, ключи **ANTHROPIC_API_KEY** и при необходимости **OPENAI_API_KEY** (для голосовых и checker_ai).

## Запуск

- Основной бот (личка + группы): `python main.py`
- Рассылка первым сообщением неконтактированным лидам: `python sender.py`
- Напоминания: `python reminder_loop.py`

Очистка всех данных лидов и переписки: `python clear_leads_data.py --yes`

## Репозиторий

Код можно хранить на GitHub. Файл `config.py`, сессии (`*.session`), база `leads.db` и Excel не попадают в репозиторий (см. `.gitignore`). После клонирования создай `config.py` из `config.example.py`.
