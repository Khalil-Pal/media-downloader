"""
generate_session.py – Run once locally to generate a Telethon session string.
Copy the output into your .env as TELETHON_SESSION=...
"""
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv
import os

load_dotenv()

API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
PHONE = os.getenv("PHONE", "")


async def main():
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.start(phone=PHONE)
    session_string = client.session.save()
    print("\n✅ Your session string (copy this into .env):\n")
    print(f"TELETHON_SESSION={session_string}\n")
    await client.disconnect()


asyncio.run(main())