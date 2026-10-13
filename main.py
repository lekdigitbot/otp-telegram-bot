import os, re, html, sqlite3, asyncio, aiohttp, hashlib
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Update

try:
    import psycopg2; HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

BOT_TOKEN, THIRDWAVE_API_KEY, IPRN_API_KEY = os.getenv("BOT_TOKEN"), os.getenv("THIRDWAVE_API_KEY"), os.getenv("IPRN_API_KEY")
DATABASE_URL, RENDER_URL = os.getenv("DATABASE_URL", "").replace('"', '').replace("'", "").strip(), os.getenv("RENDER_URL", "https://otp-telegram-bot-fpmp.onrender.com")

ADMIN_ID, TELEGRAM_GROUP_ID, DISCUSSION_GROUP_ID, BACKUP_GROUP_ID = int(os.getenv("ADMIN_ID", 0) or 0), int(os.getenv("TELEGRAM_GROUP_ID", 0) or 0), int(os.getenv("DISCUSSION_GROUP_ID", 0) or 0), int(os.getenv("BACKUP_GROUP_ID", 0) or 0)
OTP_GROUP_LINK, DISCUSSION_GROUP_LINK, BACKUP_GROUP_LINK, SUPPORT_LINK = os.getenv("OTP_GROUP_LINK", "https://t.me/lekotpzone"), os.getenv("DISCUSSION_GROUP_LINK", "https://t.me/lekdigitaldiscussiongroup"), os.getenv("BACKUP_GROUP_LINK", "https://t.me/lekdigitalbackupgroup"), os.getenv("SUPPORT_LINK", "https://t.me/lekdigitalsupport")

THIRDWAVE_BASE_URL, IPRN_BASE_URL = "https://clients.thirdwave.im/api/v1", "https://api.iprn.pro/api/stock/public"
DEFAULT_OTP_RATE, MIN_WITHDRAWAL, REFERRAL_BONUS, ASSIGN_COUNT = 0.003, 0.35, 0.01, 3

if not BOT_TOKEN: raise ValueError("❌ Missing BOT_TOKEN!")
bot, dp = Bot(token=BOT_TOKEN), Dispatcher(storage=MemoryStorage())
http_session, DB_FILE = None, "/tmp/bot_data.db"

class WithdrawalState(StatesGroup):
    waiting_for_details, waiting_for_amount = State(), State()

def db(q, p=(), fetch=None, commit=False):
    use_pg = bool(DATABASE_URL and HAS_PSYCOPG2)
    conn = psycopg2.connect(DATABASE_URL) if use_pg else sqlite3.connect(DB_FILE, timeout=10)
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(q.replace("?", "%s") if use_pg else q, p)
            res = cur.fetchone() if fetch == "one" else (cur.fetchall() if fetch == "all" else (cur.rowcount if fetch == "rowcount" else None))
            if commit: conn.commit()
            return res
    finally: conn.close()

use_pg = bool(DATABASE_URL and HAS_PSYCOPG2)
init_queries = [
    f"CREATE TABLE IF NOT EXISTS stock (id {'SERIAL' if use_pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'} PRIMARY KEY, service TEXT, country TEXT, phone_number TEXT UNIQUE, rate REAL DEFAULT 0.003)",
    f"CREATE TABLE IF NOT EXISTS assignments (phone_number TEXT PRIMARY KEY, user_id {'BIGINT' if use_pg else 'INTEGER'}, service TEXT, country TEXT, rate REAL DEFAULT 0.003)",
    f"CREATE TABLE IF NOT EXISTS balances (user_id {'BIGINT' if use_pg else 'INTEGER'} PRIMARY KEY, balance REAL)",
    f"CREATE TABLE IF NOT EXISTS referrals (user_id {'BIGINT' if use_pg else 'INTEGER'} PRIMARY KEY, referred_by {'BIGINT' if use_pg else 'INTEGER'}, otp_count INT DEFAULT 0, rewarded INT DEFAULT 0)",
    f"CREATE TABLE IF NOT EXISTS otp_logs (id {'SERIAL' if use_pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'} PRIMARY KEY, otp_identifier TEXT UNIQUE, user_id {'BIGINT' if use_pg else 'INTEGER'}, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
]
for q in init_queries: db(q, commit=True)
try: db("ALTER TABLE otp_logs ADD COLUMN IF NOT EXISTS otp_identifier TEXT UNIQUE;" if use_pg else "ALTER TABLE otp_logs ADD COLUMN otp_identifier TEXT UNIQUE;", commit=True)
except Exception: pass

