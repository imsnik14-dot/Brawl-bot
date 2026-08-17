import asyncio
import logging
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)

# ============================================
# ===== ЛОГИРОВАНИЕ =====
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot_errors.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================
# ===== КОНФИГ =====
# ============================================
BOT_TOKEN = "8719764726:AAE4UrVsK-VeBmX7ux1LfKihYktlAULk8bg"
OWNER_IDS = [5977744301, 8985475819]
ADMIN_PASSWORD = "19102012"
CRYPTOBOT_TOKEN = "618968:AAKChcSv6TWGf6AtVkLLb6eu6roYvqz6MFC"

# ============================================
# ===== ДАННЫЕ =====
# ============================================
users_db = {}
actions_log = {}
pending_orders = {}

# ============================================
# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
# ============================================
def get_moscow_time():
    return (datetime.now() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

def track_user(user: types.User):
    if user.id not in users_db:
        users_db[user.id] = {
            "name": user.full_name or "без имени",
            "username": f"@{user.username}" if user.username else "без юзернейма",
            "first_seen": get_moscow_time(),
            "last_seen": get_moscow_time(),
            "balance_rub": 0
        }
    else:
        users_db[user.id]["last_seen"] = get_moscow_time()

def add_history(user_id: int, amount: int, description: str):
    if user_id not in users_db:
        return
    if "history" not in users_db[user_id]:
        users_db[user_id]["history"] = []
    users_db[user_id]["history"].append({
        "time": get_moscow_time(),
        "amount": amount,
        "description": description
    })
    if len(users_db[user_id]["history"]) > 100:
        users_db[user_id]["history"] = users_db[user_id]["history"][-100:]

def log_action(user: types.User, action: str):
    if user.id not in actions_log:
        actions_log[user.id] = []
    actions_log[user.id].append(f"[{get_moscow_time()}] {action}")

async def notify_owners(action: str):
    for owner_id in OWNER_IDS:
        try:
            await bot.send_message(owner_id, f"🔔 <b>Уведомление</b>\n\n{action}", parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка уведомления владельца {owner_id}: {e}")

async def notify_owner(user: types.User, action: str):
    log_action(user, action)
    for owner_id in OWNER_IDS:
        try:
            await bot.send_message(owner_id, f"🔔 <b>Действие пользователя</b>\n\n{action}", parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка уведомления владельца {owner_id}: {e}")

# ============================================
# ===== CRYPTOBOT (исправленная версия) =====
# ============================================
def crypto_request(url, data):
    """Отправляет запрос к Cryptobot с обработкой ошибок"""
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
        "Content-Type": "application/json"
    }
    
    json_data = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(url, data=json_data, headers=headers, method="POST")
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            response_data = response.read().decode('utf-8')
            result = json.loads(response_data)
            logger.info(f"✅ Ответ Cryptobot: {result}")
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else str(e)
        logger.error(f"❌ HTTP ошибка {e.code}: {error_body}")
        return {"ok": False, "error": f"HTTP {e.code}: {error_body}"}
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return {"ok": False, "error": str(e)}

async def create_crypto_payment(amount_rub: int, currency: str, description: str, user_id: int):
    rates = {"BTC": 4000000, "USDT": 90, "TON": 250}
    rate = rates.get(currency, 90)
    amount_crypto = round(amount_rub / rate, 8)
    
    order_id = f"crypto_{user_id}_{int(datetime.now().timestamp())}"
    
    payload = {
        "asset": currency,
        "amount": str(amount_crypto),
        "description": description,
        "paid_btn_name": "Подтвердить",
        "paid_btn_url": "https://t.me/your_bot",
        "order_id": order_id,
        "payload": f"user_{user_id}"
    }
    
    logger.info(f"📤 Запрос к Cryptobot: {payload}")
    result = crypto_request("https://pay.cryptobot.com/api/createInvoice", payload)
    
    if result.get("ok"):
        return {
            "invoice_id": result["result"]["invoice_id"],
            "pay_url": result["result"]["pay_url"],
            "amount": amount_crypto,
            "currency": currency,
            "order_id": order_id,
            "status": "pending"
        }
    else:
        error_msg = result.get("error", "Неизвестная ошибка")
        logger.error(f"❌ Ошибка создания платежа: {error_msg}")
        raise Exception(f"Ошибка Cryptobot: {error_msg}")

async def check_crypto_payment(invoice_id: int):
    result = crypto_request("https://pay.cryptobot.com/api/getInvoices", {"invoice_ids": [invoice_id]})
    if result.get("ok") and result.get("result", {}).get("items"):
        return result["result"]["items"][0].get("status")
    return "unknown"

# ============================================
# ===== КЛАВИАТУРЫ =====
# ============================================
main_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="📦 Наличие товара")],
        [KeyboardButton(text="❓ Поддержка"), KeyboardButton(text="📋 Правила магазина")]
    ],
    resize_keyboard=True
)

