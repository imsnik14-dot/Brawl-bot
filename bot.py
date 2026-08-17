import asyncio
import logging
import json
import urllib.request
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
        except:
            pass

async def notify_owner(user: types.User, action: str):
    log_action(user, action)
    for owner_id in OWNER_IDS:
        try:
            await bot.send_message(owner_id, f"🔔 <b>Действие пользователя</b>\n\n{action}", parse_mode="HTML")
        except:
            pass

# ============================================
# ===== CRYPTOBOT (urllib) =====
# ============================================
def crypto_request(url, data):
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
        "Content-Type": "application/json"
    }
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except Exception as e:
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
        raise Exception(f"Ошибка Cryptobot: {result}")

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
# ===== ИНИЦИАЛИЗАЦИЯ (AIOGRAM 3.x) =====
# ============================================
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Регистрация мидлвара (новый синтаксис для aiogram 3.x)
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
        await callback.message.answer(f"❌ <b>Ошибка:</b> {e}", reply_markup=main_menu_kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("check_crypto_"))
async def check_crypto_payment_status(callback: CallbackQuery, state: FSMContext):
    invoice_id = int(callback.data.replace("check_crypto_", ""))
    await callback.answer("🔄 Проверяем...", show_alert=False)
    
    try:
        status = await check_crypto_payment(invoice_id)
        
        if status == "paid":
            order = pending_orders.get(invoice_id)
            if order:
                product_name = order["product"]
                price = order["price"]
                
                await bot.send_message(order["user_id"], f"✅ <b>Оплата прошла успешно!</b>\n\n🎯 {product_name}\n💰 {price} ₽\n\nСкоро с вами свяжется оператор. 🎉", parse_mode="HTML")
                await notify_owner(callback.from_user, f"🪙 <b>НОВАЯ ОПЛАТА</b>\n👤 {callback.from_user.full_name}\n🎯 {product_name}\n💰 {price} ₽")
                
                await callback.message.edit_text(f"✅ <b>Оплата подтверждена!</b>\n\n🎯 {product_name}\n💰 {price} ₽\n\nСпасибо за покупку! 🎉", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="payment_cancel")]]), parse_mode="HTML")
                del pending_orders[invoice_id]
                await callback.answer("✅ Оплачено!", show_alert=True)
            else:
                await callback.answer("❌ Заказ не найден!", show_alert=True)
        elif status == "pending":
            await callback.answer("⏳ Ещё не оплачен!", show_alert=True)
        elif status == "expired":
            await callback.answer("⏰ Время истекло!", show_alert=True)
        else:
            await callback.answer(f"❌ Статус: {status}", show_alert=True)
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)

@dp.callback_query(F.data == "payment_cancel")
async def payment_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer("❌ Отменено", show_alert=True)
    await callback.message.delete()
    await callback.message.answer("🔙 Главное меню", reply_markup=main_menu_kb)
    await state.clear()
    await state.set_state(ShopStates.main_menu)

# ============================================
# ===== ПОДДЕРЖКА =====
# ============================================
@dp.message(F.text == "❓ Поддержка")
async def handle_support(message: Message, state: FSMContext):
    await message.answer(
        "❓ <b>Поддержка</b>\n\n"
        "Напишите ваше сообщение:\n\n"
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
        await message.answer("🔙 Возврат", reply_markup=main_menu_kb)
        return
    
    user = message.from_user
    admin_text = f"📩 <b>Сообщение от пользователя</b>\n👤 {user.full_name} (@{user.username})\n🆔 {user.id}\n\n{message.text}"
    
    try:
        for owner_id in OWNER_IDS:
            try:
                await bot.send_message(owner_id, admin_text, parse_mode="HTML")
            except:
                pass
        await message.answer("✅ <b>Сообщение отправлено!</b>\n\nНаш оператор свяжется с вами.", reply_markup=main_menu_kb, parse_mode="HTML")
        await state.clear()
        await state.set_state(ShopStates.main_menu)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}", reply_markup=support_kb, parse_mode="HTML")

