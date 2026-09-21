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
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "https://otp-telegram-bot-fpmp.onrender.com")

ADMIN_ID_RAW = os.getenv("ADMIN_ID")
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else 0

TELEGRAM_GROUP_ID_RAW = os.getenv("TELEGRAM_GROUP_ID")
TELEGRAM_GROUP_ID = int(TELEGRAM_GROUP_ID_RAW) if TELEGRAM_GROUP_ID_RAW else 0

DISCUSSION_GROUP_ID_RAW = os.getenv("DISCUSSION_GROUP_ID")
DISCUSSION_GROUP_ID = int(DISCUSSION_GROUP_ID_RAW) if DISCUSSION_GROUP_ID_RAW else 0

BACKUP_GROUP_ID_RAW = os.getenv("BACKUP_GROUP_ID")
BACKUP_GROUP_ID = int(BACKUP_GROUP_ID_RAW) if BACKUP_GROUP_ID_RAW else 0

OTP_GROUP_LINK = os.getenv("OTP_GROUP_LINK", "https://t.me/lekotpzone")
DISCUSSION_GROUP_LINK = os.getenv("DISCUSSION_GROUP_LINK", "https://t.me/lekdigitaldiscussiongroup")
BACKUP_GROUP_LINK = os.getenv("BACKUP_GROUP_LINK", "https://t.me/lekdigitalbackupgroup")

THIRDWAVE_API_KEY = os.getenv("THIRDWAVE_API_KEY")
THIRDWAVE_BASE_URL = "https://clients.thirdwave.im/api/v1"

FLAT_OTP_RATE = 0.003
MIN_WITHDRAWAL = 0.25
REFERRAL_BONUS = 0.05  # Bonus reward paid to referrer after referred user gets 3 OTPs

if not BOT_TOKEN:
    raise ValueError("❌ Missing required environment variable: BOT_TOKEN")
if not THIRDWAVE_API_KEY:
    raise ValueError("❌ Missing required environment variable: THIRDWAVE_API_KEY")

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
# HELPER FUNCTIONS
# =============================================================
def mask_phone_number(phone_number: str) -> str:
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Join Main OTP Group", url=OTP_GROUP_LINK)],
            [InlineKeyboardButton(text="💬 Join Discussion Group", url=DISCUSSION_GROUP_LINK)],
            [InlineKeyboardButton(text="🛡️ Join Backup Channel", url=BACKUP_GROUP_LINK)],
            [InlineKeyboardButton(text="✅ I HAVE JOINED ALL", callback_data="check_membership")]
        ]
    )

async def is_user_member(user_id: int, chat_id: int) -> bool:
    if not chat_id:
        return True
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        print(f"[MEMBERSHIP CHECK ERROR] Chat {chat_id}, User {user_id}: {e}")
        return False

