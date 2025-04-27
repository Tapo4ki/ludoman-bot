import asyncio
import random
import sqlite3
import aiohttp
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from fastapi import FastAPI, Request
from aiogram.types import Message
import uvicorn

# === CONFIG ===
BOT_TOKEN = "7817746094:AAGGoYbMgkBhQJbjfWAU3P18c9xSIeh3RUU"
CRYPTOBOT_API = "374433:AAQ3pNU1GwWOm8OUxBH5dSqXhWqgUiApDpo"
CRYPTOBOT_WEBHOOK_SECRET = "your_secret"
CRYPTOBOT_URL = "https://pay.crypt.bot/api/createInvoice"
COMMISSION_PERCENT = 5

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

app = FastAPI()

# === DATABASE ===
conn = sqlite3.connect("users.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    balance REAL DEFAULT 0
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,
    amount REAL,
    detail TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

# === FSM ===
class GameStates(StatesGroup):
    choosing_amount = State()

class WithdrawStates(StatesGroup):
    entering_amount = State()
    entering_wallet = State()

class DepositStates(StatesGroup):
    waiting_for_amount = State()

# === TEMP MEMORY ===
temp_bets = {}
temp_withdraw = {}
temp_invoices = {}
temp_check = {}

# === KEYBOARDS ===
main_kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="🎲 Играть")],
    [KeyboardButton(text="➕ Пополнить"), KeyboardButton(text="📦 Вывести")],
    [KeyboardButton(text="📜 История"), KeyboardButton(text="ℹ️ Помощь")]
])

# === UTILS ===
def get_balance(user_id):
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0

def update_balance(user_id, amount):
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()

def log_transaction(user_id, type, amount, detail=""):
    cursor.execute("INSERT INTO transactions (user_id, type, amount, detail) VALUES (?, ?, ?, ?)",
                   (user_id, type, amount, detail))
    conn.commit()

async def create_crypto_invoice(amount: float, user_id: int):
    data = {
        "asset": "USDT",
        "amount": amount,
        "description": f"Deposit for user {user_id}",
        "hidden_message": "Thanks for the deposit!",
        "paid_btn_name": "openBot",
        "paid_btn_url": f"https://t.me/casino_lud_bot?start={user_id}"
    }
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API}

    async with aiohttp.ClientSession() as session:
        async with session.post(CRYPTOBOT_URL, data=data, headers=headers) as resp:
            res = await resp.json()
            if not res.get("ok"):
                raise Exception(f"Ошибка от API: {res.get('error')}")

            invoice_id = res["result"]["invoice_id"]
            pay_url = res["result"]["pay_url"]

            cursor.execute("INSERT INTO invoices (invoice_id, user_id, amount, status) VALUES (?, ?, ?, ?)",
                           (invoice_id, user_id, amount, "pending"))
            conn.commit()

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="🔗 Оплатить", url=pay_url)
            keyboard.button(text="✅ Проверить оплату", callback_data=f"check_{invoice_id}")
            return keyboard.as_markup()

# === HANDLERS ===
@router.message(F.text == "/start")
async def start(msg: types.Message):
    update_balance(msg.from_user.id, 0)
    await msg.answer("🌟 Добро пожаловать в кубик-бот!", reply_markup=main_kb)

@router.message(F.text == "💰 Баланс")
async def balance(msg: types.Message):
    bal = get_balance(msg.from_user.id)
    await msg.answer(f"💰 Твой баланс: {bal:.2f} USDT")

@router.message(F.text == "➕ Пополнить")
async def deposit(msg: types.Message, state: FSMContext):
    await msg.answer("💳 Введи сумму пополнения в USDT:")
    await state.set_state(DepositStates.waiting_for_amount)

@router.message(F.text == "🎲 Играть")
async def start_game(msg: types.Message, state: FSMContext):
    await msg.answer("💵 Введи сумму ставки:")
    await state.set_state(GameStates.choosing_amount)

@router.message(F.text == "📦 Вывести")
async def ask_withdraw_amount(msg: types.Message, state: FSMContext):
    await msg.answer("💸 Введи сумму для вывода:")
    await state.set_state(WithdrawStates.waiting_for_amount)

class GameStates(StatesGroup):
    choosing_amount = State()
    choosing_bet_type = State()