mask_phone = lambda p: (clean := re.sub(r"\D", "", str(p).strip())) and (clean[:2]+"****"+clean[-2:] if len(clean)<=7 else clean[:5]+"****"+clean[-4:])
extract_otp = lambda b, f="": str(f).strip() if (f and str(f) != "None") else ((m := re.search(r'\b\d{4,8}\b', str(b))) and m.group(0) or "No Code")

def main_menu(): 
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📱 GET NUMBER", style="primary"), KeyboardButton(text="🔴 LIVE TRAFFIC", style="primary")], 
        [KeyboardButton(text="💸 WITHDRAW", style="primary"), KeyboardButton(text="💰 BALANCE", style="primary")], 
        [KeyboardButton(text="♾️ REFER AND EARN", style="primary"), KeyboardButton(text="💀 SUPPORT", style="primary")], 
        [KeyboardButton(text="📊 STATUS", style="primary")]
    ], resize_keyboard=True, is_persistent=True)

def force_join_kb(): 
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Main Group", url=OTP_GROUP_LINK, style="primary")], 
        [InlineKeyboardButton(text="💬 Discussion", url=DISCUSSION_GROUP_LINK, style="primary")], 
        [InlineKeyboardButton(text="🛡️ Backup", url=BACKUP_GROUP_LINK, style="primary")], 
        [InlineKeyboardButton(text="✅ Verify join request", callback_data="check_membership", style="success")]
    ])

async def check_user_joined(uid):
    if uid == ADMIN_ID: return True
    for cid in [TELEGRAM_GROUP_ID, DISCUSSION_GROUP_ID, BACKUP_GROUP_ID]:
        if cid:
            try:
                if (await bot.get_chat_member(cid, uid)).status not in ["member", "administrator", "creator"]: return False
            except Exception: return False
    return True

def build_services_keyboard():
    rows = db("SELECT service, COUNT(*) FROM stock GROUP BY service HAVING COUNT(*) >= ?", (ASSIGN_COUNT,), fetch="all") or []
    if not rows: return None, "⚠️ <b>Out of stock!</b>"
    btns = [InlineKeyboardButton(text=f"🔹 {s.upper()}", callback_data=f"s_{s}", style="success") for s, _ in rows]
    return InlineKeyboardMarkup(inline_keyboard=[btns[i:i+2] for i in range(0, len(btns), 2)] + [[InlineKeyboardButton(text="🔙 Back to Menu", callback_data="cb_close_menu", style="danger")]]), "📌 <b>Select a Service:</b>"

def build_countries_keyboard(srv):
    rows = db("SELECT country, rate, COUNT(*) FROM stock WHERE service = ? GROUP BY country, rate HAVING COUNT(*) >= ?", (srv, ASSIGN_COUNT), fetch="all") or []
    if not rows: return None, f"⚠️ <b>Out of stock for {html.escape(srv)}.</b>"
    kb = [[InlineKeyboardButton(text=f" {c} | ${r:.3f}/OTP", callback_data=f"c_{srv}_{c}", style="primary")] for c, r, _ in rows]
    return InlineKeyboardMarkup(inline_keyboard=kb + [[InlineKeyboardButton(text="🔙 Back to Services", callback_data="cb_services_list", style="danger")]]), f"📌 <b>Select country for {srv.upper()}:</b>"

