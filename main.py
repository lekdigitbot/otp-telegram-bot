import os, re, html, sqlite3, asyncio, aiohttp
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Update

# =============================================================
# CONFIGURATION
# =============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_URL", "https://otp-telegram-bot-fpmp.onrender.com")

ADMIN_ID = int(os.getenv("ADMIN_ID", 0) or 0)
TELEGRAM_GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID", 0) or 0)
DISCUSSION_GROUP_ID = int(os.getenv("DISCUSSION_GROUP_ID", 0) or 0)
BACKUP_GROUP_ID = int(os.getenv("BACKUP_GROUP_ID", 0) or 0)

OTP_GROUP_LINK = os.getenv("OTP_GROUP_LINK", "https://t.me/lekotpzone")
DISCUSSION_GROUP_LINK = os.getenv("DISCUSSION_GROUP_LINK", "https://t.me/lekdigitaldiscussiongroup")
BACKUP_GROUP_LINK = os.getenv("BACKUP_GROUP_LINK", "https://t.me/lekdigitalbackupgroup")
SUPPORT_LINK = os.getenv("SUPPORT_LINK", "https://t.me/lekdigitalsupport")

THIRDWAVE_API_KEY = os.getenv("THIRDWAVE_API_KEY")
THIRDWAVE_BASE_URL = "https://clients.thirdwave.im/api/v1"

DEFAULT_OTP_RATE, MIN_WITHDRAWAL, REFERRAL_BONUS = 0.003, 0.25, 0.05

if not BOT_TOKEN or not THIRDWAVE_API_KEY:
    raise ValueError("❌ Missing required BOT_TOKEN or THIRDWAVE_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
http_session, PROCESSED_OTPS, DB_FILE = None, set(), "/tmp/bot_data.db"

class WithdrawalState(StatesGroup):
    waiting_for_details, waiting_for_amount = State(), State()

# =============================================================
# HELPER FUNCTIONS & KEYBOARDS
# =============================================================
def mask_phone_number(phone_number: str) -> str:
    clean = re.sub(r"\D", "", str(phone_number).strip())
    return clean[:2] + "****" + clean[-2:] if len(clean) <= 7 else clean[:5] + "****" + clean[-4:]

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
        ], resize_keyboard=True
    )

def get_force_join_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Join Main OTP Group", url=OTP_GROUP_LINK)],
        [InlineKeyboardButton(text="💬 Join Discussion Group", url=DISCUSSION_GROUP_LINK)],
        [InlineKeyboardButton(text="🛡️ Join Backup Channel", url=BACKUP_GROUP_LINK)],
        [InlineKeyboardButton(text="✅ I HAVE JOINED ALL", callback_data="check_membership")]
    ])

async def is_user_member(user_id: int, chat_id: int) -> bool:
    if not chat_id: return True
    try:
        m = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return m.status in ["member", "administrator", "creator"]
    except Exception as e:
        print(f"[MEMBERSHIP ERROR] Chat {chat_id}, User {user_id}: {e}")
        return False

async def check_user_joined(user_id: int) -> bool:
    if user_id == ADMIN_ID: return True
    return (await is_user_member(user_id, TELEGRAM_GROUP_ID)) and \
           (await is_user_member(user_id, DISCUSSION_GROUP_ID)) and \
           (await is_user_member(user_id, BACKUP_GROUP_ID))

# =============================================================
# DATABASE LAYER
# =============================================================
def db_query(query: str, params: tuple = (), fetch: str = None, commit: bool = False):
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        if commit: conn.commit()
        if fetch == "one": return cursor.fetchone()
        if fetch == "all": return cursor.fetchall()
        if fetch == "rowcount": return cursor.rowcount

def init_db():
    queries = [
        "CREATE TABLE IF NOT EXISTS stock (id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT, country TEXT, phone_number TEXT UNIQUE, rate REAL DEFAULT 0.003)",
        "CREATE TABLE IF NOT EXISTS assignments (phone_number TEXT PRIMARY KEY, user_id INTEGER, service TEXT, country TEXT, rate REAL DEFAULT 0.003)",
        "CREATE TABLE IF NOT EXISTS balances (user_id INTEGER PRIMARY KEY, balance REAL)",
        "CREATE TABLE IF NOT EXISTS referrals (user_id INTEGER PRIMARY KEY, referred_by INTEGER, otp_count INTEGER DEFAULT 0, rewarded INTEGER DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS otp_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"
    ]
    for q in queries: db_query(q, commit=True)

