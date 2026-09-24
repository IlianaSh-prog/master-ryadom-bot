import os
import sqlite3
import threading
from datetime import datetime
import telebot
from telebot import types

# [ Блок веб-сервера и настроек как в предыдущем коде ... ]
# ⚠️ ОБЯЗАТЕЛЬНО УКАЖИТЕ ВАШ ADMIN_ID
ADMIN_ID = 8725167633

bot = telebot.TeleBot(os.environ.get("TELEGRAM_BOT_TOKEN"), threaded=True)
db_lock = threading.Lock()

# Список ниш
CATEGORIES = {
    "cat_plumber": "🔧 Сантехника",
    "cat_electric": "⚡ Электрика",
    "cat_repair": "🔨 Ремонт",
    "cat_cargo": "🚚 Грузоперевозки",
    "cat_cleaning": "✨ Клининг",
    "cat_auto": "🚗 Автопомощь"
}

# =====================================================================
# ДОПОЛНЕННАЯ БАЗА ДАННЫХ
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect("global_market.db")
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, 
            city TEXT DEFAULT 'Не указан', role TEXT DEFAULT 'client', 
            category TEXT DEFAULT NULL, rating REAL DEFAULT 5.0, 
            completed_orders INTEGER DEFAULT 0, sub_exp DATETIME DEFAULT NULL,
            is_banned INTEGER DEFAULT 0, balance REAL DEFAULT 0.0)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER,
            city TEXT, category_key TEXT, category_title TEXT,
            description TEXT, budget TEXT, status TEXT DEFAULT 'open',
            selected_master_id INTEGER DEFAULT NULL)''')
        conn.commit()
        conn.close()

init_db()

# =====================================================================
# ФИШКА: ГЕНЕРАТОР ПРИГЛАШЕНИЙ ДЛЯ ВНЕШНИХ МАСТЕРОВ
# =====================================================================
def generate_invite_text(order_id, city, category):
    bot_link = f"https://t.me/{(bot.get_me().username)}?start=order_{order_id}"
    text = (
        f"🤝 Здравствуйте! Для вас есть новый заказ в г. {city}.\n"
        f"📌 Сфера: {category}\n\n"
        f"Посмотреть детали и забрать заказ можно по ссылке:\n{bot_link}"
    )
    return text

# =====================================================================
# ОБРАБОТКА КОМАНДЫ /START (С поддержкой реферального входа)
# =====================================================================
@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    args = message.text.split()
    
    # Регистрация (упрощенно)
    with db_lock:
        conn = sqlite3.connect("global_market.db")
        conn.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?,?,?)", 
                     (uid, message.from_user.username, message.from_user.first_name))
        conn.commit()
        conn.close()

    # Если мастер пришел по прямой ссылке на заказ
    if len(args) > 1 and args[1].startswith("order_"):
        order_id = args[1].replace("order_", "")
        bot.send_message(uid, f"👋 Вы перешли по приглашению на заказ №{order_id}!\nЧтобы его забрать, пройдите регистрацию как мастер.")
        # Логика регистрации мастера...
        return

    bot.send_message(uid, "👋 Добро пожаловать в МастерРядом!", reply_markup=get_main_menu(uid))

# =====================================================================
# АДМИН-ФУНКЦИИ (УСКОРЕНИЕ ПРОЦЕССА)
# =====================================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_fast_invite_"))
def admin_fast_invite(call):
    order_id = call.data.replace("admin_fast_invite_", "")
    # Получаем данные заказа из БД (упрощенно)
    # Генерируем текст
    invite_msg = generate_invite_text(order_id, "Омск", "Сантехника")
    bot.send_message(ADMIN_ID, f"📋 **Скопируйте и отправьте мастеру:**\n\n`{invite_msg}`", parse_mode="Markdown")

# =====================================================================
# РОЛЬ "МЕНЕДЖЕР-СКАУТ" (Для ваших помощников)
# =====================================================================
@bot.callback_query_handler(func=lambda c: c.data == "reg_manager")
def reg_manager(call):
    # Логика регистрации вашего помощника
    bot.send_message(call.from_user.id, "👔 Вы стали Менеджером-скаутом! Теперь вы видите заказы без мастеров и можете приводить исполнителей за бонус.")

# [ Остальной код как в предыдущих версиях ... ]

def get_main_menu(uid):
    # Меню теперь зависит от роли: Клиент, Мастер или Менеджер
    pass
