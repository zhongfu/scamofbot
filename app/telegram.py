from telethon import TelegramClient, events
from config import TG_SESSION, TG_API_ID, TG_API_HASH, TG_API_TOKEN

client = TelegramClient(TG_SESSION, TG_API_ID, TG_API_HASH)
client.parse_mode = 'html'

async def tg_start():
    await client.start(bot_token=TG_API_TOKEN)

async def tg_stop():
    await client.disconnect()