profile_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📜 История")],
        [KeyboardButton(text="🔙 Назад")]
    ],
    resize_keyboard=True
)

back_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔙 Назад")]
    ],
    resize_keyboard=True
)

support_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔙 Назад")]
    ],
    resize_keyboard=True
)

admin_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="👥 Пользователи")],
        [KeyboardButton(text="💰 Пополнить баланс")],
        [KeyboardButton(text="📋 Лог действий")],
        [KeyboardButton(text="🔙 Назад")]
    ],
    resize_keyboard=True
)

owner_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="📦 Наличие товара")],
        [KeyboardButton(text="❓ Поддержка")],
        [KeyboardButton(text="📋 Правила магазина")],
        [KeyboardButton(text="👑 Админ-панель")]
    ],
    resize_keyboard=True
)

# ============================================
# ===== СОСТОЯНИЯ =====
# ============================================
class ShopStates(StatesGroup):
    main_menu = State()
    waiting_support = State()
    admin_panel = State()
    waiting_admin_password = State()
    admin_balance_user = State()
    admin_balance_amount = State()

# ============================================
# ===== МИДЛВАРЫ =====
# ============================================
class RegistrationMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = event.from_user
        if user and user.id not in users_db:
            if isinstance(event, types.Message) and event.text and event.text.startswith('/start'):
                return await handler(event, data)
            await event.answer("⚠️ Пожалуйста, нажмите /start для начала работы.")
            return
        return await handler(event, data)

# ============================================
# ===== ИНИЦИАЛИЗАЦИЯ =====
# ============================================
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)
dp.message.middleware(RegistrationMiddleware())

# ============================================
# ===== ХЕНДЛЕР START =====
# ============================================
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    track_user(message.from_user)
    log_action(message.from_user, "ЗАПУСТИЛ БОТА (/start)")
    await state.clear()
    await state.set_state(ShopStates.main_menu)
    
    if message.from_user.id in OWNER_IDS:
        await message.answer(
            "🤖 <b>Вас приветствует CyberPuggShop!</b>\n\n"
            "👑 Добро пожаловать, владелец!\n\n"
            "🛒 Здесь вы можете приобрести аккаунты и донат для Brawl Stars.\n\n"
            "❓ <b>Что вы хотите сделать?</b>",
            reply_markup=owner_menu_kb,
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "🤖 <b>Вас приветствует CyberPuggShop!</b>\n\n"
            "🛒 Добро пожаловать в наш магазин!\n"
            "Здесь вы можете приобрести аккаунты и донат для Brawl Stars.\n\n"
            "❓ <b>Что вы хотите сделать?</b>\n\n"
            "👇 Выберите действие в меню ниже:",
            reply_markup=main_menu_kb,
            parse_mode="HTML"
        )

