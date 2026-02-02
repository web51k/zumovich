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

# ⚠️ ВНИМАНИЕ: Обязательно перевыпустите токен в @BotFather, так как старый был засвечен!
BOT_TOKEN = "8205991086:AAEhQIz1TB3T2vm8_OYkNTqEZO4GEl6mKCw"

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN)

# ID администраторов
ADMINS = [8451383116]
ADMIN_BALANCE = 999999999999999

# Состояния пользователей
user_states = {}
user_data = defaultdict(dict)

# Анти-DDoS защита
request_limits = defaultdict(list)
login_attempts = defaultdict(int)
BLOCK_TIME = 3600  # 1 час блокировки за флуд запросами

# Максимальные лимиты
MAX_REQUESTS_PER_MINUTE = 30
MAX_CAPTCHA_ATTEMPTS = 3  # Ошибки в самой капче (можно увеличить или убрать)

# --- БАЗА ДАННЫХ ---
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

# --- ЗАЩИТА ---
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
                    return False
    return False

def increment_login_attempts(user_id: int):
    """Теперь просто считаем попытки без блокировки аккаунта."""
    now = datetime.now()
    with get_db() as cursor:
        cursor.execute(
            'UPDATE users SET login_attempts = login_attempts + 1, last_login_attempt = ? WHERE user_id = ?',
            (now, user_id)
        )

def reset_login_attempts(user_id: int):
    with get_db() as cursor:
        cursor.execute('UPDATE users SET login_attempts = 0 WHERE user_id = ?', (user_id,))

# --- ВСПОМОГАТЕЛЬНОЕ ---
def generate_captcha():
    text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    image = Image.new('RGB', (200, 80), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    for _ in range(1000):
        draw.point((random.randint(0, 199), random.randint(0, 79)), fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))
    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except:
        font = ImageFont.load_default()
    for i, char in enumerate(text):
        draw.text((20 + i * 30 + random.randint(-5, 5), 20 + random.randint(-5, 5)), char, font=font, fill=(0, 0, 0))
    img_byte_arr = BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    return text, img_byte_arr

def create_address(user_id: int) -> str:
    return f"dQ{user_id}"

def get_main_menu(user_id: int):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(types.KeyboardButton("👤 Мой кошелек"), types.KeyboardButton("💸 Перевести"))
    markup.row(types.KeyboardButton("📊 P2P рынок"), types.KeyboardButton("🧾 Чеки"))
    markup.row(types.KeyboardButton("📥 Пополнить"), types.KeyboardButton("📤 Вывести"))
    markup.row(types.KeyboardButton("📊 Статистика"), types.KeyboardButton("ℹ️ О нас"))
    markup.row(types.KeyboardButton("📢 Наш канал"))
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
        if user_id in ADMINS: return handler(message)
        if check_user_blocked(user_id):
            bot.send_message(user_id, "🚫 Ваш аккаунт временно заблокирован.")
            return
        if not check_rate_limit(user_id):
            bot.send_message(user_id, "⚠️ Слишком много запросов. Попробуйте позже.")
            return
        return handler(message)
    return wrapper

# --- ХЕНДЛЕРЫ ---
@bot.message_handler(commands=['start'])
@anti_ddos_middleware
def start(message):
    user_id = message.from_user.id
    
    # БЛОК ПРОВЕРКИ НА КОЛИЧЕСТВО ПОПЫТОК ВХОДА УДАЛЕН
    
    with get_db() as cursor:
        cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, message.from_user.username))
    
    captcha_text, captcha_image = generate_captcha()
    with get_db() as cursor:
        cursor.execute('INSERT OR REPLACE INTO captchas (user_id, captcha_text, attempts) VALUES (?, ?, 0)', (user_id, captcha_text))
    
    bot.send_photo(user_id, photo=captcha_image, caption="🔐 Введите текст с картинки для входа:")
    user_states[user_id] = 'CAPTCHA_INPUT'

def handle_captcha(message):
    user_id = message.from_user.id
    user_input = message.text.strip().upper()
    
    with get_db() as cursor:
        cursor.execute('SELECT captcha_text, attempts FROM captchas WHERE user_id = ?', (user_id,))
        captcha_data = cursor.fetchone()
        
        if not captcha_data:
            bot.send_message(user_id, "❌ Сессия устарела. /start")
            return
        
        if user_input == captcha_data['captcha_text']:
            reset_login_attempts(user_id)
            cursor.execute('DELETE FROM captchas WHERE user_id = ?', (user_id,))
            
            cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
            balance = user['balance'] if user else 0
            
            bot.send_message(user_id, f"✅ Капча пройдена!\n👤 Адрес: `{create_address(user_id)}`\n💰 Баланс: {balance} D$", 
                             reply_markup=get_main_menu(user_id), parse_mode='Markdown')
            user_states[user_id] = 'MAIN_MENU'
        else:
            increment_login_attempts(user_id)
            new_attempts = captcha_data['attempts'] + 1
            if new_attempts >= MAX_CAPTCHA_ATTEMPTS:
                block_time = datetime.now() + timedelta(minutes=30)
                cursor.execute('UPDATE users SET banned_until = ?, is_banned = TRUE WHERE user_id = ?', (block_time, user_id))
                bot.send_message(user_id, "🚫 Слишком много ошибок в капче. Бан на 30 минут.")
            else:
                cursor.execute('UPDATE captchas SET attempts = ? WHERE user_id = ?', (new_attempts, user_id))
                bot.send_message(user_id, f"❌ Неверно. Осталось попыток: {MAX_CAPTCHA_ATTEMPTS - new_attempts}")

