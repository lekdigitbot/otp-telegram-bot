import os
import re
import asyncio
import aiohttp
import html
import sqlite3
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Update,
)

# =============================================================
# CONFIGURATION
# =============================================================
BOT_TOKEN = "8785747989:AAFNNyq_99EhUZwg5drc2e5rV7tnJn7QiTw"                  # Replace with your Telegram Bot Token
RENDER_URL = "https://otp-telegram-bot-fpmp.onrender.com"

ADMIN_ID = 7103520365                           # Your Telegram User ID
TELEGRAM_GROUP_ID = -1004315686306              # Telegram Group ID

# Mandatory channels/groups to join
REQUIRED_CHANNELS = [
    {"title": "Main Group", "chat_id": -1004315686306, "link": "https://t.me/your_group_link"},
    {"title": "Updates Channel 1", "chat_id": "-1004494412618", "link": "https://t.me/lekdigitaldiscussiongroup"},
    {"title": "Updates Channel 2", "chat_id": "-1004437067843", "link": "https://t.me/lekdigitalbackupgroup"}
]

THIRDWAVE_API_KEY = "tw_live_5e1666d46397c359b5ba2eda85b40fdf384c2ffd37ebb519290f358ef4260416"       # Replace with your Thirdwave API Key
THIRDWAVE_BASE_URL = "https://clients.thirdwave.im/api/v1"

FLAT_OTP_RATE = 0.003
MIN_WITHDRAWAL = 0.25

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

http_session = None

# =============================================================
# FSM STATES FOR WITHDRAWAL
# =============================================================
class WithdrawalState(StatesGroup):
    waiting_for_details = State()
    waiting_for_amount = State()