# ============================================
# ===== ПРОФИЛЬ =====
# ============================================
@dp.message(F.text == "👤 Профиль")
async def show_profile(message: Message, state: FSMContext):
    user_data = users_db.get(message.from_user.id)
    if not user_data:
        await message.answer("⚠️ Ошибка. Нажмите /start.")
        return
    
    balance = user_data.get("balance_rub", 0)
    name = user_data.get("name", "без имени")
    username = user_data.get("username", "без юзернейма")
    first_seen = user_data.get("first_seen", "неизвестно")
    last_seen = user_data.get("last_seen", "неизвестно")
    
    await message.answer(
        f"👤 <b>Ваш профиль</b>\n\n"
        f"📛 <b>Имя:</b> {name}\n"
        f"🔖 <b>Юзернейм:</b> {username}\n"
        f"🆔 <b>ID:</b> <code>{message.from_user.id}</code>\n"
        f"💰 <b>Баланс:</b> {balance} ₽\n"
        f"📅 <b>Первый визит:</b> {first_seen}\n"
        f"🕐 <b>Последний визит:</b> {last_seen}\n\n"
        f"🛒 <b>Статус:</b> Покупатель 🛍️",
        reply_markup=profile_kb,
        parse_mode="HTML"
    )

@dp.message(F.text == "📜 История")
async def show_history(message: Message, state: FSMContext):
    user_data = users_db.get(message.from_user.id)
    if not user_data:
        await message.answer("⚠️ Ошибка. Нажмите /start.")
        return
    
    history = user_data.get("history", [])
    if not history:
        await message.answer("📜 <b>История операций пуста</b>", reply_markup=profile_kb, parse_mode="HTML")
        return
    
    text = "📜 <b>История операций (последние 10):</b>\n\n"
    for entry in history[-10:][::-1]:
        sign = "+" if entry["amount"] >= 0 else ""
        text += f"🕒 {entry['time']}\n   {sign}{entry['amount']} ₽ — {entry['description']}\n\n"
    
    await message.answer(text, reply_markup=profile_kb, parse_mode="HTML")