@dp.message(F.text.startswith("/addnumber"))
async def add_num(m: Message):
    if m.from_user.id != ADMIN_ID: return
    lines = [l.strip() for l in m.text.strip().split("\n") if l.strip()]
    if len(lines) < 1 or len(lines[0].split(maxsplit=3)) < 3: 
        return await m.answer("⚠ Usage: <code>/addnumber Srv Cntry Rate\nNum1,Num2</code>", parse_mode="HTML")
    
    p = lines[0].split(maxsplit=3)
    srv, cntry = p[1].capitalize(), p[2].capitalize()
    rate = float(re.sub(r"[^\d.]", "", p[3])) if len(p) >= 4 and re.sub(r"[^\d.]", "", p[3]) else DEFAULT_OTP_RATE
    
    # Properly extract all numbers separated by commas or newlines
    raw_nums = []
    for line in lines[1:]:
        raw_nums.extend([n.strip() for n in line.split(",") if n.strip()])
    
    nums = [re.sub(r"\D", "", n) for n in raw_nums if re.sub(r"\D", "", n)]
    
    added = 0
    for n in nums:
        try:
            if use_pg:
                res = db("INSERT INTO stock (service, country, rate, phone_number) VALUES (?, ?, ?, ?) ON CONFLICT (phone_number) DO NOTHING", (srv, cntry, rate, n), commit=True)
            else:
                res = db("INSERT OR IGNORE INTO stock (service, country, rate, phone_number) VALUES (?, ?, ?, ?)", (srv, cntry, rate, n), commit=True)
            added += 1
        except Exception:
            pass

    await m.answer(f"✅ Added {added} number(s) to {srv} ({cntry}) at ${rate:.4f}!", parse_mode="HTML")
    
    bot_info = await bot.get_me()
    stock_text = f"🎉 <b>New Stock Added!</b>\n\n🔹 <b>Service:</b> {srv}\n🌍 <b>Country:</b> {cntry}\n📱 <b>Quantity:</b> <code>{added}</code> numbers\n💵 <b>Rate:</b> <code>${rate:.4f}</code> per OTP\n\n👉 <i>Press 'Get Number' from the Bot to request numbers!</i>"
    
    if TELEGRAM_GROUP_ID:
        try: 
            await bot.send_message(TELEGRAM_GROUP_ID, stock_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"📲 Get Number from Bot — {srv}", url=f"https://t.me/{bot_info.username}", style="success")]]), parse_mode="HTML")
        except Exception: 
            pass
            
    for u in (db("SELECT user_id FROM balances", fetch="all") or []):
        try:
            await bot.send_message(u[0], stock_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"📲 Get Number — {srv}", callback_data=f"s_{srv}", style="success")]]), parse_mode="HTML")
            await asyncio.sleep(0.04)
        except Exception: 
            pass

@dp.message(F.text.startswith("/broadcast"))
async def handle_broadcast(m: Message):
    if m.from_user.id != ADMIN_ID or not (txt := m.text.replace("/broadcast", "", 1).strip()): return await m.answer("⚠️ Usage: <code>/broadcast Message</code>", parse_mode="HTML")
    users, sent = db("SELECT user_id FROM balances", fetch="all") or [], 0
    await m.answer(f"⏳ Broadcasting to {len(users)} user(s)...")
    for u in users:
        try:
            await bot.send_message(u[0], f"📢 <b>ANNOUNCEMENT</b>\n\n{txt}", parse_mode="HTML")
            sent += 1; await asyncio.sleep(0.04)
        except Exception: pass
    await m.answer(f"✅ Broadcast complete! Delivered to {sent} user(s).", parse_mode="HTML")

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

@dp.callback_query(F.data == "check_membership")
async def cb_check(c: CallbackQuery):
    if await check_user_joined(c.from_user.id):
        await c.answer("✅ Thank you!", show_alert=True)
        try: await c.message.delete()
        except Exception: pass
        await c.message.answer(f"👋 Welcome <b>{c.from_user.first_name}</b>!", reply_markup=main_menu(), parse_mode="HTML")
    else: await c.answer("❌ Join required groups first!", show_alert=True)

@dp.callback_query(F.data == "cb_close_menu")
async def cb_close(c: CallbackQuery):
    try: await c.message.delete()
    except Exception: pass

@dp.callback_query(F.data == "cb_services_list")
async def cb_back_services(c: CallbackQuery):
    if not await check_user_joined(c.from_user.id): 
        await c.answer("⚠️ You must stay joined in all groups to use this!", show_alert=True)
        try: await c.message.delete()
        except Exception: pass
        return await c.message.answer("⚠️ Access Restricted! Join all groups:", reply_markup=force_join_kb(), parse_mode="HTML")
    kb, txt = build_services_keyboard()
    if not kb: return await c.answer("⚠️ Out of stock!", show_alert=True)
    await c.message.edit_text(txt, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("s_"))
