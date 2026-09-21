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
# CONFIGURATION (FETCHED FROM ENVIRONMENT VARIABLES / SECRETS)
# =============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "https://otp-telegram-bot-fpmp.onrender.com")

# Safely parse numeric IDs from environment variables
ADMIN_ID_RAW = os.getenv("ADMIN_ID")
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else 0

TELEGRAM_GROUP_ID_RAW = os.getenv("TELEGRAM_GROUP_ID")
TELEGRAM_GROUP_ID = int(TELEGRAM_GROUP_ID_RAW) if TELEGRAM_GROUP_ID_RAW else 0

# Additional Required Groups / Channels
DISCUSSION_GROUP_ID_RAW = os.getenv("DISCUSSION_GROUP_ID")
DISCUSSION_GROUP_ID = int(DISCUSSION_GROUP_ID_RAW) if DISCUSSION_GROUP_ID_RAW else 0

BACKUP_GROUP_ID_RAW = os.getenv("BACKUP_GROUP_ID")
BACKUP_GROUP_ID = int(BACKUP_GROUP_ID_RAW) if BACKUP_GROUP_ID_RAW else 0

# Channel / Group Invite Links
OTP_GROUP_LINK = os.getenv("OTP_GROUP_LINK", "https://t.me/lekotpzone")
DISCUSSION_GROUP_LINK = os.getenv("DISCUSSION_GROUP_LINK", "https://t.me/lekdigitaldiscussiongroup")
BACKUP_GROUP_LINK = os.getenv("BACKUP_GROUP_LINK", "https://t.me/lekdigitalbackupgroup")

THIRDWAVE_API_KEY = os.getenv("THIRDWAVE_API_KEY")
THIRDWAVE_BASE_URL = "https://clients.thirdwave.im/api/v1"

FLAT_OTP_RATE = 0.003

# Validate essential credentials
if not BOT_TOKEN:
    raise ValueError("❌ Missing required environment variable: BOT_TOKEN")