init_db()

def db_add_stock(service: str, country: str, rate: float, numbers: list) -> int:
    added = 0
    for num in numbers:
        try:
            db_query("INSERT INTO stock (service, country, rate, phone_number) VALUES (?, ?, ?, ?)", (service, country, rate, num), commit=True)
            added += 1
        except sqlite3.IntegrityError: pass
    return added

def db_delete_number(phone_number: str) -> bool:
    s = db_query("DELETE FROM stock WHERE phone_number = ?", (phone_number,), fetch="rowcount", commit=True)
    a = db_query("DELETE FROM assignments WHERE phone_number = ?", (phone_number,), fetch="rowcount", commit=True)
    return (s + a) > 0

def db_clear_service(service: str) -> int:
    s = db_query("DELETE FROM stock WHERE service = ?", (service,), fetch="rowcount", commit=True)
    a = db_query("DELETE FROM assignments WHERE service = ?", (service,), fetch="rowcount", commit=True)
    return s + a

def db_get_services():
    rows = db_query("SELECT service, COUNT(*) FROM stock GROUP BY service HAVING COUNT(*) >= 2", fetch="all") or []
    return {row[0]: row[1] for row in rows}

def db_get_countries_for_service(service: str):
    rows = db_query("SELECT country, rate, COUNT(*) FROM stock WHERE service = ? GROUP BY country, rate HAVING COUNT(*) >= 2", (service,), fetch="all") or []
    return {row[0]: (row[2], row[1]) for row in rows}

def db_assign_two_numbers(service: str, country: str, user_id: int):
    rows = db_query("SELECT phone_number, rate FROM stock WHERE service = ? AND country = ? LIMIT 2", (service, country), fetch="all") or []
    if len(rows) < 2: return None, 0.0
    nums, rate = [rows[0][0], rows[1][0]], rows[0][1]
    for num in nums:
        db_query("DELETE FROM stock WHERE phone_number = ?", (num,), commit=True)
        db_query("INSERT OR REPLACE INTO assignments (phone_number, user_id, service, country, rate) VALUES (?, ?, ?, ?, ?)", (num, user_id, service, country, rate), commit=True)
    return nums, rate

def db_get_assigned_user(phone_number: str):
    row = db_query("SELECT user_id, service, country, rate FROM assignments WHERE phone_number = ?", (re.sub(r"\D", "", str(phone_number).strip()),), fetch="one")
    return {"user_id": row[0], "service": row[1], "country": row[2], "rate": row[3]} if row else None

def db_add_balance(user_id: int, amount: float):
    db_query("INSERT INTO balances (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?", (user_id, amount, amount), commit=True)

def db_deduct_balance(user_id: int, amount: float) -> bool:
    row = db_query("SELECT balance FROM balances WHERE user_id = ?", (user_id,), fetch="one")
    if not row or row[0] < amount: return False
    db_query("UPDATE balances SET balance = balance - ? WHERE user_id = ?", (amount, user_id), commit=True)
    return True

def db_get_balance(user_id: int) -> float:
    row = db_query("SELECT balance FROM balances WHERE user_id = ?", (user_id,), fetch="one")
    return row[0] if row else 0.0

def db_register_referral(user_id: int, referrer_id: int):
    if user_id != referrer_id:
        try: db_query("INSERT INTO referrals (user_id, referred_by, otp_count, rewarded) VALUES (?, ?, 0, 0)", (user_id, referrer_id), commit=True)
        except sqlite3.IntegrityError: pass

def db_record_otp_and_check_referral(user_id: int):
    db_query("INSERT INTO otp_logs (user_id) VALUES (?)", (user_id,), commit=True)
    row = db_query("SELECT referred_by, otp_count, rewarded FROM referrals WHERE user_id = ?", (user_id,), fetch="one")
    reward_referrer_id = None
    if row and row[2] == 0:
        ref_id, count, _ = row
        new_count = count + 1
        if new_count >= 3:
            db_query("UPDATE referrals SET otp_count = ?, rewarded = 1 WHERE user_id = ?", (new_count, user_id), commit=True)
            reward_referrer_id = ref_id
        else:
            db_query("UPDATE referrals SET otp_count = ? WHERE user_id = ?", (new_count, user_id), commit=True)
    if reward_referrer_id:
        db_add_balance(reward_referrer_id, REFERRAL_BONUS)
        return reward_referrer_id
    return None