# =============================================================
# DATABASE SETUP (SQLITE)
# =============================================================
DB_FILE = "/tmp/bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT,
            country TEXT,
            phone_number TEXT UNIQUE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assignments (
            phone_number TEXT PRIMARY KEY,
            user_id INTEGER,
            service TEXT,
            country TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS balances (
            user_id INTEGER PRIMARY KEY,
            balance REAL
        )
    """)
    conn.commit()
    conn.close()

init_db()

def db_add_stock(service: str, country: str, numbers: list):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    added_count = 0
    for num in numbers:
        try:
            cursor.execute(
                "INSERT INTO stock (service, country, phone_number) VALUES (?, ?, ?)",
                (service, country, num)
            )
            added_count += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return added_count

def db_get_services():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT service, COUNT(*) FROM stock GROUP BY service HAVING COUNT(*) >= 2")
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}

def db_get_countries_for_service(service: str):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT country, COUNT(*) FROM stock WHERE service = ? GROUP BY country HAVING COUNT(*) >= 2",
        (service,)
    )
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}

def db_assign_two_numbers(service: str, country: str, user_id: int):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT phone_number FROM stock WHERE service = ? AND country = ? LIMIT 2",
        (service, country)
    )
    rows = cursor.fetchall()
    
    if len(rows) < 2:
        conn.close()
        return None

    nums = [rows[0][0], rows[1][0]]
    for num in nums:
        cursor.execute("DELETE FROM stock WHERE phone_number = ?", (num,))
        cursor.execute(
            "INSERT OR REPLACE INTO assignments (phone_number, user_id, service, country) VALUES (?, ?, ?, ?)",
            (num, user_id, service, country)
        )
    
    conn.commit()
    conn.close()
    return nums

def db_get_assigned_user(phone_number: str):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, service, country FROM assignments WHERE phone_number = ?", (phone_number,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"user_id": row[0], "service": row[1], "country": row[2]}
    return None

def db_add_balance(user_id: int, amount: float):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO balances (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?",
        (user_id, amount, amount)
    )
    conn.commit()
    conn.close()

def db_deduct_balance(user_id: int, amount: float):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("UPDATE balances SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def db_get_balance(user_id: int) -> float:
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM balances WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0.0

# =============================================================
# HELPER FUNCTIONS
# =============================================================
PROCESSED_OTPS = set()

def mask_phone_number(num: str) -> str:
    clean = re.sub(r"\D", "", str(num))
    if len(clean) <= 6:
        return clean
    return f"{clean[:5]}****{clean[-3:]}"

def extract_otp_code(body: str, fallback_otp: str = "") -> str:
    if fallback_otp and str(fallback_otp).strip() and str(fallback_otp) != "None":
        return str(fallback_otp).strip()
    match = re.search(r'\b\d{4,8}\b', body)
    return match.group(0) if match else "No Code"

def get_main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 GET NUMBER"), KeyboardButton(text="🔴 LIVE TRAFFIC")],
            [KeyboardButton(text="💸 WITHDRAW"), KeyboardButton(text="💰 BALANCE")],
            [KeyboardButton(text="♾️ REFER AND EARN"), KeyboardButton(text="💀 SUPPORT")],
            [KeyboardButton(text="📊 STATUS")]
        ],
        resize_keyboard=True
    )

async def check_user_joined_all(user_id: int) -> bool:
    for ch in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=ch["chat_id"], user_id=user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception:
            return False
    return True

def get_force_sub_keyboard():
    buttons = []
    for ch in REQUIRED_CHANNELS:
        buttons.append([InlineKeyboardButton(text=f"📢 Join {ch['title']}", url=ch["link"])])
    buttons.append([InlineKeyboardButton(text="✅ Check Membership", callback_data="check_join")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# -------------------------------------------------------------
# ADMIN HANDLER
# -------------------------------------------------------------
@dp.message(F.text.startswith("/addnumber"))
async def add_number_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ <b>Unauthorized:</b> Admin only command.", parse_mode="HTML")
        return

    raw_text = message.text.strip()
    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
    if not lines:
        return

    first_line_parts = lines[0].split(maxsplit=2)
    
    if len(first_line_parts) < 3:
        await message.answer(
            "⚠️ <b>Usage Format:</b>\n"
            "<code>/addnumber Bolt Nigeria\n"
            "2348052055633\n"
            "2348052265099</code>",
            parse_mode="HTML"
        )
        return

    service_name = first_line_parts[1].capitalize()
    country_name = first_line_parts[2].capitalize()
    raw_numbers = []

    if len(lines) > 1:
        for line in lines[1:]:
            raw_numbers.extend(line.split(","))

    new_numbers = [re.sub(r"\D", "", num) for num in raw_numbers if re.sub(r"\D", "", num)]

    if not new_numbers:
        await message.answer("⚠️ No valid phone numbers found in input.", parse_mode="HTML")
        return

    added_count = db_add_stock(service_name, country_name, new_numbers)

    await message.answer(
        f"✅ <b>Added {added_count} number(s) to {service_name} ({country_name})!</b>",
        parse_mode="HTML"
    )

    group_announcement = (
        f"📢 <b>NEW STOCK UPDATE!</b> 📢\n\n"
        f"🔹 <b>Service:</b> {service_name}\n"
        f"🌍 <b>Country:</b> {country_name}\n"
        f"📱 <b>New Numbers Added:</b> <code>{added_count}</code>\n"
        f"🚀 <i>Press 'GET NUMBER' in bot to request your numbers!</i>"
    )
    try:
        await bot.send_message(chat_id=TELEGRAM_GROUP_ID, text=group_announcement, parse_mode="HTML")
    except Exception as err:
        print(f"[ERROR] Stock announcement failed: {err}")

# -------------------------------------------------------------
# USER HANDLERS
# -------------------------------------------------------------
@dp.message(F.text == "/start")
async def start_handler(message: Message):
    if not await check_user_joined_all(message.from_user.id):
        await message.answer(
            "⚠️ <b>Access Denied!</b>\n"
            "You must join all our mandatory channels and main group before using this bot:",
            reply_markup=get_force_sub_keyboard(),
            parse_mode="HTML"
        )
        return

    welcome_text = f"👋 Welcome <b>{message.from_user.first_name}</b> to <b>Lekdigital Number Zone</b>!"
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode="HTML")

@dp.callback_query(F.data == "check_join")
async def check_join_callback(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        pass

    if await check_user_joined_all(callback.from_user.id):
        await callback.message.answer("✅ Thank you for joining! You now have full access.", reply_markup=get_main_menu())
    else:
        await callback.message.answer("❌ You still haven't joined all required channels/group.", reply_markup=get_force_sub_keyboard())

@dp.message(F.text == "📱 GET NUMBER")
async def show_services_handler(message: Message):
    if not await check_user_joined_all(message.from_user.id):
        await message.answer("⚠️ Please join our channels to access stock.", reply_markup=get_force_sub_keyboard())
        return

    active_services = db_get_services()

    if not active_services:
        await message.answer(
            "⚠️ <b>Out of Stock!</b> No service has at least 2 numbers available right now.",
            reply_markup=get_main_menu(),
            parse_mode="HTML"
        )
        return

    buttons = [
        [InlineKeyboardButton(text=f"🔹 {srv} ({count} total)", callback_data=f"s_{srv}")]
        for srv, count in active_services.items()
    ]
    await message.answer("📲 <b>Select a service:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@dp.callback_query(F.data.startswith("s_"))
async def process_service_selection(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        pass

    service_name = callback.data[2:]
    countries = db_get_countries_for_service(service_name)

    if not countries:
        await callback.message.answer(f"⚠️ Out of stock for <b>{html.escape(service_name)}</b>.", parse_mode="HTML")
        return

    buttons = [
        [InlineKeyboardButton(text=f"🌍 {cntry} ({count} available)", callback_data=f"c_{service_name}_{cntry}")]
        for cntry, count in countries.items()
    ]
    await callback.message.answer(
        f"🌍 <b>Select Country for {html.escape(service_name)}:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("c_"))
async def process_number_assignment(callback: CallbackQuery):
    try:
        await callback.answer("Assigning numbers...")
    except Exception:
        pass

    parts = callback.data.split("_", 2)
    if len(parts) < 3:
        await callback.message.answer("❌ Invalid request data.")
        return

    service_name = parts[1]
    country_name = parts[2]
    user_id = callback.from_user.id

    nums = db_assign_two_numbers(service_name, country_name, user_id)

    if not nums:
        await callback.message.answer(
            f"⚠️ <b>Out of Stock!</b> Need at least 2 numbers available for <b>{html.escape(service_name)} ({html.escape(country_name)})</b>.",
            parse_mode="HTML"
        )
        return

    response = (
        f"🌐 <b>2 Numbers Assigned Successfully!</b>\n\n"
        f"🔹 <b>Service:</b> {html.escape(service_name)}\n"
        f"🌍 <b>Country:</b> {html.escape(country_name)}\n"
        f"📱 <b>Number 1:</b> <code>{nums[0]}</code>\n"
        f"📱 <b>Number 2:</b> <code>{nums[1]}</code>\n\n"
        f"⏳ <b>Waiting for OTPs...</b>\n"
        f"💰 <b>Rate:</b> <code>${FLAT_OTP_RATE}</code> / OTP\n\n"
        f"📢 <i>All incoming OTPs stream directly to our main group!</i>"
    )
    await callback.message.answer(response, parse_mode="HTML")

@dp.message(F.text == "🔴 LIVE TRAFFIC")
async def live_traffic_handler(message: Message):
    global http_session
    if not http_session or http_session.closed:
        await message.answer("⚠️ Server session starting up, please try again in a few seconds.", reply_markup=get_main_menu())
        return

    headers = {"Authorization": f"Bearer {THIRDWAVE_API_KEY}", "Accept": "application/json"}
    
    try:
        async with http_session.get(f"{THIRDWAVE_BASE_URL}/traffic?pageSize=5", headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                rows = data.get("rows", [])
                
                if rows:
                    text = "🔴 <b>Recent 5 OTP Traffic:</b>\n\n"
                    for item in rows[:5]:
                        dest = mask_phone_number(item.get("destinationNumber", "N/A"))
                        body = html.escape(str(item.get("messageBody", "")))
                        otp = html.escape(extract_otp_code(body, item.get("otp")))
                        text += f"📱 <b>Num:</b> <code>{dest}</code>\n🔑 <b>OTP:</b> <code>{otp}</code>\n💬 <code>{body[:40]}</code>\n───\n"
                    await message.answer(text, reply_markup=get_main_menu(), parse_mode="HTML")
                    return
                else:
                    await message.answer("ℹ️ No recent traffic found.", reply_markup=get_main_menu())
                    return
            else:
                await message.answer(f"❌ <b>Traffic Error ({resp.status})</b>", reply_markup=get_main_menu(), parse_mode="HTML")
                return
    except Exception as err:
        await message.answer(f"❌ <b>Connection Error:</b> <code>{html.escape(str(err))}</code>", parse_mode="HTML")

@dp.message(F.text == "💰 BALANCE")
async def balance_handler(message: Message):
    user_id = message.from_user.id
    balance = db_get_balance(user_id)
    await message.answer(f"👤 <b>User ID:</b> <code>{user_id}</code>\n💵 <b>Balance:</b> <code>${balance:.4f}</code>", parse_mode="HTML")

# -------------------------------------------------------------
# WITHDRAWAL WORKFLOW
# -------------------------------------------------------------
@dp.message(F.text == "💸 WITHDRAW")
async def withdraw_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    balance = db_get_balance(user_id)

    if balance < MIN_WITHDRAWAL:
        await message.answer(
            f"❌ <b>Insufficient Balance!</b>\n"
            f"Your current balance is <code>${balance:.4f}</code>.\n"
            f"Minimum withdrawal amount is <code>${MIN_WITHDRAWAL}</code>.",
            parse_mode="HTML"
        )
        return

    await state.set_state(WithdrawalState.waiting_for_details)
    await message.answer(
        "🏦 <b>Please enter your Account Payment Details:</b>\n"
        "<i>(e.g., Bank Name, Account Number, Account Name OR USDT Address)</i>",
        parse_mode="HTML"
    )

@dp.message(WithdrawalState.waiting_for_details)
async def withdraw_details_received(message: Message, state: FSMContext):
    await state.update_data(details=message.text.strip())
    await state.set_state(WithdrawalState.waiting_for_amount)
    await message.answer("💵 <b>Enter the amount you wish to withdraw ($):</b>\n<i>Minimum is $0.25</i>", parse_mode="HTML")

@dp.message(WithdrawalState.waiting_for_amount)
async def withdraw_amount_received(message: Message, state: FSMContext):
    user_id = message.from_user.id
    balance = db_get_balance(user_id)

    try:
        amount = float(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid amount format. Enter a number (e.g., 0.50):")
        return

    if amount < MIN_WITHDRAWAL:
        await message.answer(f"❌ Amount below minimum limit of <code>${MIN_WITHDRAWAL}</code>. Try again:", parse_mode="HTML")
        return

    if amount > balance:
        await message.answer(f"❌ Insufficient funds! Your balance is <code>${balance:.4f}</code>. Enter a valid amount:", parse_mode="HTML")
        return

    data = await state.get_data()
    details = data.get("details")
    await state.clear()

    admin_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Accept", callback_data=f"w_acc_{user_id}_{amount}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"w_rej_{user_id}_{amount}")
            ]
        ]
    )

    admin_msg = (
        f"🚨 <b>NEW WITHDRAWAL REQUEST!</b>\n\n"
        f"👤 <b>User ID:</b> <code>{user_id}</code>\n"
        f"💵 <b>Amount:</b> <code>${amount:.4f}</code>\n"
        f"🏦 <b>Details:</b> <code>{html.escape(details)}</code>"
    )

    try:
        await bot.send_message(chat_id=ADMIN_ID, text=admin_msg, reply_markup=admin_keyboard, parse_mode="HTML")
        await message.answer(
            f"✅ <b>Withdrawal Request Submitted!</b>\n\n"
            f"💵 <b>Amount:</b> <code>${amount:.4f}</code>\n"
            f"⏳ Status: Pending Admin Approval.",
            reply_markup=get_main_menu(),
            parse_mode="HTML"
        )
    except Exception as err:
        await message.answer(f"❌ Error submitting request: <code>{err}</code>", parse_mode="HTML")

@dp.callback_query(F.data.startswith("w_"))
async def handle_admin_withdrawal_response(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Unauthorized!", show_alert=True)
        return

    parts = callback.data.split("_")
    action = parts[1]
    target_user_id = int(parts[2])
    amount = float(parts[3])

    if action == "acc":
        current_bal = db_get_balance(target_user_id)
        if current_bal >= amount:
            db_deduct_balance(target_user_id, amount)
            await callback.message.edit_text(callback.message.text + "\n\n✅ <b>Status: APPROVED AND PAID</b>", parse_mode="HTML")
            try:
                await bot.send_message(
                    chat_id=target_user_id,)
 
