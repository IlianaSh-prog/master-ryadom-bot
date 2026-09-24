import os
import sys
import time
import sqlite3
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot import types

# =====================================================================
# 1. ВЕБ-СЕРВЕР ДЛЯ РЕНДЕРА (ГАРАНТИРОВАННОЕ ОТКРЫТИЕ ПОРТА)
# =====================================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Service is Live!")

    def log_message(self, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# =====================================================================
# 2. ПРОВЕРКА ТОКЕНА И НАСТРОЙКИ ПЛАТФОРМЫ
# =====================================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    print("[ERROR] TELEGRAM_BOT_TOKEN is missing in Environment variables!")
    while True:
        time.sleep(10)

# ⚠️ ВСТАВЬТЕ СЮДА ВАШ ЛИЧНЫЙ ТЕЛЕГРАМ ID (Для получения отчетов и арбитража)
ADMIN_ID = 8725167633 

# ⚠️ Ссылка на вашу Оферту с Telegra.ph
OFFER_URL = "https://telegra.ph" 

bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=True)
db_lock = threading.Lock()

CITIES = [
    "Новосибирск", "Москва", "Санкт-Петербург", 
    "Екатеринбург", "Казань", "Краснодар"
]

DISTRICTS = [
    "Центральный", "Ленинский", "Кировский", "Октябрьский", 
    "Дзержинский", "Заельцовский", "Советский (Академ)", "Весь город"
]

CATEGORIES = {
    "cat_plumber": "🔧 Сантехника и отопление",
    "cat_electric": "⚡ Электрика и свет",
    "cat_repair": "🔨 Ремонт и отделка",
    "cat_cargo": "🚚 Грузоперевозки и грузчики",
    "cat_cleaning": "✨ Уборка и клининг",
    "cat_auto": "🚗 Автопомощь и эвакуатор"
}

user_states = {}

