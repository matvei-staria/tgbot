# main_bot.py

import logging
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, ConversationHandler, CallbackQueryHandler, filters
from bs4 import BeautifulSoup

# Подключаем модуль config
import config
from database import build_or_load_faiss_index, load_model

# Этапы диалога
NAME, PHONE, PROBLEM = range(3)
SEARCH_GOODS = 100
SHOW_SEARCH_RESULTS = 200

# Клавиатура
keyboard = [['Ассортимент', 'Проблема']]
reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=False)

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# === Вспомогательные функции ===

def clean_html(text):
    """Очистка HTML-тегов и замена <br> на \n"""
    if not text or text.lower() == 'nan':
        return ""
    text = text.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
    soup = BeautifulSoup(text, 'html.parser')
    return soup.get_text().strip()

def get_caption(good):
    caption = (
        f"<b>{good['title']}</b>\n"
        f"<i>{good['category']}</i>\n\n"
        f"{clean_html(good['text'])}\n\n"
    )
    if good['price']:
        caption += f"<b>💰 Цена:</b> {good['price']} руб"
    return caption

# === Команды бота ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)

# === Поиск товаров ===

async def enter_search_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("[DEBUG] Нажата кнопка АССОРТИМЕНТ")
    await update.message.reply_text('Введите ваш запрос (например, «подарочные пазлы», «книги про семью»):')
    return SEARCH_GOODS

async def handle_user_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    print(f"[DEBUG] Получен запрос: {query}")
    await update.message.reply_text('🔍 Обрабатываю ваш запрос...')

    context.user_data['query'] = query

    try:
        # Генерация эмбеддинга
        embedding = model.encode([query])
        embedding = normalize(embedding).astype('float32')
    except Exception as e:
        print(f"[ERROR] encode: {e}")
        await update.message.reply_text("❌ Не удалось обработать ваш запрос.")
        return ConversationHandler.END

    try:
        D, I = index.search(embedding, 5)
    except Exception as e:
        print(f"[ERROR] search: {e}")
        await update.message.reply_text("❌ Не удалось выполнить поиск.")
        return ConversationHandler.END

    results = []
    for i in I[0]:
        if i >= 0 and i < len(goods_metadata):
            results.append(goods_metadata[i])

    if not results:
        print("[INFO] Ничего не найдено")
        await update.message.reply_text("🤷‍♂️ Ничего не найдено.")
        return ConversationHandler.END

    context.user_data['search_results'] = results
    context.user_data['result_index'] = 0

    await send_search_result(update, context)
    return SHOW_SEARCH_RESULTS

async def send_search_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = context.user_data['search_results']
    index = context.user_data['result_index']

    good = results[index]
    photos = good['photos']
    current_photo = photos[0] if photos else None

    caption = get_caption(good)

    buttons = [
        [InlineKeyboardButton("⬅️ Назад", callback_data='prev'),
         InlineKeyboardButton("В меню", callback_data='menu'),
         InlineKeyboardButton("➡️ Следующий", callback_data='next')]
    ]

    if good['url']:
        buttons.insert(0, [InlineKeyboardButton("🌐 Перейти к товару", url=good['url'])])

    reply_markup = InlineKeyboardMarkup(buttons)

    try:
        if current_photo:
            await update.effective_message.reply_photo(
                photo=current_photo,
                caption=caption,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await update.effective_message.reply_text(
                text=caption,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
    except Exception as e:
        await update.message.reply_text("⚠️ Ошибка при отправке товара.")
        print(f"[ERROR] send_search_result: {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    results = context.user_data.get('search_results', [])
    index = context.user_data.get('result_index', 0)

    if not results:
        return

    if query.data == 'next':
        if index < len(results) - 1:
            context.user_data['result_index'] += 1
            await query.edit_message_media()
            await send_search_result(query, context)
        else:
            await query.answer("Это последний товар.")

    elif query.data == 'prev':
        if index > 0:
            context.user_data['result_index'] -= 1
            await query.edit_message_media()
            await send_search_result(query, context)
        else:
            await query.answer("Это первый товар.")

    elif query.data == 'menu':
        await query.edit_message_caption(caption="Вы вернулись в главное меню.")
        return ConversationHandler.END

    return SHOW_SEARCH_RESULTS

# === Проблема ===

async def report_problem_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Введите ваше ФИО:')
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text
    await update.message.reply_text('Введите номер телефона или ник в Telegram:')
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['phone'] = update.message.text
    await update.message.reply_text('Опишите вашу проблему:')
    return PROBLEM

async def get_problem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['problem'] = update.message.text

    name = context.user_data['name']
    phone = context.user_data['phone']
    problem = context.user_data['problem']

    message = (
        f"🔴 Новая проблема:\n\n"
        f"ФИО: {name}\n"
        f"Контакт: {phone}\n"
        f"Проблема: {problem}"
    )

    try:
        await context.bot.send_message(chat_id=config.GROUP_CHAT_ID, text=message)
    except Exception as e:
        await update.message.reply_text("Ошибка при отправке в группу.")
        print(f"[ERROR] send to group: {e}")

    save_problem(name, phone, problem)
    await update.message.reply_text("Спасибо! Ваша проблема передана.")
    return ConversationHandler.END

def save_problem(name, phone, problem):
    import csv
    from datetime import datetime
    file_exists = False
    try:
        with open(config.PROBLEMS_CSV, 'r'):
            file_exists = True
    except FileNotFoundError:
        pass

    with open(config.PROBLEMS_CSV, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['ФИО', 'Контакт', 'Проблема', 'Дата'])
        date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        writer.writerow([name, phone, problem, date])

# === Обработчики ===

def create_assortment_conversation():
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Text(['Ассортимент']), enter_search_mode)],
        states={
            SEARCH_GOODS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_query)],
            SHOW_SEARCH_RESULTS: [CallbackQueryHandler(button_handler)]
        },
        fallbacks=[CommandHandler('cancel', lambda u, c: u.message.reply_text("Действие отменено"))],
        per_message=True
    )

def create_problem_conversation():
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Text(['Проблема']), report_problem_start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            PROBLEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_problem)],
        },
        fallbacks=[CommandHandler('cancel', lambda u, c: u.message.reply_text("Действие отменено"))]
    )

# === Запуск бота ===

def main():
    application = ApplicationBuilder().token(config.BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(create_assortment_conversation())
    application.add_handler(create_problem_conversation())

    print("[INFO] Бот запущен...")
    application.run_polling()

if __name__ == '__main__':
    model = load_model()
    print('io')
    index, goods_metadata = build_or_load_faiss_index(model)
    main()
