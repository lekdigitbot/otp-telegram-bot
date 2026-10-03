import os, re, html, sqlite3, asyncio, aiohttp
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Update

# Optional PostgreSQL import
try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
THIRDWAVE_API_KEY = os.getenv("THIRDWAVE_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "")     # postgresql://postgres:password@...
RENDER_URL = os.getenv("RENDER_URL", "https://otp-telegram-bot-fpmp.onrender.com")

ADMIN_ID = int(os.getenv("ADMIN_ID", 0) or 0)
TELEGRAM_GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID", 0) or 0)
DISCUSSION_GROUP_ID = int(os.getenv("DISCUSSION_GROUP_ID", 0) or 0)
BACKUP_GROUP_ID = int(os.getenv("BACKUP_GROUP_ID", 0) or 0)

OTP_GROUP_LINK = os.getenv("OTP_GROUP_LINK", "https://t.me/lekotpzone")
DISCUSSION_GROUP_LINK = os.getenv("DISCUSSION_GROUP_LINK", "https://t.me/lekdigitaldiscussiongroup")
BACKUP_GROUP_LINK = os.getenv("BACKUP_GROUP_LINK", "https://t.me/lekdigitalbackupgroup")
SUPPORT_LINK = os.getenv("SUPPORT_LINK", "https://t.me/lekdigitalsupport")

THIRDWAVE_BASE_URL = "https://clients.thirdwave.im/api/v1"
DEFAULT_OTP_RATE, MIN_WITHDRAWAL, REFERRAL_BONUS = 0.003, 0.25, 0.05

if not BOT_TOKEN or not THIRDWAVE_API_KEY:
    raise ValueError("❌ Missing BOT_TOKEN or THIRDWAVE_API_KEY!")

bot, dp = Bot(token=BOT_TOKEN), Dispatcher(storage=MemoryStorage())
http_session, PROCESSED_OTPS, DB_FILE = None, set(), "/tmp/bot_data.db"

class WithdrawalState(StatesGroup):
    waiting_for_details, waiting_for_amount = State(), State()

# --- DATABASE CORE ENGINE ---
def db(q, p=(), fetch=None, commit=False):
    """Executes query on Supabase PostgreSQL if DATABASE_URL is set, else SQLite."""
    use_pg = bool(DATABASE_URL and HAS_PSYCOPG2)
    
    # Dynamic placeholder mapping (?, ?) -> (%s, %s) for PostgreSQL
    formatted_q = q.replace("?", "%s") if use_pg else q

    if use_pg:
        conn = psycopg2.connect(DATABASE_URL)
    else:
        conn = sqlite3.connect(DB_FILE, timeout=10)

    try:
        with conn:
            cur = conn.cursor()
            cur.execute(formatted_q, p)
            res = None
            if fetch == "one": res = cur.fetchone()
            elif fetch == "all": res = cur.fetchall()
            elif fetch == "rowcount": res = cur.rowcount
            if commit: conn.commit()
            return res
    finally:
        conn.close()

# Initialize Database Schema
if DATABASE_URL and HAS_PSYCOPG2:
    init_queries = [
        "CREATE TABLE IF NOT EXISTS stock (id SERIAL PRIMARY KEY, service TEXT, country TEXT, phone_number TEXT UNIQUE, rate REAL DEFAULT 0.003)",
        "CREATE TABLE IF NOT EXISTS assignments (phone_number TEXT PRIMARY KEY, user_id BIGINT, service TEXT, country TEXT, rate REAL DEFAULT 0.003)",
        "CREATE TABLE IF NOT EXISTS balances (user_id BIGINT PRIMARY KEY, balance REAL)",
        "CREATE TABLE IF NOT EXISTS referrals (user_id BIGINT PRIMARY KEY, referred_by BIGINT, otp_count INT DEFAULT 0, rewarded INT DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS otp_logs (id SERIAL PRIMARY KEY, user_id BIGINT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    ]
else:
    init_queries = [
        "CREATE TABLE IF NOT EXISTS stock (id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT, country TEXT, phone_number TEXT UNIQUE, rate REAL DEFAULT 0.003)",
        "CREATE TABLE IF NOT EXISTS assignments (phone_number TEXT PRIMARY KEY, user_id INTEGER, service TEXT, country TEXT, rate REAL DEFAULT 0.003)",
        "CREATE TABLE IF NOT EXISTS balances (user_id INTEGER PRIMARY KEY, balance REAL)",
        "CREATE TABLE IF NOT EXISTS referrals (user_id INTEGER PRIMARY KEY, referred_by INTEGER, otp_count INTEGER DEFAULT 0, rewarded INTEGER DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS otp_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"
    ]

for q in init_queries:
    db(q, commit=True)

# Helpers & Keyboards
def mask_phone(p): clean = re.sub(r"\D", "", str(p).strip()); return clean[:2]+"****"+clean[-2:] if len(clean)<=7 else clean[:5]+"****"+clean[-4:]
def extract_otp(b, f=""): match = re.search(r'\b\d{4,8}\b', str(b)); return str(f).strip() if (f and str(f) != "None") else (match.group(0) if match else "No Code")
def main_menu(): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 GET NUMBER"), KeyboardButton(text="🔴 LIVE TRAFFIC")], [KeyboardButton(text="💸 WITHDRAW"), KeyboardButton(text="💰 BALANCE")], [KeyboardButton(text="♾️ REFER AND EARN"), KeyboardButton(text="💀 SUPPORT")], [KeyboardButton(text="📊 STATUS")]], resize_keyboard=True)
def force_join_kb(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 Main Group", url=OTP_GROUP_LINK)], [InlineKeyboardButton(text="💬 Discussion", url=DISCUSSION_GROUP_LINK)], [InlineKeyboardButton(text="🛡️ Backup", url=BACKUP_GROUP_LINK)], [InlineKeyboardButton(text="✅ Verify join request", callback_data="check_membership")]])

async def check_user_joined(uid):
    if uid == ADMIN_ID: return True
    for cid in [TELEGRAM_GROUP_ID, DISCUSSION_GROUP_ID, BACKUP_GROUP_ID]:
        if cid:
            try:
                if (await bot.get_chat_member(cid, uid)).status not in ["member", "administrator", "creator"]: return False
            except Exception: return False
    return True

# Admin Handlers
@dp.message(F.text.startswith("/addnumber"))
async def add_num(m: Message):
    if m.from_user.id != ADMIN_ID: return
    lines = [l.strip() for l in m.text.strip().split("\n") if l.strip()]
    if len(lines) < 1 or len(lines[0].split(maxsplit=3)) < 3: return await m.answer("⚠️ Usage: <code>/addnumber Srv Cntry Rate\nNum1,Num2</code>", parse_mode="HTML")
    p = lines[0].split(maxsplit=3); srv, cntry = p[1].capitalize(), p[2].capitalize()
    rate = float(re.sub(r"[^\d.]", "", p[3])) if len(p) >= 4 and re.sub(r"[^\d.]", "", p[3]) else DEFAULT_OTP_RATE
    raw = [n for l in lines[1:] for n in l.split(",")]
    nums = [re.sub(r"\D", "", n) for n in raw if re.sub(r"\D", "", n)]
    
    added = 0
    for n in nums:
        try:
            if DATABASE_URL and HAS_PSYCOPG2:
                db("INSERT INTO stock (service, country, rate, phone_number) VALUES (?, ?, ?, ?) ON CONFLICT (phone_number) DO NOTHING", (srv, cntry, rate, n), commit=True)
            else:
                db("INSERT OR IGNORE INTO stock (service, country, rate, phone_number) VALUES (?, ?, ?, ?)", (srv, cntry, rate, n), commit=True)
            added += 1
        except Exception: pass

    await m.answer(f"✅ Added {added} number(s) to {srv} ({cntry}) at ${rate:.4f}!", parse_mode="HTML")
    try: await bot.send_message(TELEGRAM_GROUP_ID, f"📢 <b>NEW STOCK UPDATE! 📢</b>\n\n🔹 <b>Service:</b> {srv}\n🌍 <b>Country:</b> {cntry}\n💵 <b>Rate:</b> <code>${rate:.4f}</code> / OTP\n📱 <b>Added:</b> <code>{added}</code>", parse_mode="HTML")
    except Exception: pass

@dp.message(F.text.startswith("/delnumber"))
async def del_num(m: Message):
    if m.from_user.id == ADMIN_ID and len(m.text.split()) > 1:
        p = re.sub(r"\D", "", m.text.split()[1])
        r = (db("DELETE FROM stock WHERE phone_number = ?", (p,), fetch="rowcount", commit=True) or 0) + (db("DELETE FROM assignments WHERE phone_number = ?", (p,), fetch="rowcount", commit=True) or 0)
        await m.answer(f"✅ Removed {p}" if r else "❌ Not found", parse_mode="HTML")

@dp.message(F.text.startswith("/clearservice"))
async def clear_srv(m: Message):
    if m.from_user.id == ADMIN_ID and len(m.text.split(maxsplit=1)) > 1:
        s = m.text.split(maxsplit=1)[1].capitalize()
        r = (db("DELETE FROM stock WHERE service = ?", (s,), fetch="rowcount", commit=True) or 0) + (db("DELETE FROM assignments WHERE service = ?", (s,), fetch="rowcount", commit=True) or 0)
        await m.answer(f"✅ Cleared {s} ({r} entries)", parse_mode="HTML")

# Callbacks
@dp.callback_query(F.data == "check_membership")
async def cb_check(c: CallbackQuery):
    if await check_user_joined(c.from_user.id):
        await c.answer("✅ Thank you!", show_alert=True)
        try: await c.message.delete()
        except Exception: pass
        await c.message.answer(f"👋 Welcome <b>{c.from_user.first_name}</b>!", reply_markup=main_menu(), parse_mode="HTML")
    else: await c.answer("❌ Join required groups first!", show_alert=True)

@dp.callback_query(F.data.startswith(("wd_accept_", "wd_reject_")))
async def cb_wd(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    act, _, uid, amt = c.data.split("_"); uid, amt = int(uid), float(amt)
    if act == "wd_accept":
        bal = db("SELECT balance FROM balances WHERE user_id = ?", (uid,), fetch="one")
        if bal and bal[0] >= amt:
            db("UPDATE balances SET balance = balance - ? WHERE user_id = ?", (amt, uid), commit=True)
            await c.message.edit_text(f"✅ APPROVED & PAID! User: <code>{uid}</code> (${amt:.2f})", parse_mode="HTML")
            try: await bot.send_message(uid, f"🎉 Withdrawal of <b>${amt:.2f}</b> approved!", parse_mode="HTML")
            except Exception: pass
        else: await c.message.edit_text("❌ Failed: Insufficient balance.", parse_mode="HTML")
    else:
        await c.message.edit_text(f"❌ REJECTED! User: <code>{uid}</code> (${amt:.2f})", parse_mode="HTML")
        try: await bot.send_message(uid, f"❌ Withdrawal request for <b>${amt:.2f}</b> rejected.", parse_mode="HTML")
        except Exception: pass

# User Handlers
@dp.message(F.text.startswith("/start"))
async def start_h(m: Message):
    if len(m.text.split()) > 1 and m.text.split()[1].isdigit() and m.from_user.id != int(m.text.split()[1]):
        try:
            if DATABASE_URL and HAS_PSYCOPG2:
                db("INSERT INTO referrals (user_id, referred_by) VALUES (?, ?) ON CONFLICT DO NOTHING", (m.from_user.id, int(m.text.split()[1])), commit=True)
            else:
                db("INSERT OR IGNORE INTO referrals (user_id, referred_by) VALUES (?, ?)", (m.from_user.id, int(m.text.split()[1])), commit=True)
        except Exception: pass
    if not await check_user_joined(m.from_user.id): return await m.answer("⚠️ Access Restricted! Join all groups:", reply_markup=force_join_kb(), parse_mode="HTML")
    await m.answer(f"👋 Welcome <b>{m.from_user.first_name}</b> to <b>Lekdigital Number Zone</b>!", reply_markup=main_menu(), parse_mode="HTML")

@dp.message(F.text == "💀 SUPPORT")
async def supp_h(m: Message): await m.answer("🛠️ Need help?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Support", url=SUPPORT_LINK)]]))