# ============================================
# ===== НАЛИЧИЕ ТОВАРА =====
# ============================================
@dp.message(F.text == "📦 Наличие товара")
async def show_products(message: Message, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить аккаунт (899 ₽)", callback_data="buy_account")],
        [InlineKeyboardButton(text="🛒 Купить БП+ (199 ₽)", callback_data="buy_bp")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="products_back")]
    ])
    
    await message.answer(
        "📦 <b>Наличие товара</b>\n\n"
        "➖➖➖Аккаунты Brawl Stars➖➖➖\n"
        "        Аккаунт Brawl Stars | 8 Brawl Pass Plus | Отлега 7+ дней | 899 ₽  | 3 шт.\n\n"
        "➖➖➖Донат Brawl Stars➖➖➖\n"
        "        Brawl Pass Plus | Подарком | 199 ₽  | 3 шт.\n\n"
        "👇 <b>Выберите товар для покупки:</b>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "products_back")
async def products_back(callback: CallbackQuery):
    await callback.answer()
    await callback.message.delete()
    await callback.message.answer("🔙 Возврат в главное меню", reply_markup=main_menu_kb)

# ============================================
# ===== ПОКУПКА =====
# ============================================
@dp.callback_query(F.data == "buy_account")
async def buy_account(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="₿ Bitcoin (BTC)", callback_data="crypto_pay_BTC_account")],
        [InlineKeyboardButton(text="$ Tether (USDT)", callback_data="crypto_pay_USDT_account")],
        [InlineKeyboardButton(text="⍟ Toncoin (TON)", callback_data="crypto_pay_TON_account")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")]
    ])
    
    await callback.message.edit_text(
        "🪙 <b>Оплата криптовалютой</b>\n\n"
        "Выберите криптовалюту для оплаты:\n\n"
        f"💰 Стоимость: <b>899 ₽</b>\n"
        f"🎯 Товар: <b>Аккаунт Brawl Stars</b>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "buy_bp")
async def buy_bp(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="₿ Bitcoin (BTC)", callback_data="crypto_pay_BTC_bp")],
        [InlineKeyboardButton(text="$ Tether (USDT)", callback_data="crypto_pay_USDT_bp")],
        [InlineKeyboardButton(text="⍟ Toncoin (TON)", callback_data="crypto_pay_TON_bp")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")]
    ])
    
    await callback.message.edit_text(
        "🪙 <b>Оплата криптовалютой</b>\n\n"
        "Выберите криптовалюту для оплаты:\n\n"
        f"💰 Стоимость: <b>199 ₽</b>\n"
        f"🎯 Товар: <b>Brawl Pass Plus</b>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("crypto_pay_"))
async def process_crypto_pay(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    currency = parts[2]
    product_type = parts[3]
    
    if product_type == "account":
        price = 899
        product_name = "Аккаунт Brawl Stars"
        description = "Аккаунт Brawl Stars | 8 Brawl Pass Plus | Отлега 7+ дней"
    else:
        price = 199
        product_name = "Brawl Pass Plus"
        description = "Brawl Pass Plus | Подарком"
    
    user_id = callback.from_user.id
    await callback.answer("⏳ Создаём платёж...", show_alert=False)
    
    try:
        payment = await create_crypto_payment(price, currency, description, user_id)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {payment['amount']:.8f} {currency}", url=payment["pay_url"])],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_crypto_{payment['invoice_id']}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")]
        ])
        
        pending_orders[payment["invoice_id"]] = {
            "user_id": user_id,
            "product": product_name,
            "price": price,
            "currency": currency
        }
        
        await callback.message.edit_text(
            f"🪙 <b>Оплата криптовалютой</b>\n\n"
            f"🎯 <b>Товар:</b> {product_name}\n"
            f"💰 <b>Сумма в ₽:</b> {price} ₽\n"
            f"🪙 <b>Сумма в {currency}:</b> {payment['amount']:.8f}\n"
            f"📊 <b>Статус:</b> ⏳ Ожидает оплаты\n\n"
            f"1️⃣ Нажмите «Оплатить»\n"
            f"2️⃣ Переведите указанную сумму\n"
            f"3️⃣ Нажмите «Проверить оплату»",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Ошибка создания платежа: {e}")
        await callback.message.answer(
            f"❌ <b>Ошибка создания платежа!</b>\n\n"
            f"Попробуйте позже или выберите другую валюту.\n\n"
            f"Ошибка: {e}",
            reply_markup=main_menu_kb,
            parse_mode="HTML"
        )

@dp.callback_query(F.data.startswith("check_crypto_"))
async def check_crypto_payment_status(callback: CallbackQuery, state: FSMContext):
    invoice_id = int(callback.data.replace("check_crypto_", ""))
    await callback.answer("🔄 Проверяем статус платежа...", show_alert=False)
    
    try:
        status = await check_crypto_payment(invoice_id)
        logger.info(f"Статус платежа {invoice_id}: {status}")
        
        if status == "paid":
            order = pending_orders.get(invoice_id)
            if order:
                user_id = order["user_id"]
                product_name = order["product"]
                price = order["price"]
                currency = order.get("currency", "BTC")
                
                # ✅ Уведомление пользователю
                await bot.send_message(
                    user_id,
                    f"✅ <b>Оплата прошла успешно!</b>\n\n"
                    f"🎯 <b>Товар:</b> {product_name}\n"
                    f"💰 <b>Сумма:</b> {price} ₽\n"
                    f"🪙 <b>Валюта:</b> {currency}\n\n"
                    f"Скоро с вами свяжется оператор для передачи товара. 🎉",
                    parse_mode="HTML"
                )
                
                # ✅ Уведомление владельцам
                await notify_owner(
                    callback.from_user,
                    f"🪙 <b>НОВАЯ ОПЛАТА КРИПТОВАЛЮТОЙ</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 Пользователь: {callback.from_user.full_name} (@{callback.from_user.username})\n"
                    f"🆔 ID: {callback.from_user.id}\n"
                    f"🎯 Товар: {product_name}\n"
                    f"🪙 Валюта: {currency}\n"
                    f"💰 Сумма: {price} ₽\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📩 Свяжитесь с покупателем для передачи товара!"
                )
                
                # ✅ Обновляем сообщение
                await callback.message.edit_text(
                    f"✅ <b>Оплата подтверждена!</b>\n\n"
                    f"🎯 <b>Товар:</b> {product_name}\n"
                    f"💰 <b>Сумма:</b> {price} ₽\n\n"
                    f"Скоро с вами свяжется оператор.\n"
                    f"Спасибо за покупку! 🎉",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="payment_cancel")]
                    ]),
                    parse_mode="HTML"
                )
                
                del pending_orders[invoice_id]
                await callback.answer("✅ Оплата подтверждена!", show_alert=True)
            else:
                await callback.answer("❌ Заказ не найден!", show_alert=True)
                
        elif status == "pending":
            await callback.answer("⏳ Платёж ещё не оплачен!", show_alert=True)
            
        elif status == "expired":
            await callback.answer("⏰ Время оплаты истекло!", show_alert=True)
            await callback.message.edit_text(
                "❌ <b>Время оплаты истекло!</b>\n\n"
                "Попробуйте оформить заказ заново.",
                reply_markup=main_menu_kb,
                parse_mode="HTML"
            )
            if invoice_id in pending_orders:
                del pending_orders[invoice_id]
        else:
            await callback.answer(f"❌ Статус: {status}", show_alert=True)
            
    except Exception as e:
        logger.error(f"Ошибка проверки платежа: {e}")
        await callback.answer(f"❌ Ошибка проверки: {e}", show_alert=True)

