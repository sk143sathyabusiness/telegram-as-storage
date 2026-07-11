"""
Telegram Bot API wrapper for TeamVault.
Stores encrypted files as documents in Telegram channels.
Each organisation has its own channel (chat_id).
"""

import os
import requests

BOT_TOKEN = os.getenv("VAULTX_BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
DEFAULT_CHAT_ID = os.getenv("VAULTX_CHAT_ID")


def _call(method, **kwargs):
    url = f"{TELEGRAM_API}/{method}"
    try:
        r = requests.post(url, **kwargs, timeout=120)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data["result"]
    except requests.RequestException as e:
        raise RuntimeError(f"Telegram request failed: {e}")


def is_configured():
    return bool(BOT_TOKEN)


def get_chat_id(chat_id=None):
    return chat_id or DEFAULT_CHAT_ID


def send_document(chat_id, file_bytes, filename):
    """Upload encrypted bytes to a Telegram channel. Returns (file_id, message_id)."""
    result = _call(
        "sendDocument",
        data={"chat_id": get_chat_id(chat_id)},
        files={"document": (filename, file_bytes)},
    )
    doc = result["document"]
    # Pick the largest available file_id (Telegram may provide thumbnails)
    file_id = doc.get("file_id")
    message_id = result["message_id"]
    return file_id, message_id


def get_file_bytes(file_id):
    """Download file bytes from Telegram by file_id."""
    result = _call("getFile", data={"file_id": file_id})
    file_path = result["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.content


def get_chat_member_count(chat_id):
    """Verify the bot can access a channel."""
    result = _call("getChatMemberCount", data={"chat_id": get_chat_id(chat_id)})
    return result
