import os
import asyncio
import aiohttp
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, 
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton,
    ChatMemberUpdated, Update
)
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, IS_NOT_MEMBER, MEMBER

# =============================================================
# CONFIGURATION - CREDENTIALS & API KEYS
# =============================================================
BOT_TOKEN = "8815085413:AAEfnbcABdQbIdiZXSqKM9Tqt7e0VKx_SDY"               # Replace with your Telegram Bot Token
GROUP_CHAT_ID = "-1004315686306"                 # Replace with your Telegram Group ID
ADMIN_CHAT_ID = "7103520365"                      # Replace with your Admin Telegram User ID
GROUP_INVITE_LINK = "https://t.me/lekotpzone"
RENDER_URL = "https://otp-telegram-bot-fpmp.onrender.com"

# THIRDWAVE API CONFIGURATION
THIRDWAVE_API_KEY = "tw_live_5e1666d46397c359b5ba2eda85b40fdf384c2ffd37ebb519290f358ef4260416"        # Your Thirdwave Bearer API Key
THIRDWAVE_BASE_URL = "https://clients.thirdwave.im/api/v1"

# FLAT PAYOUT RATE FOR ALL SERVICES ($0.003)
FLAT_OTP_RATE = 0.003

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# In-memory storage
ASSIGNED_NUMBERS = {}  # {phone_number: {"user_id": user_id, "service": service_name}}
USER_BALANCES = {}     # {user_id: balance_float}
PROCESSED_OTPS = set() # To track and avoid duplicate OTP payouts

# -------------------------------------------------------------
# FSM STATES FOR WITHDRAWAL PROCESS
# -------------------------------------------------------------
class WithdrawalState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_bank_details = State()

# Helper: Check group membership
async def is_user_in_group(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=GROUP_CHAT_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

# Permanent Bottom Keyboard
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
# 1. WELCOME NEW MEMBERS IN GROUP
# -------------------------------------------------------------
@dp.chat_member(ChatMemberUpdatedFilter(member_status_changed=IS_NOT_MEMBER >> MEMBER))
async def on_user_join_group(event: ChatMemberUpdated):
    user_name = event.new_chat_member.user.first_name
    welcome_text = (
        f"🎉 Welcome **{user_name}** to **Lekdigital Number Zone**!\n\n"
        f"Start our bot to get virtual numbers and receive instant payouts."
    )
    bot_me = await bot.get_me()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Start Bot", url=f"https://t.me/{bot_me.username}")]
    ])
    await bot.send_message(chat_id=GROUP_CHAT_ID, text=welcome_text, reply_markup=kb, parse_mode="Markdown")

# -------------------------------------------------------------
# 2. START COMMAND & FORCE SUB CHECK
# -------------------------------------------------------------
@dp.message(F.text == "/start")
async def start_handler(message: Message):
    user_id = message.from_user.id
    
    if not await is_user_in_group(user_id):
        join_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Join Group First", url=GROUP_INVITE_LINK)],
            [InlineKeyboardButton(text="✅ I Have Joined", callback_data="check_join")]
        ])
        await message.answer(
            "⚠️ **Access Denied!**\n\nYou must join **Lekdigital Number Zone** group to use this bot.",
            reply_markup=join_kb,
            parse_mode="Markdown"
        )
        return

    welcome_text = f"👋 Welcome **{message.from_user.first_name}** to **Lekdigital Number Zone**!"
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode="Markdown")

@dp.callback_query(F.data == "check_join")
async def recheck_join(callback: CallbackQuery):
    if await is_user_in_group(callback.from_user.id):
        await callback.message.delete()
        await callback.message.answer("🎉 Verification successful!", reply_markup=get_main_menu())
    else:
        await callback.answer("❌ You haven't joined the group yet!", show_alert=True)