# --- ФУНКЦИИ КОШЕЛЬКА ---
def my_wallet(message):
    user_id = message.from_user.id
    with get_db() as cursor:
        cursor.execute('SELECT balance, rating FROM users WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        balance = user['balance'] if user else 0
        rating = user['rating'] if user else 5.0
    bot.send_message(user_id, f"👤 Ваш адрес: `{create_address(user_id)}`\n💰 Баланс: {balance} D$\n⭐ Рейтинг: {rating:.2f}/5.0", parse_mode='Markdown')

def transfer_start(message):
    user_id = message.from_user.id
    bot.send_message(user_id, "💸 Введите сумму для перевода:", reply_markup=get_cancel_keyboard())
    user_states[user_id] = 'TRANSFER_AMOUNT'

def transfer_amount(message):
    user_id = message.from_user.id
    if message.text == "🚫 Отменить": 
        menu_command(message)
        return
    try:
        amount = float(message.text.replace(',', '.'))
        if amount <= 0: raise ValueError
        with get_db() as cursor:
            cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
            if cursor.fetchone()['balance'] < amount:
                bot.send_message(user_id, "❌ Недостаточно средств.")
                return
        user_data[user_id]['transfer_amount'] = amount
        bot.send_message(user_id, "📝 Введите адрес получателя (dQ...):", reply_markup=get_cancel_keyboard())
        user_states[user_id] = 'TRANSFER_ADDRESS'
    except:
        bot.send_message(user_id, "❌ Введите корректное число.")

def transfer_address(message):
    user_id = message.from_user.id
    text = message.text.strip()
    if text == "🚫 Отменить": 
        menu_command(message)
        return
    if not text.startswith('dQ'):
        bot.send_message(user_id, "❌ Неверный формат адреса.")
        return
    try:
        to_id = int(text[2:])
        if to_id == user_id:
            bot.send_message(user_id, "❌ Нельзя переводить самому себе.")
            return
        with get_db() as cursor:
            cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (to_id,))
            if not cursor.fetchone():
                bot.send_message(user_id, "❌ Получатель не найден.")
                return
        user_data[user_id]['to_user_id'] = to_id
        user_data[user_id]['to_address'] = text
        
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.row(types.KeyboardButton("✅ Подтвердить"), types.KeyboardButton("🚫 Отменить"))
        bot.send_message(user_id, f"📋 Подтвердите перевод {user_data[user_id]['transfer_amount']} D$ на `{text}`", reply_markup=markup, parse_mode='Markdown')
        user_states[user_id] = 'CONFIRM_TRANSFER'
    except:
        bot.send_message(user_id, "❌ Ошибка в адресе.")

def confirm_transfer(message):
    user_id = message.from_user.id
    if message.text == "✅ Подтвердить":
        amount = user_data[user_id]['transfer_amount']
        to_id = user_data[user_id]['to_user_id']
        with get_db() as cursor:
            cursor.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (amount, user_id))
            cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, to_id))
            cursor.execute('INSERT INTO transactions (from_address, to_address, amount, type, status) VALUES (?, ?, ?, ?, ?)',
                           (create_address(user_id), create_address(to_id), amount, 'transfer', 'completed'))
        bot.send_message(user_id, "✅ Перевод выполнен!", reply_markup=get_main_menu(user_id))
        try: bot.send_message(to_id, f"💰 Вы получили {amount} D$ от `{create_address(user_id)}`", parse_mode='Markdown')
        except: pass
    else:
        bot.send_message(user_id, "❌ Отменено", reply_markup=get_main_menu(user_id))
    user_states[user_id] = 'MAIN_MENU'

# --- ЧЕКИ ---
def create_check_amount(message):
    user_id = message.from_user.id
    if message.text == "🚫 Отменить":
        menu_command(message)
        return
    try:
        amount = float(message.text.replace(',', '.'))
        if amount <= 0: raise ValueError
        with get_db() as cursor:
            cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
            if cursor.fetchone()['balance'] < amount:
                bot.send_message(user_id, "❌ Недостаточно средств.")
                return
            check_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
            cursor.execute('INSERT INTO checks (check_id, creator_id, amount) VALUES (?, ?, ?)', (check_id, user_id, amount))
            cursor.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (amount, user_id))
        bot.send_message(user_id, f"✅ Чек создан!\nID: `{check_id}`\nСумма: {amount} D$", reply_markup=get_main_menu(user_id), parse_mode='Markdown')
        user_states[user_id] = 'MAIN_MENU'
    except:
        bot.send_message(user_id, "❌ Введите число.")