@dp.message(F.text == "📱 GET NUMBER")
async def srv_h(m: Message):
    if not await check_user_joined(m.from_user.id): return await m.answer("⚠️ Join groups first!", reply_markup=force_join_kb())
    rows = db("SELECT service, COUNT(*) FROM stock GROUP BY service HAVING COUNT(*) >= 2", fetch="all") or []
    if not rows: return await m.answer("⚠️ Out of stock kindly contact support if not update in 5min!", reply_markup=main_menu())
    await m.answer("📲 Select service:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔹 {s} ({c})", callback_data=f"s_{s}")] for s, c in rows]), parse_mode="HTML")

@dp.callback_query(F.data.startswith("s_"))
async def srv_sel(c: CallbackQuery):
    if not await check_user_joined(c.from_user.id): return await c.answer("⚠️ Join groups first!", show_alert=True)
    srv = c.data[2:]; rows = db("SELECT country, rate, COUNT(*) FROM stock WHERE service = ? GROUP BY country, rate HAVING COUNT(*) >= 2", (srv,), fetch="all") or []
    if not rows: return await c.message.answer(f"⚠️ Out of stock for {html.escape(srv)}.")
    await c.message.answer(f"🌍 Select Country for {html.escape(srv)}:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🌍 {cnt} (${r:.4f}) - {cnt_cnt} avail", callback_data=f"c_{srv}_{cnt}")] for cnt, r, cnt_cnt in rows]), parse_mode="HTML")

