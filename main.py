import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import TOKEN, sos_removal_tasks, sos_update_tasks, break_tasks, support_tickets
from handlers import router

async def main():
    # Initialize bot and dispatcher
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Include handlers
    dp.include_router(router)

    try:
        # Start polling
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        # Отменяем все активные задачи при завершении
        for task in sos_removal_tasks.values():
            if not task.done():
                task.cancel()
        for task in sos_update_tasks.values():
            if not task.done():
                task.cancel()
        # Отменяем задачи перерывов
        for break_tasks_list in break_tasks.values():
            for task in break_tasks_list:
                if not task.done():
                    task.cancel()
        logging.info("Бот остановлен")
    finally:
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())