@dp.callback_query(F.data == "payment_cancel")
async def payment_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer("❌ Платёж отменён", show_alert=True)
    await callback.message.delete()
    await callback.message.answer("🔙 Возврат в главное меню", reply_markup=main_menu_kb)
    await state.clear()
    await state.set_state(ShopStates.main_menu)

# ============================================
# ===== ПОДДЕРЖКА =====
# ============================================
@dp.message(F.text == "❓ Поддержка")
async def handle_support(message: Message, state: FSMContext):
    await message.answer(
        "❓ <b>Поддержка</b>\n\n"
        "Если у вас возникли вопросы, проблемы или вы хотите сделать заказ,\n"
        "напишите нам, и мы ответим в ближайшее время! 💬\n\n"
        "✍️ <b>Напишите ваше сообщение:</b>\n\n"
        "🔙 Для отмены нажмите 'Назад'.",
        reply_markup=support_kb,
        parse_mode="HTML"
    )
    await state.set_state(ShopStates.waiting_support)

@dp.message(ShopStates.waiting_support)
async def process_support(message: Message, state: FSMContext):
    if message.text == "🔙 Назад":
        await state.clear()
        await state.set_state(ShopStates.main_menu)
        await message.answer("🔙 Возврат в главное меню", reply_markup=main_menu_kb)
        return
    
    user = message.from_user
    admin_text = (
        f"📩 <b>Новое сообщение от пользователя</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Юзернейм:</b> @{user.username if user.username else 'без юзернейма'}\n"
        f"📛 <b>Имя:</b> {user.full_name or 'без имени'}\n"
        f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Текст сообщения:</b>\n{message.text}"
    )
    
    log_action(user, "ОТПРАВИЛ ВОПРОС В ПОДДЕРЖКУ")
    
    try:
        for owner_id in OWNER_IDS:
            try:
                await bot.send_message(owner_id, admin_text, parse_mode="HTML")
            except:
                pass
        await message.answer(
            "✅ <b>Ваше сообщение отправлено!</b>\n\n"
            "Наш оператор свяжется с вами в ближайшее время.\n"
            "Спасибо за обращение! 🙌",
            reply_markup=main_menu_kb,
            parse_mode="HTML"
        )
        await state.clear()
        await state.set_state(ShopStates.main_menu)
    except Exception as e:
        await message.answer(
            f"❌ Ошибка при отправке: {e}\n\n"
            f"Попробуйте позже",
            reply_markup=support_kb,
            parse_mode="HTML"
        )

