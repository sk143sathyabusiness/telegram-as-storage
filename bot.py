"""
telegram_bot.py — Telethon userbot core for Telegram-as-Storage.

ZERO LOCAL STORAGE POLICY:
All file bytes (encrypted) are handled in-memory (BytesIO) only.
Nothing is ever written to local disk — no temp files, no chunk files,
no cache. Telegram is the only persistent storage. Data exists on disk
momentarily only inside process memory during upload/download.

Encryption/decryption happens in app.py, in-memory, around calls to this
module. Telegram only ever sees ciphertext bytes.
"""

import os
import io
import hashlib
import asyncio
from typing import List, Dict, Optional

from telethon import TelegramClient
from telethon.tl.types import Message
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_NAME = os.environ.get("TG_SESSION_NAME", "session_name")

# Stay safely under Telegram's ~2GB single-message file ceiling.
CHUNK_SIZE_BYTES = int(os.environ.get("CHUNK_SIZE_BYTES", 1_900_000_000))  # 1.9 GB

_client: Optional[TelegramClient] = None


async def get_client() -> TelegramClient:
    """Singleton Telethon client, connected and authorized."""
    global _client
    if _client is None:
        _client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        await _client.connect()
        if not await _client.is_user_authorized():
            raise RuntimeError(
                "Telethon session not authorized. Run the one-time login "
                "flow to generate session_name.session before using this module."
            )
    return _client


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _split_bytes(data: bytes) -> List[bytes]:
    """Split in-memory bytes into chunk_size pieces. Never touches disk."""
    if not data:
        return [b""]
    return [data[i:i + CHUNK_SIZE_BYTES] for i in range(0, len(data), CHUNK_SIZE_BYTES)]


async def upload_file(
    channel_id: int,
    file_bytes: bytes,
    remote_name: str,
    progress_callback=None,
) -> Dict:
    """
    Upload in-memory (already-encrypted) bytes to the org's Telegram channel,
    chunked if necessary. No disk writes at any point.

    Returns:
        {
            "message_ids": [int, ...],
            "size_bytes": int,
            "checksum_sha256": str,   # checksum of full ciphertext
            "chunk_count": int,
        }
    """
    client = await get_client()

    total_size = len(file_bytes)
    checksum = _sha256_bytes(file_bytes)
    chunks = _split_bytes(file_bytes)
    message_ids: List[int] = []

    for i, chunk in enumerate(chunks):
        buf = io.BytesIO(chunk)
        buf.name = f"{remote_name}.part{i}" if len(chunks) > 1 else remote_name
        msg: Message = await client.send_file(
            channel_id,
            buf,
            caption=buf.name,
            progress_callback=progress_callback,
        )
        message_ids.append(msg.id)
        buf.close()

    return {
        "message_ids": message_ids,
        "size_bytes": total_size,
        "checksum_sha256": checksum,
        "chunk_count": len(message_ids),
    }


async def download_file(
    channel_id: int,
    message_ids: List[int],
    progress_callback=None,
) -> bytes:
    """
    Reassemble a file from its ordered chunk message_ids entirely in memory
    and return the raw (still-encrypted) bytes. Caller decrypts in-memory
    and streams the result to the requesting client — never written to disk.
    """
    client = await get_client()
    assembled = io.BytesIO()

    for msg_id in message_ids:
        msg = await client.get_messages(channel_id, ids=msg_id)
        chunk_buf = io.BytesIO()
        await client.download_media(msg, file=chunk_buf, progress_callback=progress_callback)
        assembled.write(chunk_buf.getvalue())
        chunk_buf.close()

    data = assembled.getvalue()
    assembled.close()
    return data


async def delete_file(channel_id: int, message_ids: List[int]) -> None:
    """Permanently delete all chunk messages for a file version (used on version purge / trash destroy)."""
    client = await get_client()
    await client.delete_messages(channel_id, message_ids)


def verify_bytes(data: bytes, expected_checksum: str) -> bool:
    """Confirm reassembled file integrity matches the checksum stored in Supabase."""
    return _sha256_bytes(data) == expected_checksum


# ---------------------------------------------------------------------------
# Backups — since NOTHING lives locally, "essential folder" backups also
# stay on Telegram: forward the relevant chunk messages into the org's
# dedicated #backups channel rather than exporting anywhere local.
# ---------------------------------------------------------------------------
async def backup_essential_folder(channel_id: int, backup_channel_id: int, message_ids: List[int]) -> List[int]:
    """
    Forward (not re-upload/re-download) the given chunk messages into the
    backups channel. Forwarding keeps bytes on Telegram's servers only.
    Returns the new message_ids in the backup channel.
    """
    client = await get_client()
    forwarded = await client.forward_messages(backup_channel_id, message_ids, channel_id)
    if isinstance(forwarded, Message):
        forwarded = [forwarded]
    return [m.id for m in forwarded]


# ---------------------------------------------------------------------------
# One-time interactive login helper — run manually once to create
# session_name.session. This .session file is the one unavoidable local
# artifact — it's Telethon's own auth credential, required to connect at all.
# ---------------------------------------------------------------------------
async def _interactive_login():
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()  # prompts for phone/code/2FA in terminal
    print("Login successful. Session saved to:", f"{SESSION_NAME}.session")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(_interactive_login())