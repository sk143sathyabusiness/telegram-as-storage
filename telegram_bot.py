"""
Telethon userbot for TeamVault.
Uploads/downloads encrypted file chunks to/from Telegram channels.
Each chunk = one Telegram message. Message IDs stored in file_versions.message_ids.
"""

import asyncio
import os
import io
from telethon import TelegramClient, utils

API_ID = int(os.getenv("TELETHON_API_ID", "0"))
API_HASH = os.getenv("TELETHON_API_HASH", "")
SESSION_FILE = os.path.join(os.path.dirname(__file__), "session_name.session")
CHUNK_SIZE = 1_900_000_000

_loop = None
_client = None

def _get_loop():
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop

async def _get_client():
    global _client
    if _client is None:
        _client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
        await _client.start()
    return _client

def is_configured():
    return bool(API_ID and API_HASH)

async def _upload_chunks(file_bytes, filename, chat_id):
    client = await _get_client()
    entity = await client.get_entity(utils.resolve_id(chat_id))
    message_ids = []
    offset = 0
    while offset < len(file_bytes):
        chunk = file_bytes[offset:offset + CHUNK_SIZE]
        buf = io.BytesIO(chunk)
        buf.name = filename if offset == 0 else f"{filename}.part{offset // CHUNK_SIZE}"
        msg = await client.send_file(entity, buf, force_document=True)
        message_ids.append(msg.id)
        offset += CHUNK_SIZE
    return message_ids

async def _download_chunks(chat_id, message_ids):
    client = await _get_client()
    entity = await client.get_entity(utils.resolve_id(chat_id))
    chunks = []
    for mid in message_ids:
        msg = await client.get_messages(entity, ids=mid)
        if msg and msg.document:
            buf = io.BytesIO()
            await client.download_file(msg.document, buf)
            chunks.append(buf.getvalue())
    return b"".join(chunks)

def upload_chunks(file_bytes, filename, chat_id):
    loop = _get_loop()
    return loop.run_until_complete(_upload_chunks(file_bytes, filename, chat_id))

def download_chunks(chat_id, message_ids):
    loop = _get_loop()
    return loop.run_until_complete(_download_chunks(chat_id, message_ids))
