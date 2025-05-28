import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, ConversationHandler
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

WAITING_FOR_TOPIC_NAME = 1
WAITING_FOR_TOPIC_ID = 2

# Словарь для хранения тем
topics_dict = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Список тем", callback_data='list_topics')],
        [InlineKeyboardButton("Создать тему", callback_data='create_topic')],
        [InlineKeyboardButton("Удалить тему", callback_data='delete_topic')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
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

async def create_topic_with_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic_name = update.message.text
    chat = await context.bot.get_chat(update.message.chat_id)
    
    if not await check_forum_support(chat.id, context):
        await update.message.reply_text(
            "Эта группа не поддерживает создание тем. "
            "Для использования тем группа должна быть настроена как форум."
        )
        return ConversationHandler.END
    
    try:
        topic = await context.bot.create_forum_topic(
            chat_id=chat.id,
            name=topic_name
        )
        
        # Сохраняем информацию о теме
        if chat.id not in topics_dict:
            topics_dict[chat.id] = {}
        topics_dict[chat.id][topic.message_thread_id] = topic_name
        
        await update.message.reply_text(
            f"Тема '{topic_name}' успешно создана!\n"
            f"ID темы: {topic.message_thread_id}"
        )
    except Exception as e:
        error_message = str(e)
        if "chat is not a forum" in error_message.lower():
            await update.message.reply_text(
                "Эта группа не поддерживает создание тем. "
                "Для использования тем группа должна быть настроена как форум. "
                "Обратитесь к администратору группы для настройки."
            )
        else:
            await update.message.reply_text(
                f"Ошибка при создании темы: {error_message}"
            )
    
    return ConversationHandler.END

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

def main():
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(request_topic_name, pattern='^create_topic$'),
            CallbackQueryHandler(request_topic_id, pattern='^delete_topic$')
        ],
        states={
            WAITING_FOR_TOPIC_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_topic_with_name)],
            WAITING_FOR_TOPIC_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_topic_by_id)]
        },
        fallbacks=[]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main() 