async def srv_sel(c: CallbackQuery):
    if not await check_user_joined(c.from_user.id): 
        await c.answer("⚠️ You must stay joined in all groups to use this!", show_alert=True)
        try: await c.message.delete()
        except Exception: pass
        return await c.message.answer("⚠️ Access Restricted! Join all groups:", reply_markup=force_join_kb(), parse_mode="HTML")
    kb, txt = build_countries_keyboard(c.data[2:])
    if not kb: return await c.answer("⚠️ Out of stock!", show_alert=True)
    await c.message.edit_text(txt, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("c_"))
async def num_assign(c: CallbackQuery):
    if not await check_user_joined(c.from_user.id): 
        await c.answer("⚠️ You must stay joined in all groups to use this!", show_alert=True)
        try: await c.message.delete()
        except Exception: pass
        return await c.message.answer("⚠️ Access Restricted! Join all groups:", reply_markup=force_join_kb(), parse_mode="HTML")
    _, srv, cntry = c.data.split("_", 2)
    rows = db(f"SELECT phone_number, rate FROM stock WHERE service = ? AND country = ? LIMIT {ASSIGN_COUNT}", (srv, cntry), fetch="all") or []
    if len(rows) < ASSIGN_COUNT: return await c.answer("⚠️ Out of stock for selected country!", show_alert=True)
    
    nums, rate = [r[0] for r in rows], rows[0][1]
    for n in nums:
        db("DELETE FROM stock WHERE phone_number = ?", (n,), commit=True)
        q = "INSERT INTO assignments (phone_number, user_id, service, country, rate) VALUES (?, ?, ?, ?, ?) ON CONFLICT (phone_number) DO UPDATE SET user_id=EXCLUDED.user_id, service=EXCLUDED.service, country=EXCLUDED.country, rate=EXCLUDED.rate" if use_pg else "INSERT OR REPLACE INTO assignments (phone_number, user_id, service, country, rate) VALUES (?, ?, ?, ?, ?)"
        db(q, (n, c.from_user.id, srv, cntry, rate), commit=True)

    rem_count = (db("SELECT COUNT(*) FROM stock WHERE service = ? AND country = ?", (srv, cntry), fetch="one") or [0])[0]
    
    kb = [
        [InlineKeyboardButton(text=f"➕ +{n}", copy_text={"text": f"+{n}"}, style="success")] for n in nums
    ] + [
        [InlineKeyboardButton(text="🔁 Change Number", callback_data=f"c_{srv}_{cntry}", style="primary"), 
         InlineKeyboardButton(text="🌍 Change Country", callback_data=f"s_{srv}", style="primary")],
        [InlineKeyboardButton(text="🚀 Otp Group", url=OTP_GROUP_LINK, style="success")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="cb_services_list", style="danger")]
    ]
    
    await c.message.edit_text(f"🇳🇬 <b>{cntry} Number Assigned:</b>\n\n⏳ Waiting for OTP...\nPer OTP Rate: <b>${rate:.3f}</b>\n📦 <i>Remaining Stock:</i> <code>{rem_count}</code>\n", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@dp.callback_query(F.data.startswith(("wd_accept_", "wd_reject_")))
async def cb_wd(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID: return await c.answer("Unauthorized", show_alert=True)
    act, uid, amt = (parts := c.data.split("_"))[0] + "_" + parts[1], int(parts[2]), float(parts[3])
    if act == "wd_accept":
        bal = (db("SELECT balance FROM balances WHERE user_id = ?", (uid,), fetch="one") or [0.0])[0]
        if bal >= amt:
            db("UPDATE balances SET balance = balance - ? WHERE user_id = ?", (amt, uid), commit=True)
            await c.answer("Withdrawal Approved!")
            await c.message.edit_text(f"✅ APPROVED & PAID! User: <code>{uid}</code> (${amt:.2f})", parse_mode="HTML")
            try: await bot.send_message(uid, f"🎉 Withdrawal of <b>${amt:.2f}</b> approved!", parse_mode="HTML")
            except Exception: pass
        else: await c.answer("Insufficient balance!", show_alert=True); await c.message.edit_text(f"❌ Failed: Insufficient balance. (${bal:.2f})", parse_mode="HTML")
    else:
        await c.answer("Withdrawal Rejected.")
        await c.message.edit_text(f"❌ REJECTED! User: <code>{uid}</code> (${amt:.2f})", parse_mode="HTML")
        try: await bot.send_message(uid, f"❌ Withdrawal request for <b>${amt:.2f}</b> rejected.", parse_mode="HTML")
        except Exception: pass

@dp.message(F.text.startswith("/start"))
async def start_h(m: Message):
    if use_pg:
        db("INSERT INTO balances (user_id, balance) VALUES (?, 0.0) ON CONFLICT DO NOTHING", (m.from_user.id,), commit=True)
    else:
        db("INSERT OR IGNORE INTO balances (user_id, balance) VALUES (?, 0.0)", (m.from_user.id,), commit=True)

    if len(parts := m.text.split()) > 1 and parts[1].isdigit() and m.from_user.id != int(parts[1]):
        try: db(("INSERT INTO referrals (user_id, referred_by) VALUES (?, ?) ON CONFLICT DO NOTHING" if use_pg else "INSERT OR IGNORE INTO referrals (user_id, referred_by) VALUES (?, ?)"), (m.from_user.id, int(parts[1])), commit=True)
        except Exception: pass

    if not await check_user_joined(m.from_user.id): return await m.answer("⚠️ Access Restricted! Join all groups:", reply_markup=force_join_kb(), parse_mode="HTML")
    await m.answer(f"👋 Welcome <b>{m.from_user.first_name}</b> to <b>Lekdigital Number Zone</b>!", reply_markup=main_menu(), parse_mode="HTML")

@dp.message(F.text == "💀 SUPPORT")
async def supp_h(m: Message): await m.answer("🛠️ Need help?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Support", url=SUPPORT_LINK, style="primary")]]))