# ============================================
# ===== ПРАВИЛА МАГАЗИНА =====
# ============================================
@dp.message(F.text == "📋 Правила магазина")
async def show_rules(message: Message, state: FSMContext):
    await message.answer(
        "📋 <b>Правила магазина CyberPuggShop</b>\n\n"
        "📌 <b>1. Оплата</b>\n"
        "• Оплата производится в криптовалюте (BTC, USDT, TON)\n"
        "• Цены указаны в рублях (₽)\n"
        "• После оплаты товар передаётся в течение 5-30 минут\n\n"
        "📌 <b>2. Гарантии</b>\n"
        "• Все аккаунты проверены перед продажей\n"
        "• При проблемах - замена или возврат средств\n"
        "• Гарантия на аккаунты - 7 дней\n\n"
        "📌 <b>3. Возврат средств</b>\n"
        "• Возврат возможен в течение 24 часов\n"
        "• Причина должна быть обоснована\n"
        "• Возврат осуществляется в криптовалюте\n\n"
        "📌 <b>4. Запрещено</b>\n"
        "• Попытки обмана или мошенничества\n"
        "• Перепродажа аккаунтов без согласия\n"
        "• Оскорбления персонала\n\n"
        "📌 <b>5. Поддержка</b>\n"
        "• Все вопросы решаются через поддержку\n"
        "• Время ответа: до 24 часов\n"
        "• Мы всегда на связи! 💬\n\n"
        "🤝 <b>Спасибо, что выбираете CyberPuggShop!</b>",
        reply_markup=back_kb,
        parse_mode="HTML"
    )