def db_get_referral_stats(user_id: int):
    row = db_query("SELECT COUNT(*), SUM(rewarded) FROM referrals WHERE referred_by = ?", (user_id,), fetch="one")
    tot, rwd = (row[0] if row and row[0] else 0), (row[1] if row and row[1] else 0)
    return tot, rwd * REFERRAL_BONUS

def db_get_weekly_stats(user_id: int):
    user_weekly = db_query("SELECT COUNT(*) FROM otp_logs WHERE user_id = ? AND timestamp >= datetime('now', '-7 days')", (user_id,), fetch="one")[0]
    daily = db_query("SELECT date(timestamp), COUNT(*) FROM otp_logs WHERE user_id = ? AND timestamp >= datetime('now', '-7 days') GROUP BY date(timestamp) ORDER BY date(timestamp) DESC", (user_id,), fetch="all")
    top_3 = db_query("SELECT user_id, COUNT(*) as cnt FROM otp_logs WHERE timestamp >= datetime('now', '-7 days') GROUP BY user_id ORDER BY cnt DESC LIMIT 3", fetch="all")
    return user_weekly, daily, top_3

# =============================================================
# ADMIN HANDLERS
# =============================================================
@dp.message(F.text.startswith("/addnumber"))
async def add_number_admin(message: Message):
    if message.from_user.id != ADMIN_ID: return await message.answer("❌ <b>Unauthorized:</b> Admin only.", parse_mode="HTML")
    lines = [l.strip() for l in message.text.strip().split("\n") if l.strip()]
    if not lines: return
    parts = lines[0].split(maxsplit=3)
    if len(parts) < 3:
        return await message.answer("⚠️ <b>Usage Format:</b>\n<code>/addnumber Whatsapp Nigeria 0.005\n2348052055633\n2348052265099</code>", parse_mode="HTML")
    
    srv, cntry = parts[1].capitalize(), parts[2].capitalize()
    try: rate = float(re.sub(r"[^\d.]", "", parts[3])) if len(parts) >= 4 else DEFAULT_OTP_RATE
    except ValueError: rate = DEFAULT_OTP_RATE

    raw_nums = []
    for l in lines[1:]: raw_nums.extend(l.split(","))
    new_nums = [re.sub(r"\D", "", n) for n in raw_nums if re.sub(r"\D", "", n)]
    if not new_nums: return await message.answer("⚠️ No valid phone numbers found.", parse_mode="HTML")

    added = db_add_stock(srv, cntry, rate, new_nums)
    await message.answer(f"✅ <b>Added {added} number(s) to {srv} ({cntry}) at rate ${rate:.4f}!</b>", parse_mode="HTML")

    try:
        await bot.send_message(TELEGRAM_GROUP_ID, f"📢 <b>NEW STOCK UPDATE!</b>\n\n🔹 <b>Service:</b> {srv}\n🌍 <b>Country:</b> {cntry}\n💵 <b>Rate:</b> <code>${rate:.4f}</code> / OTP\n📱 <b>New Numbers Added:</b> <code>{added}</code>\n🚀 <i>Press 'GET NUMBER' in bot to request!</i>", parse_mode="HTML")
    except Exception as e: print(f"[ERROR] Stock announcement failed: {e}")

@dp.message(F.text.startswith("/delnumber"))
async def del_number_admin(message: Message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) < 2: return await message.answer("⚠️ Usage: <code>/delnumber 2348052055633</code>", parse_mode="HTML")
    phone = re.sub(r"\D", "", parts[1])
    res = "removed from bot stock & assignments." if db_delete_number(phone) else "not found."
    await message.answer(f"ℹ️ Number <code>{phone}</code> {res}", parse_mode="HTML")