@dp.message(F.text == "📱 GET NUMBER")
async def srv_h(m: Message):
    if not await check_user_joined(m.from_user.id): return await m.answer("⚠️ Access Restricted! You left one of our required groups. Please join them back to continue:", reply_markup=force_join_kb(), parse_mode="HTML")
    kb, txt = build_services_keyboard()
    await m.answer(txt, reply_markup=kb or main_menu(), parse_mode="HTML")

@dp.message(F.text == "🔴 LIVE TRAFFIC")
async def traffic_h(m: Message):
    if not await check_user_joined(m.from_user.id): 
        return await m.answer("⚠️ Access Restricted! Join required groups:", reply_markup=force_join_kb(), parse_mode="HTML")
    if not http_session or http_session.closed: 
        return await m.answer("⚠ Session starting...", reply_markup=main_menu())
    
    combined_rows = []
    
    if THIRDWAVE_API_KEY:
        try:
            async with http_session.get(f"{THIRDWAVE_BASE_URL}/traffic?page=1&limit=20&pageSize=20", headers={"Authorization": f"Bearer {THIRDWAVE_API_KEY}", "x-api-key": THIRDWAVE_API_KEY, "Accept": "application/json"}) as r:
                if r.status == 200:
                    res = await r.json()
                    rows = res.get("rows", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
                    for i in rows:
                        phone = str(i.get("destinationNumber") or i.get("phone") or i.get("number") or i.get("b_number") or "")
                        msg = str(i.get("messageBody") or i.get("message") or i.get("text") or i.get("body") or "")
                        otp = extract_otp(msg, i.get("otp") or i.get("code"))
                        if phone and msg:
                            combined_rows.append({"phone": phone, "msg": msg, "otp": otp})
        except Exception: pass

    if IPRN_API_KEY:
        try:
            async with http_session.get(f"{IPRN_BASE_URL}/edr?page=1&per_page=20", headers={"Authorization": f"Bearer {IPRN_API_KEY}", "Content-Type": "application/json", "Accept": "application/json"}) as r:
                if r.status == 200:
                    res = await r.json()
                    items = res.get("data", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
                    for i in items:
                        phone = str(i.get("b_number") or i.get("phone") or i.get("number") or "")
                        msg = str(i.get("message") or i.get("text") or i.get("body") or "")
                        otp = extract_otp(msg)
                        if phone and msg:
                            combined_rows.append({"phone": phone, "msg": msg, "otp": otp})
        except Exception: pass

    txt = "🔴 <b>Recent Live Traffic:</b>\n\n" + "".join([
        f"📱 <b>Num:</b> <code>{html.escape(mask_phone(i['phone']))}</code>\n"
        f"🔑 <b>OTP:</b> <code>{html.escape(i['otp'])}</code>\n"
        f"💬 <code>{html.escape(i['msg'][:40])}</code>\n───\n" 
        for i in combined_rows[:5]
    ])
    await m.answer(txt if combined_rows else "No traffic.", reply_markup=main_menu(), parse_mode="HTML")

@dp.message(F.text == "💰 BALANCE")
async def bal_h(m: Message):
    row = db("SELECT balance FROM balances WHERE user_id = ?", (m.from_user.id,), fetch="one")
    await m.answer(f"👤 <b>ID:</b> <code>{m.from_user.id}</code>\n💵 <b>Balance:</b> <code>${(row[0] if row else 0.0):.4f}</code>", parse_mode="HTML")

@dp.message(F.text == "♾️ REFER AND EARN")
async def ref_h(m: Message):
    bot_info, row = await bot.get_me(), db("SELECT COUNT(*), SUM(rewarded) FROM referrals WHERE referred_by = ?", (m.from_user.id,), fetch="one")
    tot, rwd = (row[0] if row and row[0] else 0), (row[1] if row and row[1] else 0)
    await m.answer(f"♾️ <b>REFER AND EARN</b>\n\nEarn <b>${REFERRAL_BONUS:.2f}</b> per invite!\n📌 <b>Rule:</b> Friend must complete <b>3 OTP transactions</b>.\n\n🔗 <b>Referral Link:</b>\n<code>https://t.me/{bot_info.username}?start={m.from_user.id}</code>\n\n👥 <b>Invited:</b> <code>{tot}</code>\n💵 <b>Rewarded:</b> <code>{rwd}</code>\n💰 <b>Earned:</b> <code>${rwd * REFERRAL_BONUS:.2f}</code>", parse_mode="HTML")

@dp.message(F.text == "💸 WITHDRAW")
async def wd_start(m: Message, state: FSMContext):
    bal = (db("SELECT balance FROM balances WHERE user_id = ?", (m.from_user.id,), fetch="one") or [0.0])[0]
    if bal < MIN_WITHDRAWAL: return await m.answer(f"❌ Balance too low! Bal: <code>${bal:.2f}</code> (Min: <code>${MIN_WITHDRAWAL:.2f}</code>)", parse_mode="HTML")
    await state.set_state(WithdrawalState.waiting_for_details); await m.answer("💵 Send your payment details (Bank/Crypto):", parse_mode="HTML")

@dp.message(WithdrawalState.waiting_for_details)
async def wd_dtls(m: Message, state: FSMContext):
    await state.update_data(details=m.text.strip()); await state.set_state(WithdrawalState.waiting_for_amount)
    await m.answer(f"Enter Amount ($) (Min: <code>${MIN_WITHDRAWAL:.2f}</code>):", parse_mode="HTML")

@dp.message(WithdrawalState.waiting_for_amount)
async def wd_amt(m: Message, state: FSMContext):
    bal = (db("SELECT balance FROM balances WHERE user_id = ?", (m.from_user.id,), fetch="one") or [0.0])[0]
    try: amt = float(m.text.strip())
    except ValueError: return await m.answer("⚠️ Enter valid Amount:")
    if amt < MIN_WITHDRAWAL or amt > bal: return await m.answer(f"⚠️ Amount blow min withdrawal! Max: <code>${bal:.2f}</code>", parse_mode="HTML")
    dtls = (await state.get_data()).get("details"); await state.clear(); await m.answer("✅ Request Submitted!")
    try: await bot.send_message(ADMIN_ID, f"🔔 <b>WITHDRAWAL!</b>\nUser: {m.from_user.first_name} (<code>{m.from_user.id}</code>)\nAmount: <code>${amt:.2f}</code>\nDetails:\n<code>{dtls}</code>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Accept", callback_data=f"wd_accept_{m.from_user.id}_{amt}", style="success"), InlineKeyboardButton(text="❌ Reject", callback_data=f"wd_reject_{m.from_user.id}_{amt}", style="danger")]]))
    except Exception: pass

