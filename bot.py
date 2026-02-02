import logging
import sqlite3
import random
import string
from datetime import datetime, timedelta
from contextlib import contextmanager
from io import BytesIO
from collections import defaultdict
import telebot
from telebot import types
from PIL import Image, ImageDraw, ImageFont

# ⚠️ ВСТАВЬТЕ ВАШ ТОКЕН БОТА СЮДА! ⚠️
BOT_TOKEN = "8205991086:AAEhQIz1TB3T2vm8_OYkNTqEZO4GEl6mKCw"
# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = telebot.TeleBot(BOT_TOKEN)

# ID администраторов
ADMINS = [2201994016, 2200422849]
ADMIN_BALANCE = 999999999999999

# Состояния пользователей
user_states = {}
user_data = defaultdict(dict)

# Анти-DDoS защита
request_limits = defaultdict(list)
login_attempts = defaultdict(int)
BLOCK_TIME = 3600  # 1 час блокировки

# Максимальные лимиты
MAX_REQUESTS_PER_MINUTE = 30
MAX_LOGIN_ATTEMPTS = 5
MAX_CAPTCHA_ATTEMPTS = 3

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('wallet_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0,
            rating REAL DEFAULT 5.0,
            rating_count INTEGER DEFAULT 0,
            banned_until TIMESTAMP,
            is_banned BOOLEAN DEFAULT FALSE,
            login_attempts INTEGER DEFAULT 0,
            last_login_attempt TIMESTAMP,
            request_count INTEGER DEFAULT 0,
            last_request TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_address TEXT,
            to_address TEXT,
            amount REAL,
            type TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS p2p_deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER,
            buyer_id INTEGER,
            amount REAL,
            status TEXT,
            rating INTEGER,
            feedback TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_id TEXT UNIQUE,
            creator_id INTEGER,
            amount REAL,
            claimed_by INTEGER,
            claimed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS captchas (
            user_id INTEGER PRIMARY KEY,
            captcha_text TEXT,
            attempts INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ip_address TEXT,
            reason TEXT,
            blocked_until TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    for admin_id in ADMINS:
        cursor.execute(
            'INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, ?)',
            (admin_id, ADMIN_BALANCE)
        )
    
    conn.commit()
    conn.close()

@contextmanager
def get_db():
    conn = sqlite3.connect('wallet_bot.db')
    conn.row_factory = sqlite3.Row
    try:
        yield conn.cursor()
        conn.commit()
    finally:
        conn.close()

def check_rate_limit(user_id: int):
    now = datetime.now()
    request_limits[user_id] = [t for t in request_limits[user_id] if now - t < timedelta(minutes=1)]
    request_limits[user_id].append(now)
    
    if len(request_limits[user_id]) > MAX_REQUESTS_PER_MINUTE:
        block_time = now + timedelta(seconds=BLOCK_TIME)
        with get_db() as cursor:
            cursor.execute(
                'UPDATE users SET banned_until = ?, is_banned = TRUE WHERE user_id = ?',
                (block_time, user_id)
            )
            cursor.execute(
                'INSERT INTO blocks (user_id, reason, blocked_until) VALUES (?, ?, ?)',
                (user_id, 'Rate limit exceeded', block_time)
            )
        return False
    return True

def check_user_blocked(user_id: int) -> bool:
    with get_db() as cursor:
        cursor.execute(
            'SELECT banned_until, is_banned FROM users WHERE user_id = ?',
            (user_id,)
        )
        user = cursor.fetchone()
        
        if user and user['is_banned']:
            if user['banned_until']:
                try:
                    banned_until = datetime.fromisoformat(user['banned_until'])
                    if datetime.now() < banned_until:
                        return True
                    else:
                        cursor.execute(
                            'UPDATE users SET is_banned = FALSE, banned_until = NULL WHERE user_id = ?',
                            (user_id,)
                        )
                        return False
                except:
                    cursor.execute(
                        'UPDATE users SET is_banned = FALSE, banned_until = NULL WHERE user_id = ?',
                        (user_id,)
                    )
                    return False
    return False

def increment_login_attempts(user_id: int):
    now = datetime.now()
    login_attempts[user_id] += 1
    
    with get_db() as cursor:
        cursor.execute(
            'UPDATE users SET login_attempts = login_attempts + 1, last_login_attempt = ? WHERE user_id = ?',
            (now, user_id)
        )
        
        if login_attempts[user_id] >= MAX_LOGIN_ATTEMPTS:
            block_time = now + timedelta(hours=24)
            cursor.execute(
                'UPDATE users SET banned_until = ?, is_banned = TRUE WHERE user_id = ?',
                (block_time, user_id)
            )
            cursor.execute(
                'INSERT INTO blocks (user_id, reason, blocked_until) VALUES (?, ?, ?)',
                (user_id, 'Too many login attempts', block_time)
            )

def reset_login_attempts(user_id: int):
    login_attempts[user_id] = 0
    with get_db() as cursor:
        cursor.execute(
            'UPDATE users SET login_attempts = 0 WHERE user_id = ?',
            (user_id,)
        )

def generate_captcha():
    text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    
    image = Image.new('RGB', (200, 80), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    
    for _ in range(1000):
        x = random.randint(0, 199)
        y = random.randint(0, 79)
        draw.point((x, y), fill=(
            random.randint(0, 255),
            random.randint(0, 255),
            random.randint(0, 255)
        ))
    
    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except:
        font = ImageFont.load_default()
    
    for i, char in enumerate(text):
        x = 20 + i * 30 + random.randint(-5, 5)
        y = 20 + random.randint(-5, 5)
        draw.text((x, y), char, font=font, fill=(
            random.randint(0, 150),
            random.randint(0, 150),
            random.randint(0, 150)
        ))
    
    img_byte_arr = BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    return text, img_byte_arr

def create_address(user_id: int) -> str:
    return f"dQ{user_id}"

def get_main_menu(user_id: int):
    with get_db() as cursor:
        cursor.execute('SELECT balance, is_banned FROM users WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        is_banned = user['is_banned'] if user else False
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    if is_banned:
        markup.add(types.KeyboardButton("📞 Написать админу"))
        return markup
    
    markup.row(
        types.KeyboardButton("👤 Мой кошелек"),
        types.KeyboardButton("💸 Перевести")
    )
    markup.row(
        types.KeyboardButton("📊 P2P рынок"),
        types.KeyboardButton("🧾 Чеки")
    )
    markup.row(
        types.KeyboardButton("📥 Пополнить"),
        types.KeyboardButton("📤 Вывести")
    )
    markup.row(
        types.KeyboardButton("📊 Статистика"),
        types.KeyboardButton("ℹ️ О нас")
    )
    markup.row(
        types.KeyboardButton("📢 Наш канал")
    )
    
    if user_id in ADMINS:
        markup.row(types.KeyboardButton("👑 Админ-панель"))
    
    return markup

def get_cancel_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🚫 Отменить"))
    return markup

def anti_ddos_middleware(handler):
    def wrapper(message):
        user_id = message.from_user.id
        
        if user_id in ADMINS:
            return handler(message)
        
        if check_user_blocked(user_id):
            bot.send_message(user_id, "🚫 Ваш аккаунт временно заблокирован за нарушение правил.")
            return
        
        if not check_rate_limit(user_id):
            bot.send_message(user_id, "⚠️ Слишком много запросов. Попробуйте позже.")
            return
        
        return handler(message)
    
    return wrapper

@bot.message_handler(commands=['start'])
@anti_ddos_middleware
def start(message):
    user_id = message.from_user.id
    
    if check_user_blocked(user_id):
        with get_db() as cursor:
            cursor.execute('SELECT banned_until FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
            if user and user['banned_until']:
                bot.send_message(user_id, f"🚫 Ваш кошелек заблокирован до {user['banned_until']}")
                return
    
    if login_attempts.get(user_id, 0) >= MAX_LOGIN_ATTEMPTS:
        bot.send_message(user_id, "🚫 Слишком много попыток входа. Попробуйте через 24 часа.")
        return
    
    with get_db() as cursor:
        cursor.execute(
            'INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)',
            (user_id, message.from_user.username)
        )
    
    captcha_text, captcha_image = generate_captcha()
    with get_db() as cursor:
        cursor.execute(
            'INSERT OR REPLACE INTO captchas (user_id, captcha_text, attempts) VALUES (?, ?, 0)',
            (user_id, captcha_text)
        )
    
    bot.send_photo(
        user_id,
        photo=captcha_image,
        caption="🔐 Введите текст с картинки для входа:"
    )
    
    user_states[user_id] = 'CAPTCHA_INPUT'
    user_data[user_id]['captcha_text'] = captcha_text

@bot.message_handler(commands=['menu'])
@anti_ddos_middleware
def menu_command(message):
    user_id = message.from_user.id
    markup = get_main_menu(user_id)
    bot.send_message(user_id, "Главное меню:", reply_markup=markup)
    user_states[user_id] = 'MAIN_MENU'

@bot.message_handler(commands=['cancel'])
@anti_ddos_middleware
def cancel_command(message):
    user_id = message.from_user.id
    markup = get_main_menu(user_id)
    bot.send_message(user_id, "❌ Действие отменено", reply_markup=markup)
    user_states[user_id] = 'MAIN_MENU'
    user_data[user_id].clear()

def handle_captcha(message):
    user_id = message.from_user.id
    user_input = message.text.strip().upper()
    
    if check_user_blocked(user_id):
        return
    
    with get_db() as cursor:
        cursor.execute('SELECT captcha_text, attempts FROM captchas WHERE user_id = ?', (user_id,))
        captcha_data = cursor.fetchone()
        
        if not captcha_data:
            bot.send_message(user_id, "❌ Сессия устарела. /start")
            user_states[user_id] = 'MAIN_MENU'
            return
        
        captcha_text = captcha_data['captcha_text']
        attempts = captcha_data['attempts']
        
        if attempts >= MAX_CAPTCHA_ATTEMPTS:
            block_time = datetime.now() + timedelta(minutes=30)
            cursor.execute(
                'UPDATE users SET banned_until = ?, is_banned = TRUE WHERE user_id = ?',
                (block_time, user_id)
            )
            cursor.execute(
                'INSERT INTO blocks (user_id, reason, blocked_until) VALUES (?, ?, ?)',
                (user_id, 'Too many captcha attempts', block_time)
            )
            cursor.execute('DELETE FROM captchas WHERE user_id = ?', (user_id,))
            bot.send_message(user_id, "🚫 Слишком много неудачных попыток. Блокировка на 30 минут.")
            user_states[user_id] = 'MAIN_MENU'
            return
        
        if user_input == captcha_text:
            reset_login_attempts(user_id)
            cursor.execute('DELETE FROM captchas WHERE user_id = ?', (user_id,))
            
            address = create_address(user_id)
            with get_db() as cursor2:
                cursor2.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
                user = cursor2.fetchone()
                balance = user['balance'] if user else 0
            
            markup = get_main_menu(user_id)
            bot.send_message(
                user_id,
                f"✅ Капча пройдена!\n\n"
                f"👤 Ваш адрес: `{address}`\n"
                f"💲 Ваш баланс: {balance} D$\n\n"
                f"Выберите действие:",
                reply_markup=markup,
                parse_mode='Markdown'
            )
            user_states[user_id] = 'MAIN_MENU'
        else:
            increment_login_attempts(user_id)
            cursor.execute(
                'UPDATE captchas SET attempts = attempts + 1 WHERE user_id = ?',
                (user_id,)
            )
            remaining = MAX_CAPTCHA_ATTEMPTS - attempts - 1
            bot.send_message(user_id, f"❌ Неверно. Осталось попыток: {remaining}")

def my_wallet(message):
    user_id = message.from_user.id
    address = create_address(user_id)
    
    with get_db() as cursor:
        cursor.execute('SELECT balance, rating FROM users WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        balance = user['balance'] if user else 0
        rating = user['rating'] if user else 5.0
    
    bot.send_message(
        user_id,
        f"👤 Ваш адрес: `{address}`\n"
        f"💲 Ваш баланс: {balance} D$\n"
        f"⭐ Рейтинг P2P: {rating:.2f}/5.0",
        parse_mode='Markdown'
    )

def deposit(message):
    user_id = message.from_user.id
    address = create_address(user_id)
    
    bot.send_message(
        user_id,
        f"📥 Для пополнения:\n\n"
        f"1. Напишите @aktvr\n"
        f"2. Адрес: `{address}`\n"
        f"3. Сумма пополнения\n\n"
        f"Зачисление в течение 15 минут.",
        parse_mode='Markdown'
    )

def transfer_start(message):
    user_id = message.from_user.id
    markup = get_cancel_keyboard()
    bot.send_message(user_id, "💸 Введите сумму для перевода:", reply_markup=markup)
    user_states[user_id] = 'TRANSFER_AMOUNT'

def transfer_amount(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == "🚫 Отменить":
        cancel_command(message)
        return
    
    try:
        amount = float(text.replace(',', '.'))
        if amount <= 0:
            bot.send_message(user_id, "❌ Сумма > 0")
            return
        
        with get_db() as cursor:
            cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
            balance = user['balance'] if user else 0
        
        if balance < amount:
            markup = get_main_menu(user_id)
            bot.send_message(user_id, f"❌ Недостаточно. Баланс: {balance} D$", reply_markup=markup)
            user_states[user_id] = 'MAIN_MENU'
            return
        
        user_data[user_id]['transfer_amount'] = amount
        markup = get_cancel_keyboard()
        bot.send_message(user_id, "📝 Введите адрес получателя (dQ...):", reply_markup=markup)
        user_states[user_id] = 'TRANSFER_ADDRESS'
    except ValueError:
        bot.send_message(user_id, "❌ Введите число")

def transfer_address(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == "🚫 Отменить":
        cancel_command(message)
        return
    
    if not text.startswith('dQ'):
        bot.send_message(user_id, "❌ Адрес должен начинаться с dQ")
        return
    
    try:
        to_user_id = int(text[2:])
    except ValueError:
        bot.send_message(user_id, "❌ Неверный адрес")
        return
    
    if to_user_id == user_id:
        bot.send_message(user_id, "❌ Нельзя себе")
        return
    
    with get_db() as cursor:
        cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (to_user_id,))
        receiver = cursor.fetchone()
        
        if not receiver:
            markup = get_main_menu(user_id)
            bot.send_message(user_id, "❌ Получатель не найден", reply_markup=markup)
            user_states[user_id] = 'MAIN_MENU'
            return
    
    user_data[user_id]['to_address'] = text
    user_data[user_id]['to_user_id'] = to_user_id
    amount = user_data[user_id]['transfer_amount']
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(
        types.KeyboardButton("✅ Подтвердить"),
        types.KeyboardButton("🚫 Отменить")
    )
    
    bot.send_message(
        user_id,
        f"📋 Подтверждение:\n\n"
        f"➡️ Кому: `{text}`\n"
        f"💰 Сумма: {amount} D$\n\n"
        f"Подтвердить?",
        reply_markup=markup,
        parse_mode='Markdown'
    )
    
    user_states[user_id] = 'CONFIRM_TRANSFER'

def confirm_transfer(message):
    user_id = message.from_user.id
    user_choice = message.text
    
    if user_choice == "🚫 Отменить":
        cancel_command(message)
        return
    
    if user_choice != "✅ Подтвердить":
        markup = get_main_menu(user_id)
        bot.send_message(user_id, "❌ Отменено", reply_markup=markup)
        user_states[user_id] = 'MAIN_MENU'
        user_data[user_id].clear()
        return
    
    amount = user_data[user_id]['transfer_amount']
    to_user_id = user_data[user_id]['to_user_id']
    to_address = user_data[user_id]['to_address']
    from_address = create_address(user_id)
    
    with get_db() as cursor:
        cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
        sender = cursor.fetchone()
        
        if not sender or sender['balance'] < amount:
            markup = get_main_menu(user_id)
            bot.send_message(user_id, "❌ Недостаточно средств", reply_markup=markup)
            user_states[user_id] = 'MAIN_MENU'
            user_data[user_id].clear()
            return
        
        cursor.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (amount, user_id))
        cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, to_user_id))
        
        cursor.execute(
            '''INSERT INTO transactions (from_address, to_address, amount, type, status)
               VALUES (?, ?, ?, ?, ?)''',
            (from_address, to_address, amount, 'transfer', 'completed')
        )
    
    try:
        bot.send_message(
            to_user_id,
            f"💰 Вы получили {amount} D$ от `{from_address}`",
            parse_mode='Markdown'
        )
    except:
        pass
    
    markup = get_main_menu(user_id)
    bot.send_message(
        user_id,
        f"✅ Перевод выполнен!\n"
        f"➡️ Кому: `{to_address}`\n"
        f"💰 Сумма: {amount} D$",
        reply_markup=markup,
        parse_mode='Markdown'
    )
    
    user_states[user_id] = 'MAIN_MENU'
    user_data[user_id].clear()

def p2p_market(message):
    user_id = message.from_user.id
    
    with get_db() as cursor:
        cursor.execute('''
            SELECT COUNT(*) as total_deals, AVG(rating) as avg_rating
            FROM p2p_deals WHERE status = 'completed'
        ''')
        stats = cursor.fetchone()
        
        cursor.execute('''
            SELECT user_id, rating FROM users 
            WHERE rating_count > 0 ORDER BY rating DESC LIMIT 5
        ''')
        top_users = cursor.fetchall()
    
    message_text = "📊 P2P Рынок\n\n"
    
    if stats and stats['total_deals'] > 0:
        message_text += f"Всего сделок: {stats['total_deals']}\n"
        message_text += f"Средний рейтинг: {stats['avg_rating']:.2f}/5.0\n\n"
    else:
        message_text += "Пока нет сделок\n\n"
    
    message_text += "🏆 Топ по рейтингу:\n"
    for i, user in enumerate(top_users, 1):
        message_text += f"{i}. dQ{user['user_id']} - ⭐ {user['rating']:.2f}\n"
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(
        types.KeyboardButton("📈 Мои сделки"),
        types.KeyboardButton("🔙 Назад")
    )
    
    bot.send_message(user_id, message_text, reply_markup=markup)

def checks_menu(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(
        types.KeyboardButton("🧾 Создать чек"),
        types.KeyboardButton("💳 Активировать чек")
    )
    markup.row(
        types.KeyboardButton("📋 Мои чеки"),
        types.KeyboardButton("🔙 Назад")
    )
    bot.send_message(message.chat.id, "🧾 Меню чеков", reply_markup=markup)

def create_check(message):
    markup = get_cancel_keyboard()
    bot.send_message(message.chat.id, "💰 Введите сумму для чека:", reply_markup=markup)
    user_states[message.from_user.id] = 'CREATE_CHECK_AMOUNT'

def activate_check(message):
    markup = get_cancel_keyboard()
    bot.send_message(message.chat.id, "🔢 Введите ID чека:", reply_markup=markup)
    user_states[message.from_user.id] = 'ACTIVATE_CHECK_ID'

def create_check_amount(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == "🚫 Отменить":
        cancel_command(message)
        return
    
    try:
        amount = float(text.replace(',', '.'))
        if amount <= 0:
            bot.send_message(user_id, "❌ Сумма > 0")
            return
        
        with get_db() as cursor:
            cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
            balance = user['balance'] if user else 0
            
            if balance < amount:
                markup = get_main_menu(user_id)
                bot.send_message(user_id, f"❌ Недостаточно. Баланс: {balance} D$", reply_markup=markup)
                user_states[user_id] = 'MAIN_MENU'
                return
        
        check_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
        
        with get_db() as cursor:
            cursor.execute(
                'INSERT INTO checks (check_id, creator_id, amount) VALUES (?, ?, ?)',
                (check_id, user_id, amount)
            )
            cursor.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (amount, user_id))
        
        markup = get_main_menu(user_id)
        bot.send_message(
            user_id,
            f"✅ Чек создан!\n\n"
            f"🧾 ID: `{check_id}`\n"
            f"💰 Сумма: {amount} D$\n\n"
            f"Передайте ID получателю",
            reply_markup=markup,
            parse_mode='Markdown'
        )
        user_states[user_id] = 'MAIN_MENU'
    except ValueError:
        bot.send_message(user_id, "❌ Введите число")

def activate_check_id(message):
    user_id = message.from_user.id
    text = message.text.strip().upper()
    
    if text == "🚫 ОТМЕНИТЬ":
        cancel_command(message)
        return
    
    check_id = text
    
    with get_db() as cursor:
        cursor.execute('SELECT * FROM checks WHERE check_id = ? AND claimed_by IS NULL', (check_id,))
        check = cursor.fetchone()
        
        if not check:
            markup = get_main_menu(user_id)
            bot.send_message(user_id, "❌ Чек не найден или активирован", reply_markup=markup)
            user_states[user_id] = 'MAIN_MENU'
            return
        
        cursor.execute(
            'UPDATE checks SET claimed_by = ?, claimed_at = CURRENT_TIMESTAMP WHERE check_id = ?',
            (user_id, check_id)
        )
        cursor.execute(
            'UPDATE users SET balance = balance + ? WHERE user_id = ?',
            (check['amount'], user_id)
        )
        
        cursor.execute(
            '''INSERT INTO transactions (from_address, to_address, amount, type, status)
               VALUES (?, ?, ?, ?, ?)''',
            (create_address(check['creator_id']), create_address(user_id), check['amount'], 'check', 'completed')
        )
    
    markup = get_main_menu(user_id)
    bot.send_message(user_id, f"✅ Чек активирован! Получено: {check['amount']} D$", reply_markup=markup)
    user_states[user_id] = 'MAIN_MENU'

def my_checks(message):
    user_id = message.from_user.id
    with get_db() as cursor:
        cursor.execute('SELECT * FROM checks WHERE creator_id = ? ORDER BY created_at DESC', (user_id,))
        user_checks = cursor.fetchall()
    
    if not user_checks:
        bot.send_message(user_id, "📭 Нет чеков")
    else:
        message_text = "📋 Ваши чеки:\n\n"
        for check in user_checks:
            status = "✅ Активирован" if check['claimed_by'] else "⏳ Ожидает"
            message_text += f"🧾 `{check['check_id']}` | {check['amount']} D$ | {status}\n"
        bot.send_message(user_id, message_text, parse_mode='Markdown')

def statistics(message):
    with get_db() as cursor:
        cursor.execute('SELECT COUNT(*) as total_users, SUM(balance) as total_balance FROM users')
        stats = cursor.fetchone()
        
        cursor.execute("SELECT COUNT(*) as total_tx FROM transactions WHERE status = 'completed'")
        tx_stats = cursor.fetchone()
        
        cursor.execute("SELECT COUNT(*) as active_deals FROM p2p_deals WHERE status = 'active'")
        deals_stats = cursor.fetchone()
    
    message_text = (
        "📊 Статистика:\n\n"
        f"👥 Пользователей: {stats['total_users'] or 0}\n"
        f"💰 Общий баланс: {stats['total_balance'] or 0:.2f} D$\n"
        f"🔗 Транзакций: {tx_stats['total_tx'] or 0}\n"
        f"🤝 Активных P2P: {deals_stats['active_deals'] or 0}\n\n"
        f"🏦 Валюта: D$"
    )
    
    bot.send_message(message.chat.id, message_text)

def about(message):
    message_text = (
        "ℹ️ О сервисе:\n\n"
        "Безопасный кошелек для D$\n\n"
        "🔒 Безопасность:\n"
        "• Защищенные транзакции\n"
        "• Капча и антиспам\n\n"
        "💡 Особенности:\n"
        "• Мгновенные переводы\n"
        "• P2P торговля\n"
        "• Чеки\n\n"
        "📞 Поддержка: @aktvr"
    )
    bot.send_message(message.chat.id, message_text)

def channel(message):
    bot.send_message(message.chat.id, "📢 Наш канал:\nhttps://t.me/aktvr/")

def admin_panel(message):
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.send_message(user_id, "❌ Нет доступа")
        return
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(
        types.KeyboardButton("📢 Рассылка"),
        types.KeyboardButton("💰 Начислить")
    )
    markup.row(
        types.KeyboardButton("🚫 Забанить"),
        types.KeyboardButton("✅ Разбанить")
    )
    markup.row(
        types.KeyboardButton("📊 Статистика админа"),
        types.KeyboardButton("🔙 Назад")
    )
    bot.send_message(user_id, "👑 Админ-панель", reply_markup=markup)

def broadcast_start(message):
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.send_message(user_id, "❌ Нет доступа")
        return
    
    markup = get_cancel_keyboard()
    bot.send_message(user_id, "📢 Введите сообщение для рассылки:", reply_markup=markup)
    user_states[user_id] = 'BROADCAST_MESSAGE'

def process_broadcast(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == "🚫 Отменить":
        cancel_command(message)
        return
    
    if user_id not in ADMINS:
        bot.send_message(user_id, "❌ Нет доступа")
        return
    
    with get_db() as cursor:
        cursor.execute('SELECT user_id FROM users')
        users = cursor.fetchall()
    
    sent = 0
    failed = 0
    
    for user in users:
        try:
            bot.send_message(chat_id=user['user_id'], text=f"📢 От администрации:\n\n{text}")
            sent += 1
        except:
            failed += 1
    
    markup = get_main_menu(user_id)
    bot.send_message(
        user_id,
        f"✅ Рассылка завершена!\n"
        f"✅ Доставлено: {sent}\n"
        f"❌ Не доставлено: {failed}",
        reply_markup=markup
    )
    
    user_states[user_id] = 'MAIN_MENU'

def admin_add_funds(message):
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.send_message(user_id, "❌ Нет доступа")
        return
    
    markup = get_cancel_keyboard()
    bot.send_message(user_id, "👤 Введите адрес (dQ...):", reply_markup=markup)
    user_states[user_id] = 'ADMIN_ADD_FUNDS_ADDRESS'

def admin_ban(message):
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.send_message(user_id, "❌ Нет доступа")
        return
    
    markup = get_cancel_keyboard()
    bot.send_message(user_id, "👤 Введите адрес для бана (dQ...):", reply_markup=markup)
    user_states[user_id] = 'ADMIN_BAN_ADDRESS'

def admin_unban(message):
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.send_message(user_id, "❌ Нет доступа")
        return
    
    markup = get_cancel_keyboard()
    bot.send_message(user_id, "👤 Введите адрес для разбана (dQ...):", reply_markup=markup)
    user_states[user_id] = 'ADMIN_UNBAN_ADDRESS'

def admin_target(message, action_type):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == "🚫 Отменить":
        cancel_command(message)
        return
    
    if not text.startswith('dQ'):
        bot.send_message(user_id, "❌ Неверный адрес")
        return
    
    try:
        target_id = int(text[2:])
    except ValueError:
        bot.send_message(user_id, "❌ Неверный адрес")
        return
    
    user_data[user_id]['admin_target'] = target_id
    user_data[user_id]['admin_action'] = action_type
    
    if action_type == 'unban':
        with get_db() as cursor:
            cursor.execute('UPDATE users SET is_banned = FALSE, banned_until = NULL WHERE user_id = ?', (target_id,))
        
        markup = get_main_menu(user_id)
        bot.send_message(user_id, f"✅ `{text}` разбанен", reply_markup=markup, parse_mode='Markdown')
        user_states[user_id] = 'MAIN_MENU'
        user_data[user_id].clear()
    else:
        markup = get_cancel_keyboard()
        bot.send_message(user_id, "💰 Введите сумму:" if action_type == 'add_funds' else "⏰ Введите время бана в часах:", reply_markup=markup)
        user_states[user_id] = 'ADMIN_AMOUNT'

def admin_amount(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == "🚫 Отменить":
        cancel_command(message)
        return
    
    try:
        amount = float(text.replace(',', '.'))
        if amount <= 0:
            bot.send_message(user_id, "❌ Сумма > 0")
            return
        
        target_id = user_data[user_id]['admin_target']
        action_type = user_data[user_id]['admin_action']
        target_address = f"dQ{target_id}"
        
        with get_db() as cursor:
            if action_type == 'add_funds':
                cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, target_id))
                cursor.execute(
                    '''INSERT INTO transactions (from_address, to_address, amount, type, status)
                       VALUES (?, ?, ?, ?, ?)''',
                    ('ADMIN', create_address(target_id), amount, 'admin_add', 'completed')
                )
                message_text = f"✅ Начислено {amount} D$ пользователю `{target_address}`"
            elif action_type == 'ban':
                ban_time = datetime.now() + timedelta(hours=amount)
                cursor.execute(
                    'UPDATE users SET banned_until = ?, is_banned = TRUE WHERE user_id = ?',
                    (ban_time, target_id)
                )
                message_text = f"🚫 `{target_address}` забанен на {amount} часов"
        
        markup = get_main_menu(user_id)
        bot.send_message(user_id, message_text, reply_markup=markup, parse_mode='Markdown')
        user_states[user_id] = 'MAIN_MENU'
        user_data[user_id].clear()
    except ValueError:
        bot.send_message(user_id, "❌ Введите число")

def admin_stats(message):
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.send_message(user_id, "❌ Нет доступа")
        return
    
    with get_db() as cursor:
        cursor.execute('SELECT COUNT(*) as total_users FROM users')
        total_users = cursor.fetchone()['total_users']
        
        cursor.execute("SELECT COUNT(*) as banned_users FROM users WHERE is_banned = TRUE")
        banned_users = cursor.fetchone()['banned_users']
        
        cursor.execute("SELECT SUM(balance) as total_balance FROM users")
        total_balance = cursor.fetchone()['total_balance'] or 0
        
        cursor.execute("SELECT COUNT(*) as total_tx FROM transactions")
        total_tx = cursor.fetchone()['total_tx']
        
        cursor.execute('SELECT * FROM transactions ORDER BY created_at DESC LIMIT 5')
        recent_tx = cursor.fetchall()
    
    message_text = (
        f"📊 Статистика админа:\n\n"
        f"👥 Пользователей: {total_users}\n"
        f"🚫 Забанено: {banned_users}\n"
        f"💰 Общий баланс: {total_balance:.2f} D$\n"
        f"🔗 Транзакций: {total_tx}\n\n"
        f"📈 Последние транзакции:\n"
    )
    
    for tx in recent_tx:
        tx_dict = dict(tx)
        message_text += f"\n{tx_dict['created_at']} | `{tx_dict['from_address']}` -> `{tx_dict['to_address']}` | {tx_dict['amount']} D$"
    
    bot.send_message(user_id, message_text, parse_mode='Markdown')

@bot.message_handler(func=lambda message: True)
@anti_ddos_middleware
def handle_all_messages(message):
    user_id = message.from_user.id
    text = message.text
    
    if check_user_blocked(user_id):
        return
    
    state = user_states.get(user_id, 'MAIN_MENU')
    
    if state == 'CAPTCHA_INPUT':
        handle_captcha(message)
        return
    
    elif state == 'TRANSFER_AMOUNT':
        transfer_amount(message)
        return
    
    elif state == 'TRANSFER_ADDRESS':
        transfer_address(message)
        return
    
    elif state == 'CONFIRM_TRANSFER':
        confirm_transfer(message)
        return
    
    elif state == 'CREATE_CHECK_AMOUNT':
        create_check_amount(message)
        return
    
    elif state == 'ACTIVATE_CHECK_ID':
        activate_check_id(message)
        return
    
    elif state == 'BROADCAST_MESSAGE':
        process_broadcast(message)
        return
    
    elif state == 'ADMIN_ADD_FUNDS_ADDRESS':
        admin_target(message, 'add_funds')
        return
    
    elif state == 'ADMIN_BAN_ADDRESS':
        admin_target(message, 'ban')
        return
    
    elif state == 'ADMIN_UNBAN_ADDRESS':
        admin_target(message, 'unban')
        return
    
    elif state == 'ADMIN_AMOUNT':
        admin_amount(message)
        return
    
    if text == "👤 Мой кошелек":
        my_wallet(message)
    
    elif text == "💸 Перевести":
        transfer_start(message)
    
    elif text == "📥 Пополнить":
        deposit(message)
    
    elif text == "📤 Вывести":
        bot.send_message(user_id, "📤 Для вывода напишите @aktvr")
    
    elif text == "📊 P2P рынок":
        p2p_market(message)
    
    elif text == "🧾 Чеки":
        checks_menu(message)
    
    elif text == "🧾 Создать чек":
        create_check(message)
    
    elif text == "💳 Активировать чек":
        activate_check(message)
    
    elif text == "📋 Мои чеки":
        my_checks(message)
    
    elif text == "📈 Мои сделки":
        bot.send_message(user_id, "📈 В разработке...")
    
    elif text == "📊 Статистика":
        statistics(message)
    
    elif text == "ℹ️ О нас":
        about(message)
    
    elif text == "📢 Наш канал":
        channel(message)
    
    elif text == "👑 Админ-панель":
        admin_panel(message)
    
    elif text == "📢 Рассылка":
        broadcast_start(message)
    
    elif text == "💰 Начислить":
        admin_add_funds(message)
    
    elif text == "🚫 Забанить":
        admin_ban(message)
    
    elif text == "✅ Разбанить":
        admin_unban(message)
    
    elif text == "📊 Статистика админа":
        admin_stats(message)
    
    elif text == "📞 Написать админу":
        bot.send_message(user_id, "📞 @aktvr")
    
    elif text == "🔙 Назад":
        menu_command(message)
    
    else:
        markup = get_main_menu(user_id)
        bot.send_message(user_id, "Выберите из меню:", reply_markup=markup)

def main():
    init_db()
    print("Бот запущен...")
    bot.infinity_polling()

if __name__ == '__main__':
    main()