@dp.callback_query(F.data.startswith("c_"))
async def num_assign(c: CallbackQuery):
    if not await check_user_joined(c.from_user.id): return await c.answer("⚠️ Join groups first!", show_alert=True)
    _, srv, cntry = c.data.split("_", 2)
    rows = db("SELECT phone_number, rate FROM stock WHERE service = ? AND country = ? LIMIT 2", (srv, cntry), fetch="all") or []
    if len(rows) < 2: return await c.message.answer("⚠️ Out of stock!")
    nums, rate = [rows[0][0], rows[1][0]], rows[0][1]
    for n in nums:
        db("DELETE FROM stock WHERE phone_number = ?", (n,), commit=True)
        if DATABASE_URL and HAS_PSYCOPG2:
            db("INSERT INTO assignments (phone_number, user_id, service, country, rate) VALUES (?, ?, ?, ?, ?) ON CONFLICT (phone_number) DO UPDATE SET user_id=EXCLUDED.user_id, service=EXCLUDED.service, country=EXCLUDED.country, rate=EXCLUDED.rate", (n, c.from_user.id, srv, cntry, rate), commit=True)
        else:
            db("INSERT OR REPLACE INTO assignments (phone_number, user_id, service, country, rate) VALUES (?, ?, ?, ?, ?)", (n, c.from_user.id, srv, cntry, rate), commit=True)
    await c.message.answer(f"🌐 <b>2 Numbers Assigned Successfully!</b>\n🔹 <b>Service:</b> {html.escape(srv)}\n🌍 <b>Country:</b> {html.escape(cntry)}\n📱 <b>1:</b> <code>{nums[0]}</code>\n📱 <b>2:</b> <code>{nums[1]}</code>\n💰 <b>Rate:</b> <code>${rate:.4f}</code>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Otp group", url=OTP_GROUP_LINK)]]), parse_mode="HTML")