@dp.message(F.text == "📊 STATUS")
async def status_h(m: Message):
    uid = m.from_user.id
    if use_pg:
        u_cnt = db("SELECT COUNT(*) FROM otp_logs WHERE user_id = ? AND timestamp >= NOW() - INTERVAL '7 days'", (uid,), fetch="one")[0]
        daily = db("SELECT DATE(timestamp), COUNT(*) FROM otp_logs WHERE user_id = ? AND timestamp >= NOW() - INTERVAL '7 days' GROUP BY DATE(timestamp)", (uid,), fetch="all") or []
        top_3 = db("SELECT user_id, COUNT(*) as c FROM otp_logs WHERE timestamp >= NOW() - INTERVAL '7 days' GROUP BY user_id ORDER BY c DESC LIMIT 3", fetch="all") or []
    else:
        u_cnt = db("SELECT COUNT(*) FROM otp_logs WHERE user_id = ? AND timestamp >= datetime('now', '-7 days')", (uid,), fetch="one")[0]
        daily = db("SELECT date(timestamp), COUNT(*) FROM otp_logs WHERE user_id = ? AND timestamp >= datetime('now', '-7 days') GROUP BY date(timestamp)", (uid,), fetch="all") or []
        top_3 = db("SELECT user_id, COUNT(*) as c FROM otp_logs WHERE timestamp >= datetime('now', '-7 days') GROUP BY user_id ORDER BY c DESC LIMIT 3", fetch="all") or []
    medals = ["🥇", "🥈", "🥉"]
    await m.answer(f"📊 <b>STATUS & LEADERBOARD</b>\n\n📱 <b>Total (7d):</b> <code>{u_cnt}</code>\n\n🗓️ <b>Daily:</b>\n" + "".join([f"📅 {d}: <code>{c}</code>\n" for d, c in daily]) + "\n🏆 <b>Top Rank:</b>\n" + "".join([f"{medals[i]}: User <code>{u}</code> — <b>{c} OTPs</b>\n" for i, (u, c) in enumerate(top_3)]), parse_mode="HTML")