# ============================================
# ===== ПРАВИЛА МАГАЗИНА =====
# ============================================
@dp.message(F.text == "📋 Правила магазина")
async def show_rules(message: Message, state: FSMContext):
    await message.answer(
        "📋 <b>Правила магазина</b>\n\n"
        "📌 Оплата в криптовалюте (BTC, USDT, TON)\n"
        "📌 Цены в рублях (₽)\n"
        "📌 Товар передаётся после оплаты\n"
        "📌 Гарантия 7 дней\n"
        "📌 Возврат в течение 24 часов\n\n"
        "🤝 <b>Спасибо за покупки!</b>",
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
    
    await message.answer("🔐 <b>Введите пароль:</b>", reply_markup=back_kb, parse_mode="HTML")
    await state.set_state(ShopStates.waiting_admin_password)

@dp.message(ShopStates.waiting_admin_password)
async def admin_password_check(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        await message.answer("⛔ Доступ запрещён.")
        return
    
    if message.text == "🔙 Назад":
        await state.clear()
        await state.set_state(ShopStates.main_menu)
        await message.answer("🔙 Возврат", reply_markup=main_menu_kb)
        return
    
    if message.text == ADMIN_PASSWORD:
        await message.answer("✅ <b>Доступ разрешён!</b>\n\n👑 <b>Админ-панель</b>", reply_markup=admin_menu_kb, parse_mode="HTML")
        await state.set_state(ShopStates.admin_panel)
    else:
        await message.answer("❌ <b>Неверный пароль!</b>", reply_markup=back_kb, parse_mode="HTML")

# ============================================
# ===== АДМИН: СТАТИСТИКА =====
# ============================================
@dp.message(F.text == "📊 Статистика")
async def admin_stats(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        return
    
    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: {len(users_db)}\n"
        f"📦 Товаров: 2\n"
        f"   • Аккаунт - 899 ₽\n"
        f"   • БП+ - 199 ₽",
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
        await message.answer("👥 Пока нет пользователей.", reply_markup=admin_menu_kb)
        return
    
    text = "👥 <b>Пользователи:</b>\n\n"
    for idx, (uid, data) in enumerate(users_db.items(), 1):
        text += f"{idx}. <b>ID:</b> <code>{uid}</code>\n"
        text += f"   📛 {data['name']}\n"
        text += f"   💰 {data.get('balance_rub', 0)} ₽\n\n"
        if idx >= 20:
            text += f"... и ещё {len(users_db) - 20}\n"
            break
    
    await message.answer(text, reply_markup=admin_menu_kb, parse_mode="HTML")

# ============================================
# ===== АДМИН: ПОПОЛНЕНИЕ БАЛАНСА =====
# ============================================
@dp.message(F.text == "💰 Пополнить баланс")
async def admin_balance_start(message: Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        return
    
    await message.answer("💰 Введите ID пользователя:", reply_markup=back_kb, parse_mode="HTML")
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
    
    try:
        target_user = int(message.text.strip())
        if target_user not in users_db:
            await message.answer("❌ Пользователь не найден.", reply_markup=back_kb)
            return
    except:
        await message.answer("❌ Введите ID (цифры).", reply_markup=back_kb)
        return
    
    await state.update_data(target_user=target_user)
    await message.answer(
        f"✅ Найден: {users_db[target_user]['name']}\n"
        f"💰 Баланс: {users_db[target_user]['balance_rub']} ₽\n\n"
        f"Введите сумму:",
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
    except:
        await message.answer("❌ Введите число.", reply_markup=back_kb)
        return
    
    data = await state.get_data()
    target_user = data.get("target_user")
    users_db[target_user]["balance_rub"] += amount
    new_balance = users_db[target_user]["balance_rub"]
    add_history(target_user, amount, f"Пополнение от админа (+{amount} ₽)")
    
    try:
        await bot.send_message(target_user, f"💰 <b>Баланс пополнен!</b>\n\n+{amount} ₽\n📊 Новый баланс: {new_balance} ₽", parse_mode="HTML")
    except:
        pass
    
    await message.answer(
        f"✅ Баланс пополнен!\n\n"
        f"💰 +{amount} ₽\n"
        f"📊 Новый баланс: {new_balance} ₽",
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
        await message.answer("🔙 Возврат", reply_markup=main_menu_kb)
        return
    
    if current_state in [ShopStates.admin_panel, ShopStates.waiting_admin_password, ShopStates.admin_balance_user, ShopStates.admin_balance_amount]:
        await state.clear()
        await state.set_state(ShopStates.main_menu)
        if message.from_user.id in OWNER_IDS:
            await message.answer("🔙 Возврат", reply_markup=owner_menu_kb)
        else:
            await message.answer("🔙 Возврат", reply_markup=main_menu_kb)
        return
    
    await state.clear()
    await state.set_state(ShopStates.main_menu)
    if message.from_user.id in OWNER_IDS:
        await message.answer("🔙 Возврат", reply_markup=owner_menu_kb)
    else:
        await message.answer("🔙 Возврат", reply_markup=main_menu_kb)

# ============================================
# ===== ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ =====
# ============================================
@dp.message()
async def catch_all_messages(message: Message, state: FSMContext):
    await message.answer("❓ Используйте кнопки меню.\nЕсли не видите кнопки, нажмите /start", reply_markup=main_menu_kb)

# ============================================
# ===== ЗАПУСК =====
# ============================================
async def main():
    print("=" * 60)
    print("🤖 CyberPuggShop БОТ ЗАПУЩЕН (AIORAM 3.x)")
    print("=" * 60)
    print("👑 Владельцы: 5977744301, 8985475819")
    print("🛒 Магазин Brawl Stars")
    print("✅ Без aiohttp (используется urllib)")
    print("=" * 60)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