@router.message(GameStates.choosing_amount)
async def get_bet_amount(msg: types.Message, state: FSMContext):
    try:
        # Заменяем запятую на точку и убираем пробелы
        amount_text = msg.text.strip().replace(",", ".")

        # Логируем, что пришло в amount_text
        print(f"Получен текст ставки: '{amount_text}'")

        # Проверка на пустой ввод
        if not amount_text:
            await msg.answer("❌ Введи корректную ставку. Пример: 0.5, 1.25, 10")
            return

        # Пробуем преобразовать в число
        amount = float(amount_text)
        amount = round(amount, 2)  # округляем до 2 знаков после запятой
        print(f"Преобразованная ставка: {amount}")

        balance = get_balance(msg.from_user.id)
        print(f"Баланс пользователя: {balance}")

        if amount <= 0:
            await msg.answer("❌ Ставка должна быть больше 0.")
            return
        if amount > balance:
            await msg.answer("❌ Недостаточно средств на балансе.")
            return

        # Обновляем данные и переходим к следующему состоянию
        await state.update_data(bet_amount=amount)
        await state.set_state(GameStates.choosing_bet_type)
        await msg.answer("🎯 Выбери тип ставки:", reply_markup=bet_type_kb())

    except ValueError as e:
        print(f"Ошибка при обработке ставки: {e}")
        await msg.answer("❌ Введи корректное число. Пример: 0.5, 1.25, 10")

def bet_type_kb():
    # Создаем экземпляр клавиатуры
    keyboard = InlineKeyboardBuilder()

    # Добавляем кнопки с нужными параметрами
    keyboard.button(text="Чёт", callback_data="bet_even")  # Кнопка для ставки на чёт
    keyboard.button(text="Нечёт", callback_data="bet_odd")  # Кнопка для ставки на нечет

    # Возвращаем клавиатуру в нужном формате
    return keyboard.as_markup()

# Обработчик выбранного типа ставки
@router.callback_query(lambda c: c.data.startswith("bet_"))
async def process_bet(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data["bet_amount"]

    bet_type = callback.data.split("_")[1]  # even / odd

    # Отправляем анимированный кубик
    dice_msg = await callback.message.answer_dice(emoji="🎲")

    # Ждём завершения анимации (около 3-4 секунд — но мы точно дождёмся через message.dice.value)
    await asyncio.sleep(3.5)  # Можно и без этого, но для надёжности

    roll = dice_msg.dice.value  # Получаем результат броска
    win = False
    payout = round(amount * 1.9, 2)

    # Логика выигрыша
    if bet_type == "even" and roll % 2 == 0:
        win = True
    elif bet_type == "odd" and roll % 2 == 1:
        win = True

    # Обновление баланса
    update_balance(callback.from_user.id, -amount)
    log_transaction(callback.from_user.id, "game_bet", -amount, f"Бросок: {roll}, Ставка: {bet_type}")

    if win:
        update_balance(callback.from_user.id, payout)
        log_transaction(callback.from_user.id, "game_win", payout, f"Выигрыш за ставку: {bet_type}")
        await callback.message.answer(
            f"🎲 Выпало число: {roll}\n🎉 Победа! Ты получил +{payout:.2f} USDT (множитель 1.9x)"
        )
    else:
        await callback.message.answer(
            f"🎲 Выпало число: {roll}\n💸 Ты проиграл {amount:.2f} USDT"
        )

    await state.clear()
    await callback.answer()

@router.message(DepositStates.waiting_for_amount)
async def handle_deposit_amount(msg: types.Message, state: FSMContext):
    try:
        amount = float(msg.text)
        commission = amount * COMMISSION_PERCENT / 100
        total = amount + commission
        markup = await create_crypto_invoice(total, msg.from_user.id)
        log_transaction(msg.from_user.id, "deposit", total, "Запрос инвойса")
        await msg.answer(
            f"Сумма пополнения: {amount:.2f} USDT\nКомиссия: {commission:.2f} USDT\nИтого к оплате: {total:.2f} USDT",
            reply_markup=markup
        )
        await state.clear()
    except Exception as e:
        await msg.answer(f"❌ Не удалось создать инвойс. Попробуй позже.\nОшибка: {e}")

@router.callback_query(F.data.startswith("check_"))
async def check_invoice_status(callback: CallbackQuery):
    invoice_id = callback.data.split("_", 1)[1]
    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API}
    data = {"invoice_ids": [invoice_id]}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=data, headers=headers) as resp:
            res = await resp.json()
            if not res.get("ok"):
                await callback.answer("❌ Ошибка при проверке.", show_alert=True)
                return

            invoice = res["result"]["items"][0]
            status = invoice["status"]
            amount = float(invoice["amount"])
            user_id = int(invoice["description"].split()[-1])

            if status == "paid":
                cursor.execute("SELECT status FROM invoices WHERE invoice_id = ?", (invoice_id,))
                row = cursor.fetchone()
                if row and row[0] == "paid":
                    await callback.answer("💰 Оплата уже зачислена!", show_alert=True)
                    return

                update_balance(user_id, amount)
                log_transaction(user_id, "deposit", amount, "Проверка вручную")
                cursor.execute("UPDATE invoices SET status = 'paid' WHERE invoice_id = ?", (invoice_id,))
                conn.commit()

                await callback.message.answer(f"✅ Успешно! Пополнение на {amount:.2f} USDT зачислено на твой счёт.")
            else:
                await callback.answer("❌ Платёж пока не выполнен.", show_alert=True)

