# Vendored + patched from mautrix-telegram parallel_file_transfer.py
# Copyright (C) 2021 Tulir Asokan - MIT license (mautrix-telegram).
# Patches for this project:
#   * upload_file()/download_file() accept an explicit `file_size` (in-memory BytesIO has no
#     real filesystem path, so we must NOT call os.path.getsize) and a `connection_count`
#     override so that small (<100MB) chunks still transfer over several parallel MTProto
#     connections.
#   * ZERO LOCAL STORAGE: nothing here ever writes to disk.
import asyncio
import hashlib
import inspect
import logging
import math
import os
from collections import defaultdict
from typing import Optional, List, AsyncGenerator, Union, Awaitable, DefaultDict, Tuple, BinaryIO

from telethon import utils, helpers, TelegramClient
from telethon.crypto import AuthKey
from telethon.network import MTProtoSender
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.auth import ExportAuthorizationRequest, ImportAuthorizationRequest
from telethon.tl.functions.upload import (GetFileRequest, SaveFilePartRequest, SaveBigFilePartRequest)
from telethon.tl.types import (Document, InputFileLocation, InputDocumentFileLocation,
                               InputPeerPhotoFileLocation, InputPhotoFileLocation, TypeInputFile,
                               InputFileBig, InputFile)

log = logging.getLogger("telethon")
TypeLocation = Union[Document, InputDocumentFileLocation, InputPeerPhotoFileLocation,
                     InputFileLocation, InputPhotoFileLocation]


class DownloadSender:
    def __init__(self, client, sender, file, offset, limit, stride, count):
        self.sender = sender
        self.client = client
        self.request = GetFileRequest(file, offset=offset, limit=limit)
        self.stride = stride
        self.remaining = count

    async def next(self):
        if not self.remaining:
            return None
        result = await self.client._call(self.sender, self.request)
        self.remaining -= 1
        self.request.offset += self.stride
        return result.bytes

    def disconnect(self):
        return self.sender.disconnect()


class UploadSender:
    def __init__(self, client, sender, file_id, part_count, big, index, stride, loop):
        self.client = client
        self.sender = sender
        self.part_count = part_count
        self.request = (SaveBigFilePartRequest(file_id, index, part_count, b"")
                        if big else SaveFilePartRequest(file_id, index, b""))
        self.stride = stride
        self.previous = None
        self.loop = loop

    async def next(self, data):
        if self.previous:
            await self.previous
        self.previous = self.loop.create_task(self._next(data))

    async def _next(self, data):
        self.request.bytes = data
        await self.client._call(self.sender, self.request)
        self.request.file_part += self.stride

    async def disconnect(self):
        if self.previous:
            await self.previous
        return await self.sender.disconnect()


