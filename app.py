"""
TeamVault — small-team private file storage backed by Telegram.

SETUP
  1. Create a Telegram bot via @BotFather and get the bot token.
  2. Create a Telegram channel (or group) and add the bot as admin.
  3. Get the channel ID (use @userinfobot or check channel info).
  4. Export environment variables:
       export TELEGRAM_BOT_TOKEN="your_bot_token"
       export TELEGRAM_CHANNEL_ID="your_channel_id"

  5. pip install flask requests cryptography python-dotenv

  6. python app.py

  7. First visit shows registration page to create organization admin.
"""

import os
import io
import sqlite3
import time
import secrets
import hashlib
import requests
from functools import wraps
from flask import Flask, request, jsonify, session, g, send_from_directory
from dotenv import load_dotenv
from datetime import datetime
from telethon import TelegramClient
import asyncio

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Telegram API credentials
api_id = os.getenv('TELEGRAM_API_ID')
api_hash = os.getenv('TELEGRAM_API_HASH')

# Ensure API credentials are set in .env.local
# Example values:
# TELEGRAM_API_ID=1234567
# TELEGRAM_API_HASH='abcdef1234567890abcdef1234567890'

client = TelegramClient('session_name', api_id, api_hash)
await client.start()

# Update file handling logic
@client.on(events.NewMessage)
async def handle_file(message):
    file_handler = FileHandler()
    file_handler.start_timer()

    if message.media:
        file = await message.download_media()
        encrypted_data = encrypt(file)
        duration = file_handler.get_duration()
        # Store metrics in Supabase
        supabase.table('file_metrics').insert({
            'file_size': len(file),
            'encryption_time': duration,
            'upload_time': datetime.now().isoformat()
        }).execute()

        # Send encrypted file
        await client.send_file(os.getenv('TELEGRAM_CHANNEL_ID'), encrypted_data)

# Add download handling timing
@client.on(events.NewMessage)
async def handle_download(message):
    file_handler = FileHandler()
    file_handler.start_timer()

    if message.media:
        file = await message.download_media()
        decrypted_data = decrypt(file)
        duration = file_handler.get_duration()
        print(f"File decrypted in {duration:.2f}s")

        # Process decrypted file
        # (Add your logic here for storage or processing)

        # Update Supabase metrics for downloads (similar to uploads)
        supabase.table('file_metrics').insert({
            'file_size': len(file),
            'decryption_time': duration,
            'download_time': datetime.now().isoformat()
        }).execute()

# Add similar timing for download handling

async def main():
    # Initialize Telegram client
    api_id = os.getenv('TELEGRAM_API_ID')
    api_hash = os.getenv('TELEGRAM_API_HASH')
    client = TelegramClient('session_name', api_id, api_hash)
    await client.start()  # Moved await inside async function

    # ...existing event handlers...

# Run the async main function
if __name__ == '__main__':
    asyncio.run(main())
