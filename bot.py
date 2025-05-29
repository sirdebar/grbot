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
    application.add_handler(CommandHandler("worker", worker_command))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main() 