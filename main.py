import os
import asyncio
import aiohttp
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, Update

# =============================================================
# CONFIGURATION - UPDATE YOUR DETAILS HERE
# =============================================================
BOT_TOKEN = "8815085413:AAEs9NQaPUQivyspR6Tcrts3Jr9PO6O61f0"                   # Replace with your Telegram Bot Token
RENDER_URL = "https://otp-telegram-bot-fpmp.onrender.com"

# ADMIN & GROUP CONFIGURATION
ADMIN_ID = 7103520365                            # Your Telegram User ID
TELEGRAM_GROUP_ID = -1004315686306               # Must be an integer! Supergroup/Channel IDs start with -100

# THIRDWAVE API CONFIGURATION
THIRDWAVE_API_KEY = "tw_live_5e1666d46397c359b5ba2eda85b40fdf384c2ffd37ebb519290f358ef4260416"        # Replace with your Thirdwave API Key
THIRDWAVE_BASE_URL = "https://clients.thirdwave.im/api/v1"

FLAT_OTP_RATE = 0.003

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# In-memory storage
AVAILABLE_NUMBERS = []       # Pool of numbers added by Admin
ASSIGNED_NUMBERS = {}        # {phone_number: user_id}
USER_BALANCES = {}           # {user_id: balance}
PROCESSED_OTPS = set()       # Processed message IDs

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
# ADMIN HANDLERS
# -------------------------------------------------------------
@dp.message(F.text.startswith("/addnumber"))
async def add_number_admin(message: Message):
    """Admin command to stock numbers. Usage: /addnumber 923001234567, 923007654321"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ <b>Unauthorized:</b> Admin only command.", parse_mode="HTML")
        return

    raw_input = message.text.replace("/addnumber", "").strip()
    if not raw_input:
        await message.answer("⚠️ <b>Usage:</b> <code>/addnumber 923001234567, 923007654321</code>", parse_mode="HTML")
        return

    new_numbers = [num.strip() for num in raw_input.split(",") if num.strip()]
    AVAILABLE_NUMBERS.extend(new_numbers)
    
    await message.answer(
        f"✅ <b>Added {len(new_numbers)} number(s)!</b>\n"
        f"📦 <b>Total numbers in stock:</b> <code>{len(AVAILABLE_NUMBERS)}</code>",
        parse_mode="HTML"
    )

# -------------------------------------------------------------
# TELEGRAM BOT HANDLERS
# -------------------------------------------------------------
@dp.message(F.text == "/start")
async def start_handler(message: Message):
    welcome_text = f"👋 Welcome <b>{message.from_user.first_name}</b> to <b>Lekdigital Number Zone</b>!"
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode="HTML")

@dp.message(F.text == "📱 GET NUMBER")
async def get_number_handler(message: Message):
    """Assigns a number from the Admin pool to the requesting user."""
    user_id = message.from_user.id
    
    if not AVAILABLE_NUMBERS:
        await message.answer("⚠️ <b>Out of Stock!</b> No numbers available right now. Try again later.", reply_markup=get_main_menu(), parse_mode="HTML")
        return

    assigned_num = AVAILABLE_NUMBERS.pop(0)
    ASSIGNED_NUMBERS[assigned_num] = user_id

    response = (
        f"🌐 <b>Number Assigned Successfully!</b>\n\n"
        f"📱 <b>Phone Number:</b> <code>{assigned_num}</code>\n"
        f"⏳ <b>Waiting for OTP...</b>\n"
        f"💰 <b>Per OTP Rate:</b> <code>${FLAT_OTP_RATE}</code>\n\n"
        f"📢 <i>All incoming OTPs stream directly to our official group!</i>"
    )
    await message.answer(response, reply_markup=get_main_menu(), parse_mode="HTML")

@dp.message(F.text == "🔴 LIVE TRAFFIC")
async def live_traffic_handler(message: Message):
    """Displays the 5 most recent OTPs to the user."""
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
                            dest = item.get("destinationNumber", "N/A")
                            otp = item.get("otp", "No Code")
                            body = item.get("messageBody", "")
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
        await message.answer(f"❌ <b>Connection Error:</b> <code>{str(err)}</code>", parse_mode="HTML")

@dp.message(F.text == "💰 BALANCE")
async def balance_handler(message: Message):
    user_id = message.from_user.id
    balance = USER_BALANCES.get(user_id, 0.0)
    await message.answer(f"👤 <b>User ID:</b> <code>{user_id}</code>\n💵 <b>Balance:</b> <code>${balance:.4f}</code>", parse_mode="HTML")

# -------------------------------------------------------------
# BACKGROUND WORKER: FORWARDS ALL OTPS TO TELEGRAM GROUP
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
                            otp_code = str(item.get("otp", "")).strip()
                            body = item.get("messageBody", "")

                            if not otp_code:
                                continue

                            PROCESSED_OTPS.add(msg_id)

                            # 1. Forward to Telegram Group using HTML
                            group_msg = (
                                f"🔥 <b>NEW OTP RECEIVED!</b> 🔥\n\n"
                                f"📱 <b>Number:</b> <code>{phone_number}</code>\n"
                                f"🔑 <b>OTP Code:</b> <code>{otp_code}</code>\n"
                                f"💬 <b>Message:</b> <code>{body}</code>"
                            )
                            try:
                                await bot.send_message(chat_id=TELEGRAM_GROUP_ID, text=group_msg, parse_mode="HTML")
                                print(f"[SUCCESS] OTP sent to Group {TELEGRAM_GROUP_ID}")
                            except Exception as group_err:
                                print(f"[ERROR] Group Forwarding Failed: {group_err}")

                            # 2. Update user balance if assigned
                            if phone_number in ASSIGNED_NUMBERS:
                                u_id = ASSIGNED_NUMBERS[phone_number]
                                USER_BALANCES[u_id] = USER_BALANCES.get(u_id, 0.0) + FLAT_OTP_RATE
                                try:
                                    await bot.send_message(
                                        chat_id=u_id,
                                        text=f"🌐 <b>Your OTP Received!</b>\n📱 <code>{phone_number}</code>\n🔑 Code: <code>{otp_code}</code>",
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
    
