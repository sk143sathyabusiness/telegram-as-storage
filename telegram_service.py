"""
telegram_service.py — Telethon userbot core for Telegram-as-Storage.

ZERO LOCAL STORAGE POLICY:
All file bytes (encrypted) are handled in-memory (BytesIO) only.
Nothing is ever written to local disk — no temp files, no chunk files,
no cache. Telegram is the only persistent storage. Data exists on disk
momentarily only inside process memory during upload/download.

Supports files up to 10GB by splitting into ~1.9GB sequential chunks.
Each chunk = one Telegram message. Chunks are ordered and reassembled
on download.
"""

import os
import io
import hashlib
import asyncio
from typing import List, Dict, Optional

from telethon import TelegramClient
from telethon.tl.types import Message
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.errors import ChatIdInvalidError, FloodWaitError
from dotenv import load_dotenv

load_dotenv()

# Load lazily and fail softly at import time — callers check is_configured()
# instead of getting a KeyError traceback that could leak env names.
def _parse_api_id(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        print("[WARN] TG_API_ID is not a valid integer — Telegram is not configured")
        return None

API_ID: int | None = _parse_api_id(os.getenv("TG_API_ID"))
API_HASH: str | None = (os.getenv("TG_API_HASH") or "").strip() or None
SESSION_NAME: str = (os.getenv("TG_SESSION_NAME") or "session").strip()
# Vercel/serverless: no persistent .session file. Support StringSession via env.
# Generate with: python -c "from telethon.sessions import StringSession; print(StringSession.load('session.session'))" or via Telethon docs
TG_SESSION_STRING: str | None = (os.getenv("TG_SESSION_STRING") or "").strip() or None

# Stay safely under Telegram's ~2GB single-message file ceiling.
# Consumes app.config.CHUNK_SIZE_BYTES as single source of truth.
try:
    from app.config import CHUNK_SIZE_BYTES  # type: ignore
except Exception:
    CHUNK_SIZE_BYTES = int(os.environ.get("CHUNK_SIZE_BYTES", 1_900_000_000))  # 1.9 GB

# Concurrent chunk uploads (1 = sequential, 2-3 = faster for very large files)
CONCURRENT_CHUNKS = int(os.environ.get("CONCURRENT_CHUNKS", 1))

# Parallel MTProto connections used per chunk by FastTelethon (vendored parallel_file_transfer).
# Forces several parallel part-transfers even for small chunks. Tune down if FloodWait hits.
TG_TRANSFER_WORKERS = int(os.environ.get("TG_TRANSFER_WORKERS", 6))

# Per-chunk DOWNLOAD connections. Each browser chunk request downloads one Telegram
# message; this many parallel part-transfers are opened *within* that single message's
# download. Raised to 4 for speed — the browser already bounds total concurrency via
# fetchAndDecrypt's CONC, so total Telegram connections stay in a sane range.
TG_DOWNLOAD_WORKERS = int(os.environ.get("TG_DOWNLOAD_WORKERS", 4))


def is_configured() -> bool:
    """Check if Telegram credentials are present (never log the hash)."""
    return bool(API_ID and API_HASH and (SESSION_NAME or TG_SESSION_STRING))


def _make_client() -> TelegramClient:
    """Create a fresh Telethon client (session file caches auth state)."""
    if not is_configured():
        raise RuntimeError("Telegram not configured — set TG_API_ID and TG_API_HASH in .env")
    if TG_SESSION_STRING:
        try:
            from telethon.sessions import StringSession
            return TelegramClient(StringSession(TG_SESSION_STRING), API_ID, API_HASH)
        except Exception as e:
            print(f"[WARN] TG_SESSION_STRING invalid, falling back to file session: {e}")
    try:
        return TelegramClient(SESSION_NAME, API_ID, API_HASH)
    except Exception as e:
        # Vercel's /var/task is read-only — SQLite session open fails with "unable to open database file"
        if "unable to open database file" in str(e).lower() or "readonly" in str(e).lower():
            raise RuntimeError(
                "Telegram session file cannot be opened on Vercel (read-only filesystem). "
                "Set TG_SESSION_STRING env var from your local session.session via StringSession and redeploy."
            ) from e
        raise


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# NOTE: we intentionally create a fresh Telethon client per request and
# disconnect in `finally`. Vercel's serverless Python opens a NEW event loop on
# every invocation, so a cached client would be bound to a closed loop on the
# next request (→ 500). Per-request clients are safe; the parallelism / FloodWait
# tuning below is what actually drives throughput.


def _split_bytes(data: bytes) -> List[bytes]:
    """Split in-memory bytes into chunk_size pieces. Never touches disk."""
    if not data:
        return [b""]
    return [data[i:i + CHUNK_SIZE_BYTES] for i in range(0, len(data), CHUNK_SIZE_BYTES)]


async def _resolve_entity(client, channel_id):
    """Resolve a numeric chat/channel ID to a Telethon entity object.

    Handles common ID format issues:
      - Basic groups:  negative IDs like -123456789
      - Supergroups:   positive IDs like 123456789
      - With prefix:   -100 prefixed like -1001234567890
      - Wrong sign:    stored as -4358806946 but actual channel is 4358806946
    """
    # 1. Try the ID as-is
    try:
        return await client.get_entity(channel_id)
    except (ChatIdInvalidError, ValueError):
        pass

    # 2. If negative, try the positive version (channel stored with wrong sign)
    if isinstance(channel_id, int) and channel_id < 0:
        try:
            return await client.get_entity(abs(channel_id))
        except (ChatIdInvalidError, ValueError):
            pass

        # 3. Try the -100 prefixed format (supergroup URL format)
        try:
            return await client.get_entity(int(f"-100{abs(channel_id)}"))
        except (ChatIdInvalidError, ValueError):
            pass

    raise ChatIdInvalidError(
        request=None,
        message=f"Cannot resolve chat ID {channel_id}. "
                "Make sure the user account has joined the channel/supergroup, "
                "and the ID is correct."
    )


async def _upload_single_chunk(client, entity, chunk_bytes, chunk_index, total_chunks, remote_name):
    """Upload a single chunk to Telegram. Returns message ID.

    Uses the vendored FastTelethon parallel part-transferrer for speed; falls back to
    Telethon's stock send_file if the parallel path raises (e.g. private-API drift).
    """
    if total_chunks > 1:
        name = f"{remote_name}.part{chunk_index + 1}_of_{total_chunks}"
    else:
        name = remote_name
    buf = io.BytesIO(chunk_bytes)
    buf.name = name
    try:
        from fast_telethon import upload_file as ft_upload_file
        input_file = await ft_upload_file(
            client, buf, file_size=len(chunk_bytes), connection_count=TG_TRANSFER_WORKERS,
        )
        input_file.name = name
        msg: Message = await client.send_file(entity, input_file, caption=name, force_document=True)
    except Exception as e:
        print(f"[TG] parallel upload failed (chunk {chunk_index}), falling back to send_file: {e}")
        buf.seek(0)
        msg: Message = await client.send_file(entity, buf, caption=name, force_document=True)
    finally:
        buf.close()
    return msg.id


async def _upload_chunks_async(
    file_bytes: bytes,
    remote_name: str,
    channel_id: int,
    progress_callback=None,
) -> List[int]:
    """
    Upload in-memory (already-encrypted) bytes to the org's Telegram channel,
    chunked if necessary. No disk writes at any point.

    For files > CHUNK_SIZE_BYTES (~1.9GB), splits into sequential parts.
    Supports concurrent chunk uploads via CONCURRENT_CHUNKS env var.

    Args:
        file_bytes: Full encrypted file bytes
        remote_name: Original filename for captions
        channel_id: Telegram channel to upload to
        progress_callback: Optional callback for progress tracking

    Returns:
        List of Telegram message IDs (ordered, one per chunk)
    """
    client = _make_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError(
            "Telethon session not authorized. Run the one-time login "
            "flow to generate session_name.session before using this module."
        )

    try:
        entity = await _resolve_entity(client, channel_id)
        chunks = _split_bytes(file_bytes)
        total_chunks = len(chunks)

        if total_chunks == 1:
            msg_id = await _upload_single_chunk(client, entity, chunks[0], 0, 1, remote_name)
            if progress_callback:
                progress_callback(1, 1)
            return [msg_id]

        if CONCURRENT_CHUNKS <= 1:
            # Sequential upload — safest, lowest memory pressure
            message_ids = []
            for i, chunk in enumerate(chunks):
                msg_id = await _upload_single_chunk(client, entity, chunk, i, total_chunks, remote_name)
                message_ids.append(msg_id)
                if progress_callback:
                    progress_callback(i + 1, total_chunks)
                # Release chunk memory immediately
                chunks[i] = None
            return message_ids
        else:
            # Concurrent upload — faster but uses more bandwidth/memory
            sem = asyncio.Semaphore(CONCURRENT_CHUNKS)

            async def _upload_with_sem(idx, chunk):
                async with sem:
                    msg_id = await _upload_single_chunk(client, entity, chunk, idx, total_chunks, remote_name)
                    if progress_callback:
                        progress_callback(idx + 1, total_chunks)
                    return msg_id

            tasks = [_upload_with_sem(i, chunk) for i, chunk in enumerate(chunks)]
            message_ids = await asyncio.gather(*tasks)
            return list(message_ids)
    finally:
        await client.disconnect()


def upload_chunks(
    file_bytes: bytes,
    remote_name: str,
    channel_id: int,
    progress_callback=None,
) -> List[int]:
    """Sync wrapper — safe to call from Flask routes."""
    return asyncio.run(_upload_chunks_async(file_bytes, remote_name, channel_id, progress_callback))


async def _upload_chunks_streaming_async(
    file_stream,
    remote_name: str,
    channel_id: int,
    progress_callback=None,
) -> List[int]:
    """
    Upload from a readable stream, chunking into ~CHUNK_SIZE_BYTES pieces.
    Peak memory stays near CHUNK_SIZE_BYTES instead of the full file size.

    Uses a fresh per-request client and retries each chunk through Telegram
    FloodWait with backoff.
    """
    client = _make_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError(
            "Telethon session not authorized. Run the one-time login "
            "flow to generate session_name.session before using this module."
        )
    try:
        entity = await _resolve_entity(client, channel_id)
        message_ids = []
        chunk_index = 0
        while True:
            chunk = file_stream.read(CHUNK_SIZE_BYTES)
            if not chunk:
                break
            total_known = None
            if hasattr(file_stream, 'seek') and hasattr(file_stream, 'tell'):
                try:
                    pos = file_stream.tell()
                    file_stream.seek(0, 2)
                    total_known = file_stream.tell()
                    file_stream.seek(pos)
                except Exception:
                    pass
            total_chunks_est = (total_known // CHUNK_SIZE_BYTES + 1) if total_known else chunk_index + 2
            # Retry the chunk through FloodWait / transient errors (same client).
            last_err = None
            for attempt in range(1, 5):
                try:
                    msg_id = await _upload_single_chunk(client, entity, chunk, chunk_index, total_chunks_est, remote_name)
                    message_ids.append(msg_id)
                    break
                except FloodWaitError as e:
                    last_err = e
                    wait = min(int(e.seconds), 30)
                    print(f"[TG] Upload FloodWait ({wait}s) on chunk {chunk_index}, retry {attempt}/4")
                    await asyncio.sleep(wait)
                except Exception as e:
                    last_err = e
                    print(f"[TG] Upload chunk {chunk_index} error (attempt {attempt}/4): {type(e).__name__}: {e}")
                    await asyncio.sleep(min(attempt, 3))
            else:
                raise last_err or RuntimeError(f"Failed to upload chunk {chunk_index}")
            chunk_index += 1
            if progress_callback:
                if total_known:
                    progress_callback(min(chunk_index * CHUNK_SIZE_BYTES, total_known), total_known)
                else:
                    progress_callback(chunk_index, total_chunks_est)
            del chunk
        print(f"[TG] Upload complete. {len(message_ids)} chunk(s) sent.")
        return message_ids
    finally:
        await client.disconnect()


def upload_chunks_streaming(
    file_stream,
    remote_name: str,
    channel_id: int,
    progress_callback=None,
) -> List[int]:
    """Sync streaming wrapper — reads from a file-like stream in CHUNK_SIZE_BYTES pieces."""
    return asyncio.run(_upload_chunks_streaming_async(file_stream, remote_name, channel_id, progress_callback))


async def _download_chunks_async(
    channel_id: int,
    message_ids: List[int],
    progress_callback=None,
) -> bytes:
    """
    Reassemble a file from its ordered chunk message_ids entirely in memory
    and return the raw (still-encrypted) bytes.

    Chunks are downloaded in order and concatenated. For very large files,
    this uses an in-memory buffer — caller should process promptly.
    """
    client = _make_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("Telethon session not authorized.")
    try:
        entity = await _resolve_entity(client, channel_id)
        print(f"[TG] Downloading {len(message_ids)} chunk(s)...")
        assembled = io.BytesIO()
        for i, msg_id in enumerate(message_ids):
            msg = await client.get_messages(entity, ids=msg_id)
            chunk_buf = io.BytesIO()
            try:
                from fast_telethon import download_file as ft_download_file
                await ft_download_file(client, msg.document, chunk_buf,
                                       connection_count=TG_DOWNLOAD_WORKERS, progress_callback=progress_callback)
            except Exception as e:
                print(f"[TG] parallel download failed (chunk {i}), falling back to download_media: {e}")
                chunk_buf.seek(0)
                chunk_buf.truncate(0)
                await client.download_media(msg, file=chunk_buf, progress_callback=progress_callback)
            assembled.write(chunk_buf.getvalue())
            print(f"[TG] Chunk {i + 1}/{len(message_ids)} downloaded")
            chunk_buf.close()

        data = assembled.getvalue()
        assembled.close()
        print(f"[TG] Download complete. Total {len(data)} bytes.")
        return data
    finally:
        await client.disconnect()


def download_chunks(
    channel_id: int,
    message_ids: List[int],
    progress_callback=None,
) -> bytes:
    """Sync wrapper — safe to call from Flask routes."""
    return asyncio.run(_download_chunks_async(channel_id, message_ids, progress_callback))


# ---------------------------------------------------------------------------
# Streaming download — yields one chunk at a time so the caller
# (Flask response generator) can stream bytes to the client without
# ever holding the entire file in memory.
# ---------------------------------------------------------------------------

async def _download_chunks_streaming_async(channel_id, message_ids, progress_callback=None):
    """Async generator — yields each chunk as bytes, in order.

    Uses a fresh per-request client and retries each chunk through Telegram
    FloodWait with backoff.
    """
    client = _make_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("Telethon session not authorized.")
    try:
        entity = await _resolve_entity(client, channel_id)
        print(f"[TG] Downloading {len(message_ids)} chunk(s) from channel...")
        for i, msg_id in enumerate(message_ids):
            print(f"[TG] Downloading chunk {i + 1}/{len(message_ids)} (msg_id={msg_id})...")
            chunk_buf = io.BytesIO()
            last_err = None
            for attempt in range(1, 5):
                try:
                    msg = await client.get_messages(entity, ids=msg_id)
                    if msg is None or getattr(msg, "document", None) is None:
                        raise ValueError(f"Message {msg_id} has no document")
                    try:
                        from fast_telethon import download_file as ft_download_file
                        await ft_download_file(client, msg.document, chunk_buf,
                                               connection_count=TG_DOWNLOAD_WORKERS, progress_callback=progress_callback)
                    except Exception as e:
                        print(f"[TG] parallel download failed (chunk {i}), falling back to download_media: {e}")
                        chunk_buf.seek(0)
                        chunk_buf.truncate(0)
                        await client.download_media(msg, file=chunk_buf, progress_callback=progress_callback)
                    break
                except FloodWaitError as e:
                    last_err = e
                    wait = min(int(e.seconds), 30)
                    print(f"[TG] FloodWait ({wait}s) on chunk {i}, retry {attempt}/4")
                    await asyncio.sleep(wait)
                except Exception as e:
                    last_err = e
                    print(f"[TG] Download chunk {i} error (attempt {attempt}/4): {type(e).__name__}: {e}")
                    await asyncio.sleep(min(attempt, 3))
            else:
                raise last_err or RuntimeError(f"Failed to download chunk {i}")
            data = chunk_buf.getvalue()
            print(f"[TG] Chunk {i + 1} downloaded ({len(data)} bytes)")
            yield data
            chunk_buf.close()
        print(f"[TG] Download complete.")
    finally:
        await client.disconnect()


def download_chunks_streaming(channel_id: int, message_ids: List[int]):
    """
    Sync generator — yields one chunk (bytes) at a time.
    Use inside a Flask streaming response:

        def generate():
            for chunk in download_chunks_streaming(chat_id, message_ids):
                yield chunk
    """
    loop = asyncio.new_event_loop()
    try:
        gen = _download_chunks_streaming_async(channel_id, message_ids)
        while True:
            try:
                chunk = loop.run_until_complete(gen.__anext__())
                yield chunk
            except StopAsyncIteration:
                break
    finally:
        loop.close()


async def delete_file(channel_id: int, message_ids: List[int]) -> None:
    """Permanently delete all chunk messages for a file version (used on version purge / trash destroy)."""
    client = _make_client()
    await client.connect()
    try:
        entity = await _resolve_entity(client, channel_id)
        await client.delete_messages(entity, message_ids)
    finally:
        await client.disconnect()


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
    client = _make_client()
    await client.connect()
    try:
        entity = await _resolve_entity(client, channel_id)
        backup_entity = await _resolve_entity(client, backup_channel_id)
        forwarded = await client.forward_messages(backup_entity, message_ids, entity)
        if isinstance(forwarded, Message):
            forwarded = [forwarded]
        return [m.id for m in forwarded]
    finally:
        await client.disconnect()


async def _create_backup_channel_async(title: str) -> int:
    """Create a new private Telegram channel for org backups. Returns the channel ID.
    
    The creator (userbot) automatically becomes admin. No local storage of channel info.
    """
    if not is_configured():
        raise RuntimeError("Telegram not configured — set TG_API_ID, TG_API_HASH, and session")
    client = _make_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("Telethon session not authorized")
    try:
        result = await client(CreateChannelRequest(
            title=title,
            about=f"Backup channel for {title}",
            megagroup=False
        ))
        chat = result.chats[0]
        raw_id = int(chat.id)
        return raw_id
    finally:
        await client.disconnect()


def create_backup_channel(title: str) -> int:
    """Sync wrapper — creates a private backup channel and returns its raw integer ID."""
    return asyncio.run(_create_backup_channel_async(title))


# ---------------------------------------------------------------------------
# One-time interactive login helper — run manually once to create
# session_name.session. This .session file is the one unavoidable local
# artifact — it's Telethon's own auth credential, required to connect at all.
# ---------------------------------------------------------------------------
async def _interactive_login():
    client = _make_client()
    await client.start()  # prompts for phone/code/2FA in terminal
    print("Login successful. Session saved to:", f"{SESSION_NAME}.session")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(_interactive_login())