# -------------------------------------------------------------
# 3. GET NUMBER FROM THIRDWAVE API
# -------------------------------------------------------------
@dp.message(F.text == "📱 GET NUMBER")
async def get_number_handler(message: Message):
    user_id = message.from_user.id
    
    headers = {"Authorization": f"Bearer {THIRDWAVE_API_KEY}", "Content-Type": "application/json"}
    payload = {"quantity": 1}  # Default allocation payload
    
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{THIRDWAVE_BASE_URL}/numbers/allocate", json=payload, headers=headers) as resp:
            if resp.status == 200:
                res_data = await resp.json()
                numbers_list = res_data.get("numbers", [])
                
                if numbers_list:
                    assigned_num = str(numbers_list[0].get("number"))
                    ASSIGNED_NUMBERS[assigned_num] = {"user_id": user_id, "service": "General"}

                    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔄 Change Number", callback_data="get_number_handler"), 
                         InlineKeyboardButton(text="🚀 Otp Group", url=GROUP_INVITE_LINK)]
                    ])

                    response = (
                        f"🌐 **Number Assigned Successfully!**\n\n"
                        f"📱 Phone Number: `{assigned_num}`\n"
                        f"⏳ **Waiting for OTP...**\n"
                        f"💰 **Per OTP Rate:** `${FLAT_OTP_RATE}`\n"
                        f"━━━━━━━━━━━━━━━━━━━"
                    )
                    await message.answer(response, reply_markup=inline_kb, parse_mode="Markdown")
                    return

            await message.answer("❌ Failed to fetch a number at the moment. Please try again shortly.")

# -------------------------------------------------------------
# 4. BALANCE & WITHDRAWAL SYSTEM
# -------------------------------------------------------------
@dp.message(F.text == "💰 BALANCE")
async def balance_handler(message: Message):
    user_id = message.from_user.id
    balance = USER_BALANCES.get(user_id, 0.0)
    await message.answer(f"👤 **User ID:** `{user_id}`\n💵 **Balance:** `${balance:.4f}`", parse_mode="Markdown")

@dp.message(F.text == "💸 WITHDRAW")
async def start_withdrawal(message: Message, state: FSMContext):
    user_id = message.from_user.id
    balance = USER_BALANCES.get(user_id, 0.0)
    
    if balance <= 0:
        await message.answer(f"❌ Your balance is `${balance:.4f}`. Insufficient funds to withdraw.")
        return

    await state.set_state(WithdrawalState.waiting_for_amount)
    await message.answer(f"💵 Available balance: `${balance:.4f}`\n\nEnter the amount you want to withdraw:")

@dp.message(WithdrawalState.waiting_for_amount)
async def process_amount(message: Message, state: FSMContext):
    user_id = message.from_user.id
    balance = USER_BALANCES.get(user_id, 0.0)
    
    try:
        amount = float(message.text)
        if amount <= 0 or amount > balance:
            await message.answer("❌ Invalid amount within balance limit.")
            return
    except ValueError:
        await message.answer("❌ Please enter a valid number.")
        return

    await state.update_data(amount=amount)
    await state.set_state(WithdrawalState.waiting_for_bank_details)
    await message.answer("🏦 Send your Bank Name, Account Number, and Account Name:")

@dp.message(WithdrawalState.waiting_for_bank_details)
async def process_bank_details(message: Message, state: FSMContext):
    user_data = await state.get_data()
    amount = user_data['amount']
    bank_details = message.text
    user = message.from_user

    await message.answer("✅ Withdrawal request submitted! Awaiting admin approval.")
    await state.clear()

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Accept", callback_data=f"wd_accept_{user.id}_{amount}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"wd_reject_{user.id}_{amount}")
        ]
    ])

    admin_msg = (
        f"🚨 **NEW WITHDRAWAL REQUEST**\n\n"
        f"👤 User: {user.first_name} (`{user.id}`)\n"
        f"💰 Amount: `${amount}`\n"
        f"🏦 Details: {bank_details}"
    )
    await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_msg, reply_markup=admin_kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("wd_"))
