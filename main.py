import os
import re
import asyncio
import aiohttp
import html
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Update

# =============================================================
# CONFIGURATION
# =============================================================
BOT_TOKEN = "8815085413:AAEs9NQaPUQivyspR6Tcrts3Jr9PO6O61f0"                   # Replace with your Telegram Bot Token
RENDER_URL = "https://otp-telegram-bot-fpmp.onrender.com"

ADMIN_ID = 7103520365                            # Your Telegram User ID
TELEGRAM_GROUP_ID = -1004315686306               # Must be an integer starting with -100!

THIRDWAVE_API_KEY = "tw_live_5e1666d46397c359b5ba2eda85b40fdf384c2ffd37ebb519290f358ef4260416"        # Replace with your Thirdwave API Key
THIRDWAVE_BASE_URL = "https://clients.thirdwave.im/api/v1"

FLAT_OTP_RATE = 0.003

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# In-memory database structures
AVAILABLE_NUMBERS = {}      # Format: {"WhatsApp": ["234701...", "234702..."]}
ASSIGNED_NUMBERS = {}       # Format: {phone_number: {"user_id": 12345, "service": "WhatsApp"}}
USER_BALANCES = {}          # Format: {user_id: balance}
PROCESSED_OTPS = set()       # Processed OTP IDs

def extract_otp_code(body: str, fallback_otp: str = "") -> str:
    """Extract 4 to 8 digit code from message text if API returns empty OTP."""
    if fallback_otp and str(fallback_otp).strip() and str(fallback_otp) != "None":
        return str(fallback_otp).strip()
    
    match = re.search(r'\b\d{4,8}\b', body)
    if match:
        return match.group(0)
    return "No Code"

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

    lines = message.text.strip().split("\n")
    header_parts = lines[0].replace("/addnumber", "").strip().split(maxsplit=1)
    
    if not header_parts:
        await message.answer("⚠️ <b>Usage:</b> <code>/addnumber Bolt 2348052278526, 2347010747834</code>", parse_mode="HTML")
        return

    service_name = header_parts[0].capitalize()
    raw_numbers = []

    if len(header_parts) > 1:
        raw_numbers.extend(header_parts[1].split(","))

    if len(lines) > 1:
        for line in lines[1:]:
            raw_numbers.extend(line.split(","))

    new_numbers = [num.strip() for num in raw_numbers if num.strip()]

    if not new_numbers:
        await message.answer("⚠️ No valid numbers found.", parse_mode="HTML")
        return

    if service_name not in AVAILABLE_NUMBERS:
        AVAILABLE_NUMBERS[service_name] = []
    
    AVAILABLE_NUMBERS[service_name].extend(new_numbers)

    await message.answer(
        f"✅ <b>Added {len(new_numbers)} number(s) to {service_name}!</b>\n"
        f"📦 <b>Total Stock:</b> <code>{len(AVAILABLE_NUMBERS[service_name])}</code>",
        parse_mode="HTML"
    )

    group_announcement = (
        f"📢 <b>NEW STOCK UPDATE!</b> 📢\n\n"
        f"🔹 <b>Service:</b> {service_name}\n"
        f"📱 <b>New Numbers Added:</b> <code>{len(new_numbers)}</code>\n"
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
    active_services = {srv: nums for srv, nums in AVAILABLE_NUMBERS.items() if len(nums) >= 2}

    if not active_services:
        await message.answer("⚠️ <b>Out of Stock!</b> No services have at least 2 numbers available right now.", reply_markup=get_main_menu(), parse_mode="HTML")
        return

    buttons = []
    for srv, nums in active_services.items():
        buttons.append([InlineKeyboardButton(text=f"🔹 {srv} ({len(nums)} in stock)", callback_data=f"get_{srv}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("📲 <b>Select a service to get 2 numbers:</b>", reply_markup=keyboard, parse_mode="HTML")

@dp.callback_query(F.data.startswith("get_"))
async def process_service_selection(callback: CallbackQuery):
    # Answer immediately to stop the loading spinner instantly
    await callback.answer()

    service_name = callback.data.replace("get_", "")
    user_id = callback.from_user.id

    if service_name not in AVAILABLE_NUMBERS or len(AVAILABLE_NUMBERS[service_name]) < 2:
        await callback.message.answer(
            f"⚠️ <b>Out of Stock!</b> Not enough numbers available for <b>{service_name}</b>.",
            parse_mode="HTML"
        )
        return

    try:
        assigned_1 = AVAILABLE_NUMBERS[service_name].pop(0)
        assigned_2 = AVAILABLE_NUMBERS[service_name].pop(0)

        ASSIGNED_NUMBERS[assigned_1] = {"user_id": user_id, "service": service_name}
        ASSIGNED_NUMBERS[assigned_2] = {"user_id": user_id, "service": service_name}

        response = (
            f"🌐 <b>2 Numbers Assigned Successfully!</b>\n\n"
            f"🔹 <b>Service:</b> {service_name}\n"
            f"📱 <b>Number 1:</b> <code>{assigned_1}</code>\n"
            f"📱 <b>Number 2:</b> <code>{assigned_2}</code>\n\n"
            f"⏳ <b>Waiting for OTPs...</b>\n"
            f"💰 <b>Rate:</b> <code>${FLAT_OTP_RATE}</code> / OTP\n\n"
            f"📢 <i>All incoming OTPs stream directly to our main group!</i>"
        )

        await callback.message.answer(response, parse_mode="HTML")

    except Exception as err:
        print(f"[ERROR] Service selection failed: {err}")
        await callback.message.answer("❌ An error occurred while assigning numbers. Please try again.")

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
    balance = USER_BALANCES.get(user_id, 0.0)
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
                            if phone_number in ASSIGNED_NUMBERS:
                                user_info = ASSIGNED_NUMBERS[phone_number]
                                u_id = user_info["user_id"]
                                srv = user_info["service"]
                                USER_BALANCES[u_id] = USER_BALANCES.get(u_id, 0.0) + FLAT_OTP_RATE
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
        
