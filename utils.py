
#hello

import sqlite3
import asyncio
import logging
import time
from datetime import datetime, time as time_obj
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import (
    topics_dict, active_topics, active_topics_info, sos_activation_times,
    sos_removal_tasks, sos_update_tasks, workers_dict, ADMIN_ID, KYIV_TZ
)

# Database functions
def init_pc_database():
    conn = sqlite3.connect('pc_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pc_list (
            id INTEGER PRIMARY KEY,
            is_available BOOLEAN DEFAULT TRUE
        )
    ''')
    conn.commit()
    conn.close()

def get_available_pcs():
    """Получить список доступных ПК"""
    conn = sqlite3.connect('pc_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM pc_list WHERE is_available = TRUE ORDER BY id')
    result = [row[0] for row in cursor.fetchall()]
    conn.close()
    return result

def take_pc(pc_id):
    """Занять ПК"""
    conn = sqlite3.connect('pc_database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE pc_list SET is_available = FALSE WHERE id = ?', (pc_id,))
    conn.commit()
    conn.close()

def release_pc(pc_id):
    """Освободить ПК"""
    conn = sqlite3.connect('pc_database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE pc_list SET is_available = TRUE WHERE id = ?', (pc_id,))
    conn.commit()
    conn.close()

def add_pcs(count):
    """Добавить ПК в базу"""
    conn = sqlite3.connect('pc_database.db')
    cursor = conn.cursor()
    for i in range(1, count + 1):
        cursor.execute('INSERT OR IGNORE INTO pc_list (id, is_available) VALUES (?, TRUE)', (i,))
    conn.commit()
    conn.close()

def clear_all_pcs():
    """Очистить все ПК"""
    conn = sqlite3.connect('pc_database.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM pc_list')
    conn.commit()
    conn.close()

# Helper functions
async def check_forum_support(chat_id: int, bot: Bot) -> bool:
    try:
        chat = await bot.get_chat(chat_id)
        return chat.is_forum
    except Exception:
        return False

async def is_admin(chat_id: int, user_id: int, bot: Bot) -> bool:
    try:
        # Проверяем, является ли пользователь главным админом
        if user_id == ADMIN_ID:
            return True

        # Проверяем, есть ли пользователь в списке дополнительных админов
        from config import admin_list
        if user_id in admin_list:
            return True

        # Проверяем статус в чате
        chat_member = await bot.get_chat_member(chat_id, user_id)
        return chat_member.status in ['creator', 'administrator']
    except Exception:
        return False

def clear_rename_context(data: dict):
    """Очищает контекст переименования"""
    data.pop('current_rename_topic', None)
    data.pop('confirmation_message_id', None)
    data.pop('request_name_message_id', None)
    data.pop('pc_selection_message_id', None)
    data.pop('topic_name', None)
    data.pop('waiting_for_rename', None)

async def create_active_topics_thread(chat_id: int, bot: Bot):
    """Создает топик 'Активные темы' если его нет"""
    try:
        # Проверяем, есть ли уже такой топик
        if chat_id in active_topics_info:
            return active_topics_info[chat_id]['topic_id']

        # Создаем новый топик
        topic = await bot.create_forum_topic(
            chat_id=chat_id,
            name="Активные темы"
        )

        topic_id = topic.message_thread_id

        # Отправляем первое сообщение
        message = await bot.send_message(
            chat_id=chat_id,
            message_thread_id=topic_id,
            text="Темы в которых нужен номер:\n(пока нет активных тем)"
        )

        # Сохраняем информацию
        active_topics_info[chat_id] = {
            'topic_id': topic_id,
            'message_id': message.message_id
        }

        # Добавляем в общий словарь тем
        if chat_id not in topics_dict:
            topics_dict[chat_id] = {}
        topics_dict[chat_id][topic_id] = "Активные темы"

        logging.info(f"Создан топик 'Активные темы' с ID {topic_id}")
        return topic_id

    except Exception as e:
        logging.error(f"Ошибка при создании топика 'Активные темы': {str(e)}")
        return None

async def update_active_topics_message(chat_id: int, bot: Bot):
    """Обновляет сообщение в топике 'Активные темы'"""
    try:
        # Проверяем, есть ли топик "Активные темы"
        if chat_id not in active_topics_info:
            await create_active_topics_thread(chat_id, bot)

        if chat_id not in active_topics_info:
            return

        topic_id = active_topics_info[chat_id]['topic_id']
        message_id = active_topics_info[chat_id]['message_id']

        # Формируем текст сообщения
        if chat_id in active_topics and active_topics[chat_id]:
            text = "Темы в которых нужен номер:\n"
            chat = await bot.get_chat(chat_id)

            for active_topic_id in active_topics[chat_id]:
                topic_name = topics_dict.get(chat_id, {}).get(active_topic_id, f"Тема {active_topic_id}")

                # Вычисляем время простоя
                activation_time = sos_activation_times.get((chat_id, active_topic_id))
                if activation_time:
                    elapsed_seconds = int(time.time() - activation_time)
                    if elapsed_seconds < 60:
                        time_str = f"({elapsed_seconds} секунд простой)"
                    else:
                        minutes = elapsed_seconds // 60
                        seconds = elapsed_seconds % 60
                        if seconds > 0:
                            time_str = f"({minutes} минут {seconds} секунд простой)"
                        else:
                            time_str = f"({minutes} минут простой)"
                else:
                    time_str = "(время неизвестно)"

                # Создаем ссылку на топик
                if chat.username:
                    link = f"https://t.me/{chat.username}/{active_topic_id}"
                else:
                    # Для приватных чатов используем другой формат
                    link = f"https://t.me/c/{str(chat_id)[4:]}/{active_topic_id}"

                text += f"• {topic_name} {time_str} - {link}\n"
        else:
            text = "Темы в которых нужен номер:\n(пока нет активных тем)"

        # Обновляем сообщение
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            disable_web_page_preview=True
        )

        logging.info(f"Обновлено сообщение в топике 'Активные темы' для чата {chat_id}")

    except Exception as e:
        logging.error(f"Ошибка при обновлении сообщения 'Активные темы': {str(e)}")

async def auto_remove_sos(chat_id: int, topic_id: int, bot: Bot):
    """Автоматически снимает SOS через 5 минут"""
    try:
        await asyncio.sleep(300)  # 5 минут = 300 секунд

        # Проверяем, активен ли ещё SOS
        if (chat_id in active_topics and 
            topic_id in active_topics[chat_id]):

            logging.info(f"Автоматическое снятие SOS для темы {topic_id} в чате {chat_id}")

            # Убираем тему из активных
            active_topics[chat_id].remove(topic_id)
            if not active_topics[chat_id]:
                del active_topics[chat_id]

            # Убираем время активации
            if (chat_id, topic_id) in sos_activation_times:
                del sos_activation_times[(chat_id, topic_id)]

            # Обновляем сообщение в активных темах
            await update_active_topics_message(chat_id, bot)

        # Убираем задачу из словаря
        if (chat_id, topic_id) in sos_removal_tasks:
            del sos_removal_tasks[(chat_id, topic_id)]

    except asyncio.CancelledError:
        logging.info(f"Задача автоснятия SOS отменена для темы {topic_id}")
    except Exception as e:
        logging.error(f"Ошибка в auto_remove_sos: {str(e)}")

async def update_sos_times(chat_id: int, bot: Bot):
    """Обновляет время простоя каждые 30 секунд"""
    try:
        while chat_id in active_topics and active_topics[chat_id]:
            await asyncio.sleep(30)  # Обновляем каждые 30 секунд
            if chat_id in active_topics and active_topics[chat_id]:
                await update_active_topics_message(chat_id, bot)

        # Убираем задачу из словаря когда нет активных тем
        if chat_id in sos_update_tasks:
            del sos_update_tasks[chat_id]

    except asyncio.CancelledError:
        logging.info(f"Задача обновления времени SOS отменена для чата {chat_id}")
    except Exception as e:
        logging.error(f"Ошибка в update_sos_times: {str(e)}")

async def schedule_break_notification(time_str: str, message_text: str, bot: Bot):
    """Планировать уведомление о перерыве"""
    try:
        while True:
            # Получаем текущее время в Киеве
            now = datetime.now(KYIV_TZ)
            target_time = datetime.strptime(time_str, "%H:%M").time()

            # Создаем datetime для сегодняшнего целевого времени
            today_target = datetime.combine(now.date(), target_time)
            today_target = KYIV_TZ.localize(today_target)

            # Если время уже прошло сегодня, планируем на завтра
            if today_target <= now:
                tomorrow = now.date().replace(day=now.day + 1) if now.day < 28 else now.date().replace(month=now.month + 1, day=1)
                today_target = datetime.combine(tomorrow, target_time)
                today_target = KYIV_TZ.localize(today_target)

            # Вычисляем время ожидания
            wait_seconds = (today_target - now).total_seconds()

            # Ждем до целевого времени
            await asyncio.sleep(wait_seconds)

            # Отправляем уведомления во все топики
            await send_break_notification_to_all_topics(message_text, bot)

            # Ждем до следующего дня (24 часа - небольшой буфер)
            await asyncio.sleep(24 * 60 * 60 - 60)

    except asyncio.CancelledError:
        logging.info(f"Задача уведомления о перерыве в {time_str} отменена")
    except Exception as e:
        logging.error(f"Ошибка в планировщике перерыва: {str(e)}")

async def send_break_notification_to_all_topics(message_text: str, bot: Bot):
    """Отправить уведомление о перерыве во все топики"""
    try:
        for chat_id, topics in topics_dict.items():
            for topic_id in topics.keys():
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        message_thread_id=topic_id,
                        text=f"☕ {message_text}"
                    )
                    # Небольшая задержка между отправками
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logging.error(f"Ошибка при отправке уведомления о перерыве в топик {topic_id}: {str(e)}")

        logging.info(f"Уведомление о перерыве отправлено во все топики: {message_text}")

    except Exception as e:
        logging.error(f"Ошибка при отправке уведомлений о перерыве: {str(e)}")

async def schedule_break_tasks(break_id: int, break_data: dict, bot: Bot):
    """Планировать задачи для перерыва"""
    from config import break_tasks
    
    if break_id not in break_tasks:
        break_tasks[break_id] = []

    # Создаем задачи для начала и окончания перерыва
    start_task = asyncio.create_task(
        schedule_break_notification(break_data['start_time'], break_data['start_text'], bot)
    )
    end_task = asyncio.create_task(
        schedule_break_notification(break_data['end_time'], break_data['end_text'], bot)
    )

    break_tasks[break_id] = [start_task, end_task]

# Initialize PC database
init_pc_database()