# ============================================
# ===== АДМИН-ПАНЕЛЬ =====
# ============================================
@dp.message(F.text == "👑 Админ-панель")
async def admin_panel_request(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        await message.answer("⛔ Доступ запрещён.")
        return
    
    await message.answer(
        "🔐 <b>Введите пароль для доступа к админ-панели:</b>\n\n"
        "🔙 Для отмены нажмите 'Назад'.",
        reply_markup=back_kb,
        parse_mode="HTML"
    )
    await state.set_state(ShopStates.waiting_admin_password)

@dp.message(ShopStates.waiting_admin_password)
async def admin_password_check(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        await message.answer("⛔ Доступ запрещён.")
        return
    
    if message.text == "🔙 Назад":
        await state.clear()
        await state.set_state(ShopStates.main_menu)
        await message.answer("🔙 Возврат в главное меню", reply_markup=main_menu_kb)
        return
    
    if message.text == ADMIN_PASSWORD:
        await message.answer(
            "✅ <b>Доступ разрешён!</b>\n\n"
            "👑 <b>Админ-панель</b>\n\n"
            "Выберите действие:",
            reply_markup=admin_menu_kb,
            parse_mode="HTML"
        )
        await state.set_state(ShopStates.admin_panel)
    else:
        await message.answer(
            "❌ <b>Неверный пароль!</b>\n\n"
            "Попробуйте ещё раз или нажмите 'Назад'.",
            reply_markup=back_kb,
            parse_mode="HTML"
        )

# ============================================
# ===== АДМИН: СТАТИСТИКА =====
# ============================================
@dp.message(F.text == "📊 Статистика")
async def admin_stats(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        return
    
    total_users = len(users_db)
    total_actions = sum(len(logs) for logs in actions_log.values())
    
    await message.answer(
        f"📊 <b>Статистика магазина</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"📋 Всего действий: <b>{total_actions}</b>\n"
        f"📦 Товаров в наличии: 2\n"
        f"   • Аккаунт Brawl Stars - 899 ₽\n"
        f"   • Brawl Pass Plus - 199 ₽",
        reply_markup=admin_menu_kb,
        parse_mode="HTML"
    )

# ============================================
# ===== АДМИН: ПОЛЬЗОВАТЕЛИ =====
# ============================================
@dp.message(F.text == "👥 Пользователи")
async def admin_users(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        return
    
    if not users_db:
        await message.answer("👥 Пока ни одного пользователя.", reply_markup=admin_menu_kb)
        return
    
    text = "👥 <b>Список пользователей:</b>\n\n"
    for idx, (uid, data) in enumerate(users_db.items(), 1):
        text += f"{idx}. <b>ID:</b> <code>{uid}</code>\n"
        text += f"   📛 {data['name']}\n"
        text += f"   🔖 {data['username']}\n"
        text += f"   💰 Баланс: {data.get('balance_rub', 0)} ₽\n"
        text += f"   🕐 Последний визит: {data['last_seen']}\n\n"
        if idx >= 20:
            text += f"... и ещё {len(users_db) - 20} пользователей.\n"
            break
    
    await message.answer(text, reply_markup=admin_menu_kb, parse_mode="HTML")

# ============================================
# ===== АДМИН: ПОПОЛНЕНИЕ БАЛАНСА =====
# ============================================
@dp.message(F.text == "💰 Пополнить баланс")
async def admin_balance_start(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        return
    
    await message.answer(
        "💰 <b>Пополнение баланса пользователя</b>\n\n"
        "Введите <b>ID или @юзернейм</b> пользователя.\n"
        "Примеры: <code>123456789</code> или <code>@username</code>\n\n"
        "🔙 Для отмены нажмите 'Назад'.",
        reply_markup=back_kb,
        parse_mode="HTML"
    )
    await state.set_state(ShopStates.admin_balance_user)

@dp.message(ShopStates.admin_balance_user)
async def admin_balance_user_input(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        return
    
    if message.text == "🔙 Назад":
        await state.clear()
        await state.set_state(ShopStates.admin_panel)
        await message.answer("🔙 Возврат", reply_markup=admin_menu_kb)
        return
    
    identifier = message.text.strip()
    target_user = None
    
    if identifier.startswith('@'):
        username = identifier[1:].lower()
        for uid, data in users_db.items():
            if data.get("username", "").lower() == f"@{username}":
                target_user = uid
                break
    else:
        try:
            uid = int(identifier)
            if uid in users_db:
                target_user = uid
        except ValueError:
            pass
    
    if target_user is None:
        await message.answer(
            "❌ Пользователь не найден.\n\n"
            "Попробуйте ещё раз или нажмите 'Назад'.",
            reply_markup=back_kb,
            parse_mode="HTML"
        )
        return
    
    await state.update_data(target_user=target_user)
    await message.answer(
        f"✅ Найден пользователь: <b>{users_db[target_user]['name']}</b>\n"
        f"💰 Текущий баланс: <b>{users_db[target_user]['balance_rub']} ₽</b>\n\n"
        f"Введите <b>сумму пополнения</b> (целое число):\n\n"
        f"🔙 Для отмены нажмите 'Назад'.",
        reply_markup=back_kb,
        parse_mode="HTML"
    )
    await state.set_state(ShopStates.admin_balance_amount)

@dp.message(ShopStates.admin_balance_amount)
async def admin_balance_amount_input(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        return
    
    if message.text == "🔙 Назад":
        await state.clear()
        await state.set_state(ShopStates.admin_panel)
        await message.answer("🔙 Возврат", reply_markup=admin_menu_kb)
        return
    
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ Введите положительное целое число.\n\n"
            "Попробуйте ещё раз или нажмите 'Назад'.",
            reply_markup=back_kb,
            parse_mode="HTML"
        )
        return
    
    data = await state.get_data()
    target_user = data.get("target_user")
    if target_user is None:
        await message.answer("❌ Ошибка: пользователь не найден.", reply_markup=admin_menu_kb)
        await state.set_state(ShopStates.admin_panel)
        return
    
    users_db[target_user]["balance_rub"] += amount
    new_balance = users_db[target_user]["balance_rub"]
    add_history(target_user, amount, f"Пополнение от администратора (+{amount} ₽)")
    log_action(message.from_user, f"ПОПОЛНИЛ БАЛАНС пользователю {users_db[target_user]['username']} на {amount} ₽")
    
    try:
        await bot.send_message(
            target_user,
            f"💰 <b>Ваш баланс пополнен!</b>\n\n"
            f"Сумма: <b>+{amount} ₽</b>\n"
            f"📊 Новый баланс: <b>{new_balance} ₽</b>\n\n"
            f"👤 <b>Кто:</b> Администратор",
            parse_mode="HTML"
        )
    except:
        pass
    
    await message.answer(
        f"✅ Баланс пользователя <b>{users_db[target_user]['name']}</b> пополнен!\n\n"
        f"💰 Сумма: <b>+{amount} ₽</b>\n"
        f"📊 Новый баланс: <b>{new_balance} ₽</b>",
        reply_markup=admin_menu_kb,
        parse_mode="HTML"
    )
    await state.clear()
    await state.set_state(ShopStates.admin_panel)

# ============================================
# ===== АДМИН: ЛОГ ДЕЙСТВИЙ =====
# ============================================
@dp.message(F.text == "📋 Лог действий")
async def admin_actions(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        return
    
    all_actions = []
    for user_id, logs in actions_log.items():
        user_data = users_db.get(user_id, {})
        username = user_data.get("username", f"ID:{user_id}")
        for log in logs[-5:]:
            all_actions.append(f"{log} — {username}")
    
    if not all_actions:
        await message.answer("📋 Пока нет действий.", reply_markup=admin_menu_kb)
        return
    
    text = "📋 <b>Последние действия:</b>\n\n"
    for entry in all_actions[-20:]:
        text += f"• {entry}\n"
    
    await message.answer(text, reply_markup=admin_menu_kb, parse_mode="HTML")

# ============================================
# ===== ОБРАБОТЧИК НАЗАД =====
# ============================================
@dp.message(F.text == "🔙 Назад")
async def handle_back(message: Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state == ShopStates.waiting_support:
        await state.clear()
        await state.set_state(ShopStates.main_menu)
        await message.answer("🔙 Возврат в главное меню", reply_markup=main_menu_kb)
        return
    
    if current_state in [ShopStates.admin_panel, ShopStates.waiting_admin_password, ShopStates.admin_balance_user, ShopStates.admin_balance_amount]:
        await state.clear()
        await state.set_state(ShopStates.main_menu)
        if message.from_user.id in OWNER_IDS:
            await message.answer("🔙 Возврат в главное меню", reply_markup=owner_menu_kb)
        else:
            await message.answer("🔙 Возврат в главное меню", reply_markup=main_menu_kb)
        return
    
    await state.clear()
    await state.set_state(ShopStates.main_menu)
    if message.from_user.id in OWNER_IDS:
        await message.answer("🔙 Возврат в главное меню", reply_markup=owner_menu_kb)
    else:
        await message.answer("🔙 Возврат в главное меню", reply_markup=main_menu_kb)

# ============================================
# ===== ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ =====
# ============================================
@dp.message()
async def catch_all_messages(message: Message, state: FSMContext):
    await message.answer(
        "❓ Используйте кнопки меню.\n"
        "Если вы не видите кнопки, нажмите /start",
        reply_markup=main_menu_kb
    )

# ============================================
# ===== ЗАПУСК БОТА =====
# ============================================
async def main():
    print("=" * 60)
    print("🤖 CyberPuggShop БОТ ЗАПУЩЕН")
    print("=" * 60)
    print("👑 Владельцы: 5977744301, 8985475819")
    print("🛒 Магазин аккаунтов и доната Brawl Stars")
    print("✅ Все функции активны:")
    print("   - 👤 Профиль")
    print("   - 📦 Наличие товара")
    print("   - 🛒 Покупка через криптовалюту")
    print("   - ❓ Поддержка")
    print("   - 📋 Правила магазина")
    print("   - 👑 Админ-панель")
    print("   - 🔔 Уведомления владельцам")
    print("=" * 60)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
