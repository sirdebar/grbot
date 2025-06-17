import os
import logging
import asyncio
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, ConversationHandler
from telegram.error import RetryAfter
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
GAMES_ENABLED = os.getenv('GAMES_ENABLED', 'true').lower() == 'true'

# Варианты ID эмодзи для иконки SOS (попробуйте разные)
# Эти ID могут работать для custom emoji в темах форума
SOS_EMOJI_OPTIONS = [
    "5789953862016985048",  # Красный восклицательный знак
    "5787544344906959608",  # Оранжевый восклицательный знак  
    "5780043138200730399",  # Жёлтый восклицательный знак
    "5789678423060137046",  # Альтернативный вариант
]

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

WAITING_FOR_TOPIC_NAME = 1
WAITING_FOR_TOPIC_ID = 2
WAITING_FOR_BROADCAST = 3
WAITING_FOR_RENAME_COUNT = 4
WAITING_FOR_RENAME = 5
PLAYING_GUESS_NUMBER = 6
PLAYING_DICE = 7

# Словарь для хранения тем
topics_dict = {}
workers_dict = {}
rename_topics_dict = {}
# Список SOS-слов
sos_words = {"сос", "sos", "помогите", "помощь", "номер"}
# Словарь для хранения оригинальных аватарок чатов
original_avatars = {}
# Словарь для хранения активных тем (тем с SOS)
active_topics = {}
# Словарь для хранения ID топика "Активные темы" и ID сообщения в нем
active_topics_info = {}
# Словари для игр
game_sessions = {}
guess_numbers = {}
tic_tac_toe_games = {}
blackjack_games = {}
battleship_games = {}

