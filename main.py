import os
import asyncio
import aiohttp
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# =============================================================
# CONFIGURATION - EDIT YOUR CREDENTIALS HERE
# =============================================================
BOT_TOKEN = "8815085413:AAEs9NQaPUQivyspR6Tcrts3Jr9PO6O61f0"                   # Replace with your Telegram Bot Token
GROUP_CHAT_ID = "-1004315686306"                 # Replace with your Telegram Group ID
ADMIN_CHAT_ID = "7103520365"                      # Replace with your Admin Telegram User ID
RENDER_URL = "https://otp-telegram-bot-fpmp.onrender.com"

# THIRDWAVE API CONFIGURATION
THIRDWAVE_API_KEY = "tw_live_5e1666d46397c359b5ba2eda85b40fdf384c2ffd37ebb519290f358ef4260416"        # Your Thirdwave Bearer API Key
THIRDWAVE_BASE_URL = "https://clients.thirdwave.im/api/v1"

FLAT_OTP_RATE = 0.003

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# In-memory storage
ASSIGNED_NUMBERS = {}
USER_BALANCES = {}
PROCESSED_OTPS = set()

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
# TELEGRAM BOT HANDLERS
# -------------------------------------------------------------
@dp.message(F.text == "/start")
async def start_handler(message: Message):
    welcome_text = f"👋 Welcome **{message.from_user.first_name}** to **Lekdigital Number Zone**!"
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode="Markdown")

@dp.message(F.text == "📱 GET NUMBER")
async def get_number_handler(message: Message):
    user_id = message.from_user.id
    headers = {"Authorization": f"Bearer {THIRDWAVE_API_KEY}", "Content-Type": "application/json"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{THIRDWAVE_BASE_URL}/numbers/allocate", json={"quantity": 1}, headers=headers) as resp:
                if resp.status == 200:
                    res_data = await resp.json()
                    numbers_list = res_data.get("numbers", [])
                    if numbers_list:
                        assigned_num = str(numbers_list[0].get("number"))
                        ASSIGNED_NUMBERS[assigned_num] = {"user_id": user_id}
                        
                        response = (
                            f"🌐 **Number Assigned Successfully!**\n\n"
                            f"📱 Phone Number: `{assigned_num}`\n"
                            f"⏳ **Waiting for OTP...**\n"
                            f"💰 **Per OTP Rate:** `${FLAT_OTP_RATE}`"
                        )
                        await message.answer(response, reply_markup=get_main_menu(), parse_mode="Markdown")
                        return
    except Exception as e:
        print(f"Error fetching number: {e}")
        
    await message.answer("❌ Service busy or out of stock. Try again shortly!")

@dp.message(F.text == "💰 BALANCE")
async def balance_handler(message: Message):
    user_id = message.from_user.id
    balance = USER_BALANCES.get(user_id, 0.0)
    await message.answer(f"👤 **User ID:** `{user_id}`\n💵 **Balance:** `${balance:.4f}`", parse_mode="Markdown")

# -------------------------------------------------------------
# THIRDWAVE BACKGROUND TRAFFIC WORKER
# -------------------------------------------------------------
async def poll_thirdwave_traffic():
    headers = {"Authorization": f"Bearer {THIRDWAVE_API_KEY}"}
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{THIRDWAVE_BASE_URL}/traffic", headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        rows = data.get("rows", [])
                        for item in rows:
                            msg_id = item.get("id")
                            if msg_id in PROCESSED_OTPS:
                                continue
                            
                            phone_number = str(item.get("number", "")).strip()
                            otp_code = str(item.get("otp", "")).strip()
                            if not otp_code:
                                continue

                            PROCESSED_OTPS.add(msg_id)
                            number_info = ASSIGNED_NUMBERS.get(phone_number)
                            
                            if number_info:
                                u_id = number_info["user_id"]
                                USER_BALANCES[u_id] = USER_BALANCES.get(u_id, 0.0) + FLAT_OTP_RATE
                                await bot.send_message(
                                    chat_id=u_id, 
                                    text=f"🌐 **OTP Received!**\n📱 `{phone_number}`\n🔑 Code: `{otp_code}`", 
                                    parse_mode="Markdown"
                                )
        except Exception:
            pass
        await asyncio.sleep(5)

# -------------------------------------------------------------
# FASTAPI LIFESPAN & WEBHOOK SETUP
# -------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Register webhooks automatically on both root and subpath to stop 405 errors
    webhook_url = f"{RENDER_URL}/telegram-webhook"
    await bot.set_webhook(webhook_url, drop_pending_updates=True)
    polling_task = asyncio.create_task(poll_thirdwave_traffic())
    yield
    polling_task.cancel()
    await bot.delete_webhook()

app = FastAPI(lifespan=lifespan)

# Handle requests on both paths to catch all Telegram updates
@app.post("/")
@app.post("/telegram-webhook")
async def process_telegram_update(request: Request):
    data = await request.json()
    from aiogram.types import Update
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"status": "ok"}

@app.get("/")
async def health_check():
    return {"status": "bot is running"}
    
