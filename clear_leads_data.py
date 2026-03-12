#!/usr/bin/env python3
"""
Полная очистка данных о лидах и переписке.

Удаляет:
- все записи из БД (leads, conversation_messages, reminders);
- все данные из Excel (остаются только заголовки).

Запуск: python clear_leads_data.py
Для подтверждения можно добавить флаг: python clear_leads_data.py --yes
"""

import sys

import config
import database
import excel_export


def main() -> None:
    if "--yes" not in sys.argv and "-y" not in sys.argv:
        print("Это удалит ВСЕ данные о лидах и переписке из БД и Excel.")
        print("Для подтверждения запустите: python clear_leads_data.py --yes")
        sys.exit(1)

    database.init_db()
    database.clear_all_leads()
    print("БД очищена: leads, conversation_messages, reminders.")

    excel_export.clear_all_data()
    print("Excel очищен: остались только заголовки.")

    print("Готово.")


if __name__ == "__main__":
    main()
