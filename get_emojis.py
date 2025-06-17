import os
import asyncio
from telegram import Bot
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')

async def get_available_emojis():
    bot = Bot(token=TOKEN)
    try:
        stickers = await bot.get_forum_topic_icon_stickers()
        print("\nДоступные эмодзи для иконок тем:")
        print("-" * 50)
        for sticker in stickers:
            print(f"Эмодзи: {sticker.emoji}")
            print(f"ID: {sticker.file_id}")
            print("-" * 50)
    except Exception as e:
        print(f"Ошибка при получении списка эмодзи: {str(e)}")
    finally:
        await bot.close()

if __name__ == '__main__':
    asyncio.run(get_available_emojis()) 