async def handle_admin_decision(callback: CallbackQuery):
    data_parts = callback.data.split("_")
    action = data_parts[1]
    user_id = int(data_parts[2])
    amount = float(data_parts[3])

    if action == "accept":
        USER_BALANCES[user_id] = USER_BALANCES.get(user_id, 0.0) - amount
        await bot.send_message(chat_id=user_id, text=f"🎉 Withdrawal request for **${amount}** ACCEPTED!")
        await callback.message.edit_text(callback.message.text + "\n\n✅ **STATUS: ACCEPTED**")
    else:
        await bot.send_message(chat_id=user_id, text=f"❌ Withdrawal request for **${amount}** REJECTED.")
        await callback.message.edit_text(callback.message.text + "\n\n❌ **STATUS: REJECTED**")

    await callback.answer()

# -------------------------------------------------------------
# 5. BACKGROUND POLLING FOR THIRDWAVE TRAFFIC & OTPs
# -------------------------------------------------------------
async def poll_thirdwave_traffic():
    headers = {"Authorization": f"Bearer {THIRDWAVE_API_KEY}"}
    bot_info = await bot.get_me()
    
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
                            service_name = str(item.get("service", "General")).strip().capitalize()
                            country_name = str(item.get("country", "Global")).strip()

                            if not otp_code:
                                continue

                            PROCESSED_OTPS.add(msg_id)

                            # 1. Private DM Notification
                            number_info = ASSIGNED_NUMBERS.get(phone_number)
                            if number_info:
                                u_id = number_info["user_id"]
                                USER_BALANCES[u_id] = USER_BALANCES.get(u_id, 0.0) + FLAT_OTP_RATE
                                
                                p_msg = (
                                    f"🌐 **#{service_name.upper()} OTP Received!**\n\n"
                                    f"📍 Country: {country_name}\n"
                                    f"📱 Number: `{phone_number}`\n"
                                    f"🔑 Code: `{otp_code}`\n"
                                    f"💰 Credited: +${FLAT_OTP_RATE}"
                                )
                                try:
                                    await bot.send_message(chat_id=u_id, text=p_msg, parse_mode="Markdown")
                                except Exception as e:
                                    print(f"Error sending DM: {e}")

                            # 2. Public Group Broadcast
                            g_msg = (
                                f"🌐 **Temp number ({country_name})**\n"
                                f"🟢 {service_name} | `{phone_number}`\n"
                                f"💰 Rate: `${FLAT_OTP_RATE}` per OTP\n\n"
                                f"**OTP Code:** `{otp_code}`"
                            )
                            g_kb = InlineKeyboardMarkup(inline_keyboard=[
                                [
                                    InlineKeyboardButton(text="📢 Channel", url=GROUP_INVITE_LINK),
                                    InlineKeyboardButton(text=f"🛡️ {otp_code}", callback_data="copy_otp")
                                ],
                                [InlineKeyboardButton(text="📞 Get Number", url=f"https://t.me/{bot_info.username}")]
                            ])
                            try:
                                await bot.send_message(chat_id=GROUP_CHAT_ID, text=g_msg, reply_markup=g_kb, parse_mode="Markdown")
                            except Exception as e:
                                print(f"Error posting group message: {e}")

        except Exception as e:
            print(f"Traffic Polling Error: {e}")

        await asyncio.sleep(5)  # Poll every 5 seconds

# -------------------------------------------------------------
# 6. FASTAPI WEBHOOK & LIFECYCLE
# -------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    # Register Webhook dynamically on startup
    webhook_url = f"{RENDER_URL}/telegram-webhook"
    await bot.set_webhook(webhook_url)
    asyncio.create_task(poll_thirdwave_traffic())

@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"status": "ok"}

@app.get("/")
async def health_check():
    return {"status": "bot online"}