async def check_user_joined(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True

    joined_otp = await is_user_member(user_id, TELEGRAM_GROUP_ID)
    joined_disc = await is_user_member(user_id, DISCUSSION_GROUP_ID)
    joined_back = await is_user_member(user_id, BACKUP_GROUP_ID)

    return joined_otp and joined_disc and joined_back

# =============================================================
# DATABASE SETUP
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            user_id INTEGER PRIMARY KEY,
            referred_by INTEGER,
            otp_count INTEGER DEFAULT 0,
            rewarded INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS otp_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
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

def db_deduct_balance(user_id: int, amount: float) -> bool:
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM balances WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row or row[0] < amount:
        conn.close()
        return False
    cursor.execute("UPDATE balances SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()
    return True

def db_get_balance(user_id: int) -> float:
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM balances WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0.0

def db_register_referral(user_id: int, referrer_id: int):
    if user_id == referrer_id:
        return
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO referrals (user_id, referred_by, otp_count, rewarded) VALUES (?, ?, 0, 0)",
            (user_id, referrer_id)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

def db_record_otp_and_check_referral(user_id: int):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    
    # Record weekly log
    cursor.execute("INSERT INTO otp_logs (user_id) VALUES (?)", (user_id,))
    
    # Update referral progress
    cursor.execute("SELECT referred_by, otp_count, rewarded FROM referrals WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    reward_referrer_id = None
    if row and row[2] == 0:  # Not yet rewarded
        referrer_id, current_count, _ = row
        new_count = current_count + 1
        if new_count >= 3:
            cursor.execute("UPDATE referrals SET otp_count = ?, rewarded = 1 WHERE user_id = ?", (new_count, user_id))
            reward_referrer_id = referrer_id
        else:
            cursor.execute("UPDATE referrals SET otp_count = ? WHERE user_id = ?", (new_count, user_id))
            
    conn.commit()
    conn.close()

    if reward_referrer_id:
        db_add_balance(reward_referrer_id, REFERRAL_BONUS)
        return reward_referrer_id
    return None

def db_get_referral_stats(user_id: int):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), SUM(rewarded) FROM referrals WHERE referred_by = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    total_ref = row[0] if row and row[0] else 0
    rewarded_ref = row[1] if row and row[1] else 0
    earned = rewarded_ref * REFERRAL_BONUS
    return total_ref, earned

def db_get_weekly_stats(user_id: int):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    
    # User count for the last 7 days
    cursor.execute(
        "SELECT COUNT(*) FROM otp_logs WHERE user_id = ? AND timestamp >= datetime('now', '-7 days')",
        (user_id,)
    )
    user_weekly_count = cursor.fetchone()[0]

    # Top 3 Leaderboard over the last 7 days
    cursor.execute("""
        SELECT user_id, COUNT(*) as cnt 
        FROM otp_logs 
        WHERE timestamp >= datetime('now', '-7 days') 
        GROUP BY user_id 
        ORDER BY cnt DESC 
        LIMIT 3
    """)
    top_3 = cursor.fetchall()
    conn.close()
    
    return user_weekly_count, top_3

PROCESSED_OTPS = set()

# =============================================================
# ADMIN HANDLERS
# =============================================================
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

# =============================================================
# CALLBACK HANDLERS
# =============================================================
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

@dp.callback_query(F.data.startswith("wd_accept_"))
async def process_withdraw_accept(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Unauthorized action.", show_alert=True)
        return

    parts = callback.data.split("_")
    user_id = int(parts[2])
    amount = float(parts[3])

    if db_deduct_balance(user_id, amount):
        await callback.message.edit_text(
            f"✅ <b>WITHDRAWAL APPROVED & PAID!</b>\n\n"
            f"👤 <b>User ID:</b> <code>{user_id}</code>\n"
            f"💵 <b>Amount Deducted:</b> <code>${amount:.2f}</code>",
            parse_mode="HTML"
        )
        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"🎉 <b>Withdrawal Approved!</b>\n\nYour withdrawal of <b>${amount:.2f}</b> has been processed successfully.",
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        await callback.message.edit_text("❌ <b>Failed:</b> User has insufficient balance.", parse_mode="HTML")

@dp.callback_query(F.data.startswith("wd_reject_"))
async def process_withdraw_reject(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Unauthorized action.", show_alert=True)
        return

    parts = callback.data.split("_")
    user_id = int(parts[2])
    amount = float(parts[3])

    await callback.message.edit_text(
        f"❌ <b>WITHDRAWAL REJECTED!</b>\n\n"
        f"👤 <b>User ID:</b> <code>{user_id}</code>\n"
        f"💵 <b>Amount:</b> <code>${amount:.2f}</code>",
        parse_mode="HTML"
    )
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"❌ <b>Withdrawal Rejected!</b>\n\nYour request for <b>${amount:.2f}</b> was rejected by admin. Your balance remains unchanged.",
            parse_mode="HTML"
        )
    except Exception:
        pass

# =============================================================
# USER COMMAND HANDLERS
# =============================================================
@dp.message(F.text.startswith("/start"))
async def start_handler(message: Message):
    user_id = message.from_user.id
    text_parts = message.text.split()
    
    # Handle Referral Link Parsing
    if len(text_parts) > 1 and text_parts[1].isdigit():
        referrer_id = int(text_parts[1])
        db_register_referral(user_id, referrer_id)

    if not await check_user_joined(user_id):
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

# --- FEATURE 1: REFERRAL SYSTEM ---
@dp.message(F.text == "♾️ REFER AND EARN")
async def referral_handler(message: Message):
    if not await check_user_joined(message.from_user.id):
        await message.answer("⚠️ Please join all our official groups/channels first!", reply_markup=get_force_join_keyboard(), parse_mode="HTML")
        return

    user_id = message.from_user.id
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    
    total_ref, earned = db_get_referral_stats(user_id)

    msg = (
        f"♾️ <b>REFERRAL PROGRAM</b> ♾️\n\n"
        f"Share your link and earn rewards for every user who joins and gets at least 3 OTPs!\n\n"
        f"🔗 <b>Your Referral Link:</b>\n<code>{ref_link}</code>\n\n"
        f"👥 <b>Total Invited:</b> <code>{total_ref}</code> users\n"
        f"💵 <b>Earned Bonus:</b> <code>${earned:.2f}</code>\n\n"
        f"<i>Note: Referred user must successfully request at least 3 OTPs to qualify for reward!</i>"
    )
    await message.answer(msg, parse_mode="HTML")

# --- FEATURE 2: WITHDRAWAL SYSTEM WITH FSM ---
@dp.message(F.text == "💸 WITHDRAW")
async def withdraw_start(message: Message, state: FSMContext):
    if not await check_user_joined(message.from_user.id):
        await message.answer("⚠️ Please join all our official groups/channels first!", reply_markup=get_force_join_keyboard(), parse_mode="HTML")
        return

    user_id = message.from_user.id
    balance = db_get_balance(user_id)

    if balance < MIN_WITHDRAWAL:
        await message.answer(
            f"❌ <b>Insufficient Balance!</b>\n\n"
            f"💵 <b>Your Balance:</b> <code>${balance:.2f}</code>\n"
            f"⚠️ <b>Minimum Withdrawal:</b> <code>${MIN_WITHDRAWAL:.2f}</code>",
            parse_mode="HTML"
        )
        return

    await state.set_state(WithdrawalState.waiting_for_details)
    await message.answer(
        f"💵 <b>Your Balance:</b> <code>${balance:.2f}</code>\n\n"
        f"Please send your **Account Details** / **Payment Method** (e.g. Bank Name, Account Number, Name, or Crypto Address):",
        parse_mode="HTML"
    )

@dp.message(WithdrawalState.waiting_for_details)
async def withdraw_details_received(message: Message, state: FSMContext):
    await state.update_data(details=message.text.strip())
    await state.set_state(WithdrawalState.waiting_for_amount)
    await message.answer(f"Enter the **Amount ($)** you wish to withdraw (Minimum: <code>${MIN_WITHDRAWAL:.2f}</code>):", parse_mode="HTML")

@dp.message(WithdrawalState.waiting_for_amount)
async def withdraw_amount_received(message: Message, state: FSMContext):
    user_id = message.from_user.id
    balance = db_get_balance(user_id)

    try:
        amount = float(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Invalid amount. Please enter a valid number (e.g. 0.50):")
        return

    if amount < MIN_WITHDRAWAL:
        await message.answer(f"⚠️ Minimum withdrawal amount is <code>${MIN_WITHDRAWAL:.2f}</code>. Try again:", parse_mode="HTML")
        return

    if amount > balance:
        await message.answer(f"⚠️ Insufficient funds! Maximum available: <code>${balance:.2f}</code>. Try again:", parse_mode="HTML")
        return

    user_data = await state.get_data()
    details = user_data.get("details")
    await state.clear()

    await message.answer("✅ <b>Withdrawal Request Submitted!</b>\nYour request has been sent to Admin for review.", parse_mode="HTML")

    # Send Request to Admin
    admin_btn = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Accept", callback_data=f"wd_accept_{user_id}_{amount}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"wd_reject_{user_id}_{amount}")
            ]
        ]
    )

    admin_msg = (
        f"🔔 <b>NEW WITHDRAWAL REQUEST!</b>\n\n"
        f"👤 <b>User:</b> {message.from_user.first_name} (<code>{user_id}</code>)\n"
        f"💵 <b>Requested Amount:</b> <code>${amount:.2f}</code>\n"
        f"🏦 <b>Payment Details:</b>\n<code>{details}</code>"
    )
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=admin_msg, reply_markup=admin_btn, parse_mode="HTML")
    except Exception as err:
        print(f"[ERROR] Failed notifying admin: {err}")