class ParallelTransferrer:
    def __init__(self, client, dc_id=None):
        self.client = client
        self.loop = client.loop
        self.dc_id = dc_id or client.session.dc_id
        self.auth_key = (None if (dc_id and client.session.dc_id != dc_id) else client.session.auth_key)
        self.senders = None
        self.upload_ticker = 0

    async def _cleanup(self):
        await asyncio.gather(*[s.disconnect() for s in self.senders])
        self.senders = None

    @staticmethod
    def _get_connection_count(file_size, max_count=20, full_size=100 * 1024 * 1024):
        return max_count if file_size > full_size else math.ceil((file_size / full_size) * max_count)

    async def _init_download(self, connections, file, part_count, part_size):
        minimum, remainder = divmod(part_count, connections)

        def get_part_count():
            nonlocal remainder
            if remainder > 0:
                remainder -= 1
                return minimum + 1
            return minimum

        self.senders = [
            await self._create_download_sender(file, 0, part_size, connections * part_size, get_part_count()),
            *await asyncio.gather(*[
                self._create_download_sender(file, i, part_size, connections * part_size, get_part_count())
                for i in range(1, connections)
            ]),
        ]

    async def _create_download_sender(self, file, index, part_size, stride, part_count):
        return DownloadSender(self.client, await self._create_sender(), file, index * part_size, part_size, stride, part_count)

    async def _init_upload(self, connections, file_id, part_count, big):
        self.senders = [
            await self._create_upload_sender(file_id, part_count, big, 0, connections),
            *await asyncio.gather(*[
                self._create_upload_sender(file_id, part_count, big, i, connections)
                for i in range(1, connections)
            ]),
        ]

    async def _create_upload_sender(self, file_id, part_count, big, index, stride):
        return UploadSender(self.client, await self._create_sender(), file_id, part_count, big, index, stride, loop=self.loop)

    async def _create_sender(self):
        dc = await self.client._get_dc(self.dc_id)
        sender = MTProtoSender(self.auth_key, loggers=self.client._log)
        await sender.connect(self.client._connection(dc.ip_address, dc.port, dc.id,
                                                     loggers=self.client._log, proxy=self.client._proxy))
        if not self.auth_key:
            auth = await self.client(ExportAuthorizationRequest(self.dc_id))
            self.client._init_request.query = ImportAuthorizationRequest(id=auth.id, bytes=auth.bytes)
            await sender.send(InvokeWithLayerRequest(LAYER, self.client._init_request))
            self.auth_key = sender.auth_key
        return sender

    async def init_upload(self, file_id, file_size, part_size_kb=None, connection_count=None):
        connection_count = connection_count or self._get_connection_count(file_size)
        part_size = (part_size_kb or utils.get_appropriated_part_size(file_size)) * 1024
        part_count = (file_size + part_size - 1) // part_size
        is_large = file_size > 10 * 1024 * 1024
        await self._init_upload(connection_count, file_id, part_count, is_large)
        return part_size, part_count, is_large

    async def upload(self, part):
        await self.senders[self.upload_ticker].next(part)
        self.upload_ticker = (self.upload_ticker + 1) % len(self.senders)

    async def finish_upload(self):
        await self._cleanup()

    async def download(self, file, file_size, part_size_kb=None, connection_count=None):
        connection_count = connection_count or self._get_connection_count(file_size)
        part_size = (part_size_kb or utils.get_appropriated_part_size(file_size)) * 1024
        part_count = math.ceil(file_size / part_size)
        await self._init_download(connection_count, file, part_count, part_size)
        part = 0
        while part < part_count:
            tasks = [self.loop.create_task(s.next()) for s in self.senders]
            for task in tasks:
                data = await task
                if not data:
                    break
                yield data
                part += 1
        await self._cleanup()


parallel_transfer_locks = defaultdict(lambda: asyncio.Lock())


def stream_file(file_to_stream, chunk_size=1024):
    while True:
        data_read = file_to_stream.read(chunk_size)
        if not data_read:
            break
        yield data_read


async def _internal_transfer_to_telegram(client, response, progress_callback, file_size=None, connection_count=None):
    file_id = helpers.generate_random_long()
    file_size = file_size or os.path.getsize(response.name)
    hash_md5 = hashlib.md5()
    uploader = ParallelTransferrer(client)
    part_size, part_count, is_large = await uploader.init_upload(file_id, file_size, connection_count=connection_count)
    buffer = bytearray()
    for data in stream_file(response):
        if progress_callback:
            r = progress_callback(response.tell(), file_size)
            if inspect.isawaitable(r):
                await r
        if not is_large:
            hash_md5.update(data)
        if len(buffer) == 0 and len(data) == part_size:
            await uploader.upload(data)
            continue
        new_len = len(buffer) + len(data)
        if new_len >= part_size:
            cutoff = part_size - len(buffer)
            buffer.extend(data[:cutoff])
            await uploader.upload(bytes(buffer))
            buffer.clear()
            buffer.extend(data[cutoff:])
        else:
            buffer.extend(data)
    if len(buffer) > 0:
        await uploader.upload(bytes(buffer))
    await uploader.finish_upload()
    if is_large:
        return InputFileBig(file_id, part_count, "upload"), file_size
    return InputFile(file_id, part_count, "upload", hash_md5.hexdigest()), file_size


async def download_file(client, location, out, progress_callback=None, connection_count=None):
    size = location.size
    dc_id, location = utils.get_input_location(location)
    downloader = ParallelTransferrer(client, dc_id)
    downloaded = downloader.download(location, size, connection_count=connection_count)
    async for x in downloaded:
        out.write(x)
        if progress_callback:
            r = progress_callback(out.tell(), size)
            if inspect.isawaitable(r):
                await r
    return out


async def upload_file(client, file, file_size=None, connection_count=None, progress_callback=None):
    return (await _internal_transfer_to_telegram(
        client, file, progress_callback, file_size=file_size, connection_count=connection_count))[0]