def activate_check_id(message):
    user_id = message.from_user.id
    check_id = message.text.strip().upper()
    if check_id == "🚫 ОТМЕНИТЬ":
        menu_command(message)
        return
    with get_db() as cursor:
        cursor.execute('SELECT * FROM checks WHERE check_id = ? AND claimed_by IS NULL', (check_id,))
        check = cursor.fetchone()
        if not check:
            bot.send_message(user_id, "❌ Чек не найден или уже активирован.")
            return
        cursor.execute('UPDATE checks SET claimed_by = ?, claimed_at = CURRENT_TIMESTAMP WHERE check_id = ?', (user_id, check_id))
        cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (check['amount'], user_id))
    bot.send_message(user_id, f"✅ Получено {check['amount']} D$!", reply_markup=get_main_menu(user_id))
    user_states[user_id] = 'MAIN_MENU'

# --- ОСТАЛЬНЫЕ КОМАНДЫ (P2P, СТАТИСТИКА И Т.Д.) ---
def p2p_market(message):
    user_id = message.from_user.id
    with get_db() as cursor:
        cursor.execute("SELECT user_id, rating FROM users WHERE rating_count > 0 ORDER BY rating DESC LIMIT 5")
        top = cursor.fetchall()
    text = "📊 Топ P2P пользователей:\n\n"
    for i, u in enumerate(top, 1):
        text += f"{i}. dQ{u['user_id']} — ⭐ {u['rating']:.2f}\n"
    bot.send_message(user_id, text or "Пока нет активных торговцев.")

def admin_panel(message):
    if message.from_user.id in ADMINS:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.row("📢 Рассылка", "💰 Начислить")
        markup.row("🚫 Забанить", "✅ Разбанить")
        markup.row("📊 Статистика админа", "🔙 Назад")
        bot.send_message(message.from_user.id, "👑 Админ-панель", reply_markup=markup)

@bot.message_handler(commands=['menu', 'cancel'])
@anti_ddos_middleware
def menu_command(message):
    user_id = message.from_user.id
    user_states[user_id] = 'MAIN_MENU'
    bot.send_message(user_id, "Главное меню:", reply_markup=get_main_menu(user_id))

# --- ГЛАВНЫЙ ОБРАБОТЧИК ---
@bot.message_handler(func=lambda message: True)
@anti_ddos_middleware
def handle_all_messages(message):
    user_id = message.from_user.id
    text = message.text
    state = user_states.get(user_id, 'MAIN_MENU')

    if state == 'CAPTCHA_INPUT': handle_captcha(message); return
    if state == 'TRANSFER_AMOUNT': transfer_amount(message); return
    if state == 'TRANSFER_ADDRESS': transfer_address(message); return
    if state == 'CONFIRM_TRANSFER': confirm_transfer(message); return
    if state == 'CREATE_CHECK_AMOUNT': create_check_amount(message); return
    if state == 'ACTIVATE_CHECK_ID': activate_check_id(message); return

    # Меню
    if text == "👤 Мой кошелек": my_wallet(message)
    elif text == "💸 Перевести": transfer_start(message)
    elif text == "🧾 Чеки": 
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.row("🧾 Создать чек", "💳 Активировать чек")
        markup.row("🔙 Назад")
        bot.send_message(user_id, "🧾 Меню чеков", reply_markup=markup)
    elif text == "🧾 Создать чек":
        bot.send_message(user_id, "💰 Сумма чека:", reply_markup=get_cancel_keyboard())
        user_states[user_id] = 'CREATE_CHECK_AMOUNT'
    elif text == "💳 Активировать чек":
        bot.send_message(user_id, "🔢 ID чека:", reply_markup=get_cancel_keyboard())
        user_states[user_id] = 'ACTIVATE_CHECK_ID'
    elif text == "📊 P2P рынок": p2p_market(message)
    elif text == "📊 Статистика":
        with get_db() as cursor:
            cursor.execute('SELECT COUNT(*), SUM(balance) FROM users')
            s = cursor.fetchone()
        bot.send_message(user_id, f"📊 Статистика:\n👥 Юзеров: {s[0]}\n💰 Всего D$: {s[1]:.2f}")
    elif text == "ℹ️ О нас":
        bot.send_message(user_id, "Безопасный кошелек D$\nПоддержка: @mrvudik")
    elif text == "📢 Наш канал":
        bot.send_message(user_id, "https://t.me/darryl_coin/")
    elif text == "🔙 Назад": menu_command(message)
    elif text == "👑 Админ-панель": admin_panel(message)
    elif text == "📥 Пополнить":
        bot.send_message(user_id, f"📥 Для пополнения:\n1. @mrvudik\n2. Адрес: `{create_address(user_id)}`", parse_mode='Markdown')
    elif text == "📤 Вывести":
        bot.send_message(user_id, "📤 Для вывода напишите @aktvr")

if __name__ == '__main__':
    init_db()
    print("Бот запущен. Ограничения на вход сняты.")
    bot.infinity_polling()