# --- FEATURE 3: STATUS & WEEKLY LEADERBOARD ---
@dp.message(F.text == "📊 STATUS")
async def status_handler(message: Message):
    if not await check_user_joined(message.from_user.id):
        await message.answer("⚠️ Please join all our official groups/channels first!", reply_markup=get_force_join_keyboard(), parse_mode="HTML")
        return

    user_id = message.from_user.id
    user_count, top_3 = db_get_weekly_stats(user_id)

    medals = ["🥇 1st Place", "🥈 2nd Place", "🥉 3rd Place"]
    leaderboard_text = ""
    
    if top_3:
        for idx, (uid, cnt) in enumerate(top_3):
            leaderboard_text += f"{medals[idx]}: User <code>{uid}</code> — <b>{cnt} OTPs</b>\n"
    else:
        leaderboard_text = "<i>No traffic recorded this week yet.</i>\n"

    status_msg = (
        f"📊 <b>WEEKLY STATUS & LEADERBOARD</b> 📊\n\n"
        f"📱 <b>Your OTPs (Last 7 Days):</b> <code>{user_count}</code> OTPs\n\n"
        f"🏆 <b>Top Weekly Performers (Giveaway Rank):</b>\n"
        f"{leaderboard_text}\n"
        f"🎁 <i>Top 3 users get special weekly rewards! Keep completing OTPs to rank higher!</i>"
    )
    await message.answer(status_msg, parse_mode="HTML")

