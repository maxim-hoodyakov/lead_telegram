"""
Parser: detect if a message contains any of the lead keywords (case insensitive).
No Telethon dependency — works on plain text.
"""

import config


def is_lead_message(text: str) -> bool:
    """
    Return True if the message contains any of the configured keywords.
    Matching is case insensitive.
    """
    if not text or not text.strip():
        return False
    lower = text.lower().strip()
    for keyword in config.KEYWORDS:
        if keyword.lower() in lower:
            return True
    return False
