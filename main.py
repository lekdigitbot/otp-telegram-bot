import os
import asyncio
import aiohttp
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, Update

# =============================================================
# CONFIGURATION - PUT YOUR EXACT CREDENTIALS HERE
# =============================================================
BOT_TOKEN = "8815085413:AAEs9NQaPUQivyspR6Tcrts3Jr9PO6O61f0"                   # Replace with your actual Telegram Bot Token
RENDER_URL = "https://otp-telegram-bot-fpmp.onrender.com"

# THIRDWAVE API CONFIGURATION
THIRDWAVE_API_KEY = "tw_live_5e1666d46397c359b5ba2eda85b40fdf384c2ffd37ebb519290f358ef4260416"        # Replace with your actual Thirdwave API Key
THIRDWAVE_BASE_URL = "https://clients.thirdwave.im/api/v1"

FLAT_OTP_RATE = 0.003

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# In-memory storage
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
    """Fetches active ranges from Thirdwave's /access-list endpoint."""
    headers = {
        "Authorization": f"Bearer {THIRDWAVE_API_KEY}",
        "Accept": "application/json"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{THIRDWAVE_BASE_URL}/access-list", headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Expecting a list or dict of available ranges
                    items = data if isinstance(data, list) else data.get("rows", [])
                    
                    if items:
                        text = "📱 **Available Shared Ranges & Sender IDs:**\n\n"
                        for item in items[:10]:  # Limit output length
                            range_name = item.get("rangeName", "Unknown")
                            source = item.get("sourceAddress", "All")
                            template = item.get("rangeTemplate", "N/A")
                            rate = item.get("rate", FLAT_OTP_RATE)
                            text += f"🔹 **{range_name}** ({source})\n    Format: `{template}` | Rate: `${rate}`\n\n"
                        
                        text += "💡 *Use these active number patterns to receive SMS. Tap 🔴 LIVE TRAFFIC to view incoming OTPs!*"
                        await message.answer(text, reply_markup=get_main_menu(), parse_mode="Markdown")
                        return
                    else:
                        await message.answer("⚠️ No active ranges found right now.", reply_markup=get_main_menu())
                        return
                else:
                    raw_err = await resp.text()
                    await message.answer(f"❌ **API Error ({resp.status}):**\n`{raw_err[:200]}`", parse_mode="Markdown")
                    return
    except Exception as err:
        await message.answer(f"❌ **Connection Error:** `{str(err)}`", parse_mode="Markdown")

@dp.message(F.text == "🔴 LIVE TRAFFIC")
async def live_traffic_handler(message: Message):
    """Fetches latest incoming messages from Thirdwave's /traffic endpoint."""
    headers = {
        "Authorization": f"Bearer {THIRDWAVE_API_KEY}",
        "Accept": "application/json"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{THIRDWAVE_BASE_URL}/traffic?pageSize=5", headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rows = data.get("rows", [])
                    
                    if rows:
                        text = "🔴 **Recent Live OTP Traffic:**\n\n"
                        for item in rows[:5]:
                            dest = item.get("destinationNumber", "N/A")
                            otp = item.get("otp", "No Code")
                            body = item.get("messageBody", "")
                            text += f"📱 **Num:** `{dest}`\n🔑 **OTP:** `{otp}`\n💬 `{body[:50]}`\n───\n"
                        await message.answer(text, reply_markup=get_main_menu(), parse_mode="Markdown")
                        return
                    else:
                        await message.answer("ℹ️ No recent traffic found.", reply_markup=get_main_menu())
                        return
                else:
                    raw_err = await resp.text()
                    await message.answer(f"❌ **Traffic API Error ({resp.status}):**\n`{raw_err[:200]}`", parse_mode="Markdown")
                    return
    except Exception as err:
        await message.answer(f"❌ **Connection Error:** `{str(err)}`", parse_mode="Markdown")

@dp.message(F.text == "💰 BALANCE")
async def balance_handler(message: Message):
    user_id = message.from_user.id
    balance = USER_BALANCES.get(user_id, 0.0)
    await message.answer(f"👤 **User ID:** `{user_id}`\n💵 **Balance:** `${balance:.4f}`", parse_mode="Markdown")

# -------------------------------------------------------------
# FASTAPI WEB SERVER SETUP
# -------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    webhook_url = f"{RENDER_URL}/telegram-webhook"
    await bot.set_webhook(webhook_url, drop_pending_updates=True)
    yield
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
