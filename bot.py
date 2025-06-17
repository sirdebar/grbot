import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, ConversationHandler
from telegram.error import RetryAfter
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

WAITING_FOR_TOPIC_NAME = 1
WAITING_FOR_TOPIC_ID = 2
WAITING_FOR_BROADCAST = 3
WAITING_FOR_RENAME_COUNT = 4
WAITING_FOR_RENAME = 5

# Словарь для хранения тем
topics_dict = {}
workers_dict = {}
rename_topics_dict = {}
# Список SOS-слов
sos_words = set()
# Словарь для хранения оригинальных аватарок чатов
original_avatars = {}

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
                logging.error(f"Ошибка при создании темы {i}: {str(e)}")

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
    message_text = update.message.text.lower()
    logging.info(f"Проверка сообщения: {message_text}")

    # Проверяем, содержит ли сообщение SOS-слово
    for word in sos_words:
        if word in message_text:
            logging.info(f"Найдено SOS-слово: {word}")
            try:
                # Проверяем все темы в чате
                if chat_id in topics_dict:
                    for topic_id in topics_dict[chat_id].keys():
                        try:
                            # Сохраняем оригинальную иконку темы, если её ещё нет
                            if (chat_id, topic_id) not in original_avatars:
                                try:
                                    topic = await context.bot.get_forum_topic(chat_id=chat_id, message_thread_id=topic_id)
                                    if topic and topic.icon_custom_emoji_id:
                                        original_avatars[(chat_id, topic_id)] = topic.icon_custom_emoji_id
                                except Exception as e:
                                    logging.error(f"Ошибка при получении информации о теме {topic_id}: {str(e)}")

                            # Меняем иконку темы на SOS
                            await context.bot.edit_forum_topic(
                                chat_id=chat_id,
                                message_thread_id=topic_id,
                                icon_custom_emoji_id="🆘"
                            )
                            logging.info(f"Иконка темы {topic_id} успешно изменена на SOS")
                        except Exception as e:
                            logging.error(f"Ошибка при изменении иконки темы {topic_id}: {str(e)}")
                            # Пробуем альтернативный метод
                            try:
                                await context.bot.edit_forum_topic(
                                    chat_id=chat_id,
                                    message_thread_id=topic_id,
                                    name=topics_dict[chat_id][topic_id],
                                    icon_custom_emoji_id="🆘"
                                )
                                logging.info(f"Иконка темы {topic_id} успешно изменена альтернативным методом")
                            except Exception as e2:
                                logging.error(f"Ошибка при альтернативном изменении иконки темы {topic_id}: {str(e2)}")
            except Exception as e:
                logging.error(f"Общая ошибка при обработке SOS-слова: {str(e)}")

async def restore_topic_icon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    message_thread_id = update.message.message_thread_id
    
    if (chat_id, message_thread_id) in original_avatars:
        try:
            await context.bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                icon_custom_emoji_id=original_avatars[(chat_id, message_thread_id)]
            )
            del original_avatars[(chat_id, message_thread_id)]
        except Exception as e:
            logging.error(f"Ошибка при восстановлении иконки темы: {str(e)}")

async def test_sos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        await update.message.reply_text("Эта команда доступна только администраторам")
        return

    if not context.args:
        await update.message.reply_text("Использование: /gtest слово")
        return

    word = context.args[0].lower()
    if word not in sos_words:
        await update.message.reply_text(f"Слово '{word}' не найдено в списке SOS-слов")
        return

    # Создаем тестовое сообщение
    test_message = type('TestMessage', (), {
        'chat_id': update.message.chat_id,
        'text': word,
        'message_thread_id': update.message.message_thread_id
    })
    
    # Создаем тестовый update
    test_update = type('TestUpdate', (), {'message': test_message})
    
    # Вызываем проверку
    await check_sos_word(test_update, context)
    await update.message.reply_text("Тест SOS-слова выполнен")

def main():
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(request_topic_name, pattern='^create_topic$'),
            CallbackQueryHandler(request_topic_id, pattern='^delete_topic$'),
            CallbackQueryHandler(request_broadcast_message, pattern='^create_broadcast$'),
            CallbackQueryHandler(request_rename_topics_count, pattern='^create_rename_topics$'),
            CallbackQueryHandler(button_handler, pattern='^confirm_rename_')
        ],
        states={
            WAITING_FOR_TOPIC_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_topic_with_name)],
            WAITING_FOR_TOPIC_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_topic_by_id)],
            WAITING_FOR_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_broadcast)],
            WAITING_FOR_RENAME_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_rename_topics)],
            WAITING_FOR_RENAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, rename_topic)]
        },
        fallbacks=[]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("worker", worker_command))
    application.add_handler(CommandHandler("gadd", add_sos_word))
    application.add_handler(CommandHandler("gdel", delete_sos_word))
    application.add_handler(CommandHandler("gall", list_sos_words))
    application.add_handler(CommandHandler("grestore", restore_topic_icon))
    application.add_handler(CommandHandler("gtest", test_sos))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_sos_word))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main() 