if not THIRDWAVE_API_KEY:
    raise ValueError("❌ Missing required environment variable: THIRDWAVE_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

http_session = None

# =============================================================
# HELPER FUNCTIONS & FORCED JOIN LOGIC
# =============================================================
def mask_phone_number(phone_number: str) -> str:
    """Masks the middle digits of a phone number."""
    clean_num = str(phone_number).strip()
    if len(clean_num) <= 7:
        return clean_num[:2] + "****" + clean_num[-2:]
    return clean_num[:5] + "****" + clean_num[-4:]

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

def get_force_join_keyboard():
    """Generates the join links keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Join Main OTP Group", url=OTP_GROUP_LINK)],
            [InlineKeyboardButton(text="💬 Join Discussion Group", url=DISCUSSION_GROUP_LINK)],
            [InlineKeyboardButton(text="🛡️ Join Backup Channel", url=BACKUP_GROUP_LINK)],
            [InlineKeyboardButton(text="✅ I HAVE JOINED ALL", callback_data="check_membership")]
        ]
    )

async def is_user_member(user_id: int, chat_id: int) -> bool:
    """Checks if a user is a member/admin of a target group."""
    if not chat_id:
        return True  # Skip check if ID is not set
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        print(f"[MEMBERSHIP CHECK ERROR] Chat {chat_id}, User {user_id}: {e}")
        return False

async def check_user_joined(user_id: int) -> bool:
    """Verifies user membership across all mandatory channels/groups."""
    if user_id == ADMIN_ID:
        return True

    joined_otp = await is_user_member(user_id, TELEGRAM_GROUP_ID)
    joined_disc = await is_user_member(user_id, DISCUSSION_GROUP_ID)
    joined_back = await is_user_member(user_id, BACKUP_GROUP_ID)

    return joined_otp and joined_disc and joined_back

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

def db_get_balance(user_id: int) -> float:
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM balances WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0.0

PROCESSED_OTPS = set()

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
# MEMBERSHIP CHECK CALLBACK
# -------------------------------------------------------------
@dp.callback_query(F.data == "check_membership")
async def process_membership_check(callback: CallbackQuery):
    user_id = callback.from_user.id
    if await check_user_joined(user_id):
        await callback.answer("✅ Thank you! You have joined all channels.", show_alert=True)
        try:
            await callback.message.delete()
        except Exception:
            pass
        welcome_text = f"👋 Welcome <b>{callback.from_user.first_name}</b> to <b>Lekdigital Number Zone</b>!"
        await callback.message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode="HTML")
    else:
        await callback.answer("❌ You haven't joined all required groups/channels yet!", show_alert=True)

# -------------------------------------------------------------
# USER HANDLERS (PROTECTED WITH JOIN CHECK)
# -------------------------------------------------------------
@dp.message(F.text == "/start")
async def start_handler(message: Message):
    if not await check_user_joined(message.from_user.id):
        join_msg = (
            f"⚠️ <b>Access Restricted!</b>\n\n"
            f"Hello <b>{message.from_user.first_name}</b>, to use this bot you must first join all our official channels and groups below:"
        )
        await message.answer(join_msg, reply_markup=get_force_join_keyboard(), parse_mode="HTML")
        return

    welcome_text = f"👋 Welcome <b>{message.from_user.first_name}</b> to <b>Lekdigital Number Zone</b>!"
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode="HTML")

@dp.message(F.text == "📱 GET NUMBER")
async def show_services_handler(message: Message):
    if not await check_user_joined(message.from_user.id):
        await message.answer("⚠️ Please join all our official groups/channels first!", reply_markup=get_force_join_keyboard(), parse_mode="HTML")
        return

    active_services = db_get_services()

    if not active_services:
        await message.answer(
            "⚠️ <b>Out of Stock!</b> No service has at least 2 numbers available right now.\n"
            "<i>(Admin needs to add stock using /addnumber Service Country)</i>",
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
    if not await check_user_joined(callback.from_user.id):
        await callback.answer("⚠️ You must join all groups first!", show_alert=True)
        return

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
    if not await check_user_joined(callback.from_user.id):
        await callback.answer("⚠️ You must join all groups first!", show_alert=True)
        return

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
    if not await check_user_joined(message.from_user.id):
        await message.answer("⚠️ Please join all our official groups/channels first!", reply_markup=get_force_join_keyboard(), parse_mode="HTML")
        return

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
                        raw_dest = str(item.get("destinationNumber", "N/A"))
                        masked_dest = html.escape(mask_phone_number(raw_dest))
                        body = html.escape(str(item.get("messageBody", "")))
                        otp = html.escape(extract_otp_code(body, item.get("otp")))
                        text += f"📱 <b>Num:</b> <code>{masked_dest}</code>\n🔑 <b>OTP:</b> <code>{otp}</code>\n💬 <code>{body[:40]}</code>\n───\n"
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
    if not await check_user_joined(message.from_user.id):
        await message.answer("⚠️ Please join all our official groups/channels first!", reply_markup=get_force_join_keyboard(), parse_mode="HTML")
        return

    user_id = message.from_user.id
    balance = db_get_balance(user_id)
    await message.answer(f"👤 <b>User ID:</b> <code>{user_id}</code>\n💵 <b>Balance:</b> <code>${balance:.4f}</code>", parse_mode="HTML")

# -------------------------------------------------------------
# BACKGROUND WORKER
# -------------------------------------------------------------
async def poll_thirdwave_traffic():
    global http_session
    headers = {"Authorization": f"Bearer {THIRDWAVE_API_KEY}"}
    while True:
        try:
            if http_session and not http_session.closed:
                async with http_session.get(f"{THIRDWAVE_BASE_URL}/traffic?pageSize=15", headers=headers) as resp:
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

                            masked_phone = html.escape(mask_phone_number(phone_number))
                            full_phone = html.escape(phone_number)
                            safe_otp = html.escape(otp_code)
                            safe_body = html.escape(body)

                            group_msg = (
                                f"🔥 <b>NEW OTP RECEIVED!</b> 🔥\n\n"
                                f"📱 <b>Number:</b> <code>{masked_phone}</code>\n"
                                f"🔑 <b>OTP Code:</b> <code>{safe_otp}</code>\n"
                              
