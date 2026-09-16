import os
import asyncio
import aiohttp
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.types import Update

# =============================================================
# CONFIGURATION - PUT YOUR EXACT API KEYS HERE
# =============================================================
BOT_TOKEN = "8815085413:AAEs9NQaPUQivyspR6Tcrts3Jr9PO6O61f0"                   # Replace with your actual Bot Token
RENDER_URL = "https://otp-telegram-bot-fpmp.onrender.com"

# THIRDWAVE API CONFIGURATION
THIRDWAVE_API_KEY = "tw_live_5e1666d46397c359b5ba2eda85b40fdf384c2ffd37ebb519290f358ef4260416"        # Replace with your Thirdwave API Key
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
    headers = {
        "Authorization": f"Bearer {THIRDWAVE_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    # Send request to Thirdwave API
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{THIRDWAVE_BASE_URL}/numbers/allocate", json={"quantity": 1}, headers=headers) as resp:
                status = resp.status
                text_response = await resp.text()
                
                # IF SUCCESS
                if status in [200, 201]:
                    try:
                        res_data = await resp.json()
                        numbers_list = res_data.get("numbers", []) or res_data.get("data", [])
                        if numbers_list:
                            assigned_num = str(numbers_list[0].get("number") if isinstance(numbers_list[0], dict) else numbers_list[0])
                            ASSIGNED_NUMBERS[assigned_num] = {"user_id": user_id}
                            
                            response = (
                                f"🌐 **Number Assigned Successfully!**\n\n"
                                f"📱 Phone Number: `{assigned_num}`\n"
                                f"⏳ **Waiting for OTP...**\n"
                                f"💰 **Per OTP Rate:** `${FLAT_OTP_RATE}`"
                            )
                            await message.answer(response, reply_markup=get_main_menu(), parse_mode="Markdown")
                            return
                        else:
                            await message.answer(f"⚠️ Response received, but no numbers in array:\n`{text_response}`", parse_mode="Markdown")
                            return
                    except Exception as json_err:
                        await message.answer(f"⚠️ JSON Parse Error: {json_err}\nRaw: `{text_response}`", parse_mode="Markdown")
                        return
                else:
                    # DIRECT DIAGNOSTIC ERROR SENT TO TELEGRAM CHAT
                    await message.answer(f"❌ **API Error (Status Code {status}):**\n`{text_response[:300]}`", parse_mode="Markdown")
                    return
    except Exception as err:
        await message.answer(f"❌ **Connection Exception:**\n`{str(err)}`", parse_mode="Markdown")

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
                            
