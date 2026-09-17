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
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Update

# =============================================================
# CONFIGURATION
# =============================================================
BOT_TOKEN = "8815085413:AAEs9NQaPUQivyspR6Tcrts3Jr9PO6O61f0."                   # Replace with your Telegram Bot Token
RENDER_URL = "https://otp-telegram-bot-fpmp.onrender.com"

ADMIN_ID = 7103520365                            # Your Telegram User ID
TELEGRAM_GROUP_ID = -1004315686306               # Telegram Group ID

THIRDWAVE_API_KEY = "tw_live_5e1666d46397c359b5ba2eda85b40fdf384c2ffd37ebb519290f358ef4260416"        # Replace with your Thirdwave API Key
THIRDWAVE_BASE_URL = "https://clients.thirdwave.im/api/v1"

FLAT_OTP_RATE = 0.003

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

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
            phone_number TEXT UNIQUE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assignments (
            phone_number TEXT PRIMARY KEY,
            user_id INTEGER,
            service TEXT
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

def db_add_stock(service: str, numbers: list):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    added_count = 0
    for num in numbers:
        try:
            cursor.execute("INSERT INTO stock (service, phone_number) VALUES (?, ?)", (service, num))
            added_count += 1
        except sqlite3.IntegrityError:
            pass  # Ignore duplicates
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

def db_assign_two_numbers(service: str, user_id: int):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT phone_number FROM stock WHERE service = ? LIMIT 2", (service,))
    rows = cursor.fetchall()
    
    if len(rows) < 2:
        conn.close()
        return None

    nums = [rows[0][0], rows[1][0]]
    for num in nums:
        cursor.execute("DELETE FROM stock WHERE phone_number = ?", (num,))
        cursor.execute("INSERT OR REPLACE INTO assignments (phone_number, user_id, service) VALUES (?, ?, ?)", (num, user_id, service))
    
    conn.commit()
    conn.close()
    return nums

def db_get_assigned_user(phone_number: str):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, service FROM assignments WHERE phone_number = ?", (phone_number,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"user_id": row[0], "service": row[1]}
    return None

def db_add_balance(user_id: int, amount: float):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO balances (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?", (user_id, amount, amount))
    conn.commit()
    conn.close()

def db_get_balance(user_id: int) -> float:
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM balances WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0.0

PROCESSED_OTPS = set()

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

# -------------------------------------------------------------
# ADMIN HANDLER: ADD STOCK BY SERVICE
# -------------------------------------------------------------
@dp.message(F.text.startswith("/addnumber"))
async def add_number_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ <b>Unauthorized:</b> Admin only command.", parse_mode="HTML")
        return

    text = message.text.strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    first_line_parts = lines[0].split(maxsplit=2)
    
    if len(first_line_parts) < 2:
        await message.answer("⚠️ <b>Usage:</b>\n<code>/addnumber Bolt\n2348052055633\n2348052265099</code>", parse_mode="HTML")
        return

    service_name = first_line_parts[1].capitalize()
    raw_numbers = []

    if len(first_line_parts) > 2:
        raw_numbers.extend(first_line_parts[2].split(","))

    if len(lines) > 1:
        for line in lines[1:]:
            raw_numbers.extend(line.split(","))

    new_numbers = [re.sub(r"\D", "", num) for num in raw_numbers if re.sub(r"\D", "", num)]

    if not new_numbers:
        await message.answer("⚠️ No valid phone numbers found in input.", parse_mode="HTML")
        return

    added_count = db_add_stock(service_name, new_numbers)

    await message.answer(
        f"✅ <b>Added {added_count} number(s) to {service_name}!</b>",
        parse_mode="HTML"
    )

    group_announcement = (
        f"📢 <b>NEW STOCK UPDATE!</b> 📢\n\n"
        f"🔹 <b>Service:</b> {service_name}\n"
        f"📱 <b>New Numbers Added:</b> <code>{added_count}</code>\n"
        f"🚀 <i>Press 'GET NUMBER' in bot to request your numbers!</i>"
    )
    try:
        await bot.send_message(chat_id=TELEGRAM_GROUP_ID, text=group_announcement, parse_mode="HTML")
    except Exception as err:
        print(f"[ERROR] Stock announcement failed: {err}")

# -------------------------------------------------------------
# USER HANDLERS & SERVICE SELECTION
# -------------------------------------------------------------
@dp.message(F.text == "/start")
async def start_handler(message: Message):
    welcome_text = f"👋 Welcome <b>{message.from_user.first_name}</b> to <b>Lekdigital Number Zone</b>!"
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode="HTML")

@dp.message(F.text == "📱 GET NUMBER")
async def show_services_handler(message: Message):
    active_services = db_get_services()

    if not active_services:
        await message.answer("⚠️ <b>Out of Stock!</b> No services have at least 2 numbers available right now.", reply_markup=get_main_menu(), parse_mode="HTML")
        return

    buttons = []
    for srv, count in active_services.items():
        buttons.append([InlineKeyboardButton(text=f"🔹 {srv} ({count} in stock)", callback_data=f"srv:{srv}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("📲 <b>Select a service to get 2 numbers:</b>", reply_markup=keyboard, parse_mode="HTML")

@dp.callback_query(F.data.startswith("srv:"))
async def process_service_selection(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        pass

    try:
        service_name = callback.data.split("srv:", 1)[1]
        user_id = callback.from_user.id

        nums = db_assign_two_numbers(service_name, user_id)

        if not nums:
            await callback.message.answer(f"⚠️ <b>Out of Stock!</b> Not enough numbers available for <b>{html.escape(service_name)}</b>.", parse_mode="HTML")
            return

        response = (
            f"🌐 <b>2 Numbers Assigned Successfully!</b>\n\n"
            f"🔹 <b>Service:</b> {html.escape(service_name)}\n"
            f"📱 <b>Number 1:</b> <code>{nums[0]}</code>\n"
            f"📱 <b>Number 2:</b> <code>{nums[1]}</code>\n\n"
            f"⏳ <b>Waiting for OTPs...</b>\n"
            f"💰 <b>Rate:</b> <code>${FLAT_OTP_RATE}</code> / OTP\n\n"
            f"📢 <i>All incoming OTPs stream directly to our main group!</i>"
        )

        await callback.message.answer(response, parse_mode="HTML")
    except Exception as exc:
        print(f"[CALLBACK EXCEPTION] {exc}")
        await callback.message.answer("❌ An internal error occurred while fetching numbers. Please try again.")

@dp.message(F.text == "🔴 LIVE TRAFFIC")
async def live_traffic_handler(message: Message):
    headers = {"Authorization": f"Bearer {THIRDWAVE_API_KEY}", "Accept": "application/json"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{THIRDWAVE_BASE_URL}/traffic?pageSize=5", headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rows = data.get("rows", [])
                    
                    if rows:
                        text = "🔴 <b>Recent 5 OTP Traffic:</b>\n\n"
                        for item in rows[:5]:
                            dest = html.escape(str(item.get("destinationNumber", "N/A")))
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
# BACKGROUND WORKER: FORWARD ALL OTPS TO TELEGRAM GROUP
# -------------------------------------------------------------
async def poll_thirdwave_traffic():
    headers = {"Authorization": f"Bearer {THIRDWAVE_API_KEY}"}
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{THIRDWAVE_BASE_URL}/traffic?pageSize=15", headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        rows = data.get("rows", [])
                        
                        for item in rows:
                            msg_id = item.get("id")
                            if msg_id in PROCESSED_OTPS:
                                continue

                            phone_number = str(item.get("destinationNumber", "")).strip()
                            body = str(item.get("messageBody", ""))
                            otp_code = extract_otp_code(body, item.get("otp"))

                            PROCESSED_OTPS.add(msg_id)

                            safe_phone = html.escape(phone_number)
                            safe_otp = html.escape(otp_code)
                            safe_body = html.escape(body)

                            # 1. Forward to Telegram Group
                            group_msg = (
                                f"🔥 <b>NEW OTP RECEIVED!</b> 🔥\n\n"
                                f"📱 <b>Number:</b> <code>{safe_phone}</code>\n"
                                f"🔑 <b>OTP Code:</b> <code>{safe_otp}</code>\n"
                                f"💬 <b>Message:</b> <code>{safe_body}</code>"
                            )
                            try:
                                await bot.send_message(chat_id=TELEGRAM_GROUP_ID, text=group_msg, parse_mode="HTML")
                                print(f"[SUCCESS] OTP dropped in Group {TELEGRAM_GROUP_ID}")
                            except Exception as group_err:
                                print(f"[ERROR] Group Forwarding Failed: {group_err}")

                            # 2. Direct message to assigned user
                            user_info = db_get_assigned_user(phone_number)
                            if user_info:
                                u_id = user_info["user_id"]
                                srv = user_info["service"]
                                db_add_balance(u_id, FLAT_OTP_RATE)
                                try:
                                    await bot.send_message(
                                        chat_id=u_id,
                                        text=f"🌐 <b>Your OTP Received ({srv})!</b>\n📱 <code>{safe_phone}</code>\n🔑 Code: <code>{safe_otp}</code>",
                                        parse_mode="HTML"
                                    )
                                except Exception:
                                    pass
        except Exception as e:
            print(f"[WORKER ERROR] {e}")
        await asyncio.sleep(5)

# -------------------------------------------------------------
# FASTAPI WEB SERVER SETUP
# -------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    webhook_url = f"{RENDER_URL}/telegram-webhook"
    await bot.set_webhook(webhook_url, drop_pending_updates=True)
    polling_task = asyncio.create_task(poll_thirdwave_traffic())
    yield
    polling_task.cancel()
    await bot.delete_webhook()

app = FastAPI(lifespan=lifespan)

@app.post("/")
@app.post("/telegram-webhook")
async def process_telegram_update(request: Request):
    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"status": "ok"}

@app.get("/")
async def health_check():
    return {"status": "bot is running"}