async def process_incoming_otp(otp_identifier, phone, body, raw_otp=""):
    exists = await asyncio.to_thread(db, "SELECT 1 FROM otp_logs WHERE otp_identifier = ?", (otp_identifier,), "one")
    if exists:
        return

    otp_code = extract_otp(body, raw_otp)
    clean_num = re.sub(r"\D", "", str(phone))

    if not clean_num:
        return

    row = await asyncio.to_thread(db, "SELECT user_id, service, country, rate FROM assignments WHERE phone_number = ?", (clean_num,), "one")
    uid = row[0] if row else None

    if use_pg:
        await asyncio.to_thread(db, "INSERT INTO otp_logs (otp_identifier, user_id) VALUES (?, ?) ON CONFLICT (otp_identifier) DO NOTHING", (otp_identifier, uid), commit=True)
    else:
        await asyncio.to_thread(db, "INSERT OR IGNORE INTO otp_logs (otp_identifier, user_id) VALUES (?, ?)", (otp_identifier, uid), commit=True)

    srv, cntry, rate = (row[1], row[2], row[3]) if row else ("OTP", "Service", DEFAULT_OTP_RATE)

    grp_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 CHANNEL", url=BACKUP_GROUP_LINK, style="primary"), InlineKeyboardButton(text="💬 CHAT", url=DISCUSSION_GROUP_LINK, style="success")]])
    
    if TELEGRAM_GROUP_ID:
        try:
            await bot.send_message(
                TELEGRAM_GROUP_ID, 
                f"🔥 <b>New OTP Received! 🔥</b>\n\n🌍 <b>Country:</b> {cntry}\n🛒 <b>Service:</b> {srv}\n📱 <b>Number:</b> <code>+{html.escape(mask_phone(phone))}</code>\n🔑 <b>OTP:</b> <code>{html.escape(otp_code)}</code>\n\n✉️ <b>Message:</b>\n<code>{html.escape(body)}</code>", 
                reply_markup=grp_kb, 
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"[GROUP MSG ERROR] {e}")

    if row and uid:
        if use_pg:
            await asyncio.to_thread(db, "INSERT INTO balances (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balances.balance + ?", (uid, rate, rate), commit=True)
        else:
            await asyncio.to_thread(db, "INSERT INTO balances (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?", (uid, rate, rate), commit=True)
        
        ref = await asyncio.to_thread(db, "SELECT referred_by, otp_count, rewarded FROM referrals WHERE user_id = ?", (uid,), "one")
        if ref and ref[2] == 0:
            if ref[1] + 1 >= 3:
                await asyncio.to_thread(db, "UPDATE referrals SET otp_count = 3, rewarded = 1 WHERE user_id = ?", (uid,), commit=True)
                if use_pg:
                    await asyncio.to_thread(db, "INSERT INTO balances (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balances.balance + ?", (ref[0], REFERRAL_BONUS, REFERRAL_BONUS), commit=True)
                else:
                    await asyncio.to_thread(db, "INSERT INTO balances (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?", (ref[0], REFERRAL_BONUS, REFERRAL_BONUS), commit=True)
                try: await bot.send_message(ref[0], f"🎉 Referral Bonus Credited! Earned <b>${REFERRAL_BONUS:.2f}</b>!", parse_mode="HTML")
                except Exception: pass
            else:
                await asyncio.to_thread(db, "UPDATE referrals SET otp_count = otp_count + 1 WHERE user_id = ?", (uid,), commit=True)

        try:
            await bot.send_message(uid, f"🌐 <b>OTP Received ({srv} - {cntry})!</b>\n📱 <code>{html.escape(str(phone))}</code>\n🔑 Code: <code>{html.escape(otp_code)}</code>", parse_mode="HTML")
        except Exception as e:
            print(f"[USER MSG ERROR] {e}")