async def create_active_topics_thread(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Создает топик 'Активные темы' если его нет"""
    try:
        # Проверяем, есть ли уже такой топик
        if chat_id in active_topics_info:
            return active_topics_info[chat_id]['topic_id']

        # Создаем новый топик
        topic = await context.bot.create_forum_topic(
            chat_id=chat_id,
            name="Активные темы"
        )

        topic_id = topic.message_thread_id

        # Отправляем первое сообщение
        message = await context.bot.send_message(
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

async def update_active_topics_message(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Обновляет сообщение в топике 'Активные темы'"""
    try:
        # Проверяем, есть ли топик "Активные темы"
        if chat_id not in active_topics_info:
            await create_active_topics_thread(chat_id, context)

        if chat_id not in active_topics_info:
            return

        topic_id = active_topics_info[chat_id]['topic_id']
        message_id = active_topics_info[chat_id]['message_id']

        # Формируем текст сообщения
        if chat_id in active_topics and active_topics[chat_id]:
            text = "Темы в которых нужен номер:\n"
            chat = await context.bot.get_chat(chat_id)

            for active_topic_id in active_topics[chat_id]:
                topic_name = topics_dict.get(chat_id, {}).get(active_topic_id, f"Тема {active_topic_id}")
                # Создаем ссылку на топик
                if chat.username:
                    link = f"https://t.me/{chat.username}/{active_topic_id}"
                else:
                    # Для приватных чатов используем другой формат
                    link = f"https://t.me/c/{str(chat_id)[4:]}/{active_topic_id}"

                text += f"• {topic_name} - {link}\n"
        else:
            text = "Темы в которых нужен номер:\n(пока нет активных тем)"

        # Обновляем сообщение
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            disable_web_page_preview=True
        )

        logging.info(f"Обновлено сообщение в топике 'Активные темы' для чата {chat_id}")

    except Exception as e:
        logging.error(f"Ошибка при обновлении сообщения 'Активные темы': {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Список тем", callback_data='list_topics')],
        [InlineKeyboardButton("Создать тему", callback_data='create_topic')],
        [InlineKeyboardButton("Удалить тему", callback_data='delete_topic')],
        [InlineKeyboardButton("Удалить все темы", callback_data='delete_all_topics')],
        [InlineKeyboardButton("Создать рассылку", callback_data='create_broadcast')],
        [InlineKeyboardButton("🖌 Создать темы с переименованием", callback_data='create_rename_topics')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("👋")
    await update.message.reply_text(
        'Привет! Я бот для управления темами в группах. Выберите действие:',
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'list_topics':
        await list_topics(update, context)
    elif query.data == 'create_topic':
        await request_topic_name(update, context)
    elif query.data == 'delete_topic':
        await request_topic_id(update, context)
    elif query.data == 'delete_all_topics':
        await delete_all_topics(update, context)
    elif query.data == 'create_broadcast':
        await request_broadcast_message(update, context)
    elif query.data == 'create_rename_topics':
        await request_rename_topics_count(update, context)
    elif query.data.startswith('confirm_rename_'):
        topic_id = int(query.data.split('_')[2])
        chat = await context.bot.get_chat(query.message.chat_id)

        if chat.id not in rename_topics_dict or topic_id not in rename_topics_dict[chat.id]:
            await query.message.reply_text("Эта тема больше не доступна для переименования")
            return

        context.user_data['current_rename_topic'] = {
            'chat_id': chat.id,
            'topic_id': topic_id,
            'old_name': topics_dict[chat.id][topic_id]
        }

        # Сохраняем ID сообщения с кнопкой подтверждения
        context.user_data['confirmation_message_id'] = query.message.message_id

        # Отправляем сообщение с запросом имени и сохраняем его ID
        message = await query.message.reply_text("Введите своё имя:")
        context.user_data['request_name_message_id'] = message.message_id

        return WAITING_FOR_RENAME

async def check_forum_support(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        chat = await context.bot.get_chat(chat_id)
        return chat.is_forum
    except Exception:
        return False

async def request_topic_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat = await context.bot.get_chat(query.message.chat_id)

    if chat.type in ['group', 'supergroup']:
        if await check_forum_support(chat.id, context):
            await query.message.reply_text(
                "Пожалуйста, введите название для новой темы:"
            )
            return WAITING_FOR_TOPIC_NAME
        else:
            await query.message.reply_text(
                "Эта группа не поддерживает создание тем. "
                "Для использования тем группа должна быть настроена как форум. "
                "Обратитесь к администратору группы для настройки."
            )
            return ConversationHandler.END
    else:
        await query.message.reply_text(
            "Эта команда доступна только в группах."
        )
        return ConversationHandler.END

async def worker_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /worker название_темы @user1 @user2 ...")
        return

    topic_name = context.args[0]
    users = context.args[1:]

    if not users:
        await update.message.reply_text("Необходимо указать хотя бы одного пользователя")
        return

    chat = await context.bot.get_chat(update.message.chat_id)

    if not await check_forum_support(chat.id, context):
        await update.message.reply_text("Эта группа не поддерживает темы")
        return

    topic_id = None
    for tid, name in topics_dict.get(chat.id, {}).items():
        if name == topic_name:
            topic_id = tid
            break

    if not topic_id:
        await update.message.reply_text(f"Тема '{topic_name}' не найдена")
        return

    user_mentions = " ".join(users)
    await context.bot.send_message(
        chat_id=chat.id,
        message_thread_id=topic_id,
        text=f"Внимание! {user_mentions}"
    )

    if chat.id not in workers_dict:
        workers_dict[chat.id] = {}
    workers_dict[chat.id][topic_id] = users

async def create_topic_with_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat = await context.bot.get_chat(update.message.chat_id)

    if not await check_forum_support(chat.id, context):
        await update.message.reply_text(
            "Эта группа не поддерживает создание тем"
        )
        return ConversationHandler.END

    try:
        if text.isdigit():
            count = int(text)
            for i in range(1, count + 1):
                topic_name = f"ПК{i}"
                await create_single_topic(chat.id, topic_name, context)
            await update.message.reply_text(f"Создано {count} тем")
        else:
            topics = [t.strip() for t in text.split('\n') if t.strip()]
            for topic_name in topics:
                await create_single_topic(chat.id, topic_name, context)
            await update.message.reply_text(f"Создано {len(topics)} тем")
    except Exception as e:
        await update.message.reply_text(f"Ошибка при создании тем: {str(e)}")

    return ConversationHandler.END

async def create_single_topic(chat_id: int, topic_name: str, context: ContextTypes.DEFAULT_TYPE):
    topic = await context.bot.create_forum_topic(
        chat_id=chat_id,
        name=topic_name
    )

    if chat_id not in topics_dict:
        topics_dict[chat_id] = {}
    topics_dict[chat_id][topic.message_thread_id] = topic_name

async def request_topic_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat = await context.bot.get_chat(query.message.chat_id)

    if chat.type in ['group', 'supergroup']:
        if await check_forum_support(chat.id, context):
            # Показываем список тем с их ID
            if chat.id in topics_dict and topics_dict[chat.id]:
                topics_list = "\n".join([
                    f"- {name} (ID: {topic_id})"
                    for topic_id, name in topics_dict[chat.id].items()
                ])
                await query.message.reply_text(
                    f"Список доступных тем:\n{topics_list}\n\n"
                    "Введите ID темы, которую хотите удалить:"
                )
            else:
                await query.message.reply_text(
                    "В этой группе пока нет тем. "
                    "Сначала создайте тему через кнопку 'Создать тему'."
                )
                return ConversationHandler.END
            return WAITING_FOR_TOPIC_ID
        else:
            await query.message.reply_text(
                "Эта группа не поддерживает темы. "
                "Для использования тем группа должна быть настроена как форум."
            )
            return ConversationHandler.END
    else:
        await query.message.reply_text(
            "Эта команда доступна только в группах."
        )
        return ConversationHandler.END

async def delete_topic_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic_id = update.message.text
    chat = await context.bot.get_chat(update.message.chat_id)

    try:
        topic_id = int(topic_id)
        if chat.id in topics_dict and topic_id in topics_dict[chat.id]:
            topic_name = topics_dict[chat.id][topic_id]
            await context.bot.delete_forum_topic(
                chat_id=chat.id,
                message_thread_id=topic_id
            )
            del topics_dict[chat.id][topic_id]
            await update.message.reply_text(
                f"Тема '{topic_name}' успешно удалена!"
            )
        else:
            await update.message.reply_text(
                "Тема с таким ID не найдена. "
                "Используйте команду 'Список тем' для просмотра доступных тем."
            )
    except ValueError:
        await update.message.reply_text(
            "Пожалуйста, введите корректный ID темы (число)."
        )
    except Exception as e:
        await update.message.reply_text(
            f"Ошибка при удалении темы: {str(e)}"
        )

    return ConversationHandler.END

async def list_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        chat = await context.bot.get_chat(query.message.chat_id)

        if chat.type not in ['group', 'supergroup']:
            await query.message.reply_text(
                "Эта команда доступна только в группах."
            )
            return

        if not chat.is_forum:
            await query.message.reply_text(
                "Эта группа не настроена как форум. "
                "Для использования тем группа должна быть настроена как форум."
            )
            return

        if chat.id in topics_dict and topics_dict[chat.id]:
            topics_list = "\n".join([
                f"- {name} (ID: {topic_id})"
                for topic_id, name in topics_dict[chat.id].items()
            ])
            await query.message.reply_text(
                f"Список тем в группе {chat.title}:\n{topics_list}"
            )
        else:
            await query.message.reply_text(
                "В этой группе пока нет тем. "
                "Создайте новую тему через кнопку 'Создать тему'."
            )

    except Exception as e:
        logging.error(f"Ошибка при работе с группами: {str(e)}")
        await query.message.reply_text(
            "Произошла ошибка при работе с группами. "
            "Убедитесь, что бот добавлен в группу как администратор."
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    # Сначала проверяем SOS слова
    await check_sos_word(update, context)

    # Затем проверяем специальные команды
    if update.message.text.lower() == "номер":
        chat_id = update.message.chat_id
        message_thread_id = update.message.message_thread_id

        if chat_id in workers_dict and message_thread_id in workers_dict[chat_id]:
            workers = workers_dict[chat_id][message_thread_id]
            user_mentions = " ".join(workers)
            await update.message.reply_text(f"Внимание! {user_mentions}")

async def delete_all_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat = await context.bot.get_chat(query.message.chat_id)

    if chat.type not in ['group', 'supergroup']:
        await query.message.reply_text("Эта команда доступна только в группах.")
        return

    if not await check_forum_support(chat.id, context):
        await query.message.reply_text("Эта группа не поддерживает темы")
        return

    if chat.id not in topics_dict or not topics_dict[chat.id]:
        await query.message.reply_text("В этой группе нет тем для удаления")
        return

    deleted_count = 0
    for topic_id in list(topics_dict[chat.id].keys()):
        try:
            await context.bot.delete_forum_topic(
                chat_id=chat.id,
                message_thread_id=topic_id
            )
            deleted_count += 1
        except Exception as e:
            logging.error(f"Ошибка при удалении темы {topic_id}: {str(e)}")

    topics_dict[chat.id] = {}
    if chat.id in workers_dict:
        workers_dict[chat.id] = {}

    await query.message.reply_text(f"Успешно удалено {deleted_count} тем")

async def request_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat = await context.bot.get_chat(query.message.chat_id)

    if chat.type not in ['group', 'supergroup']:
        await query.message.reply_text("Эта команда доступна только в группах.")
        return

    if not await check_forum_support(chat.id, context):
        await query.message.reply_text("Эта группа не поддерживает темы")
        return

    if chat.id not in topics_dict or not topics_dict[chat.id]:
        await query.message.reply_text("В этой группе нет тем для рассылки")
        return

    await query.message.reply_text("Введите сообщение для рассылки во все темы:")
    return WAITING_FOR_BROADCAST

async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    chat = await context.bot.get_chat(update.message.chat_id)

    if chat.id not in topics_dict or not topics_dict[chat.id]:
        await update.message.reply_text("В этой группе нет тем для рассылки")
        return ConversationHandler.END

    sent_count = 0
    for topic_id in topics_dict[chat.id].keys():
        try:
            await context.bot.send_message(
                chat_id=chat.id,
                message_thread_id=topic_id,
                text=message_text
            )
            sent_count += 1
        except Exception as e:
            logging.error(f"Ошибка при отправке сообщения в тему {topic_id}: {str(e)}")

    await update.message.reply_text(f"Сообщение отправлено в {sent_count} тем")
    return ConversationHandler.END

async def request_rename_topics_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat = await context.bot.get_chat(query.message.chat_id)

    if chat.type not in ['group', 'supergroup']:
        await query.message.reply_text("Эта команда доступна только в группах.")
        return

    if not await check_forum_support(chat.id, context):
        await query.message.reply_text("Эта группа не поддерживает темы")
        return

    await query.message.reply_text("Введите количество тем для создания:")
    return WAITING_FOR_RENAME_COUNT

async def create_rename_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text)
        chat = await context.bot.get_chat(update.message.chat_id)

        if chat.id not in rename_topics_dict:
            rename_topics_dict[chat.id] = set()

        created_count = 0
        for i in range(1, count + 1):
            try:
                topic = await context.bot.create_forum_topic(
                    chat_id=chat.id,
                    name=f"{i}:Без названия"
                )

                if chat.id not in topics_dict:
                    topics_dict[chat.id] = {}
                topics_dict[chat.id][topic.message_thread_id] = f"{i}:Без названия"

                rename_topics_dict[chat.id].add(topic.message_thread_id)

                keyboard = [[InlineKeyboardButton("✅", callback_data=f'confirm_rename_{topic.message_thread_id}')]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                try:
                    await context.bot.send_message(
                        chat_id=chat.id,
                        message_thread_id=topic.message_thread_id,
                        text="Чтобы изменить название темы, введите своё имя.\n"
                             "Перед этим нажмите на галочку ниже.",
                        reply_markup=reply_markup
                    )
                except RetryAfter as e:
                    await asyncio.sleep(e.retry_after)
                    await context.bot.send_message(
                        chat_id=chat.id,
                        message_thread_id=topic.message_thread_id,
                        text="Чтобы изменить название темы, введите своё имя.\n"
                             "Перед этим нажмите на галочку ниже.",
                        reply_markup=reply_markup
                    )

                created_count += 1

                if i < count:  # Не ждем после создания последней темы
                    await asyncio.sleep(5)  # Увеличиваем задержку до 5 секунд

            except RetryAfter as e:
                await asyncio.sleep(e.retry_after)
                continue
            except Exception as e:
                logging.error(f"Ошибка при создании témы {i}: {str(e)}")

        try:
            await update.message.reply_text(f"Создано {created_count} тем для переименования.")
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await update.message.reply_text(f"Создано {created_count} тем для переименования.")
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите корректное число")
    except Exception as e:
        try:
            await update.message.reply_text(f"Ошибка при создании тем: {str(e)}")
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await update.message.reply_text(f"Ошибка при создании тем: {str(e)}")

    return ConversationHandler.END

async def rename_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем, является ли это ответом на сообщение бота
    if not update.message.reply_to_message:
        # Если это не ответ на сообщение, продолжаем ждать правильный ответ
        return WAITING_FOR_RENAME

    # Проверяем, что это ответ на сообщение с запросом имени
    request_message_id = context.user_data.get('request_name_message_id')
    if not request_message_id or update.message.reply_to_message.message_id != request_message_id:
        # Если это ответ не на то сообщение, продолжаем ждать правильный ответ
        return WAITING_FOR_RENAME

    new_name = update.message.text
    topic_data = context.user_data.get('current_rename_topic')

    if not topic_data:
        await update.message.reply_text("Ошибка: не найдены данные о теме")
        return ConversationHandler.END

    chat_id = topic_data['chat_id']
    topic_id = topic_data['topic_id']
    old_name = topic_data['old_name']
    number = old_name.split(":")[0]

    try:
        await context.bot.edit_forum_topic(
            chat_id=chat_id,
            message_thread_id=topic_id,
            name=f"{number}:{new_name}"
        )

        topics_dict[chat_id][topic_id] = f"{number}:{new_name}"
        if chat_id in rename_topics_dict:
            rename_topics_dict[chat_id].discard(topic_id)

        await update.message.reply_text(f"Тема успешно переименована в {number}:{new_name}")

        # Удаляем сообщения бота
        try:
            # Удаляем сообщение с кнопкой подтверждения
            if 'confirmation_message_id' in context.user_data:
                await context.bot.delete_message(
                    chat_id=chat_id,
                    message_id=context.user_data['confirmation_message_id']
                )
            # Удаляем сообщение с запросом имени
            if 'request_name_message_id' in context.user_data:
                await context.bot.delete_message(
                    chat_id=chat_id,
                    message_id=context.user_data['request_name_message_id']
                )
        except Exception as e:
            print(f"Ошибка при удалении сообщений: {str(e)}")

    except Exception as e:
        await update.message.reply_text(f"Ошибка при переименовании темы: {str(e)}")

    context.user_data.pop('current_rename_topic', None)
    context.user_data.pop('confirmation_message_id', None)
    context.user_data.pop('request_name_message_id', None)
    return ConversationHandler.END

async def is_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        return chat_member.status in ['creator', 'administrator']
    except Exception:
        return False

async def add_sos_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        await update.message.reply_text("Эта команда доступна только администраторам")
        return

    if not context.args:
        await update.message.reply_text("Использование: /gadd слово")
        return

    word = context.args[0].lower()
    if word in sos_words:
        await update.message.reply_text(f"Слово '{word}' уже есть в списке")
        return

    sos_words.add(word)
    await update.message.reply_text(f"Слово '{word}' добавлено в список SOS-слов")

async def delete_sos_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        await update.message.reply_text("Эта команда доступна только администраторам")
        return

    if not context.args:
        await update.message.reply_text("Использование: /gdel слово")
        return

    word = context.args[0].lower()
    if word not in sos_words:
        await update.message.reply_text(f"Слово '{word}' не найдено в списке")
        return

    sos_words.remove(word)
    await update.message.reply_text(f"Слово '{word}' удалено из списка SOS-слов")

async def list_sos_words(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        await update.message.reply_text("Эта команда доступна только администраторам")
        return

    if not sos_words:
        await update.message.reply_text("Список SOS-слов пуст")
        return

    words_list = "\n".join(sorted(sos_words))
    await update.message.reply_text(f"Список SOS-слов:\n{words_list}")

async def check_sos_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    message_thread_id = update.message.message_thread_id
    message_text = update.message.text.lower()

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
            # Создаем топик "Активные темы" если его нет
            await create_active_topics_thread(chat_id, context)

            # Добавляем тему в активные
            if chat_id not in active_topics:
                active_topics[chat_id] = set()
            active_topics[chat_id].add(message_thread_id)

            # Сначала получаем информацию о текущей теме
            try:
                # Пытаемся получить информацию о теме через API
                chat = await context.bot.get_chat(chat_id)
                # Сохраняем оригинальную иконку если её ещё нет
                if (chat_id, message_thread_id) not in original_avatars:
                    # Устанавливаем None как значение по умолчанию
                    original_avatars[(chat_id, message_thread_id)] = None
                    logging.info(f"Сохранена информация о теме {message_thread_id}")
            except Exception as e:
                logging.error(f"Ошибка при получении информации о чате: {str(e)}")

            # Пробуем разные варианты SOS emoji
            success = False
            for emoji_id in SOS_EMOJI_OPTIONS:
                try:
                    logging.info(f"Попытка установить иконку с ID: {emoji_id}")
                    await context.bot.edit_forum_topic(
                        chat_id=chat_id,
                        message_thread_id=message_thread_id,
                        icon_custom_emoji_id=emoji_id
                    )
                    logging.info(f"Успешно установлена SOS иконка с ID: {emoji_id}")
                    success = True
                    break
                except Exception as e:
                    logging.error(f"Ошибка с emoji ID {emoji_id}: {str(e)}")
                    continue

            if not success:
                # Если не удалось установить custom emoji, попробуем изменить название
                try:
                    current_name = topics_dict.get(chat_id, {}).get(message_thread_id, "Тема")
                    if not current_name.startswith("🚨"):
                        new_name = f"🚨 {current_name}"
                        await context.bot.edit_forum_topic(
                            chat_id=chat_id,
                            message_thread_id=message_thread_id,
                            name=new_name
                        )
                        # Обновляем наш словарь
                        if chat_id in topics_dict:
                            topics_dict[chat_id][message_thread_id] = new_name
                        logging.info(f"Добавлен SOS эмодзи в название темы: {new_name}")
                except Exception as e:
                    logging.error(f"Ошибка при изменении названия темы: {str(e)}")

            # Обновляем сообщение в топике "Активные темы"
            await update_active_topics_message(chat_id, context)

        except Exception as e:
            logging.error(f"Общая ошибка в check_sos_word: {str(e)}")

async def restore_topic_icon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    message_thread_id = update.message.message_thread_id

    if not message_thread_id:
        await update.message.reply_text("Эта команда должна использоваться в теме")
        return

    # Получаем текущее название темы
    current_name = topics_dict.get(chat_id, {}).get(message_thread_id, None)

    if not current_name:
        await update.message.reply_text("Информация о теме не найдена")
        return

    # Проверяем, начинается ли название с 🚨
    if current_name.startswith("🚨 "):
        try:
            # Убираем "🚨 " из начала названия
            new_name = current_name[2:]  # Убираем первые 3 символа: "🚨 "

            await context.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                name=new_name
            )

            # Обновляем наш словарь
            topics_dict[chat_id][message_thread_id] = new_name

            # Убираем тему из активных
            if chat_id in active_topics and message_thread_id in active_topics[chat_id]:
                active_topics[chat_id].remove(message_thread_id)
                # Обновляем сообщение в топике "Активные темы"
                await update_active_topics_message(chat_id, context)

            await update.message.reply_text(f"SOS эмодзи убран. Новое название: {new_name}")

        except Exception as e:
            logging.error(f"Ошибка при восстановлении названия темы: {str(e)}")
            await update.message.reply_text("Произошла ошибка при восстановлении названия темы.")
    else:
        await update.message.reply_text("В названии темы нет SOS эмодзи для удаления")

async def game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню игр"""
    if not GAMES_ENABLED:
        await update.message.reply_text("🚫 Игры временно отключены администратором")
        return

    keyboard = [
        [InlineKeyboardButton("🎲 Угадай число", callback_data='game_guess_number')],
        [InlineKeyboardButton("🎯 Кости", callback_data='game_dice')],
        [InlineKeyboardButton("🃏 Камень-Ножницы-Бумага", callback_data='game_rps')],
        [InlineKeyboardButton("❌ Крестики-нолики", callback_data='game_tictactoe')],
        [InlineKeyboardButton("🃏 Блекджек", callback_data='game_blackjack')],
        [InlineKeyboardButton("🚢 Морской бой", callback_data='game_battleship')],
        [InlineKeyboardButton("❌ Закрыть", callback_data='close_games')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎮 Выберите игру:\n\n"
        "🎲 Угадай число - угадайте число от 1 до 100\n"
        "🎯 Кости - бросьте кости и посмотрите результат\n"
        "🃏 Камень-Ножницы-Бумага - сыграйте против бота\n"
        "❌ Крестики-нолики - классическая игра 3x3\n"
        "🃏 Блекджек - наберите 21 очко\n"
        "🚢 Морской бой - потопите корабли противника\n\n"
        "💡 Если есть идеи для новых игр - пишите @sirdebar",
        reply_markup=reply_markup
    )

async def stopgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отключить/включить игры (только для админа)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Эта команда доступна только администратору")
        return

    global GAMES_ENABLED
    GAMES_ENABLED = not GAMES_ENABLED
    
    status = "включены" if GAMES_ENABLED else "отключены"
    emoji = "✅" if GAMES_ENABLED else "🚫"
    
    await update.message.reply_text(f"{emoji} Игры {status}")

async def game_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик игровых кнопок"""
    query = update.callback_query
    await query.answer()
    
    if not GAMES_ENABLED and not query.data == 'close_games':
        await query.edit_message_text("🚫 Игры временно отключены администратором")
        return

    if query.data == 'game_guess_number':
        await start_guess_number_game(update, context)
    elif query.data == 'game_dice':
        await play_dice_game(update, context)
    elif query.data == 'game_rps':
        await start_rps_game(update, context)
    elif query.data == 'game_tictactoe':
        await start_tictactoe_game(update, context)
    elif query.data == 'game_blackjack':
        await start_blackjack_game(update, context)
    elif query.data == 'game_battleship':
        await start_battleship_game(update, context)
    elif query.data == 'close_games':
        await query.edit_message_text("🎮 Игры закрыты")
    elif query.data.startswith('rps_'):
        await handle_rps_choice(update, context)
    elif query.data.startswith('ttt_'):
        await handle_tictactoe_move(update, context)
    elif query.data.startswith('bj_'):
        await handle_blackjack_action(update, context)
    elif query.data.startswith('bs_'):
        await handle_battleship_move(update, context)

async def start_guess_number_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать игру угадай число"""
    query = update.callback_query
    user_id = query.from_user.id
    
    number = random.randint(1, 100)
    guess_numbers[user_id] = {
        'number': number,
        'attempts': 0,
        'max_attempts': 7
    }
    
    await query.edit_message_text(
        "🎲 Игра 'Угадай число'\n\n"
        "Я загадал число от 1 до 100.\n"
        f"У вас есть {guess_numbers[user_id]['max_attempts']} попыток!\n\n"
        "Напишите ваше число:"
    )
    
    context.user_data['playing_guess'] = True
    return PLAYING_GUESS_NUMBER

async def handle_guess_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработать попытку угадать число"""
    if not context.user_data.get('playing_guess'):
        return
    
    user_id = update.effective_user.id
    
    if user_id not in guess_numbers:
        await update.message.reply_text("❌ Игра не найдена. Начните новую игру командой /game")
        context.user_data['playing_guess'] = False
        return ConversationHandler.END
    
    try:
        guess = int(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите число!")
        return PLAYING_GUESS_NUMBER
    
    game_data = guess_numbers[user_id]
    game_data['attempts'] += 1
    target = game_data['number']
    
    if guess == target:
        await update.message.reply_text(
            f"🎉 Поздравляю! Вы угадали число {target} за {game_data['attempts']} попыток!"
        )
        del guess_numbers[user_id]
        context.user_data['playing_guess'] = False
        return ConversationHandler.END
    
    elif game_data['attempts'] >= game_data['max_attempts']:
        await update.message.reply_text(
            f"😔 Игра окончена! Было загадано число {target}.\n"
            "Попробуйте еще раз командой /game"
        )
        del guess_numbers[user_id]
        context.user_data['playing_guess'] = False
        return ConversationHandler.END
    
    else:
        hint = "больше" if guess < target else "меньше"
        remaining = game_data['max_attempts'] - game_data['attempts']
        await update.message.reply_text(
            f"❌ Неверно! Загаданное число {hint} чем {guess}\n"
            f"Осталось попыток: {remaining}"
        )
        return PLAYING_GUESS_NUMBER

async def play_dice_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Игра в кости"""
    query = update.callback_query
    
    user_dice = random.randint(1, 6)
    bot_dice = random.randint(1, 6)
    
    dice_emoji = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}
    
    if user_dice > bot_dice:
        result = "🎉 Вы выиграли!"
    elif user_dice < bot_dice:
        result = "😔 Вы проиграли!"
    else:
        result = "🤝 Ничья!"
    
    await query.edit_message_text(
        f"🎯 Игра в кости\n\n"
        f"Ваш результат: {dice_emoji[user_dice]} ({user_dice})\n"
        f"Результат бота: {dice_emoji[bot_dice]} ({bot_dice})\n\n"
        f"{result}"
    )

async def start_rps_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать игру камень-ножницы-бумага"""
    query = update.callback_query
    
    keyboard = [
        [InlineKeyboardButton("🗿 Камень", callback_data='rps_rock')],
        [InlineKeyboardButton("✂️ Ножницы", callback_data='rps_scissors')],
        [InlineKeyboardButton("📄 Бумага", callback_data='rps_paper')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🃏 Камень-Ножницы-Бумага\n\n"
        "Выберите ваш ход:",
        reply_markup=reply_markup
    )

async def handle_rps_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработать выбор в камень-ножницы-бумага"""
    query = update.callback_query
    user_choice = query.data.split('_')[1]
    bot_choice = random.choice(['rock', 'scissors', 'paper'])
    
    choices = {
        'rock': '🗿 Камень',
        'scissors': '✂️ Ножницы',
        'paper': '📄 Бумага'
    }
    
    # Определяем победителя
    if user_choice == bot_choice:
        result = "🤝 Ничья!"
    elif (user_choice == 'rock' and bot_choice == 'scissors') or \
         (user_choice == 'scissors' and bot_choice == 'paper') or \
         (user_choice == 'paper' and bot_choice == 'rock'):
        result = "🎉 Вы выиграли!"
    else:
        result = "😔 Вы проиграли!"
    
    await query.edit_message_text(
        f"🃏 Камень-Ножницы-Бумага\n\n"
        f"Ваш выбор: {choices[user_choice]}\n"
        f"Выбор бота: {choices[bot_choice]}\n\n"
        f"{result}"
    )

async def start_tictactoe_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать игру крестики-нолики"""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Создаем новую игру
    tic_tac_toe_games[user_id] = {
        'board': [' ' for _ in range(9)],
        'current_player': 'X'  # X - игрок, O - бот
    }
    
    await query.edit_message_text(
        "❌ Крестики-нолики\n\n"
        "Вы играете крестиками (X), бот - ноликами (O)\n"
        "Выберите клетку:",
        reply_markup=get_tictactoe_keyboard(user_id)
    )

def get_tictactoe_keyboard(user_id: int):
    """Создать клавиатуру для крестиков-ноликов"""
    if user_id not in tic_tac_toe_games:
        return None
    
    board = tic_tac_toe_games[user_id]['board']
    keyboard = []
    
    for i in range(3):
        row = []
        for j in range(3):
            pos = i * 3 + j
            cell = board[pos] if board[pos] != ' ' else '⬜'
            row.append(InlineKeyboardButton(cell, callback_data=f'ttt_{pos}'))
        keyboard.append(row)
    
    return InlineKeyboardMarkup(keyboard)

async def handle_tictactoe_move(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработать ход в крестики-нолики"""
    query = update.callback_query
    user_id = query.from_user.id
    position = int(query.data.split('_')[1])
    
    if user_id not in tic_tac_toe_games:
        await query.answer("Игра не найдена!")
        return
    
    game = tic_tac_toe_games[user_id]
    
    # Проверяем, свободна ли клетка
    if game['board'][position] != ' ':
        await query.answer("Эта клетка уже занята!")
        return
    
    # Ход игрока
    game['board'][position] = 'X'
    
    # Проверяем победу игрока
    winner = check_tictactoe_winner(game['board'])
    if winner == 'X':
        await query.edit_message_text(
            "❌ Крестики-нолики\n\n"
            "🎉 Поздравляю! Вы выиграли!",
            reply_markup=get_tictactoe_keyboard(user_id)
        )
        del tic_tac_toe_games[user_id]
        return
    
    # Проверяем ничью
    if ' ' not in game['board']:
        await query.edit_message_text(
            "❌ Крестики-нолики\n\n"
            "🤝 Ничья!",
            reply_markup=get_tictactoe_keyboard(user_id)
        )
        del tic_tac_toe_games[user_id]
        return
    
    # Ход бота
    bot_move = get_bot_tictactoe_move(game['board'])
    game['board'][bot_move] = 'O'
    
    # Проверяем победу бота
    winner = check_tictactoe_winner(game['board'])
    if winner == 'O':
        await query.edit_message_text(
            "❌ Крестики-нолики\n\n"
            "😔 Бот выиграл!",
            reply_markup=get_tictactoe_keyboard(user_id)
        )
        del tic_tac_toe_games[user_id]
        return
    
    # Проверяем ничью после хода бота
    if ' ' not in game['board']:
        await query.edit_message_text(
            "❌ Крестики-нолики\n\n"
            "🤝 Ничья!",
            reply_markup=get_tictactoe_keyboard(user_id)
        )
        del tic_tac_toe_games[user_id]
        return
    
    # Продолжаем игру
    await query.edit_message_text(
        "❌ Крестики-нолики\n\n"
        "Ваш ход:",
        reply_markup=get_tictactoe_keyboard(user_id)
    )

def check_tictactoe_winner(board):
    """Проверить победителя в крестики-нолики"""
    winning_combinations = [
        [0, 1, 2], [3, 4, 5], [6, 7, 8],  # горизонтали
        [0, 3, 6], [1, 4, 7], [2, 5, 8],  # вертикали
        [0, 4, 8], [2, 4, 6]              # диагонали
    ]
    
    for combo in winning_combinations:
        if board[combo[0]] == board[combo[1]] == board[combo[2]] != ' ':
            return board[combo[0]]
    return None

def get_bot_tictactoe_move(board):
    """Получить ход бота для крестиков-ноликов"""
    # Простая стратегия: сначала пытаемся выиграть, потом блокировать игрока
    for player in ['O', 'X']:
        for i in range(9):
            if board[i] == ' ':
                board[i] = player
                if check_tictactoe_winner(board) == player:
                    board[i] = ' '
                    return i
                board[i] = ' '
    
    # Если нет срочных ходов, занимаем центр или угол
    if board[4] == ' ':
        return 4
    
    for corner in [0, 2, 6, 8]:
        if board[corner] == ' ':
            return corner
    
    # Иначе любая свободная клетка
    for i in range(9):
        if board[i] == ' ':
            return i

async def start_blackjack_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать игру в блекджек"""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Создаем колоду
    suits = ['♠', '♥', '♦', '♣']
    ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
    deck = [(rank, suit) for suit in suits for rank in ranks]
    random.shuffle(deck)
    
    # Раздаем карты
    player_hand = [deck.pop(), deck.pop()]
    dealer_hand = [deck.pop(), deck.pop()]
    
    blackjack_games[user_id] = {
        'deck': deck,
        'player_hand': player_hand,
        'dealer_hand': dealer_hand,
        'game_over': False
    }
    
    player_score = calculate_blackjack_score(player_hand)
    dealer_visible_score = calculate_blackjack_score([dealer_hand[0]])
    
    # Проверяем блекджек у игрока
    if player_score == 21:
        dealer_score = calculate_blackjack_score(dealer_hand)
        if dealer_score == 21:
            result = "🤝 Ничья! У обоих блекджек!"
        else:
            result = "🎉 Блекджек! Вы выиграли!"
        
        await query.edit_message_text(
            f"🃏 Блекджек\n\n"
            f"Ваши карты: {format_blackjack_hand(player_hand)} = {player_score}\n"
            f"Карты дилера: {format_blackjack_hand(dealer_hand)} = {dealer_score}\n\n"
            f"{result}"
        )
        del blackjack_games[user_id]
        return
    
    keyboard = [
        [InlineKeyboardButton("🃏 Взять карту", callback_data='bj_hit')],
        [InlineKeyboardButton("✋ Остановиться", callback_data='bj_stand')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🃏 Блекджек\n\n"
        f"Ваши карты: {format_blackjack_hand(player_hand)} = {player_score}\n"
        f"Карта дилера: {format_blackjack_hand([dealer_hand[0]])} = {dealer_visible_score}\n\n"
        f"Выберите действие:",
        reply_markup=reply_markup
    )

def calculate_blackjack_score(hand):
    """Вычислить счет в блекджеке"""
    score = 0
    aces = 0
    
    for rank, suit in hand:
        if rank in ['J', 'Q', 'K']:
            score += 10
        elif rank == 'A':
            aces += 1
            score += 11
        else:
            score += int(rank)
    
    # Обрабатываем тузы
    while score > 21 and aces > 0:
        score -= 10
        aces -= 1
    
    return score

def format_blackjack_hand(hand):
    """Форматировать руку для отображения"""
    return ' '.join([f"{rank}{suit}" for rank, suit in hand])

async def handle_blackjack_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработать действие в блекджеке"""
    query = update.callback_query
    user_id = query.from_user.id
    action = query.data.split('_')[1]
    
    if user_id not in blackjack_games:
        await query.answer("Игра не найдена!")
        return
    
    game = blackjack_games[user_id]
    
    if action == 'hit':
        # Игрок берет карту
        card = game['deck'].pop()
        game['player_hand'].append(card)
        player_score = calculate_blackjack_score(game['player_hand'])
        
        if player_score > 21:
            # Перебор у игрока
            dealer_score = calculate_blackjack_score(game['dealer_hand'])
            await query.edit_message_text(
                f"🃏 Блекджек\n\n"
                f"Ваши карты: {format_blackjack_hand(game['player_hand'])} = {player_score}\n"
                f"Карты дилера: {format_blackjack_hand(game['dealer_hand'])} = {dealer_score}\n\n"
                f"😔 Перебор! Вы проиграли!"
            )
            del blackjack_games[user_id]
            return
        
        # Продолжаем игру
        dealer_visible_score = calculate_blackjack_score([game['dealer_hand'][0]])
        keyboard = [
            [InlineKeyboardButton("🃏 Взять карту", callback_data='bj_hit')],
            [InlineKeyboardButton("✋ Остановиться", callback_data='bj_stand')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🃏 Блекджек\n\n"
            f"Ваши карты: {format_blackjack_hand(game['player_hand'])} = {player_score}\n"
            f"Карта дилера: {format_blackjack_hand([game['dealer_hand'][0]])} = {dealer_visible_score}\n\n"
            f"Выберите действие:",
            reply_markup=reply_markup
        )
    
    elif action == 'stand':
        # Игрок останавливается, ход дилера
        player_score = calculate_blackjack_score(game['player_hand'])
        
        # Дилер берет карты до 17
        while calculate_blackjack_score(game['dealer_hand']) < 17:
            card = game['deck'].pop()
            game['dealer_hand'].append(card)
        
        dealer_score = calculate_blackjack_score(game['dealer_hand'])
        
        # Определяем победителя
        if dealer_score > 21:
            result = "🎉 Дилер перебрал! Вы выиграли!"
        elif dealer_score > player_score:
            result = "😔 Дилер выиграл!"
        elif player_score > dealer_score:
            result = "🎉 Вы выиграли!"
        else:
            result = "🤝 Ничья!"
        
        await query.edit_message_text(
            f"🃏 Блекджек\n\n"
            f"Ваши карты: {format_blackjack_hand(game['player_hand'])} = {player_score}\n"
            f"Карты дилера: {format_blackjack_hand(game['dealer_hand'])} = {dealer_score}\n\n"
            f"{result}"
        )
        del blackjack_games[user_id]

async def start_battleship_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать игру в морской бой"""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Создаем поля игрока и бота
    player_field = create_battleship_field()
    bot_field = create_battleship_field()
    
    # Размещаем корабли
    place_battleship_ships(bot_field)
    
    # Создаем видимое поле бота (скрытое)
    bot_visible_field = [['🌊' for _ in range(6)] for _ in range(6)]
    
    battleship_games[user_id] = {
        'player_field': player_field,
        'bot_field': bot_field,
        'bot_visible_field': bot_visible_field,
        'ships_to_place': 3,  # 3 корабля по 1 клетке
        'placing_ships': True,
        'player_ships': 0,
        'bot_ships': 3,
        'current_turn': 'player'
    }
    
    await query.edit_message_text(
        "🚢 Морской бой\n\n"
        "Разместите 3 корабля на своем поле.\n"
        "Выберите клетку для размещения корабля:",
        reply_markup=get_battleship_keyboard(user_id, show_player_field=True)
    )

def create_battleship_field():
    """Создать поле для морского боя 6x6"""
    return [['🌊' for _ in range(6)] for _ in range(6)]

def place_battleship_ships(field):
    """Разместить корабли на поле бота"""
    ships_placed = 0
    while ships_placed < 3:
        row = random.randint(0, 5)
        col = random.randint(0, 5)
        if field[row][col] == '🌊':
            field[row][col] = '🚢'
            ships_placed += 1

def get_battleship_keyboard(user_id: int, show_player_field: bool = False):
    """Создать клавиатуру для морского боя"""
    if user_id not in battleship_games:
        return None
    
    game = battleship_games[user_id]
    keyboard = []
    
    if show_player_field:
        # Показываем поле игрока для размещения кораблей
        field = game['player_field']
    else:
        # Показываем видимое поле бота для атаки
        field = game['bot_visible_field']
    
    for i in range(6):
        row = []
        for j in range(6):
            cell = field[i][j]
            callback_data = f'bs_{i}_{j}_{"place" if show_player_field else "attack"}'
            row.append(InlineKeyboardButton(cell, callback_data=callback_data))
        keyboard.append(row)
    
    return InlineKeyboardMarkup(keyboard)

async def handle_battleship_move(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработать ход в морском бою"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id not in battleship_games:
        await query.answer("Игра не найдена!")
        return
    
    game = battleship_games[user_id]
    data_parts = query.data.split('_')
    row, col, action = int(data_parts[1]), int(data_parts[2]), data_parts[3]
    
    if action == 'place':
        # Размещение кораблей игрока
        if game['player_field'][row][col] == '🌊':
            game['player_field'][row][col] = '🚢'
            game['ships_to_place'] -= 1
            game['player_ships'] += 1
            
            if game['ships_to_place'] == 0:
                # Все корабли размещены, начинаем игру
                game['placing_ships'] = False
                await query.edit_message_text(
                    "🚢 Морской бой\n\n"
                    "Корабли размещены! Начинаем бой!\n"
                    "Атакуйте поле противника:",
                    reply_markup=get_battleship_keyboard(user_id, show_player_field=False)
                )
            else:
                await query.edit_message_text(
                    f"🚢 Морской бой\n\n"
                    f"Осталось разместить кораблей: {game['ships_to_place']}\n"
                    "Выберите клетку для размещения корабля:",
                    reply_markup=get_battleship_keyboard(user_id, show_player_field=True)
                )
        else:
            await query.answer("Эта клетка уже занята!")
    
    elif action == 'attack':
        # Атака игрока
        if game['bot_visible_field'][row][col] not in ['🌊', '🔥']:
            await query.answer("Вы уже стреляли в эту клетку!")
            return
        
        if game['bot_field'][row][col] == '🚢':
            # Попадание
            game['bot_visible_field'][row][col] = '🔥'
            game['bot_ships'] -= 1
            
            if game['bot_ships'] == 0:
                # Игрок выиграл
                await query.edit_message_text(
                    "🚢 Морской бой\n\n"
                    "🎉 Поздравляю! Вы потопили все корабли противника!",
                    reply_markup=get_battleship_keyboard(user_id, show_player_field=False)
                )
                del battleship_games[user_id]
                return
            
            await query.edit_message_text(
                f"🚢 Морской бой\n\n"
                f"🔥 Попадание! Осталось кораблей противника: {game['bot_ships']}\n"
                "Продолжайте атаку:",
                reply_markup=get_battleship_keyboard(user_id, show_player_field=False)
            )
        else:
            # Промах
            game['bot_visible_field'][row][col] = '💨'
            
            # Ход бота
            bot_row, bot_col = get_bot_battleship_move(game['player_field'])
            if bot_row is not None:
                if game['player_field'][bot_row][bot_col] == '🚢':
                    # Бот попал
                    game['player_field'][bot_row][bot_col] = '🔥'
                    game['player_ships'] -= 1
                    
                    if game['player_ships'] == 0:
                        # Бот выиграл
                        await query.edit_message_text(
                            "🚢 Морской бой\n\n"
                            "😔 Противник потопил все ваши корабли! Вы проиграли!"
                        )
                        del battleship_games[user_id]
                        return
                    
                    await query.edit_message_text(
                        f"🚢 Морской бой\n\n"
                        f"💨 Промах! Противник попал в ваш корабль!\n"
                        f"Осталось ваших кораблей: {game['player_ships']}\n"
                        "Ваш ход:",
                        reply_markup=get_battleship_keyboard(user_id, show_player_field=False)
                    )
                else:
                    # Бот промахнулся
                    game['player_field'][bot_row][bot_col] = '💨'
                    await query.edit_message_text(
                        "🚢 Морской бой\n\n"
                        "💨 Промах! Противник тоже промахнулся.\n"
                        "Ваш ход:",
                        reply_markup=get_battleship_keyboard(user_id, show_player_field=False)
                    )

def get_bot_battleship_move(player_field):
    """Получить ход бота для морского боя"""
    # Простая стратегия: случайный выбор незатронутых клеток
    available_cells = []
    for i in range(6):
        for j in range(6):
            if player_field[i][j] in ['🌊', '🚢']:
                available_cells.append((i, j))
    
    if available_cells:
        return random.choice(available_cells)
    return None, None

async def post_init(application):
    """Функция, которая выполняется после инициализации бота"""
    # Здесь можно добавить логику, которая должна выполняться при запуске
    pass

def main():
    application = Application.builder().token(TOKEN).post_init(post_init).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(request_topic_name, pattern='^create_topic$'),
            CallbackQueryHandler(request_topic_id, pattern='^delete_topic$'),
            CallbackQueryHandler(request_broadcast_message, pattern='^create_broadcast$'),
            CallbackQueryHandler(request_rename_topics_count, pattern='^create_rename_topics$'),
            CallbackQueryHandler(button_handler, pattern='^confirm_rename_'),
            CallbackQueryHandler(start_guess_number_game, pattern='^game_guess_number$')
        ],
        states={
            WAITING_FOR_TOPIC_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_topic_with_name)],
            WAITING_FOR_TOPIC_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_topic_by_id)],
            WAITING_FOR_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_broadcast)],
            WAITING_FOR_RENAME_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_rename_topics)],
            WAITING_FOR_RENAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, rename_topic)],
            PLAYING_GUESS_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_guess_number)]
        },
        fallbacks=[]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("worker", worker_command))
    application.add_handler(CommandHandler("gadd", add_sos_word))
    application.add_handler(CommandHandler("gdel", delete_sos_word))
    application.add_handler(CommandHandler("gall", list_sos_words))
    application.add_handler(CommandHandler("grestore", restore_topic_icon))
    application.add_handler(CommandHandler("game", game_command))
    application.add_handler(CommandHandler("stopgame", stopgame_command))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(game_callback_handler, pattern='^(game_|rps_|ttt_|bj_|bs_|close_games)'))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()