@dp.message(F.text == "🔴 LIVE TRAFFIC")
async def traffic_h(m: Message):
    if not await check_user_joined(m.from_user.id): return await m.answer("⚠️ Join groups first!", reply_markup=force_join_kb())
    if not http_session or http_session.closed: return await m.answer("⚠️ Session starting...", reply_markup=main_menu())
    try:
        async with http_session.get(f"{THIRDWAVE_BASE_URL}/traffic?pageSize=5", headers={"Authorization": f"Bearer {THIRDWAVE_API_KEY}"}) as r:
            if r.status == 200:
                rows = (await r.json()).get("rows", [])
                txt = "🔴 <b>Recent 5 Traffic:</b>\n\n" + "".join([f"📱 <b>Num:</b> <code>{html.escape(mask_phone(i.get('destinationNumber','')))}</code>\n🔑 <b>OTP:</b> <code>{html.escape(extract_otp(str(i.get('messageBody','')), i.get('otp')))}</code>\n💬 <code>{html.escape(str(i.get('messageBody',''))[:40])}</code>\n───\n" for i in rows[:5]])
                await m.answer(txt or "No traffic.", reply_markup=main_menu(), parse_mode="HTML")
    except Exception as e: await m.answer(f"❌ Error: {e}")

@dp.message(F.text == "💰 BALANCE")
async def bal_h(m: Message):
    row = db("SELECT balance FROM balances WHERE user_id = ?", (m.from_user.id,), fetch="one")
    await m.answer(f"👤 <b>ID:</b> <code>{m.from_user.id}</code>\n💵 <b>Balance:</b> <code>${(row[0] if row else 0.0):.4f}</code>", parse_mode="HTML")