@dp.message(F.text.startswith("/clearservice"))
async def clear_service_admin(message: Message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return await message.answer("⚠️ Usage: <code>/clearservice Whatsapp</code>", parse_mode="HTML")
    srv = parts[1].capitalize()
    await message.answer(f"✅ Removed service <b>{srv}</b> ({db_clear_service(srv)} total entries purged).", parse_mode="HTML")

# =============================================================
# CALLBACK HANDLERS
# =============================================================
@dp.callback_query(F.data == "check_membership")
async def process_membership_check(callback: CallbackQuery):
    if await check_user_joined(callback.from_user.id):
        await callback.answer("✅ Thank you! You have joined all channels.", show_alert=True)
        try: await callback.message.delete()
        except Exception: pass
        await callback.message.answer(f"👋 Welcome <b>{callback.from_user.first_name}</b> to <b>Lekdigital Number Zone</b>!", reply_markup=get_main_menu(), parse_mode="HTML")
    else:
        await callback.answer("❌ You haven't joined all required groups/channels yet!", show_alert=True)

@dp.callback_query(F.data.startswith("wd_accept_"))
async def process_withdraw_accept(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return await callback.answer("❌ Unauthorized action.", show_alert=True)
    _, _, u_id, amt = callback.data.split("_")
    u_id, amt = int(u_id), float(amt)

    if db_deduct_balance(u_id, amt):
        await callback.message.edit_text(f"✅ <b>WITHDRAWAL APPROVED & PAID!</b>\n\n👤 <b>User ID:</b> <code>{u_id}</code>\n💵 <b>Amount Deducted:</b> <code>${amt:.2f}</code>", parse_mode="HTML")
        try: await bot.send_message(u_id, f"🎉 <b>Withdrawal Approved!</b>\n\nYour withdrawal of <b>${amt:.2f}</b> has been processed successfully.", parse_mode="HTML")
        except Exception: pass
    else:
        await callback.message.edit_text("❌ <b>Failed:</b> User has insufficient balance.", parse_mode="HTML")

@dp.callback_query(F.data.startswith("wd_reject_"))
async def process_withdraw_reject(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return await callback.answer("❌ Unauthorized action.", show_alert=True)
    _, _, u_id, amt = callback.data.split("_")
    u_id, amt = int(u_id), float(amt)
    await callback.message.edit_text(f"❌ <b>WITHDRAWAL REJECTED!</b>\n\n👤 <b>User ID:</b> <code>{u_id}</code>\n💵 <b>Amount:</b> <code>${amt:.2f}</code>", parse_mode="HTML")
    try: await bot.send_message(u_id, f"❌ <b>Withdrawal Rejected!</b>\n\nYour request for <b>${amt:.2f}</b> was rejected by admin.", parse_mode="HTML")
    except Exception: pass

# =============================================================
# USER HANDLERS
# =============================================================
@dp.message(F.text.startswith("/start"))
async def start_handler(message: Message):
    parts = message.text.split()
    if len(parts) > 1 and parts[1].isdigit(): db_register_referral(message.from_user.id, int(parts[1]))
    if not await check_user_joined(message.from_user.id):
        return await message.answer(f"⚠️ <b>Access Restricted!</b>\n\nHello <b>{message.from_user.first_name}</b>, please join all our channels to use the bot:", reply_markup=get_force_join_keyboard(), parse_mode="HTML")
    await message.answer(f"👋 Welcome <b>{message.from_user.first_name}</b> to <b>Lekdigital Number Zone</b>!", reply_markup=get_main_menu(), parse_mode="HTML")

@dp.message(F.text == "💀 SUPPORT")
async def support_handler(message: Message):
    await message.answer("🛠️ <b>Need help?</b>\nClick below to reach support:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Contact Support", url=SUPPORT_LINK)]]), parse_mode="HTML")