async def poll_traffic():
    while True:
        try:
            if http_session and not http_session.closed:
                if THIRDWAVE_API_KEY:
                    try:
                        async with http_session.get(f"{THIRDWAVE_BASE_URL}/traffic?page=1&limit=50&pageSize=50", headers={"Authorization": f"Bearer {THIRDWAVE_API_KEY}", "x-api-key": THIRDWAVE_API_KEY, "Accept": "application/json"}) as r:
                            if r.status == 200:
                                res_json = await r.json()
                                rows = res_json.get("rows", []) if isinstance(res_json, dict) else (res_json if isinstance(res_json, list) else [])
                                for item in rows:
                                    phone = str(item.get("destinationNumber") or item.get("phone") or item.get("number") or "")
                                    body = str(item.get("messageBody") or item.get("message") or item.get("text") or "")
                                    raw_otp = str(item.get("otp") or item.get("code") or "")
                                    if not phone or not body: continue
                                    unique_str = f"tw_{phone}_{body.strip()}"
                                    mid = hashlib.md5(unique_str.encode()).hexdigest()
                                    await process_incoming_otp(otp_identifier=mid, phone=phone, body=body, raw_otp=raw_otp)
                    except Exception as tw_e: print(f"[THIRDWAVE ERROR] {tw_e}")

                if IPRN_API_KEY:
                    try:
                        async with http_session.get(f"{IPRN_BASE_URL}/edr?page=1&per_page=50", headers={"Authorization": f"Bearer {IPRN_API_KEY}", "Content-Type": "application/json", "Accept": "application/json"}) as r:
                            if r.status == 200:
                                res_data = await r.json()
                                items = res_data.get("data", []) if isinstance(res_data, dict) else (res_data if isinstance(res_data, list) else [])
                                for item in items:
                                    phone = str(item.get("b_number") or item.get("phone") or "")
                                    body = str(item.get("message") or item.get("text") or "")
                                    if not phone or not body: continue
                                    unique_str = f"iprn_{phone}_{body.strip()}"
                                    mid = hashlib.md5(unique_str.encode()).hexdigest()
                                    await process_incoming_otp(otp_identifier=mid, phone=phone, body=body)
                    except Exception as iprn_e: print(f"[IPRN ERROR] {iprn_e}")
        except Exception as e: print(f"[WORKER ERROR] {e}")
        await asyncio.sleep(1.0)

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
async def health_check(): return {"status": "bot is running"}