@dp.message(F.text == "♾️ REFER AND EARN")
async def ref_h(m: Message):
    bot_info = await bot.get_me()
    row = db("SELECT COUNT(*), SUM(rewarded) FROM referrals WHERE referred_by = ?", (m.from_user.id,), fetch="one")
    tot, rwd = (row[0] if row and row[0] else 0), (row[1] if row and row[1] else 0)
    await m.answer(f"♾️ <b>REFERRALS</b>\n\n🔗 <code>https://t.me/{bot_info.username}?start={m.from_user.id}</code>\n👥 <b>Invited:</b> <code>{tot}</code>\n💵 <b>Earned:</b> <code>${rwd * REFERRAL_BONUS:.2f}</code>", parse_mode="HTML")

@dp.message(F.text == "💸 WITHDRAW")
async def wd_start(m: Message, state: FSMContext):
    row = db("SELECT balance FROM balances WHERE user_id = ?", (m.from_user.id,), fetch="one"); bal = row[0] if row else 0.0
    if bal < MIN_WITHDRAWAL: return await m.answer(f"❌ Balance too low! Bal: <code>${bal:.2f}</code> (Min: <code>${MIN_WITHDRAWAL:.2f}</code>)", parse_mode="HTML")
    await state.set_state(WithdrawalState.waiting_for_details)
    await m.answer("💵 Send your payment details (Bank/Crypto):", parse_mode="HTML")

@dp.message(WithdrawalState.waiting_for_details)
async def wd_dtls(m: Message, state: FSMContext):
    await state.update_data(details=m.text.strip()); await state.set_state(WithdrawalState.waiting_for_amount)
    await m.answer(f"Enter Amount ($) (Min: <code>${MIN_WITHDRAWAL:.2f}</code>):", parse_mode="HTML")

@dp.message(WithdrawalState.waiting_for_amount)
async def wd_amt(m: Message, state: FSMContext):
    row = db("SELECT balance FROM balances WHERE user_id = ?", (m.from_user.id,), fetch="one"); bal = row[0] if row else 0.0
    try: amt = float(m.text.strip())
    except ValueError: return await m.answer("⚠️ Enter valid number:")
    if amt < MIN_WITHDRAWAL or amt > bal: return await m.answer(f"⚠️ Invalid/Insufficient amount! Max: <code>${bal:.2f}</code>", parse_mode="HTML")
    dtls = (await state.get_data()).get("details"); await state.clear()
    await m.answer("✅ Request Submitted!")
    try: await bot.send_message(ADMIN_ID, f"🔔 <b>WITHDRAWAL!</b>\nUser: {m.from_user.first_name} (<code>{m.from_user.id}</code>)\nAmount: <code>${amt:.2f}</code>\nDetails:\n<code>{dtls}</code>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Accept", callback_data=f"wd_accept_{m.from_user.id}_{amt}"), InlineKeyboardButton(text="❌ Reject", callback_data=f"wd_reject_{m.from_user.id}_{amt}")]]) , parse_mode="HTML")
    except Exception: pass

@dp.message(F.text == "📊 STATUS")
async def status_h(m: Message):
    uid = m.from_user.id
    if DATABASE_URL and HAS_PSYCOPG2:
        u_cnt = db("SELECT COUNT(*) FROM otp_logs WHERE user_id = ? AND timestamp >= NOW() - INTERVAL '7 days'", (uid,), fetch="one")[0]
        daily = db("SELECT DATE(timestamp), COUNT(*) FROM otp_logs WHERE user_id = ? AND timestamp >= NOW() - INTERVAL '7 days' GROUP BY DATE(timestamp)", (uid,), fetch="all") or []
        top_3 = db("SELECT user_id, COUNT(*) as c FROM otp_logs WHERE timestamp >= NOW() - INTERVAL '7 days' GROUP BY user_id ORDER BY c DESC LIMIT 3", fetch="all") or []
    else:
        u_cnt = db("SELECT COUNT(*) FROM otp_logs WHERE user_id = ? AND timestamp >= datetime('now', '-7 days')", (uid,), fetch="one")[0]
        daily = db("SELECT date(timestamp), COUNT(*) FROM otp_logs WHERE user_id = ? AND timestamp >= datetime('now', '-7 days') GROUP BY date(timestamp)", (uid,), fetch="all") or []
        top_3 = db("SELECT user_id, COUNT(*) as c FROM otp_logs WHERE timestamp >= datetime('now', '-7 days') GROUP BY user_id ORDER BY c DESC LIMIT 3", fetch="all") or []

    medals = ["🥇", "🥈", "🥉"]
    await m.answer(f"📊 <b>STATUS & LEADERBOARD</b>\n\n📱 <b>Total (7d):</b> <code>{u_cnt}</code>\n\n🗓️ <b>Daily:</b>\n" + "".join([f"📅 {d}: <code>{c}</code>\n" for d, c in daily]) + "\n🏆 <b>Top Rank:</b>\n" + "".join([f"{medals[i]}: User <code>{u}</code> — <b>{c} OTPs</b>\n" for i, (u, c) in enumerate(top_3)]), parse_mode="HTML")

