
import asyncio
import logging
import sqlite3
import time
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramRetryAfter

from config import (
    ADMIN_ID, topics_dict, workers_dict, rename_topics_dict, sos_words,
    active_topics, active_topics_info, sos_activation_times, sos_removal_tasks,
    sos_update_tasks, restricted_topics, admin_list, breaks_dict, break_id_counter,
    break_tasks, pc_mode_enabled, KYIV_TZ
)
from utils import (
    check_forum_support, is_admin, clear_rename_context, create_active_topics_thread,
    update_active_topics_message, auto_remove_sos, update_sos_times, get_available_pcs,
    take_pc, release_pc, add_pcs, clear_all_pcs, schedule_break_tasks
)

router = Router()

class TopicStates(StatesGroup):
    waiting_for_topic_name = State()
    waiting_for_topic_id = State()
    waiting_for_broadcast = State()
    waiting_for_rename_count = State()
    waiting_for_rename = State()
    waiting_for_pc_selection = State()
    waiting_for_break_name = State()
    waiting_for_break_start_time = State()
    waiting_for_break_start_text = State()
    waiting_for_break_end_time = State()
    waiting_for_break_end_text = State()

@router.message(Command("start"))
async def start_command(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Проверяем, является ли пользователь администратором
    is_user_admin = await is_admin(chat_id, user_id, message.bot)

    keyboard = [
        [InlineKeyboardButton(text="Список тем", callback_data='list_topics')],
        [InlineKeyboardButton(text="Создать тему", callback_data='create_topic')],
        [InlineKeyboardButton(text="Удалить тему", callback_data='delete_topic')],
        [InlineKeyboardButton(text="Удалить все темы", callback_data='delete_all_topics')],
        [InlineKeyboardButton(text="Создать рассылку", callback_data='create_broadcast')],
        [InlineKeyboardButton(text="🖌 Создать темы с переименованием", callback_data='create_rename_topics')]
    ]

    # Добавляем кнопку "Перерыв" только для админов
    if is_user_admin:
        keyboard.append([InlineKeyboardButton(text="☕ Перерыв", callback_data='break_menu')])

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    greeting_message = await message.answer("👋")

    # Планируем удаление ладошки через 0.5 секунд
    async def delete_greeting():
        await asyncio.sleep(0.5)
        try:
            await message.bot.delete_message(
                chat_id=greeting_message.chat.id,
                message_id=greeting_message.message_id
            )
        except Exception as e:
            logging.error(f"Ошибка при удалении приветствия: {str(e)}")

    asyncio.create_task(delete_greeting())

    await message.answer(
        'Привет! Я бот для управления темами в группах. Выберите действие:',
        reply_markup=reply_markup
    )

@router.callback_query(F.data.in_(['list_topics', 'create_topic', 'delete_topic', 'delete_all_topics', 
                                   'create_broadcast', 'create_rename_topics', 'break_menu', 'create_break',
                                   'list_breaks', 'back_to_start']))
async def button_handler(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id

    # Проверяем административные права для всех действий кроме confirm_rename и select_pc
    if not callback.data.startswith('confirm_rename_') and not callback.data.startswith('select_pc_') and not callback.data.startswith('occupied_pc_'):
        if not await is_admin(chat_id, user_id, callback.bot):
            await callback.answer("❌ У вас нет прав администратора", show_alert=True)
            return

    await callback.answer()

    if callback.data == 'list_topics':
        await list_topics(callback)
    elif callback.data == 'create_topic':
        await request_topic_name(callback, state)
    elif callback.data == 'delete_topic':
        await request_topic_id(callback, state)
    elif callback.data == 'delete_all_topics':
        await delete_all_topics(callback)
    elif callback.data == 'create_broadcast':
        await request_broadcast_message(callback, state)
    elif callback.data == 'create_rename_topics':
        await request_rename_topics_count(callback, state)
    elif callback.data == 'break_menu':
        await show_break_menu(callback)
    elif callback.data == 'create_break':
        await request_break_name(callback, state)
    elif callback.data == 'list_breaks':
        await list_breaks(callback)
    elif callback.data == 'back_to_start':
        await start_menu_after_back(callback)

@router.callback_query(F.data.startswith('delete_break_'))
async def delete_break_callback(callback: CallbackQuery):
    break_id = int(callback.data.split('_')[2])
    await delete_break(callback, break_id)

@router.callback_query(F.data.startswith('confirm_rename_'))
async def handle_rename_confirmation(callback: CallbackQuery, state: FSMContext):
    topic_id = int(callback.data.split('_')[2])
    chat_id = callback.message.chat.id

    # Проверяем, что тема существует и доступна для переименования
    if (chat_id not in rename_topics_dict or 
        topic_id not in rename_topics_dict[chat_id] or
        chat_id not in topics_dict or
        topic_id not in topics_dict[chat_id]):
        await callback.answer("❌ Эта тема больше не доступна для переименования", show_alert=True)
        return

    # Сохраняем данные темы
    await state.update_data(
        current_rename_topic={
            'chat_id': chat_id,
            'topic_id': topic_id,
            'old_name': topics_dict[chat_id][topic_id]
        },
        waiting_for_rename=True,
        confirmation_message_id=callback.message.message_id
    )

    # Отправляем сообщение с запросом имени
    message = await callback.message.answer(
        "⚠️ Введите имя ответом на сообщение — это необходимо для присвоения названия теме.\n"
        "> ⛔️ Без этого тема останется в статусе «Без названия» и будет неактивной.",
        parse_mode='Markdown'
    )
    
    await state.update_data(request_name_message_id=message.message_id)

    # Отправляем дополнительное сообщение в тему
    await callback.bot.send_message(
        chat_id=chat_id,
        message_thread_id=topic_id,
        text="♻️ До присвоения имени тема закрыта для сообщений (кроме администраторов).\n"
             "🚫 Запрещается занимать более двух тем, вне зависимости от чатов."
    )

    await state.set_state(TopicStates.waiting_for_rename)
    await callback.answer()

@router.callback_query(F.data.startswith('select_pc_'))
async def handle_pc_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора ПК"""
    await callback.answer()

    pc_id = int(callback.data.split('_')[2])
    data = await state.get_data()
    topic_data = data.get('current_rename_topic')
    topic_name = data.get('topic_name')

    if not topic_data or not topic_name:
        await callback.message.edit_text("Ошибка: не найдены данные о теме")
        await state.clear()
        return

    chat_id = topic_data['chat_id']
    topic_id = topic_data['topic_id']
    old_name = topic_data['old_name']

    # Проверяем формат старого имени
    if ":" not in old_name:
        await callback.message.edit_text("Ошибка: неверный формат названия темы")
        await state.clear()
        return

    number = old_name.split(":")[0]

    # Проверяем, что ПК ещё доступен
    conn = sqlite3.connect('pc_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT is_available FROM pc_list WHERE id = ?', (pc_id,))
    result = cursor.fetchone()
    conn.close()

    if not result or not result[0]:
        await callback.answer("❌ Этот ПК уже занят. Выберите другой.", show_alert=True)
        return

    # Занимаем ПК
    take_pc(pc_id)

    # Формируем новое название темы с номером ПК
    final_topic_name = f"{number}:{topic_name} (#ПК{pc_id})"

    try:
        # Переименовываем тему
        await callback.bot.edit_forum_topic(
            chat_id=chat_id,
            message_thread_id=topic_id,
            name=final_topic_name
        )

        # Обновляем словарь тем
        topics_dict[chat_id][topic_id] = final_topic_name
        if chat_id in rename_topics_dict:
            rename_topics_dict[chat_id].discard(topic_id)

        # Удаляем сообщение с выбором ПК
        try:
            pc_selection_message_id = data.get('pc_selection_message_id')
            if pc_selection_message_id:
                await callback.bot.delete_message(
                    chat_id=chat_id,
                    message_id=pc_selection_message_id
                )
        except Exception as e:
            logging.error(f"Ошибка при удалении сообщения выбора ПК: {str(e)}")

        # Отправляем подтверждение
        await callback.bot.send_message(
            chat_id=chat_id,
            message_thread_id=topic_id,
            text=f"✅ Тема успешно переименована: {final_topic_name}\n🖥️ ПК {pc_id} закреплён за вами"
        )

        # Отправляем уведомление в топик
        chat = await callback.bot.get_chat(chat_id)
        if chat.username:
            topic_link = f"https://t.me/{chat.username}/{topic_id}"
        else:
            topic_link = f"https://t.me/c/{str(chat_id)[4:]}/{topic_id}"

        topic_info_message = (
            f"Темы 🆔: {topic_id}\n"
            f"Наименование: {final_topic_name}\n"
            f"ПК: {pc_id}\n"
            f"Ссылка 🔗 на тему: {topic_link}"
        )

        # Отправляем в топик
        await callback.bot.send_message(
            chat_id=chat_id,
            message_thread_id=topic_id,
            text=topic_info_message,
            disable_web_page_preview=True
        )

        # Отправляем главному админу в ЛС (если ADMIN_ID настроен)
        if ADMIN_ID != 0:
            try:
                await callback.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"Новая тема переименована:\n\n{topic_info_message}",
                    disable_web_page_preview=True
                )
            except Exception as e:
                logging.error(f"Не удалось отправить уведомление админу: {str(e)}")

    except Exception as e:
        # Если что-то пошло не так, освобождаем ПК
        release_pc(pc_id)
        await callback.message.edit_text(f"Ошибка при переименовании темы: {str(e)}")

    # Очищаем временные данные
    await state.clear()

@router.callback_query(F.data.startswith('occupied_pc_'))
async def occupied_pc_callback(callback: CallbackQuery):
    pc_id = callback.data.split('_')[2]
    await callback.answer(f"ПК {pc_id} уже занят", show_alert=True)

async def request_topic_name(callback: CallbackQuery, state: FSMContext):
    chat = await callback.bot.get_chat(callback.message.chat.id)

    if chat.type in ['group', 'supergroup']:
        if await check_forum_support(chat.id, callback.bot):
            await callback.message.answer(
                "Пожалуйста, введите название для новой темы:"
            )
            await state.set_state(TopicStates.waiting_for_topic_name)
        else:
            await callback.message.answer(
                "Эта группа не поддерживает создание тем. "
                "Для использования тем группа должна быть настроена как форум. "
                "Обратитесь к администратору группы для настройки."
            )
    else:
        await callback.message.answer(
            "Эта команда доступна только в группах."
        )

@router.message(TopicStates.waiting_for_topic_name)
async def create_topic_with_name(message: Message, state: FSMContext):
    text = message.text
    chat = await message.bot.get_chat(message.chat.id)

    if not await check_forum_support(chat.id, message.bot):
        await message.answer(
            "Эта группа не поддерживает создание тем"
        )
        await state.clear()
        return

    try:
        if text.isdigit():
            count = int(text)
            if count > 10:
                await message.answer("❌ Слишком большое число, создайте не больше 10 тем")
                await state.clear()
                return

            for i in range(1, count + 1):
                topic_name = f"ПК{i}"
                await create_single_topic(chat.id, topic_name, message.bot)
            await message.answer(f"Создано {count} тем")
        else:
            topics = [t.strip() for t in text.split('\n') if t.strip()]
            if len(topics) > 10:
                 await message.answer("❌ Слишком большое число тем, создайте не больше 10 тем")
                 await state.clear()
                 return

            for topic_name in topics:
                await create_single_topic(chat.id, topic_name, message.bot)
            await message.answer(f"Создано {len(topics)} тем")
    except Exception as e:
        await message.answer(f"Ошибка при создании тем: {str(e)}")

    await state.clear()

async def create_single_topic(chat_id: int, topic_name: str, bot: Bot):
    try:
        topic = await bot.create_forum_topic(
            chat_id=chat_id,
            name=topic_name
        )

        if chat_id not in topics_dict:
            topics_dict[chat_id] = {}
        topics_dict[chat_id][topic.message_thread_id] = topic_name
    except Exception as e:
        logging.error(f"Ошибка при создании темы {topic_name}: {str(e)}")

async def request_topic_id(callback: CallbackQuery, state: FSMContext):
    chat = await callback.bot.get_chat(callback.message.chat.id)

    if chat.type in ['group', 'supergroup']:
        if await check_forum_support(chat.id, callback.bot):
            # Показываем список тем с их ID
            if chat.id in topics_dict and topics_dict[chat.id]:
                topics_list = "\n".join([
                    f"- {name} (ID: {topic_id})"
                    for topic_id, name in topics_dict[chat.id].items()
                ])
                await callback.message.answer(
                    f"Список доступных тем:\n{topics_list}\n\n"
                    "Введите ID темы, которую хотите удалить:"
                )
                await state.set_state(TopicStates.waiting_for_topic_id)
            else:
                await callback.message.answer(
                    "В этой группе пока нет тем. "
                    "Сначала создайте тему через кнопку 'Создать тему'."
                )
        else:
            await callback.message.answer(
                "Эта группа не поддерживает темы. "
                "Для использования тем группа должна быть настроена как форум."
            )
    else:
        await callback.message.answer(
            "Эта команда доступна только в группах."
        )

@router.message(TopicStates.waiting_for_topic_id)
async def delete_topic_by_id(message: Message, state: FSMContext):
    topic_id = message.text
    chat = await message.bot.get_chat(message.chat.id)

    try:
        topic_id = int(topic_id)
        if chat.id not in topics_dict or topic_id not in topics_dict[chat.id]:
             await message.answer(
                "Тема с таким ID не найдена. "
                "Используйте команду 'Список тем' для просмотра доступных тем."
            )
             await state.clear()
             return

        topic_name = topics_dict[chat.id][topic_id]
        await message.bot.delete_forum_topic(
            chat_id=chat.id,
            message_thread_id=topic_id
        )
        del topics_dict[chat.id][topic_id]
        await message.answer(
            f"Тема '{topic_name}' успешно удалена!"
        )
    except ValueError:
        await message.answer(
            "Пожалуйста, введите корректный ID темы (число)."
        )
    except Exception as e:
        await message.answer(
            f"Ошибка при удалении темы: {str(e)}"
        )

    await state.clear()

async def list_topics(callback: CallbackQuery):
    try:
        chat = await callback.bot.get_chat(callback.message.chat.id)

        if chat.type not in ['group', 'supergroup']:
            await callback.message.answer(
                "Эта команда доступна только в группах."
            )
            return

        if not chat.is_forum:
            await callback.message.answer(
                "Эта группа не настроена как форум. "
                "Для использования тем группа должна быть настроена как форум."
            )
            return

        if chat.id in topics_dict and topics_dict[chat.id]:
            topics_list = "\n".join([
                f"- {name} (ID: {topic_id})"
                for topic_id, name in topics_dict[chat.id].items()
            ])
            await callback.message.answer(
                f"Список тем в группе {chat.title}:\n{topics_list}"
            )
        else:
            await callback.message.answer(
                "В этой группе пока нет тем. "
                "Создайте новую тему через кнопку 'Создать тему'."
            )

    except Exception as e:
        logging.error(f"Ошибка при работе с группами: {str(e)}")
        await callback.message.answer(
            "Произошла ошибка при работе с группами. "
            "Убедитесь, что бот добавлен в группу как администратор."
        )

async def delete_all_topics(callback: CallbackQuery):
    chat = await callback.bot.get_chat(callback.message.chat.id)

    if chat.type not in ['group', 'supergroup']:
        await callback.message.answer("Эта команда доступна только в группах.")
        return

    if not await check_forum_support(chat.id, callback.bot):
        await callback.message.answer("Эта группа не поддерживает темы")
        return

    if chat.id not in topics_dict or not topics_dict[chat.id]:
        await callback.message.answer("В этой группе нет тем для удаления")
        return

    deleted_count = 0
    for topic_id in list(topics_dict[chat.id].keys()):
        try:
            await callback.bot.delete_forum_topic(
                chat_id=chat.id,
                message_thread_id=topic_id
            )
            deleted_count += 1
        except Exception as e:
            logging.error(f"Ошибка при удалении темы {topic_id}: {str(e)}")

    topics_dict[chat.id] = {}
    if chat.id in workers_dict:
        workers_dict[chat.id] = {}

    await callback.message.answer(f"Успешно удалено {deleted_count} тем")

async def request_broadcast_message(callback: CallbackQuery, state: FSMContext):
    chat = await callback.bot.get_chat(callback.message.chat.id)

    if chat.type not in ['group', 'supergroup']:
        await callback.message.answer("Эта команда доступна только в группах.")
        return

    if not await check_forum_support(chat.id, callback.bot):
        await callback.message.answer("Эта группа не поддерживает темы")
        return

    if chat.id not in topics_dict or not topics_dict[chat.id]:
        await callback.message.answer("В этой группе нет тем для рассылки")
        return

    await callback.message.answer("Введите сообщение для рассылки во все темы:")
    await state.set_state(TopicStates.waiting_for_broadcast)

@router.message(TopicStates.waiting_for_broadcast)
async def send_broadcast(message: Message, state: FSMContext):
    message_text = message.text
    chat = await message.bot.get_chat(message.chat.id)

    if chat.id not in topics_dict or not topics_dict[chat.id]:
        await message.answer("В этой группе нет тем для рассылки")
        await state.clear()
        return

    sent_count = 0
    for topic_id in topics_dict[chat.id].keys():
        try:
            await message.bot.send_message(
                chat_id=chat.id,
                message_thread_id=topic_id,
                text=message_text
            )
            sent_count += 1
        except Exception as e:
            logging.error(f"Ошибка при отправке сообщения в тему {topic_id}: {str(e)}")

    await message.answer(f"Сообщение отправлено в {sent_count} тем")
    await state.clear()

async def request_rename_topics_count(callback: CallbackQuery, state: FSMContext):
    chat = await callback.bot.get_chat(callback.message.chat.id)

    if chat.type not in ['group', 'supergroup']:
        await callback.message.answer("Эта команда доступна только в группах.")
        return

    if not await check_forum_support(chat.id, callback.bot):
        await callback.message.answer("Эта группа не поддерживает темы")
        return

    await callback.message.answer("Введите количество тем для создания:")
    await state.set_state(TopicStates.waiting_for_rename_count)

@router.message(TopicStates.waiting_for_rename_count)
async def create_rename_topics(message: Message, state: FSMContext):
    global break_id_counter
    
    try:
        count = int(message.text)
        if count <= 0:
            await message.answer("Количество тем должно быть положительным числом")
            await state.clear()
            return

        chat = await message.bot.get_chat(message.chat.id)

        if chat.id not in rename_topics_dict:
            rename_topics_dict[chat.id] = set()

        created_count = 0
        for i in range(1, count + 1):
            try:
                topic = await message.bot.create_forum_topic(
                    chat_id=chat.id,
                    name=f"{i}:Без названия"
                )

                if chat.id not in topics_dict:
                    topics_dict[chat.id] = {}
                topics_dict[chat.id][topic.message_thread_id] = f"{i}:Без названия"

                rename_topics_dict[chat.id].add(topic.message_thread_id)

                keyboard = [[InlineKeyboardButton(text="✅", callback_data=f'confirm_rename_{topic.message_thread_id}')]]
                reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

                try:
                    await message.bot.send_message(
                        chat_id=chat.id,
                        message_thread_id=topic.message_thread_id,
                        text="🌏 Чтобы задать название темы, сначала укажите своё имя.\n"
                             "ℹ️ Не забудьте нажать на галочку под сообщением",
                        reply_markup=reply_markup
                    )
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after)
                    await message.bot.send_message(
                        chat_id=chat.id,
                        message_thread_id=topic.message_thread_id,
                        text="🌏 Чтобы задать название темы, сначала укажите своё имя.\n"
                             "ℹ️ Не забудьте нажать на галочку под сообщением",
                        reply_markup=reply_markup
                    )

                created_count += 1

                if i < count:  # Не ждем после создания последней темы
                    await asyncio.sleep(3)  # Задержка между созданием тем

            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                continue
            except Exception as e:
                logging.error(f"Ошибка при создании темы {i}: {str(e)}")

        try:
            await message.answer(f"Создано {created_count} тем для переименования.")
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await message.answer(f"Создано {created_count} тем для переименования.")
    except ValueError:
        await message.answer("Пожалуйста, введите корректное число")
    except Exception as e:
        try:
            await message.answer(f"Ошибка при создании тем: {str(e)}")
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await message.answer(f"Ошибка при создании тем: {str(e)}")

    await state.clear()

@router.message(TopicStates.waiting_for_rename)
async def rename_topic(message: Message, state: FSMContext):
    # Проверяем, что это ответ на сообщение бота
    if not message.reply_to_message:
        return

    data = await state.get_data()
    # Проверяем, что это ответ на сообщение с запросом имени
    request_message_id = data.get('request_name_message_id')
    if not request_message_id or message.reply_to_message.message_id != request_message_id:
        return

    new_name = message.text.strip()
    if not new_name:
        await message.answer("Название не может быть пустым. Введите название темы:")
        return

    topic_data = data.get('current_rename_topic')
    if not topic_data:
        await message.answer("Ошибка: данные о теме не найдены")
        await state.clear()
        return

    # Сохраняем название темы для следующего этапа
    await state.update_data(topic_name=new_name)

    # Проверяем, включен ли режим выбора ПК
    if not pc_mode_enabled:
        # Режим выбора ПК отключен - сразу переименовываем тему без ПК
        chat_id = topic_data['chat_id']
        topic_id = topic_data['topic_id']
        old_name = topic_data['old_name']

        # Проверяем формат старого имени
        if ":" not in old_name:
            await message.answer("Ошибка: неверный формат названия темы")
            await state.clear()
            return

        number = old_name.split(":")[0]
        final_topic_name = f"{number}:{new_name}"

        try:
            # Переименовываем тему
            await message.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=topic_id,
                name=final_topic_name
            )

            # Обновляем словарь тем
            topics_dict[chat_id][topic_id] = final_topic_name
            if chat_id in rename_topics_dict:
                rename_topics_dict[chat_id].discard(topic_id)

            # Удаляем предыдущие сообщения
            try:
                confirmation_message_id = data.get('confirmation_message_id')
                request_name_message_id = data.get('request_name_message_id')
                
                if confirmation_message_id:
                    await message.bot.delete_message(
                        chat_id=chat_id,
                        message_id=confirmation_message_id
                    )
                if request_name_message_id:
                    await message.bot.delete_message(
                        chat_id=chat_id,
                        message_id=request_name_message_id
                    )
            except Exception as e:
                logging.error(f"Ошибка при удалении предыдущих сообщений: {str(e)}")

            # Отправляем подтверждение
            await message.bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text=f"✅ Тема успешно переименована: {final_topic_name}"
            )

            # Отправляем информацию о теме
            chat = await message.bot.get_chat(chat_id)
            if chat.username:
                topic_link = f"https://t.me/{chat.username}/{topic_id}"
            else:
                topic_link = f"https://t.me/c/{str(chat_id)[4:]}/{topic_id}"

            topic_info_message = (
                f"Темы 🆔: {topic_id}\n"
                f"Наименование: {final_topic_name}\n"
                f"Ссылка 🔗 на тему: {topic_link}"
            )

            # Отправляем в топик
            await message.bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text=topic_info_message,
                disable_web_page_preview=True
            )

            # Отправляем главному админу в ЛС (если ADMIN_ID настроен)
            if ADMIN_ID != 0:
                try:
                    await message.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"Новая тема переименована:\n\n{topic_info_message}",
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    logging.error(f"Не удалось отправить уведомление админу: {str(e)}")

        except Exception as e:
            await message.answer(f"Ошибка при переименовании темы: {str(e)}")

        # Очищаем временные данные
        await state.clear()
        return

    # Переходим к выбору ПК (старая логика)
    available_pcs = get_available_pcs()

    if not available_pcs:
        await message.answer("❌ Нет доступных ПК. Обратитесь к администратору.")
        await state.clear()
        return

    # Получаем все ПК из базы данных
    conn = sqlite3.connect('pc_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, is_available FROM pc_list ORDER BY id')
    all_pcs = cursor.fetchall()
    conn.close()

    if not all_pcs:
        await message.answer("❌ ПК не настроены. Обратитесь к администратору.")
        await state.clear()
        return

    # Создаем клавиатуру со всеми ПК
    keyboard = []
    row = []
    for pc_id, is_available in all_pcs:
        if is_available:
            button_text = str(pc_id)
            callback_data = f'select_pc_{pc_id}'
        else:
            button_text = f"{pc_id}❌"
            callback_data = f'occupied_pc_{pc_id}'

        row.append(InlineKeyboardButton(text=button_text, callback_data=callback_data))
        if len(row) == 5:  # По 5 кнопок в ряду
            keyboard.append(row)
            row = []
    if row:  # Добавляем оставшиеся кнопки
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    # Удаляем предыдущие сообщения
    try:
        confirmation_message_id = data.get('confirmation_message_id')
        request_name_message_id = data.get('request_name_message_id')
        
        if confirmation_message_id:
            await message.bot.delete_message(
                chat_id=topic_data['chat_id'],
                message_id=confirmation_message_id
            )
        if request_name_message_id:
            await message.bot.delete_message(
                chat_id=topic_data['chat_id'],
                message_id=request_name_message_id
            )
    except Exception as e:
        logging.error(f"Ошибка при удалении предыдущих сообщений: {str(e)}")

    # Отправляем сообщение с выбором ПК
    pc_selection_message = await message.answer(
        "Выберите ПК по нумерации:\n(наклейка в уголку монитора, или сверху в уголку системного блока)",
        reply_markup=reply_markup
    )

    # Сохраняем ID сообщения для последующего удаления
    await state.update_data(pc_selection_message_id=pc_selection_message.message_id)
    await state.set_state(TopicStates.waiting_for_pc_selection)

# Break management functions
async def show_break_menu(callback: CallbackQuery):
    """Показать меню управления перерывами"""
    breaks_count = len(breaks_dict)

    keyboard = []

    if breaks_count < 5:
        keyboard.append([InlineKeyboardButton(text="➕ Создать перерыв", callback_data='create_break')])

    if breaks_count > 0:
        keyboard.append([InlineKeyboardButton(text="📋 Список перерывов", callback_data='list_breaks')])

    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data='back_to_start')])

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.message.edit_text(
        f"☕ Управление перерывами\n\n"
        f"Активных перерывов: {breaks_count}/5\n\n"
        f"Здесь вы можете создавать автоматические напоминания о перерывах, "
        f"которые будут отправляться во все топики в указанное время.",
        reply_markup=reply_markup
    )

async def request_break_name(callback: CallbackQuery, state: FSMContext):
    """Запросить название перерыва"""
    await callback.message.edit_text(
        "☕ Создание нового перерыва\n\n"
        "Шаг 1/5: Введите название перерыва\n"
        "Пример: Кофе Брейк"
    )

    await state.set_state(TopicStates.waiting_for_break_name)

@router.message(TopicStates.waiting_for_break_name)
async def process_break_name(message: Message, state: FSMContext):
    """Обработать название перерыва"""
    break_name = message.text.strip()

    if len(break_name) > 50:
        await message.answer(
            "❌ Название слишком длинное. Максимум 50 символов.\n"
            "Введите название перерыва:"
        )
        return

    await state.update_data(break_data={'name': break_name})

    await message.answer(
        f"✅ Название сохранено: {break_name}\n\n"
        "Шаг 2/5: Введите время начала перерыва (по Киеву)\n"
        "Формат: ЧЧ:ММ\n"
        "Пример: 10:00"
    )

    await state.set_state(TopicStates.waiting_for_break_start_time)

@router.message(TopicStates.waiting_for_break_start_time)
async def process_break_start_time(message: Message, state: FSMContext):
    """Обработать время начала перерыва"""
    time_text = message.text.strip()

    try:
        # Проверяем формат времени
        time_obj = datetime.strptime(time_text, "%H:%M").time()

        data = await state.get_data()
        break_data = data['break_data']
        break_data['start_time'] = time_text
        await state.update_data(break_data=break_data)

        await message.answer(
            f"✅ Время начала сохранено: {time_text}\n\n"
            "Шаг 3/5: Введите текст сообщения о начале перерыва\n"
            "Пример: Все уходим на кофе брейк!"
        )

        await state.set_state(TopicStates.waiting_for_break_start_text)

    except ValueError:
        await message.answer(
            "❌ Неверный формат времени. Используйте формат ЧЧ:ММ\n"
            "Пример: 10:00\n"
            "Введите время начала перерыва:"
        )

@router.message(TopicStates.waiting_for_break_start_text)
async def process_break_start_text(message: Message, state: FSMContext):
    """Обработать текст начала перерыва"""
    start_text = message.text.strip()

    if len(start_text) > 200:
        await message.answer(
            "❌ Текст слишком длинный. Максимум 200 символов.\n"
            "Введите текст начала перерыва:"
        )
        return

    data = await state.get_data()
    break_data = data['break_data']
    break_data['start_text'] = start_text
    await state.update_data(break_data=break_data)

    await message.answer(
        f"✅ Текст начала сохранен\n\n"
        "Шаг 4/5: Введите время окончания перерыва (по Киеву)\n"
        "Формат: ЧЧ:ММ\n"
        "Пример: 11:00"
    )

    await state.set_state(TopicStates.waiting_for_break_end_time)

@router.message(TopicStates.waiting_for_break_end_time)
async def process_break_end_time(message: Message, state: FSMContext):
    """Обработать время окончания перерыва"""
    time_text = message.text.strip()

    try:
        # Проверяем формат времени
        end_time_obj = datetime.strptime(time_text, "%H:%M").time()
        
        data = await state.get_data()
        start_time_obj = datetime.strptime(data['break_data']['start_time'], "%H:%M").time()

        # Проверяем, что время окончания позже времени начала
        if end_time_obj <= start_time_obj:
            await message.answer(
                "❌ Время окончания должно быть позже времени начала.\n"
                "Введите время окончания перерыва:"
            )
            return

        break_data = data['break_data']
        break_data['end_time'] = time_text
        await state.update_data(break_data=break_data)

        await message.answer(
            f"✅ Время окончания сохранено: {time_text}\n\n"
            "Шаг 5/5: Введите текст сообщения об окончании перерыва\n"
            "Пример: Обратно за работу!"
        )

        await state.set_state(TopicStates.waiting_for_break_end_text)

    except ValueError:
        await message.answer(
            "❌ Неверный формат времени. Используйте формат ЧЧ:ММ\n"
            "Пример: 11:00\n"
            "Введите время окончания перерыва:"
        )

@router.message(TopicStates.waiting_for_break_end_text)
async def process_break_end_text(message: Message, state: FSMContext):
    """Обработать текст окончания перерыва и создать перерыв"""
    global break_id_counter

    end_text = message.text.strip()

    if len(end_text) > 200:
        await message.answer(
            "❌ Текст слишком длинный. Максимум 200 символов.\n"
            "Введите текст окончания перерыва:"
        )
        return

    # Сохраняем перерыв
    data = await state.get_data()
    break_data = data['break_data']
    break_data['end_text'] = end_text

    break_id = break_id_counter
    breaks_dict[break_id] = break_data
    break_id_counter += 1

    # Планируем задачи для перерыва
    await schedule_break_tasks(break_id, break_data, message.bot)

    await message.answer(
        f"✅ Перерыв '{break_data['name']}' успешно создан!\n\n"
        f"📋 Детали:\n"
        f"Название: {break_data['name']}\n"
        f"Начало: {break_data['start_time']} - {break_data['start_text']}\n"
        f"Окончание: {break_data['end_time']} - {break_data['end_text']}\n\n"
        f"Уведомления будут отправляться во все активные топики автоматически."
    )

    await state.clear()

async def list_breaks(callback: CallbackQuery):
    """Показать список перерывов"""
    if not breaks_dict:
        await callback.message.edit_text(
            "📋 Список перерывов пуст\n\n"
            "Создайте первый перерыв, чтобы он появился здесь.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Назад", callback_data='break_menu')
            ]])
        )
        return

    text = "📋 Активные перерывы:\n\n"
    keyboard = []

    for break_id, break_data in breaks_dict.items():
        text += (
            f"🔸 {break_data['name']}\n"
            f"   Начало: {break_data['start_time']}\n"
            f"   Окончание: {break_data['end_time']}\n\n"
        )
        keyboard.append([InlineKeyboardButton(
            text=f"🗑 Удалить '{break_data['name']}'", 
            callback_data=f'delete_break_{break_id}'
        )])

    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data='break_menu')])

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.message.edit_text(text, reply_markup=reply_markup)

async def delete_break(callback: CallbackQuery, break_id: int):
    """Удалить перерыв"""
    if break_id not in breaks_dict:
        await callback.answer("Перерыв не найден!", show_alert=True)
        return

    break_name = breaks_dict[break_id]['name']

    # Отменяем задачи для этого перерыва
    if break_id in break_tasks:
        for task in break_tasks[break_id]:
            if not task.done():
                task.cancel()
        del break_tasks[break_id]

    # Удаляем перерыв
    del breaks_dict[break_id]

    await callback.answer(f"Перерыв '{break_name}' удален!", show_alert=True)

    # Обновляем список перерывов
    await list_breaks(callback)

async def start_menu_after_back(callback: CallbackQuery):
    """Показать стартовое меню после возврата"""
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id

    # Проверяем, является ли пользователь администратором
    is_user_admin = await is_admin(chat_id, user_id, callback.bot)

    keyboard = [
        [InlineKeyboardButton(text="Список тем", callback_data='list_topics')],
        [InlineKeyboardButton(text="Создать тему", callback_data='create_topic')],
        [InlineKeyboardButton(text="Удалить тему", callback_data='delete_topic')],
        [InlineKeyboardButton(text="Удалить все темы", callback_data='delete_all_topics')],
        [InlineKeyboardButton(text="Создать рассылку", callback_data='create_broadcast')],
        [InlineKeyboardButton(text="🖌 Создать темы с переименованием", callback_data='create_rename_topics')]
    ]

    # Добавляем кнопку "Перерыв" только для админов
    if is_user_admin:
        keyboard.append([InlineKeyboardButton(text="☕ Перерыв", callback_data='break_menu')])

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.message.edit_text(
        'Привет! Я бот для управления темами в группах. Выберите действие:',
        reply_markup=reply_markup
    )

# Commands
@router.message(Command("worker"))
async def worker_command(message: Message):
    chat_id = message.chat.id
    message_thread_id = message.message_thread_id

    # Проверяем, что команда используется в теме
    if not message_thread_id:
        await message.answer("Команда /worker должна использоваться в теме")
        return

    # Получаем аргументы из текста сообщения
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if not args:
        await message.answer("Использование: /worker @user1 @user2 ...")
        return

    users = args

    if not users:
        await message.answer("Необходимо указать хотя бы одного пользователя")
        return

    chat = await message.bot.get_chat(chat_id)

    if not await check_forum_support(chat.id, message.bot):
        await message.answer("Эта группа не поддерживает темы")
        return

    # Инициализируем словари если их нет
    if chat_id not in workers_dict:
        workers_dict[chat_id] = {}

    # Добавляем или обновляем воркеров для текущей темы
    if message_thread_id not in workers_dict[chat_id]:
        workers_dict[chat_id][message_thread_id] = []

    # Добавляем новых воркеров к существующим (избегаем дубликатов)
    existing_workers = set(workers_dict[chat_id][message_thread_id])
    new_workers = []

    for user in users:
        if user not in existing_workers:
            workers_dict[chat_id][message_thread_id].append(user)
            new_workers.append(user)

    if new_workers:
        new_workers_text = " ".join(new_workers)
        all_workers_text = " ".join(workers_dict[chat_id][message_thread_id])

        topic_name = topics_dict.get(chat_id, {}).get(message_thread_id, f"Тема {message_thread_id}")

        await message.answer(
            f"✅ Воркеры добавлены в тему '{topic_name}'\n"
            f"Новые воркеры: {new_workers_text}\n"
            f"Все воркеры темы: {all_workers_text}"
        )
    else:
        await message.answer("Все указанные пользователи уже являются воркерами этой темы")

@router.message(Command("only"))
async def only_command(message: Message):
    chat_id = message.chat.id
    message_thread_id = message.message_thread_id
    user_id = message.from_user.id

    # Проверяем, что команда используется в теме
    if not message_thread_id:
        await message.answer("Команда /only должна использоваться в теме")
        return

    # Проверяем, является ли пользователь администратором
    if not await is_admin(chat_id, user_id, message.bot):
        await message.answer("Эта команда доступна только администраторам")
        return

    # Получаем аргументы из текста сообщения
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if not args:
        # Если нет аргументов, показываем текущие ограничения или убираем их
        if (chat_id in restricted_topics and 
            message_thread_id in restricted_topics[chat_id]):
            # Убираем ограничения
            del restricted_topics[chat_id][message_thread_id]
            if not restricted_topics[chat_id]:
                del restricted_topics[chat_id]

            topic_name = topics_dict.get(chat_id, {}).get(message_thread_id, f"Тема {message_thread_id}")
            await message.answer(
                f"🔓 Ограничения доступа к теме '{topic_name}' сняты.\n"
                "Теперь все пользователи могут писать в этой теме."
            )
        else:
            await message.answer(
                "Использование: /only @user1 @user2 ... - ограничить доступ к теме\n"
                "/only - снять ограничения с темы"
            )
        return

    users = args

    # Инициализируем словарь если его нет
    if chat_id not in restricted_topics:
        restricted_topics[chat_id] = {}

    # Устанавливаем ограничения для темы
    restricted_topics[chat_id][message_thread_id] = users

    users_text = " ".join(users)
    topic_name = topics_dict.get(chat_id, {}).get(message_thread_id, f"Тема {message_thread_id}")

    await message.answer(
        f"🔒 Доступ к теме '{topic_name}' ограничен.\n"
        f"Писать могут только: {users_text}\n"
        f"Администраторы могут писать всегда.\n\n"
        f"Для снятия ограничений используйте: /only"
    )

@router.message(Command("new"))
async def new_command(message: Message):
    chat_id = message.chat.id
    message_thread_id = message.message_thread_id
    user_id = message.from_user.id

    # Проверяем, что команда используется в теме
    if not message_thread_id:
        await message.answer("Команда /new должна использоваться в теме")
        return

    # Проверяем, является ли пользователь администратором
    if not await is_admin(chat_id, user_id, message.bot):
        await message.answer("Эта команда доступна только администраторам")
        return

    # Проверяем, есть ли эта тема в словаре тем
    if (chat_id not in topics_dict or 
        message_thread_id not in topics_dict[chat_id]):
        await message.answer("Тема не найдена в базе данных")
        return

    current_name = topics_dict[chat_id][message_thread_id]

    # Проверяем, что это тема с номером (содержит ":")
    if ":" not in current_name:
        await message.answer("Эта команда работает только с пронумерованными темами")
        return

    # Получаем номер темы
    number = current_name.split(":")[0]
    new_name = f"{number}:Без названия"

    try:
        # Переименовываем тему обратно в "Без названия"
        await message.bot.edit_forum_topic(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            name=new_name
        )

        # Обновляем словарь тем
        topics_dict[chat_id][message_thread_id] = new_name

        # Добавляем тему в список тем для переименования
        if chat_id not in rename_topics_dict:
            rename_topics_dict[chat_id] = set()
        rename_topics_dict[chat_id].add(message_thread_id)

        # Создаем новое сообщение с галочкой для переименования
        keyboard = [[InlineKeyboardButton(text="✅", callback_data=f'confirm_rename_{message_thread_id}')]]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        await message.bot.send_message(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text="🌏 Чтобы задать название темы, сначала укажите своё имя.\n"
                 "ℹ️ Не забудьте нажать на галочку под сообщением",
            reply_markup=reply_markup
        )

        await message.answer(f"✅ Тема сброшена в режим переименования: {new_name}")

    except Exception as e:
        await message.answer(f"Ошибка при сбросе темы: {str(e)}")

@router.message(Command("pc"))
async def pc_command(message: Message):
    """Управление списком ПК (только для админов)"""
    global pc_mode_enabled
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Проверяем права администратора
    if not await is_admin(chat_id, user_id, message.bot):
        await message.answer("❌ Эта команда доступна только администраторам")
        return

    # Получаем аргументы из текста сообщения
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if not args:
        # Показываем текущее состояние ПК
        mode_status = "включен" if pc_mode_enabled else "отключен"
        available_pcs = get_available_pcs()
        if available_pcs and pc_mode_enabled:
            pcs_text = ", ".join(map(str, available_pcs))
            await message.answer(
                f"📋 Режим выбора ПК: {mode_status}\n"
                f"Доступные ПК: {pcs_text}\n\n"
                "Использование:\n"
                "/pc <количество> - добавить ПК (например: /pc 20)\n"
                "/pc 0 - отключить режим выбора ПК\n"
                "/pc clear - очистить весь список ПК"
            )
        else:
            await message.answer(
                f"📋 Режим выбора ПК: {mode_status}\n"
                f"Список ПК пуст\n\n"
                "Использование:\n"
                "/pc <количество> - добавить ПК (например: /pc 20)\n"
                "/pc 0 - отключить режим выбора ПК\n"
                "/pc clear - очистить весь список ПК"
            )
        return

    arg = args[0].lower()

    if arg == "clear":
        clear_all_pcs()
        await message.answer("✅ Весь список ПК очищен")
    elif arg == "0":
        pc_mode_enabled = False
        await message.answer("✅ Режим выбора ПК отключен. Теперь при переименовании тем не нужно выбирать ПК")
    else:
        try:
            count = int(arg)
            if count < 0:
                await message.answer("❌ Количество ПК не может быть отрицательным")
                return
            elif count == 0:
                pc_mode_enabled = False
                await message.answer("✅ Режим выбора ПК отключен. Теперь при переименовании тем не нужно выбирать ПК")
                return

            pc_mode_enabled = True
            add_pcs(count)
            available_pcs = get_available_pcs()
            pcs_text = ", ".join(map(str, available_pcs))
            await message.answer(f"✅ Режим выбора ПК включен\nДобавлено ПК до номера {count}\nДоступные ПК: {pcs_text}")
        except ValueError:
            await message.answer("❌ Неверный формат. Используйте: /pc <число>, /pc 0 или /pc clear")

@router.message(Command("gadd"))
async def add_sos_word(message: Message):
    if not await is_admin(message.chat.id, message.from_user.id, message.bot):
        await message.answer("Эта команда доступна только администраторам")
        return

    # Получаем аргументы из текста сообщения
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if not args:
        await message.answer("Использование: /gadd слово")
        return

    word = args[0].lower()
    if word in sos_words:
        await message.answer(f"Слово '{word}' уже есть в списке")
        return

    sos_words.add(word)
    await message.answer(f"Слово '{word}' добавлено в список SOS-слов")

@router.message(Command("gdel"))
async def delete_sos_word(message: Message):
    if not await is_admin(message.chat.id, message.from_user.id, message.bot):
        await message.answer("Эта команда доступна только администраторам")
        return

    # Получаем аргументы из текста сообщения
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if not args:
        await message.answer("Использование: /gdel слово")
        return

    word = args[0].lower()
    if word not in sos_words:
        await message.answer(f"Слово '{word}' не найдено в списке")
        return

    sos_words.remove(word)
    await message.answer(f"Слово '{word}' удалено из списка SOS-слов")

@router.message(Command("gall"))
async def list_sos_words(message: Message):
    if not await is_admin(message.chat.id, message.from_user.id, message.bot):
        await message.answer("Эта команда доступна только администраторам")
        return

    if not sos_words:
        await message.answer("Список SOS-слов пуст")
        return

    words_list = "\n".join(sorted(sos_words))
    await message.answer(f"Список SOS-слов:\n{words_list}")

@router.message(Command("admin"))
async def admin_command(message: Message):
    """Управление списком дополнительных администраторов"""
    user_id = message.from_user.id

    # Отладочная информация
    logging.info(f"admin_command: user_id={user_id}, ADMIN_ID={ADMIN_ID}")

    # Только главный админ может управлять списком администраторов
    if ADMIN_ID == 0:
        await message.answer("❌ ADMIN_ID не настроен. Установите переменную окружения ADMIN_ID")
        return

    if user_id != ADMIN_ID:
        await message.answer(f"❌ Эта команда доступна только главному администратору (ID: {ADMIN_ID})")
        return

    # Получаем аргументы из текста сообщения
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if not args:
        # Показываем текущий список админов
        if admin_list:
            admin_text = "\n".join([f"• {admin_id}" for admin_id in sorted(admin_list)])
            await message.answer(
                f"📋 Список дополнительных администраторов:\n{admin_text}\n\n"
                "Использование:\n"
                "/admin add <user_id|@username> - добавить админа\n"
                "/admin del <user_id> - удалить админа\n"
                "/admin list - показать список"
            )
        else:
            await message.answer(
                "📋 Список дополнительных администраторов пуст\n\n"
                "Использование:\n"
                "/admin add <user_id|@username> - добавить админа\n"
                "/admin del <user_id> - удалить админа\n"
                "/admin list - показать список"
            )
        return

    action = args[0].lower()

    if action == "list":
        if admin_list:
            admin_text = "\n".join([f"• {admin_id}" for admin_id in sorted(admin_list)])
            await message.answer(f"📋 Список дополнительных администраторов:\n{admin_text}")
        else:
            await message.answer("📋 Список дополнительных администраторов пуст")

    elif action == "add":
        if len(args) < 2:
            await message.answer("❌ Укажите user_id или @username: /admin add <user_id>")
            return

        user_input = args[1]
        new_admin_id = None

        # Проверяем, если это тег пользователя (@username)
        if user_input.startswith('@'):
            username = user_input[1:]  # Убираем @
            try:
                # Пытаемся найти пользователя по username в текущем чате
                chat_id = message.chat.id

                # Получаем администраторов чата
                chat_admins = await message.bot.get_chat_administrators(chat_id)
                for admin in chat_admins:
                    if admin.user.username and admin.user.username.lower() == username.lower():
                        new_admin_id = admin.user.id
                        break

                # Если не нашли среди админов, пробуем среди участников
                if not new_admin_id:
                    try:
                        chat_member = await message.bot.get_chat_member(chat_id, f"@{username}")
                        if chat_member and chat_member.user:
                            new_admin_id = chat_member.user.id
                    except Exception:
                        pass

                if not new_admin_id:
                    await message.answer(f"❌ Пользователь @{username} не найден в этом чате")
                    return

            except Exception as e:
                await message.answer(f"❌ Ошибка при поиске пользователя @{username}: {str(e)}")
                return
        else:
            # Это ID пользователя
            try:
                new_admin_id = int(user_input)
            except ValueError:
                await message.answer("❌ Неверный формат. Используйте user_id или @username")
                return

        if new_admin_id == ADMIN_ID:
            await message.answer("❌ Вы уже являетесь главным администратором")
            return

        if new_admin_id in admin_list:
            await message.answer("❌ Этот пользователь уже является администратором")
            return

        admin_list.add(new_admin_id)

        # Получаем информацию о пользователе для подтверждения
        try:
            user_info = await message.bot.get_chat_member(message.chat.id, new_admin_id)
            user_display = f"@{user_info.user.username}" if user_info.user.username else f"ID:{new_admin_id}"
            await message.answer(f"✅ Пользователь {user_display} добавлен в администраторы")
        except Exception:
            await message.answer(f"✅ Пользователь с ID {new_admin_id} добавлен в администраторы")

    elif action == "del":
        if len(args) < 2:
            await message.answer("❌ Укажите user_id: /admin del <user_id>")
            return

        try:
            admin_to_remove = int(args[1])
            if admin_to_remove == ADMIN_ID:
                await message.answer("❌ Нельзя удалить главного администратора")
                return

            if admin_to_remove not in admin_list:
                await message.answer("❌ Этот пользователь не является администратором")
                return

            admin_list.remove(admin_to_remove)
            await message.answer(f"✅ Пользователь {admin_to_remove} удален из администраторов")

        except ValueError:
            await message.answer("❌ Неверный формат user_id")

    else:
        await message.answer(
            "❌ Неизвестная команда\n\n"
            "Доступные команды:\n"
            "/admin add <user_id|@username> - добавить админа\n"
            "/admin del <user_id> - удалить админа\n"
            "/admin list - показать список"
        )

# Message handler for general messages
@router.message()
async def handle_message(message: Message):
    if not message.text:
        return

    chat_id = message.chat.id
    message_thread_id = message.message_thread_id
    user_id = message.from_user.id
    username = message.from_user.username

    # Проверяем ограничения доступа к топику (только если есть message_thread_id)
    if message_thread_id:
        if (chat_id in restricted_topics and 
            message_thread_id in restricted_topics[chat_id]):

            # Проверяем, является ли пользователь администратором
            is_user_admin = await is_admin(chat_id, user_id, message.bot)

            if not is_user_admin:
                # Проверяем, есть ли пользователь в списке разрешенных
                allowed_users = restricted_topics[chat_id][message_thread_id]
                user_allowed = False

                # Проверяем по username (с @ и без)
                if username:
                    user_allowed = (f"@{username}" in allowed_users or 
                                  username in allowed_users)

                # Проверяем по user_id (если указан в формате @123456789)
                if not user_allowed:
                    user_id_mention = f"@{user_id}"
                    user_allowed = user_id_mention in allowed_users

                if not user_allowed:
                    # Пользователь не имеет права писать в этом топике
                    try:
                        await message.bot.delete_message(
                            chat_id=chat_id,
                            message_id=message.message_id
                        )
                        logging.info(f"Удалено сообщение от неразрешенного пользователя в ограниченном топике: {chat_id}/{message_thread_id}")
                    except Exception as e:
                        logging.error(f"Ошибка при удалении сообщения в ограниченном топике: {str(e)}")
                    return

    # Проверяем, не в теме ли для переименования написано сообщение
    if (chat_id in rename_topics_dict and 
        message_thread_id in rename_topics_dict[chat_id]):
        # Удаляем ВСЕ сообщения в темах переименования, кроме ответов на запрос имени от пользователей
        is_reply_to_name_request = False

        # Проверяем только сообщения от пользователей (не от бота)
        if (not message.from_user.is_bot and 
            message.reply_to_message):
            # Проверяем, является ли это ответом на сообщение с запросом имени
            reply_text = message.reply_to_message.text
            if (message.reply_to_message.from_user.is_bot and
                reply_text and "Введите имя ответом на сообщение" in reply_text):
                is_reply_to_name_request = True

        if not is_reply_to_name_request:
            try:
                await message.bot.delete_message(
                    chat_id=chat_id,
                    message_id=message.message_id
                )
                logging.info(f"Удалено сообщение в теме для переименования: {chat_id}/{message_thread_id}")
            except Exception as e:
                logging.error(f"Ошибка при удалении сообщения в теме переименования: {str(e)}")
            return

    # Сначала проверяем SOS слова
    await check_sos_word(message)

    # Затем проверяем специальные команды
    if message.text.lower() == "номер":
        if chat_id in workers_dict and message_thread_id in workers_dict[chat_id]:
            workers = workers_dict[chat_id][message_thread_id]
            user_mentions = " ".join(workers)
            await message.answer(f"Внимание! {user_mentions}")

async def check_sos_word(message: Message):
    if not message.text:
        return

    chat_id = message.chat.id
    message_thread_id = message.message_thread_id
    message_text = message.text.lower()

    logging.info(f"check_sos_word вызван. Chat ID: {chat_id}, Topic ID: {message_thread_id}, Текст: {message_text}")

    if not message_thread_id:
        logging.info("Сообщение не в теме, пропускаем проверку SOS-слов.")
        return

    # Проверяем, содержит ли сообщение SOS-слово
    sos_found = False
    for word in sos_words:
        if word in message_text:
            sos_found = True
            logging.info(f"Найдено SOS-слово: {word}")
            break

    if sos_found:
        try:
            # Проверяем, не активен ли уже SOS в этой теме
            if (chat_id in active_topics and 
                message_thread_id in active_topics[chat_id]):
                # SOS уже активен, отменяем старые задачи и создаем новые
                task_key = (chat_id, message_thread_id)
                if task_key in sos_removal_tasks:
                    sos_removal_tasks[task_key].cancel()
                    del sos_removal_tasks[task_key]
            else:
                # Создаем топик "Активные темы" если его нет
                await create_active_topics_thread(chat_id, message.bot)

                # Добавляем тему в активные
                if chat_id not in active_topics:
                    active_topics[chat_id] = set()
                active_topics[chat_id].add(message_thread_id)

            # Сохраняем время активации SOS
            sos_activation_times[(chat_id, message_thread_id)] = time.time()

            # Запускаем задачу автоматического снятия SOS через 5 минут
            task_key = (chat_id, message_thread_id)
            sos_removal_tasks[task_key] = asyncio.create_task(
                auto_remove_sos(chat_id, message_thread_id, message.bot)
            )

            # Запускаем задачу обновления времени, если её ещё нет для этого чата
            if chat_id not in sos_update_tasks:
                sos_update_tasks[chat_id] = asyncio.create_task(
                    update_sos_times(chat_id, message.bot)
                )

            # Обновляем сообщение в топике "Активные темы"
            await update_active_topics_message(chat_id, message.bot)

            # Проверяем, есть ли назначенные воркеры для этой темы
            if chat_id in workers_dict and message_thread_id in workers_dict[chat_id]:
                workers = workers_dict[chat_id][message_thread_id]
                topic_name = topics_dict.get(chat_id, {}).get(message_thread_id, f"Тема {message_thread_id}")

                # Создаем ссылку на топик
                try:
                    chat = await message.bot.get_chat(chat_id)
                    if chat.username:
                        link = f"https://t.me/{chat.username}/{message_thread_id}"
                    else:
                        # Для приватных чатов используем другой формат
                        link = f"https://t.me/c/{str(chat_id)[4:]}/{message_thread_id}"

                    # Отправляем уведомления воркерам в личные сообщения
                    for worker in workers:
                        # Убираем @ из упоминания пользователя, если есть
                        username = worker.replace('@', '') if worker.startswith('@') else worker

                        try:
                            notification_text = (
                                f"🚨 ВНИМАНИЕ! Нужен номер в теме '{topic_name}'\n"
                                f"Ссылка: {link}"
                            )

                            # Пытаемся найти пользователя по username и отправить ЛС
                            try:
                                # Получаем информацию о участниках чата для поиска user_id по username
                                chat_members = await message.bot.get_chat_administrators(chat_id)
                                user_id = None

                                # Ищем user_id по username среди администраторов
                                for member in chat_members:
                                    if member.user.username and member.user.username.lower() == username.lower():
                                        user_id = member.user.id
                                        break

                                # Если не нашли среди админов, пробуем среди обычных участников
                                if not user_id:
                                    try:
                                        chat_member = await message.bot.get_chat_member(chat_id, f"@{username}")
                                        if chat_member:
                                            user_id = chat_member.user.id
                                    except:
                                        pass

                                if user_id:
                                    # Пытаемся отправить ЛС
                                    await message.bot.send_message(
                                        chat_id=user_id,
                                        text=notification_text,
                                        disable_web_page_preview=True
                                    )
                                    logging.info(f"Отправлено ЛС воркеру {worker} (ID: {user_id})")
                                else:
                                    # Если не смогли найти user_id, отправляем в группу
                                    await message.bot.send_message(
                                        chat_id=chat_id,
                                        message_thread_id=message_thread_id,
                                        text=f"🚨 ВНИМАНИЕ! {worker} - нужен номер!",
                                        disable_web_page_preview=True
                                    )
                                    logging.info(f"Не удалось найти user_id для {worker}, отправлено в группу")

                            except Exception as dm_error:
                                # Если не удалось отправить ЛС (пользователь не начинал диалог с ботом)
                                logging.warning(f"Не удалось отправить ЛС воркеру {worker}: {str(dm_error)}")
                                # Отправляем в группу как fallback
                                await message.bot.send_message(
                                    chat_id=chat_id,
                                    message_thread_id=message_thread_id,
                                    text=f"🚨 ВНИМАНИЕ! {worker} - нужен номер!",
                                    disable_web_page_preview=True
                                )
                                logging.info(f"Отправлено в группу как fallback для {worker}")

                        except Exception as e:
                            logging.error(f"Общая ошибка при отправке уведомления воркеру {worker}: {str(e)}")

                    logging.info(f"Отправлены уведомления воркерам для темы {message_thread_id}")

                except Exception as e:
                    logging.error(f"Ошибка при отправке уведомлений воркерам: {str(e)}")

        except Exception as e:
            logging.error(f"Общая ошибка в check_sos_word: {str(e)}")
