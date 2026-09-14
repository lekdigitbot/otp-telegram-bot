import os
import requests
from fastapi import FastAPI, Request, HTTPException
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, 
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton,
    ChatMemberUpdated
)
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, IS_NOT_MEMBER, MEMBER

# =============================================================
# CONFIGURATION - REPLACE WITH YOUR REAL DETAILS
# =============================================================
BOT_TOKEN = "8815085413:AAEfnbcABdQbIdiZXSqKM9Tqt7e0VKx_SDY"
GROUP_CHAT_ID = "-1004315686306"         # Your Telegram Group ID
ADMIN_CHAT_ID = "7103520365"              # Your personal Telegram User ID
SITE_API_KEY = "tw_live_5e1666d46397c359b5ba2eda85b40fdf384c2ffd37ebb519290f358ef4260416"
GROUP_INVITE_LINK = "https://t.me/lekotpzone"

# FLAT PAYOUT RATE FOR ALL SERVICES ($0.003)
FLAT_OTP_RATE = 0.003

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# In-memory storage (Replace with database for long-term storage)
ASSIGNED_NUMBERS = {}
USER_BALANCES = {}

# -------------------------------------------------------------
# FSM STATES FOR WITHDRAWAL PROCESS
# -------------------------------------------------------------
class WithdrawalState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_bank_details = State()

# Helper: Check if user is in group
async def is_user_in_group(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=GROUP_CHAT_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

# Permanent Bottom Menu
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Start Bot", url=f"https://t.me/{(await bot.get_me()).username}")]
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
# 3. GET NUMBER BUTTON
# -------------------------------------------------------------
@dp.message(F.text == "📱 GET NUMBER")
async def get_number_handler(message: Message):
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Change Number", callback_data="change_num"), 
         InlineKeyboardButton(text="🌐 Change Country", callback_data="change_country")],
        [InlineKeyboardButton(text="🚫 Remove Country Code", callback_data="remove_code")],
        [InlineKeyboardButton(text="🚀 Otp Group", url=GROUP_INVITE_LINK)],
        [InlineKeyboardButton(text="🔙 Back", callback_data="go_back")]
    ])

    response = (
        "🌐 **Number Assigned:**\n\n"
        "⏳ **Waiting for OTP...**\n"
        f"**Per OTP Rate:** `${FLAT_OTP_RATE}`\n"
        "━━━━━━━━━━━━━━━━━━━"
    )
    await message.answer(response, reply_markup=inline_kb, parse_mode="Markdown")

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
# 5. DYNAMIC WEBHOOK ENDPOINT (ALL COUNTRIES, SERVICES & TAP-TO-COPY)
# -------------------------------------------------------------
@app.get("/")
async def root():
    return {"status": "online"}

@app.post("/api/incoming-otp")
async def handle_incoming_otp(request: Request):
    if request.headers.get("X-API-KEY") != SITE_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await request.json()
    phone_number = data.get("phone_number", "").strip()
    otp_code = data.get("otp", "").strip()
    service_name = data.get("service", "General").strip().capitalize()
    country_name = data.get("country", "Global").strip()

    if not phone_number or not otp_code:
        raise HTTPException(status_code=400, detail="Missing phone_number or otp parameters")

    # 1. DIRECT USER PAYOUT NOTIFICATION (TAP-TO-COPY NUMBER & CODE)
    number_info = ASSIGNED_NUMBERS.get(phone_number)
    if number_info:
        user_id = number_info["user_id"]
        USER_BALANCES[user_id] = USER_BALANCES.get(user_id, 0.0) + FLAT_OTP_RATE
        
        private_msg = (
            f"🌐 **#{service_name.upper()} OTP Received!**\n\n"
            f"📍 Country: {country_name}\n"
            f"📱 Number: `{phone_number}`\n"
            f"🔑 Code: `{otp_code}`\n"
            f"💰 Credited: +${FLAT_OTP_RATE}"
        )
        try:
            await bot.send_message(chat_id=user_id, text=private_msg, parse_mode="Markdown")
        except Exception as e:
            print(f"Error sending DM: {e}")

    # 2. PUBLIC GROUP BROADCAST (TAP-TO-COPY NUMBER & CODE)
    group_msg = (
        f"🌐 **Temp number ({country_name})**\n"
        f"🟢 {service_name} | `{phone_number}`\n"
        f"💰 Rate: `${FLAT_OTP_RATE}` per OTP\n\n"
        f"**OTP Code:** `{otp_code}`"
    )
    
    bot_username = (await bot.get_me()).username
    group_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📢 Channel", url=GROUP_INVITE_LINK),
            InlineKeyboardButton(text=f"🛡️ {otp_code}", callback_data="copy_otp")
        ],
        [
            InlineKeyboardButton(text="📞 Get Number", url=f"https://t.me/{bot_username}")
        ]
    ])

    try:
        await bot.send_message(chat_id=GROUP_CHAT_ID, text=group_msg, reply_markup=group_kb, parse_mode="Markdown")
    except Exception as e:
        print(f"Error posting to group: {e}")

    return {"status": "success", "country": country_name, "service": service_name, "rate": FLAT_OTP_RATE}
  