# =====================================================================
# 3. БАЗА ДАННЫХ SQLITE
# =====================================================================
def get_db():
    conn = sqlite3.connect("marketplace.db", timeout=60.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 60000;")
    return conn

def init_db():
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                city TEXT DEFAULT 'Новосибирск',
                district TEXT DEFAULT 'Весь город',
                role TEXT DEFAULT 'client',
                category TEXT DEFAULT NULL,
                rating REAL DEFAULT 5.0,
                completed_orders INTEGER DEFAULT 0,
                free_leads_left INTEGER DEFAULT 3,
                accepted_offer INTEGER DEFAULT 0,
                subscription_expires DATETIME DEFAULT NULL,
                is_banned INTEGER DEFAULT 0
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                city TEXT,
                district TEXT,
                category_key TEXT,
                category_title TEXT,
                description TEXT,
                budget TEXT,
                client_contact TEXT,
                status TEXT DEFAULT 'open',
                selected_master_id INTEGER DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS offers (
                offer_id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER,
                master_id INTEGER,
                offer_text TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()

init_db()

def register_user_if_not_exists(user_id, username, full_name):
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not c.fetchone():
            c.execute("INSERT INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
                      (user_id, username or "Без username", full_name))
            conn.commit()
        conn.close()

def get_user(user_id):
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            SELECT user_id, username, full_name, city, district, role, category, 
                   rating, completed_orders, free_leads_left, accepted_offer, subscription_expires, is_banned 
            FROM users WHERE user_id = ?
        """, (user_id,))
        row = c.fetchone()
        conn.close()
        return row

def is_master_active(user_id):
    user = get_user(user_id)
    if not user or user[12] == 1: # Забанен
        return False
    if user[9] > 0: # Остались бесплатные лиды
        return True
    sub_exp = user[11]
    if not sub_exp:
        return False
    try:
        exp_date = datetime.strptime(sub_exp, "%Y-%m-%d %H:%M:%S.%f") if "." in sub_exp else datetime.strptime(sub_exp, "%Y-%m-%d %H:%M:%S")
        return datetime.now() < exp_date
    except Exception:
        return False

# =====================================================================
# 4. ГЛАВНОЕ МЕНЮ
# =====================================================================
def get_main_menu(user_id):
    user = get_user(user_id)
    if not user or user[12] == 1:
        return None
        
    city = user[3] or "Новосибирск"
    district = user[4] or "Весь город"
    role = user[5]
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    btn_order = types.InlineKeyboardButton("📝 Опубликовать заявку", callback_data="client_create_order")
    btn_my_orders = types.InlineKeyboardButton("📂 Мои активные сделки", callback_data="client_my_orders")
    btn_geo = types.InlineKeyboardButton(f"📍 {city}, {district} (Сменить)", callback_data="select_city_menu")
    
    if role == 'master':
        cat_title = CATEGORIES.get(user[6], "Не выбрана")
        btn_master = types.InlineKeyboardButton(f"🛠 Кабинет профи ({cat_title})", callback_data="master_profile")
        btn_sub = types.InlineKeyboardButton("💳 Продлить подписку", callback_data="master_subscription_menu")
        btn_switch = types.InlineKeyboardButton("🔄 Режим заказчика", callback_data="switch_to_client")
        markup.add(btn_order, btn_my_orders, btn_master, btn_sub, btn_geo, btn_switch)
    else:
        btn_become_master = types.InlineKeyboardButton("💼 Стать исполнителем", callback_data="master_register")
        markup.add(btn_order, btn_my_orders, btn_become_master, btn_geo)
        
    return markup

# =====================================================================
# 5. СТАРТ И ЮРИДИЧЕСКАЯ ОФЕРТА
# =====================================================================
@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.first_name or "Пользователь"
    
    register_user_if_not_exists(user_id, username, full_name)
    user = get_user(user_id)
    
    if user[12] == 1:
        bot.send_message(message.chat.id, "⛔ Ваш аккаунт заблокирован за нарушение правил сервиса.")
        return
        
    user_states.pop(user_id, None)
    
    if not user[10]: # accepted_offer == 0
        text = (
            f"👋 **Здравствуйте, {full_name}!**\n\n"
            "Добро пожаловать в **«МастерРядом»** — платформу прямого взаимодействия заказчиков и специалистов.\n\n"
            "Перед началом работы ознакомьтесь с условиями Пользовательского соглашения."
        )
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("📄 Читать Публичную оферту", url=OFFER_URL))
        markup.add(types.InlineKeyboardButton("✅ Принимаю условия", callback_data="accept_legal_offer"))
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")
        return

    city, district = user[3], user[4]
    text = (
        f"👋 **Рад видеть вас, {full_name}!**\n"
        f"📍 Ваш регион: **{city} ({district})**\n\n"
        "Выберите нужное действие в меню ниже:"
    )
    bot.send_message(message.chat.id, text, reply_markup=get_main_menu(user_id), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "accept_legal_offer")
def callback_accept_offer(call):
    user_id = call.from_user.id
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET accepted_offer = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        
    markup = types.InlineKeyboardMarkup(row_width=2)
    for city in CITIES:
        markup.add(types.InlineKeyboardButton(city, callback_data=f"set_city_{city}"))
    markup.add(types.InlineKeyboardButton("✍️ Ввести другой город вручную", callback_data="input_custom_city"))
        
    bot.edit_message_text(
        "🏙 **Шаг 1 из 2: Выберите ваш город или введите его вручную:**",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data == "select_city_menu")
def callback_select_city_menu(call):
    markup = types.InlineKeyboardMarkup(row_width=2)
    for city in CITIES:
        markup.add(types.InlineKeyboardButton(city, callback_data=f"set_city_{city}"))
    markup.add(types.InlineKeyboardButton("✍️ Ввести другой город вручную", callback_data="input_custom_city"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu"))
    bot.edit_message_text("🏙 **Изменение региона:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "input_custom_city")
def callback_input_custom_city(call):
    user_id = call.from_user.id
    user_states[user_id] =
 {"step": "waiting_custom_city_name"}
    bot.send_message(call.message.chat.id, "🏙 **Напишите название вашего города в ответном сообщении:**\n*(Например: Тюмень, Сочи, Барнаул)*")

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_city_"))
def callback_set_city(call):
    city_name = call.data.replace("set_city_", "")
    user_id = call.from_user.id
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET city = ? WHERE user_id = ?", (city_name, user_id))
        conn.commit()
        conn.close()
        
    markup = types.InlineKeyboardMarkup(row_width=2)
    for dist in DISTRICTS:
        markup.add(types.InlineKeyboardButton(dist, callback_data=f"set_dist_{dist}"))
        
    bot.edit_message_text(
        f"📍 Город: **{city_name}**\n🏙 **Шаг 2 из 2: Выберите ваш рабочий район:**",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_dist_"))
def callback_set_dist(call):
    dist_name = call.data.replace("set_dist_", "")
    user_id = call.from_user.id
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET district = ? WHERE user_id = ?", (dist_name, user_id))
        conn.commit()
        conn.close()
        
    bot.edit_message_text(
        "✅ Регион успешно настроен!",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=get_main_menu(user_id),
        parse_mode="Markdown"
    )
    
    user = get_user(user_id)
    try:
        bot.send_message(ADMIN_ID, f"👤 **Новый пользователь в системе!**\nИмя: {user[2]} (@{user[1]})\nГород: {user[3]}, {user[4]}", parse_mode="Markdown")
    except Exception:
        pass

# =====================================================================
# 6. АДМИН-ПАНЕЛЬ И АНАЛИТИКА (/admin)
# =====================================================================
@bot.message_handler(commands=['admin', 'stats'])
def handle_admin(message):
    if message.from_user.id != ADMIN_ID:
        return
        
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE role = 'master'")
        total_masters = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM orders")
        total_orders = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM orders WHERE status = 'closed'")
        closed_orders = c.fetchone()[0]
        
        c.execute("SELECT city, COUNT(*) FROM orders GROUP BY city")
        city_stats = c.fetchall()
        
        c.execute("SELECT category_title, COUNT(*) FROM orders GROUP BY category_title")
        cat_stats = c.fetchall()
        
        c.execute("SELECT AVG(rating) FROM users WHERE role = 'master'")
        avg_rating = c.fetchone()[0] or 5.0
        conn.close()
        
    city_text = "\n".join([f"• {row[0]}: {row[1]}" for row in city_stats]) or "Нет данных"
    cat_text = "\n".join([f"• {row[0]}: {row[1]}" for row in cat_stats]) or "Нет данных"
    
    text = (
        "📊 **АНАЛИТИКА ПЛАТФОРМЫ:**\n\n"
        f"👥 Пользователей: **{total_users}**\n"
        f"🛠 Мастеров в базе: **{total_masters}**\n"
        f"📦 Всего заявок: **{total_orders}** (Сделок закрыто: {closed_orders})\n"
        f"⭐ Средний рейтинг системы: **{avg_rating:.1f} / 5.0**\n\n"
        f"🏙 **Города:**\n{city_text}\n\n"
        f"📌 **Ниши:**\n{cat_text}\n\n"
        "💡 *Для блокировки:* `/ban ID причина`"
    )
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['ban'])
def handle_ban(message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) > 2 and parts[1].isdigit():
        target_id = int(parts[1])
        reason = " ".join(parts[2:])
        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target_id,))
            conn.commit()
            conn.close()
        bot.reply_to(message, f"⛔ Пользователь {target_id} заблокирован.\nПричина: {reason}")
        try:
            bot.send_message(target_id, f"⛔ Ваш аккаунт заблокирован.\nПричина: {reason}")
        except Exception:
            pass

# =====================================================================
# 7. ПОДПИСКИ И ОПЛАТА
# =====================================================================
@bot.callback_query_handler(func=lambda call: call.data == "master_subscription_menu")
def callback_subscription_menu(call):
    user_id = call.from_user.id
    user = get_user(user_id)
    free_leads = user[9]
    sub_exp = user[11]
    
    status_str = "🟢 Активна" if is_master_active(user_id) else "🔴 Требуется пополнение"
    
    text = (
        "💳 **УПРАВЛЕНИЕ ПОДПИСКОЙ**\n\n"
        f"Статус: **{status_str}**\n"
        f"🎁 Бесплатных откликов: **{free_leads} шт.**\n"
        f"📅 Действует до: `{sub_exp if sub_exp else 'Нет подписки'}`\n\n"
        "Выберите тариф для непрерывной работы:"
    )
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("⭐ Неделя доступа (199 Stars)", callback_data="pay_stars_week"))
    markup.add(types.InlineKeyboardButton("⭐ Месяц доступа (499 Stars - ХИТ)", callback_data="pay_stars_month"))
    markup.add(types.InlineKeyboardButton("💳 Оплатить картой РФ (ЮKassa) [850 ₽/мес]", callback_data="pay_yookassa_month"))
    markup.add(types.InlineKeyboardButton("🔙 В кабинет", callback_data="master_profile"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data in ["pay_stars_week", "pay_stars_month"])
def callback_pay_stars(call):
    is_month = "month" in call.data
    amount = 499 if is_month else 199
    title = "Безлимит на 1 месяц (МастерРядом)" if is_month else "Безлимит на 1 неделю (МастерРядом)"
    
    prices = [types.LabeledPrice(label=title, amount=amount)]
    bot.send_invoice(
        chat_id=call.message.chat.id,
        title=title,
        description="Неограниченные отклики на заявки в вашем городе.",
        invoice_payload=f"sub_{'month' if is_month else 'week'}_{call.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=prices
    )

@bot.pre_checkout_query_handler(func=lambda q: True)
def process_pre_checkout(q):
    bot.answer_pre_checkout_query(q.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def process_payment(message):
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload
    amount_stars = message.successful_payment.total_amount
    days_to_add = 30 if "month" in payload else 7
    
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT subscription_expires FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        current_exp = datetime.now()
        if row and row[0]:
            try:
                exp_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S.%f") if "." in row[0] else datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                if exp_date > datetime.now():
                    current_exp = exp_date
            except Exception:
                pass
        new_exp = current_exp + timedelta(days=days_to_add)
        c.execute("UPDATE users SET subscription_expires = ? WHERE user_id = ?", (new_exp, user_id))
        conn.commit()
        conn.close()
        
    bot.send_message(message.chat.id, f"🎉 **Оплата прошла успешно!**\nПодписка продлена до: `{new_exp.strftime('%Y-%m-%d %H:%M')}`.", reply_markup=get_main_menu(user_id), parse_mode="Markdown")
    
    user = get_user(user_id)
    try:
        bot.send_message(ADMIN_ID, f"💰 **ОПЛАТА ПОДПИСКИ!**\nМастер: {user[2]} (@{user[1]})\nСумма: {amount_stars} Stars", parse_mode="Markdown")
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: call.data == "pay_yookassa_month")
def callback_yookassa_stub(call):
    bot.answer_callback_query(call.id, "ЮKassa подключается. Используйте Telegram Stars ⭐ для моментальной активации!", show_alert=True)

# =====================================================================
# 8. КАБИНЕТ МАСТЕРА И СОЗДАНИЕ ЗАКАЗОВ
# =====================================================================
@bot.callback_query_handler(func=lambda call: call.data == "master_register")
def callback_master_register(call):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for key, title in CATEGORIES.items():
        markup.add(types.InlineKeyboardButton(title, callback_data=f"set_cat_{key}"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu"))
    bot.edit_message_text("🛠 **Выберите вашу специализацию:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_cat_"))
def callback_set_category(call):
    cat_key = call.data.replace("set_cat_", "")
    user_id = call.from_user.id
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET role = 'master', category = ? WHERE user_id = ?", (cat_key, user_id))
        conn.commit()
        conn.close()
        
    bot.answer_callback_query(call.id, "Специализация сохранена")
    
    master_welcome_text = (
        "🎉 **Вы успешно зарегистрированы в реестре специалистов «МастерРядом»!**\n\n"
        "🎁 **Ваш стартовый бонус:** Вам начислено **3 бесплатных отклика** на заявки по вашей специализации.\n\n"
        "🚀 **С нашим сервисом ваши шансы на стабильный высокий доход возрастают в разы:** вам больше не нужно тратить деньги на рекламу и самостоятельный поиск клиентов. Бот автоматически направляет горячие заказы из вашего района прямо на экран телефона — вам остается только принять в работу подходящую заявку.\n\n"
        "🤝 **Начнем продуктивную работу — ваш новый клиент уже близко!**\n\n"
        "📲 **Рекомендуем держать уведомления включенными, чтобы первыми узнавать о новых заявках и оперативно брать их в работу.**"
    )
    
    bot.edit_message_text(master_welcome_text, call.message.chat.id, call.message.message_id, reply_markup=get_main_menu(user_id), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "switch_to_client")
def callback_switch_client(call):
    user_id = call.from_user.id
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET role = 'client' WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    bot.edit_message_text("Вы перешли в режим заказчика.", call.message.chat.id, call.message.message_id, reply_markup=get_main_menu(user_id))

@bot.callback_query_handler(func=lambda call: call.data == "master_profile")
def callback_master_profile(call):
    user = get_user(call.from_user.id)
    cat_title = CATEGORIES.get(user[6], "Не выбрана")
    sub_status = "🟢 Активна" if is_master_active(call.from_user.id) else "🔴 Требуется пополнение"
    
    text = (
        "💼 **КАБИНЕТ СПЕЦИАЛИСТА**\n\n"
        f"👤 Имя: **{user[2]}**\n"
        f"📍 Регион: **{user[3]}, {user[4]}**\n"
        f"📌 Ниша: **{cat_title}**\n"
        f"⭐ Рейтинг: **{user[7]:.1f} / 5.0** (Сделок: {user[8]})\n"
        f"🎁 Бесплатных откликов: **{user[9]} шт.**\n"
        f"💳 Статус: **{sub_status}**"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💳 Управление подпиской", callback_data="master_subscription_menu"))
    markup.add(types.InlineKeyboardButton("🔄 Сменить нишу", callback_data="master_register"))
    markup.add(types.InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_menu"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "client_create_order")
def callback_create_order(call):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for key, title in CATEGORIES.items():
        markup.add(types.InlineKeyboardButton(title, callback_data=f"order_cat_{key}"))
    markup.add(types.InlineKeyboardButton("🔙 Отмена", callback_data="back_to_menu"))
    bot.edit_message_text("📋 **Шаг 1 из 3: Выберите категорию:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("order_cat_"))
def callback_order_cat_selected(call):
    cat_key = call.data.replace("order_cat_", "")
    user_id = call.from_user.id
    user = get_user(user_id)
    city, district = user[3], user[4]
    
    user_states[user_id] = {"step": "waiting_description", "category_key": cat_key, "category_title": CATEGORIES.get(cat_key), "city": city, "district": district}
    bot.edit_message_text(f"📍 Регион: **{city}, {district}**\n\n📋 **Шаг 2 из 3: Опишите задачу (описание, адрес, удобное время):**", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

@bot.message_handler(func=lambda msg: True)
def handle_text_steps(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if user and user[12] == 1:
        return
        
    state = user_states.get(user_id)
    if not state:
        return
        
    step = state.get("step")
    
    # Ручной ввод города
    if step == "waiting_custom_city_name":
        custom_city = message.text.strip().title()
        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute("UPDATE users SET city = ?, district = 'Весь город' WHERE user_id = ?", (custom_city, user_id))
            conn.commit()
            conn.close()
            
        user_states.pop(user_id, None)
        bot.reply_to(message, f"✅ Ваш город успешно установлен: **{custom_city}**!", reply_markup=get_main_menu(user_id), parse_mode="Markdown")
        return
    
    if step == "waiting_description":
        state["description"] = message.text.strip()
        state["step"] = "waiting_budget"
        bot.reply_to(message, "📋 **Шаг 3 из 3: Укажите ваш бюджет и контакты (телефон / Telegram):**", parse_mode="Markdown")
        return

    elif step == "waiting_budget":
        budget_contact = message.text.strip()
        cat_key = state["category_key"]
        cat_title = state["category_title"]
        desc = state["description"]
        city = state["city"]
        district = state["district"]
        
        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute("INSERT INTO orders (client_id, city, district, category_key, category_title, description, budget, client_contact) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (user_id, city, district, cat_key, cat_title, desc, budget_contact, budget_contact))
            order_id = c.lastrowid
            
            c.execute("SELECT user_id FROM users WHERE role = 'master' AND category = ? AND city = ? AND user_id != ?", (cat_key, city, user_id))
            all_masters = [r[0] for r in c.fetchall()]
            conn.close()
            
        active_masters = [m_id for m_id in all_masters if is_master_active(m_id)]
        
        user_states.pop(user_id, None)
        bot.send_message(message.chat.id, f"✅ Заявка №{order_id} опубликована! Уведомлено специалистов в г. {city}: {len(active_masters)}", reply_markup=get_main_menu(user_id))
        
        order_card = f"🚨 **НОВЫЙ ЗАКАЗ №{order_id} ({city}, {district})!**\n\n📌 Ниша: **{cat_title}**\n📝 {desc}\n💰 {budget_contact}"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📩 Откликнуться", callback_data=f"apply_order_{order_id}"))
        
        for m_id in active_masters:
            try:
                bot.send_message(m_id, order_card, reply_markup=markup, parse_mode="Markdown")
            except Exception:
                pass
                
        # Сигнал админу, если мастеров мало
        if len(active_masters) < 3:
            try:
                bot.send_message(ADMIN_ID, f"⚠️ **МАЛО МАСТЕРОВ В ГОРОДЕ!**\nГород: {city} | Ниша: {cat_title}\nЗаказ №{order_id}: {desc}", parse_mode="Markdown")
            except Exception:
                pass
        return

    elif step == "waiting_master_offer":
        order_id = state["order_id"]
        offer_text = message.text.strip()
        
        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT status, client_id FROM orders WHERE order_id = ?", (order_id,))
            order_data = c.fetchone()
            if not order_data or order_data[0] != 'open':
                bot.reply_to(message, "❌ Заказ уже закрыт.")
                user_states.pop(user_id, None)
                conn.close()
                return
            client_id = order_data[1]
            c.execute("INSERT INTO offers (order_id, master_id, offer_text) VALUES (?, ?, ?)", (order_id, user_id, offer_text))
            
            c.execute("SELECT free_leads_left FROM users WHERE user_id = ?", (user_id,))
            fl = c.fetchone()[0]
            if fl > 0:
                c.execute("UPDATE users SET free_leads_left = free_leads_left - 1 WHERE user_id = ?", (user_id,))
                conn.commit()
                
            c.execute("SELECT full_name, username, rating, completed_orders FROM users WHERE user_id = ?", (user_id,))
            m_info = c.fetchone()
            conn.close()
            
        user_states.pop(user_id, None)
        bot.reply_to(message, "✅ Ваш отклик успешно отправлен заказчику!")
        
        client_msg = f"🔔 **Новый отклик на заявку №{order_id}!**\n\n👤 Мастер: **{m_info[0]}** (@{m_info[1]})\n⭐ Рейтинг: **{m_info[2]:.1f}** (Сделок: {m_info[3]})\n💬 {offer_text}"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🤝 Принять мастера", callback_data=f"accept_master_{order_id}_{user_id}"))
        try:
            bot.send_message(client_id, client_msg, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            pass
        return

# =====================================================================
# 9. СДЕЛКИ И АРБИТРАЖ
# =====================================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("apply_order_"))
def callback_apply_order(call):
    if not is_master_active(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ У вас закончились бесплатные лиды. Продлите подписку!", show_alert=True)
        return
        
    order_id = int(call.data.replace("apply_order_", ""))
    user_states[call.from_user.id] = {"step": "waiting_master_offer", "order_id": order_id}
    bot.send_message(call.message.chat.id, f"✍️ Напишите ваше предложение к заказу №{order_id}:")

@bot.callback_query_handler(func=lambda call: call.data.startswith("accept_master_"))
def callback_accept_master(call):
    parts = call.data.split("_")
    order_id, master_id, client_id = int(parts[2]), int(parts[3]), call.from_user.id
    
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE orders SET status = 'in_progress', selected_master_id = ? WHERE order_id = ?", (master_id, order_id))
        c.execute("SELECT client_contact, description FROM orders WHERE order_id = ?", (order_id,))
        order = c.fetchone()
        c.execute("SELECT full_name, username FROM users WHERE user_id = ?", (master_id,))
        master_info = c.fetchone()
        c.execute("SELECT full_name, username FROM users WHERE user_id = ?", (client_id,))
        client_info = c.fetchone()
        conn.commit()
        conn.close()
        
    bot.edit_message_text(f"🤝 Вы выбрали мастера **{master_info[0]}** (@{master_info[1]}). Заказ переведен в работу!", call.message.chat.id, call.message.message_id)
    try:
        bot.send_message(master_id, f"🎉 Вас выбрали исполнителем по заказу №{order_id}!\n📞 Контакты: {order[0]}\n📝 {order[1]}")
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: call.data == "client_my_orders")
def callback_my_orders(call):
    user_id = call.from_user.id
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT order_id, category_title, description, status FROM orders WHERE client_id = ? ORDER BY order_id DESC LIMIT 10", (user_id,))
        rows = c.fetchall()
        conn.close()
        
    if not rows:
        bot.answer_callback_query(call.id, "У вас нет активных заказов.", show_alert=True)
        return
        
    text = "📂 **ВАШИ ЗАКАЗЫ И ИСТОРИЯ:**\n\n"
    markup = types.InlineKeyboardMarkup(row_width=1)
    for r in rows:
        order_id, cat, desc, status = r[0], r[1], r[2][:25], r[3]
        if status == "open":
            markup.add(types.InlineKeyboardButton(f"❌ Отменить №{order_id}", callback_data=f"cancel_order_{order_id}"))
        elif status == "in_progress":
            markup.add(types.InlineKeyboardButton(f"✅ Работы выполнены! Оценить мастерa (№{order_id})", callback_data=f"finish_order_{order_id}"))
        text += f"• **№{order_id}** ({cat}) — {status}\n"
        
    markup.add(types.InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_menu"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_order_"))
def callback_cancel(call):
    order_id = int(call.data.replace("cancel_order_", ""))
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE orders SET status = 'closed' WHERE order_id = ?", (order_id,))
        conn.commit()
        conn.close()
    bot.answer_callback_query(call.id, "Заказ отменен.")
    callback_my_orders(call)

@bot.callback_query_handler(func=lambda call: call.data.startswith("finish_order_"))
def callback_finish(call):
    order_id = int(call.data.replace("finish_order_", ""))
    markup = types.InlineKeyboardMarkup(row_width=5)
    stars = [types.InlineKeyboardButton(f"⭐ {i}", callback_data=f"rate_{order_id}_{i}") for i in range(1, 6)]
    markup.add(*stars)
    bot.edit_message_text(f"🏆 Оцените качество работы мастера по заказу №{order_id}:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("rate_"))
def callback_rate(call):
    parts = call.data.split("_")
    order_id, score = int(parts[1]), int(parts[2])
    client_id = call.from_user.id
    
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT selected_master_id, category_title FROM orders WHERE order_id = ?", (order_id,))
        row = c.fetchone()
        
        if row and row[0]:
            master_id, cat_title = row[0], row[1]
            c.execute("UPDATE orders SET status = 'closed' WHERE order_id = ?", (order_id,))
            c.execute("SELECT rating, completed_orders, full_name, username FROM users WHERE user_id = ?", (master_id,))
            m_stat = c.fetchone()
            cur_rating, cur_orders, m_name, m_username = m_stat[0], m_stat[1], m_stat[2], m_stat[3]
            
            new_orders = cur_orders + 1
            new_rating = round(((cur_rating * cur_orders) + score) / new_orders, 2)
            c.execute("UPDATE users SET rating = ?, completed_orders = ? WHERE user_id = ?", (new_rating, new_orders, master_id))
            conn.commit()
            
            if score <= 3:
                c.execute("SELECT full_name, username FROM users WHERE user_id = ?", (client_id,))
                c_info = c.fetchone()
                alert_text = (
                    f"⚠️ **АРБИТРАЖ: НИЗКАЯ ОЦЕНКА ({score}/5)!**\n\n"
                    f"📦 Заказ №{order_id} ({cat_title})\n"
                    f"🛠 Мастер: **{m_name}** (ID: `{master_id}`, @{m_username})\n"
                    f"👤 Заказчик: **{c_info[0]}** (ID: `{client_id}`, @{c_info[1]})\n\n"
                    "💡 *Свяжитесь с заказчиком и уточните причину!*\n"
                    f"Для бана мастера отправите: `/ban {master_id} [причина]`"
                )
                try:
                    bot.send_message(ADMIN_ID, alert_text, parse_mode="Markdown")
                except Exception:
                    pass
                    
            try:
                bot.send_message(master_id, f"🌟 Заказчик оценил работу на **{score} из 5** (Заказ №{order_id}). Рейтинг: {new_rating:.1f}")
            except Exception:
                pass
                
        conn.close()
    bot.edit_message_text("🎉 Спасибо за оценку! Сделка закрыта.", call.message.chat.id, call.message.message_id, reply_markup=get_main_menu(client_id))

@bot.callback_query_handler(func=lambda call: call.data == "back_to_menu")
def callback_back(call):
    bot.edit_message_text("Главное меню:", call.message.chat.id, call.message.message_id, reply_markup=get_main_menu(call.from_user.id))

# =====================================================================
# 10. ЗАПУСК БОТА
# =====================================================================
if __name__ == "__main__":
    bot.infinity_polling()