# --- THIRDWAVE WORKER ---
async def poll_traffic():
    while True:
        try:
            if http_session and not http_session.closed:
                async with http_session.get(f"{THIRDWAVE_BASE_URL}/traffic?pageSize=15", headers={"Authorization": f"Bearer {THIRDWAVE_API_KEY}"}) as r:
                    if r.status == 200:
                        for item in (await r.json()).get("rows", []):
                            mid = item.get("id") or f"{item.get('destinationNumber')}_{item.get('otp')}"
                            if mid in PROCESSED_OTPS: continue
                            PROCESSED_OTPS.add(mid)

                            phone = str(item.get("destinationNumber","")).strip()
                            body = str(item.get("messageBody",""))
                            otp_code = extract_otp(body, item.get("otp"))
                            clean_num = re.sub(r"\D", "", phone)

                            row = db("SELECT user_id, service, country, rate FROM assignments WHERE phone_number = ?", (clean_num,), fetch="one")
                            srv, cntry, rate = (row[1], row[2], row[3]) if row else ("OTP", "Service", DEFAULT_OTP_RATE)

                            grp_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 CHANNEL", url=BACKUP_GROUP_LINK), InlineKeyboardButton(text="💬 CHAT", url=DISCUSSION_GROUP_LINK)]])
                            
                            try:
                                await bot.send_message(TELEGRAM_GROUP_ID, f"🔥 <b>New OTP Received! 🔥</b>\n\n🌍 <b>Country:</b> {cntry}\n🛒 <b>Service:</b> {srv}\n📱 <b>Number:</b> <code>+{html.escape(mask_phone(phone))}</code>\n🔑 <b>OTP:</b> <code>{html.escape(otp_code)}</code>\n\n✉️ <b>Message:</b>\n<code>{html.escape(body)}</code>", reply_markup=grp_kb, parse_mode="HTML")
                            except Exception: pass

                            if row:
                                uid = row[0]
                                if DATABASE_URL and HAS_PSYCOPG2:
                                    db("INSERT INTO balances (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balances.balance + ?", (uid, rate, rate), commit=True)
                                else:
                                    db("INSERT INTO balances (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?", (uid, rate, rate), commit=True)
                                
                                db("INSERT INTO otp_logs (user_id) VALUES (?)", (uid,), commit=True)
                                
                                ref = db("SELECT referred_by, otp_count, rewarded FROM referrals WHERE user_id = ?", (uid,), fetch="one")
                                if ref and ref[2] == 0:
                                    if ref[1] + 1 >= 3:
                                        db("UPDATE referrals SET otp_count = 3, rewarded = 1 WHERE user_id = ?", (uid,), commit=True)
                                        if DATABASE_URL and HAS_PSYCOPG2:
                                            db("INSERT INTO balances (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balances.balance + ?", (ref[0], REFERRAL_BONUS, REFERRAL_BONUS), commit=True)
                                        else:
                                            db("INSERT INTO balances (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?", (ref[0], REFERRAL_BONUS, REFERRAL_BONUS), commit=True)
                                        try: await bot.send_message(ref[0], f"🎉 Referral Bonus Credited! Earned <b>${REFERRAL_BONUS:.2f}</b>!", parse_mode="HTML")
                                        except Exception: pass
                                    else:
                                        db("UPDATE referrals SET otp_count = otp_count + 1 WHERE user_id = ?", (uid,), commit=True)
                                try:
                                    await bot.send_message(uid, f"🌐 <b>OTP Received ({srv} - {cntry})!</b>\n📱 <code>{html.escape(phone)}</code>\n🔑 Code: <code>{html.escape(otp_code)}</code>", parse_mode="HTML")
                                except Exception: pass
        except Exception as e: print(f"[WORKER ERROR] {e}")
        await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_session; http_session = aiohttp.ClientSession()
    await bot.set_webhook(f"{RENDER_URL}/telegram-webhook", drop_pending_updates=True)
    task = asyncio.create_task(poll_traffic())
    yield
    task.cancel()
    if http_session and not http_session.closed: await http_session.close()
    await bot.delete_webhook(); await bot.session.close()

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