# =============================================================
# BACKGROUND WORKER
# =============================================================
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
                                f"💬 <b>Message:</b> <code>{safe_body}</code>"
                            )
                            try:
                                await bot.send_message(chat_id=TELEGRAM_GROUP_ID, text=group_msg, parse_mode="HTML")
                            except Exception as group_err:
                                print(f"[ERROR] Group Forwarding Failed: {group_err}")

                            user_info = db_get_assigned_user(phone_number)
                            if user_info:
                                u_id = user_info["user_id"]
                                srv = user_info["service"]
                                cntry = user_info["country"]
                                db_add_balance(u_id, FLAT_OTP_RATE)
                                
                                # Track OTP & process referral check
                                rewarded_referrer = db_record_otp_and_check_referral(u_id)
                                if rewarded_referrer:
                                    try:
                                        await bot.send_message(
                                            chat_id=rewarded_referrer,
                                            text=f"🎉 <b>Referral Bonus Credited!</b>\nYour referred user has completed 3 OTPs. You earned <b>${REFERRAL_BONUS:.2f}</b>!",
                                            parse_mode="HTML"
                                        )
                                    except Exception:
                                        pass

                                try:
                                    await bot.send_message(
                                        chat_id=u_id,
                                        text=f"🌐 <b>Your OTP Received ({srv} - {cntry})!</b>\n📱 <code>{full_phone}</code>\n🔑 Code: <code>{safe_otp}</code>",
                                        parse_mode="HTML"
                                    )
                                except Exception:
                                    pass
        except Exception as e:
            print(f"[WORKER ERROR] {e}")
        await asyncio.sleep(5)

# =============================================================
# FASTAPI LIFESPAN SETUP
# =============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_session
    http_session = aiohttp.ClientSession()
    
    webhook_url = f"{RENDER_URL}/telegram-webhook"
    await bot.set_webhook(webhook_url, drop_pending_updates=True)
    
    polling_task = asyncio.create_task(poll_thirdwave_traffic())
    
    yield
    
    polling_task.cancel()
    if http_session and not http_session.closed:
        await http_session.close()
    await bot.delete_webhook()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.on_event("startup")
async def force_webhook_on_startup():
    webhook_url = f"{RENDER_URL}/telegram-webhook"
    await bot.set_webhook(webhook_url, drop_pending_updates=True)

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