class WithdrawStates(StatesGroup):
    waiting_for_amount = State()
MIN_WITHDRAW = 0.1  # Минимальная сумма вывода

API_TOKEN = "374433:AAQ3pNU1GwWOm8OUxBH5dSqXhWqgUiApDpo"


async def create_crypto_bot_check(amount: float, user_id: int) -> InlineKeyboardMarkup:
    url = "https://pay.crypt.bot/api/createCheck"
    headers = {
        "Content-Type": "application/json",
        "Crypto-Pay-API-Token": API_TOKEN  # Обязательно правильный заголовок
    }
    payload = {
        "asset": "USDT",
        "amount": amount,
        "description": f"Вывод средств для пользователя {user_id}"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as response:
            data = await response.json()
            # Логируем ответ от API для диагностики
            print(f"Ответ от API: {data}")
            if data.get("ok") and "result" in data:
                result = data["result"]
                if "bot_check_url" in result:
                    pay_url = result["bot_check_url"]
                else:
                    raise Exception(f"Ошибка: отсутствует ключ 'bot_check_url' в ответе: {result}")
            else:
                raise Exception(f"Ошибка при создании чека: {data}")

    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(text="💸 Забрать чек", url=pay_url)
    )

    print(f"Создан чек на сумму {amount}. Ссылка: {pay_url}")

    return keyboard.as_markup()


@router.message(WithdrawStates.waiting_for_amount)
async def process_withdraw_amount(msg: types.Message, state: FSMContext):
    user_id = msg.from_user.id
    balance = get_balance(user_id)

    try:
        # Заменяем запятую на точку для корректного преобразования
        amount = float(msg.text.replace(",", "."))
    except ValueError:
        await msg.answer("❗ Введи корректную сумму.")
        return

    if amount < MIN_WITHDRAW:
        await msg.answer(f"❗ Минимальная сумма для вывода — {MIN_WITHDRAW} USDT.")
        return

    if amount > balance:
        await msg.answer("❌ У тебя недостаточно средств для вывода.")
        return

    # Обновляем баланс и создаём чек
    update_balance(user_id, -amount)

    # Создаем клавиатуру с чек-ссылкой
    check_keyboard = await create_crypto_bot_check(amount=amount, user_id=user_id)

    if check_keyboard:
        # Отправляем клавиатуру с кнопкой для получения чека
        await msg.answer(f"✅ Чек на {amount:.2f} USDT создан. Перейдите по кнопке ниже для его получения:",
                         reply_markup=check_keyboard)
        log_transaction(user_id, "withdraw", -amount, f"Создан чек на вывод")
    else:
        await msg.answer("⚠️ Не удалось создать чек. Попробуй позже.")
        update_balance(user_id, +amount)  # Возврат средств при ошибке

    await state.clear()

def get_transaction_history(user_id: int) -> list[dict]:
    cursor.execute("SELECT type, amount, timestamp, detail FROM transactions WHERE user_id = ? ORDER BY timestamp DESC", (user_id,))
    rows = cursor.fetchall()
    history = []
    for row in rows:
        history.append({
            "type": row[0],
            "amount": row[1],
            "timestamp": row[2],
            "description": row[3]
        })
    return history

@router.message(F.text == "📜 История")
async def show_history(msg: Message):
    user_id = msg.from_user.id
    history = get_transaction_history(user_id)

    if not history:
        await msg.answer("❗ История пуста.")
        return

    text = "📜 <b>История транзакций:</b>\n\n"

    for tx in history[-10:][::-1]:  # Показываем последние 10 записей (можешь убрать ограничение)
        action = "➕ Пополнение" if tx["type"] == "deposit" else "➖ Вывод"
        amount = tx["amount"]
        timestamp = tx["timestamp"]
        description = tx.get("description", "")
        text += f"{action} на <b>{amount:.2f} USDT</b>\n🕓 <i>{timestamp}</i>\n{description}\n\n"

    await msg.answer(text)

# === BOT START ===
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import threading
    threading.Thread(target=lambda: uvicorn.run(app, host="0.0.0.0", port=8000)).start()
    asyncio.run(main())