@dp.message(F.text == "📱 GET NUMBER")
async def show_services_handler(message: Message):
    if not await check_user_joined(message.from_user.id): return await message.answer("⚠️ Please join required channels first!", reply_markup=get_force_join_keyboard(), parse_mode="HTML")
    services = db_get_services()
    if not services: return await message.answer("⚠️ <b>Out of Stock!</b> No service available right now.", reply_markup=get_main_menu(), parse_mode="HTML")
    btn = [[InlineKeyboardButton(text=f"🔹 {s} ({c} total)", callback_data=f"s_{s}")] for s, c in services.items()]
    await message.answer("📲 <b>Select a service:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btn), parse_mode="HTML")

@dp.callback_query(F.data.startswith("s_"))
async def process_service_selection(callback: CallbackQuery):
    if not await check_user_joined(callback.from_user.id): return await callback.answer("⚠️ Join all groups first!", show_alert=True)
    try: await callback.answer()
    except Exception: pass
    srv = callback.data[2:]
    countries = db_get_countries_for_service(srv)
    if not countries: return await callback.message.answer(f"⚠️ Out of stock for <b>{html.escape(srv)}</b>.", parse_mode="HTML")
    btn = [[InlineKeyboardButton(text=f"🌍 {cnt} (${r:.4f}/OTP) - {c} avail", callback_data=f"c_{srv}_{cnt}")] for cnt, (c, r) in countries.items()]
    await callback.message.answer(f"🌍 <b>Select Country for {html.escape(srv)}:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btn), parse_mode="HTML")

@dp.callback_query(F.data.startswith("c_"))
async def process_number_assignment(callback: CallbackQuery):
    if not await check_user_joined(callback.from_user.id): return await callback.answer("⚠️ Join all groups first!", show_alert=True)
    try: await callback.answer("Assigning numbers...")
    except Exception: pass
    parts = callback.data.split("_", 2)
    if len(parts) < 3: return
    srv, cntry, u_id = parts[1], parts[2], callback.from_user.id
    nums, rate = db_assign_two_numbers(srv, cntry, u_id)
    if not nums: return await callback.message.answer(f"⚠️ <b>Out of Stock!</b> Need at least 2 numbers available for <b>{html.escape(srv)} ({html.escape(cntry)})</b>.", parse_mode="HTML")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 JOIN OTP GROUP STREAM ⚡", url=OTP_GROUP_LINK)]])
    await callback.message.answer(f"🌐 <b>2 Numbers Assigned!</b>\n\n🔹 <b>Service:</b> {html.escape(srv)}\n🌍 <b>Country:</b> {html.escape(cntry)}\n📱 <b>Number 1:</b> <code>{nums[0]}</code>\n📱 <b>Number 2:</b> <code>{nums[1]}</code>\n\n⏳ <b>Waiting for OTPs...</b>\n💰 <b>Rate:</b> <code>${rate:.4f}</code> / OTP", reply_markup=kb, parse_mode="HTML")

@dp.message(F.text == "🔴 LIVE TRAFFIC")
async def live_traffic_handler(message: Message):
    if not await check_user_joined(message.from_user.id): return await message.answer("⚠️ Join all required groups first!", reply_markup=get_force_join_keyboard(), parse_mode="HTML")
    if not http_session or http_session.closed: return await message.answer("⚠️ Server session starting up, try again soon.", reply_markup=get_main_menu())
    headers = {"Authorization": f"Bearer {THIRDWAVE_API_KEY}", "Accept": "application/json"}
    try:
        async with http_session.get(f"{THIRDWAVE_BASE_URL}/traffic?pageSize=5", headers=headers) as resp:
            if resp.status == 200:
                rows = (await resp.json()).get("rows", [])
                if not rows: return await message.answer("ℹ️ No recent traffic found.", reply_markup=get_main_menu())
                txt = "🔴 <b>Recent 5 OTP Traffic:</b>\n\n" + "".join([f"📱 <b>Num:</b> <code>{html.escape(mask_phone_number(i.get('destinationNumber', '')))}</code>\n🔑 <b>OTP:</b> <code>{html.escape(extract_otp_code(str(i.get('messageBody', '')), i.get('otp')))}</code>\n💬 <code>{html.escape(str(i.get('messageBody', ''))[:40])}</code>\n───\n" for i in rows[:5]])
                await message.answer(txt, reply_markup=get_main_menu(), parse_mode="HTML")
            else: await message.answer(f"❌ <b>Traffic Error ({resp.status})</b>", reply_markup=get_main_menu(), parse_mode="HTML")
    except Exception as err: await message.answer(f"❌ <b>Connection Error:</b> <code>{html.escape(str(err))}</code>", parse_mode="HTML")

@dp.message(F.text == "💰 BALANCE")
async def balance_handler(message: Message):
    if not await check_user_joined(message.from_user.id): return await message.answer("⚠️ Join all groups first!", reply_markup=get_force_join_keyboard(), parse_mode="HTML")
    u_id = message.from_user.id
    await message.answer(f"👤 <b>User ID:</b> <code>{u_id}</code>\n💵 <b>Balance:</b> <code>${db_get_balance(u_id):.4f}</code>", parse_mode="HTML")

@dp.message(F.text == "♾️ REFER AND EARN")
async def referral_handler(message: Message):
    if not await check_user_joined(message.from_user.id): return await message.answer("⚠️ Join all groups first!", reply_markup=get_force_join_keyboard(), parse_mode="HTML")
    u_id = message.from_user.id
    bot_info = await bot.get_me()
    tot, earned = db_get_referral_stats(u_id)
    await message.answer(f"♾️ <b>REFERRAL PROGRAM</b> ♾️\n\n🔗 <b>Your Link:</b>\n<code>https://t.me/{bot_info.username}?start={u_id}</code>\n\n👥 <b>Total Invited:</b> <code>{tot}</code>\n💵 <b>Earned Bonus:</b> <code>${earned:.2f}</code>\n\n<i>Note: Invited users must request at least 3 OTPs to qualify!</i>", parse_mode="HTML")

@dp.message(F.text == "💸 WITHDRAW")
async def withdraw_start(message: Message, state: FSMContext):
    if not await check_user_joined(message.from_user.id): return await message.answer("⚠️ Join all groups first!", reply_markup=get_force_join_keyboard(), parse_mode="HTML")
    bal = db_get_balance(message.from_user.id)
    if bal < MIN_WITHDRAWAL: return await message.answer(f"❌ <b>Insufficient Balance!</b>\n\n💵 <b>Balance:</b> <code>${bal:.2f}</code>\n⚠️ <b>Min Withdrawal:</b> <code>${MIN_WITHDRAWAL:.2f}</code>", parse_mode="HTML")
    await state.set_state(WithdrawalState.waiting_for_details)
    await message.answer(f"💵 <b>Your Balance:</b> <code>${bal:.2f}</code>\n\nPlease send your payment details (Bank, Name, Crypto):", parse_mode="HTML")

@dp.message(WithdrawalState.waiting_for_details)
async def withdraw_details_received(message: Message, state: FSMContext):
    await state.update_data(details=message.text.strip())
    await state.set_state(WithdrawalState.waiting_for_amount)
    await message.answer(f"Enter the <b>Amount ($)</b> to withdraw (Min: <code>${MIN_WITHDRAWAL:.2f}</code>):", parse_mode="HTML")

@dp.message(WithdrawalState.waiting_for_amount)
async def withdraw_amount_received(message: Message, state: FSMContext):
    u_id, bal = message.from_user.id, db_get_balance(message.from_user.id)
    try: amt = float(message.text.strip())
    except ValueError: return await message.answer("⚠️ Invalid amount. Try again:")
    if amt < MIN_WITHDRAWAL or amt > bal: return await message.answer(f"⚠️ Invalid/Insufficient amount! Bal: <code>${bal:.2f}</code>", parse_mode="HTML")

    details = (await state.get_data()).get("details")
    await state.clear()
    await message.answer("✅ <b>Withdrawal Request Submitted!</b>", parse_mode="HTML")
    admin_btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Accept", callback_data=f"wd_accept_{u_id}_{amt}"), InlineKeyboardButton(text="❌ Reject", callback_data=f"wd_reject_{u_id}_{amt}")]])
    try: await bot.send_message(ADMIN_ID, f"🔔 <b>NEW WITHDRAWAL REQUEST!</b>\n\n👤 <b>User:</b> {message.from_user.first_name} (<code>{u_id}</code>)\n💵 <b>Amount:</b> <code>${amt:.2f}</code>\n🏦 <b>Details:</b>\n<code>{details}</code>", reply_markup=admin_btn, parse_mode="HTML")
    except Exception as e: print(f"[ERROR] Admin notification failed: {e}")

@dp.message(F.text == "📊 STATUS")
async def status_handler(message: Message):
    if not await check_user_joined(message.from_user.id): return await message.answer("⚠️ Join all groups first!", reply_markup=get_force_join_keyboard(), parse_mode="HTML")
    u_cnt, daily, top_3 = db_get_weekly_stats(message.from_user.id)
    daily_txt = "".join([f"📅 <b>{d}:</b> <code>{c}</code> OTPs\n" for d, c in daily]) if daily else "<i>No daily records.</i>\n"
    medals = ["🥇 1st", "🥈 2nd", "🥉 3rd"]
    lb_txt = "".join([f"{medals[i]}: User <code>{uid}</code> — <b>{c} OTPs</b>\n" for i, (uid, c) in enumerate(top_3)]) if top_3 else "<i>No traffic recorded.</i>\n"
    await message.answer(f"📊 <b>WEEKLY STATUS & LEADERBOARD</b> 📊\n\n📱 <b>Your Total (7 Days):</b> <code>{u_cnt}</code> OTPs\n\n🗓️ <b>Daily Breakdown:</b>\n{daily_txt}\n🏆 <b>Top Performers:</b>\n{lb_txt}", parse_mode="HTML")

# =============================================================
# BACKGROUND WORKER & LIFESPAN
# =============================================================
async def poll_thirdwave_traffic():
    headers = {"Authorization": f"Bearer {THIRDWAVE_API_KEY}"}
    grp_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 CHANNEL", url=BACKUP_GROUP_LINK), InlineKeyboardButton(text="💬 CHAT", url=DISCUSSION_GROUP_LINK)]])
    while True:
        try:
            if http_session and not http_session.closed:
                async with http_session.get(f"{THIRDWAVE_BASE_URL}/traffic?pageSize=15", headers=headers) as resp:
                    if resp.status == 200:
                        for item in (await resp.json()).get("rows", []):
                            msg_id = item.get("id")
                            if msg_id in PROCESSED_OTPS: continue
                            PROCESSED_OTPS.add(msg_id)

                            phone, body = str(item.get("destinationNumber", "")).strip(), str(item.get("messageBody", ""))
                            otp_code = extract_otp_code(body, item.get("otp"))
                            u_info = db_get_assigned_user(phone)

                            srv = u_info["service"] if u_info else "OTP"
                            cntry = u_info["country"] if u_info else "Service"
                            rate = u_info["rate"] if u_info else DEFAULT_OTP_RATE

                            try:
                                await bot.send_message(TELEGRAM_GROUP_ID, f"🔥 <b>New OTP Received!</b> ✨\n\n🌍 <b>Country:</b> {cntry}\n🛒 <b>Service:</b> {srv}\n📱 <b>Number:</b> <code>+{html.escape(mask_phone_number(phone))}</code>\n🔑 <b>OTP:</b> <code>{html.escape(otp_code)}</code>\n\n✉️ <b>Full Message:</b>\n<code>{html.escape(body)}</code>", reply_markup=grp_kb, parse_mode="HTML")
                            except Exception as ge: print(f"[FORWARD ERROR] {ge}")

                            if u_info:
                                uid = u_info["user_id"]
                                db_add_balance(uid, rate)
                                ref_id = db_record_otp_and_check_referral(uid)
                                if ref_id:
                                    try: await bot.send_message(ref_id, f"🎉 <b>Referral Bonus Credited!</b> Your referred user completed 3 OTPs. Earned <b>${REFERRAL_BONUS:.2f}</b>!", parse_mode="HTML")
                                    except Exception: pass
                                try: await bot.send_message(uid, f"🌐 <b>OTP Received ({srv} - {cntry})!</b>\n📱 <code>{html.escape(phone)}</code>\n🔑 Code: <code>{html.escape(otp_code)}</code>", parse_mode="HTML")
                                except Exception: pass
        except Exception as e: print(f"[WORKER ERROR] {e}")
        await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_session
    http_session = aiohttp.ClientSession()
    await bot.set_webhook(f"{RENDER_URL}/telegram-webhook", drop_pending_updates=True)
    polling_task = asyncio.create_task(poll_thirdwave_traffic())
    yield
    polling_task.cancel()
    if http_session and not http_session.closed: await http_session.close()
    await bot.delete_webhook()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.post("/")
@app.post("/telegram-webhook")
async def process_telegram_update(request: Request):
    data = await request.json()
    await dp.feed_update(bot, Update.model_validate(data, context={"bot": bot}))
    return {"status": "ok"}

@app.get("/")
async def health_check():
    return {"status": "bot is running"}
