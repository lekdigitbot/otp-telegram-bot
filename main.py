import asyncio
import logging
import random
import re
import httpx
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# Configuration
BOT_TOKEN = "8815085413:AAHafS-X17wdy2a7FiJzw-jpualdeIetRYc"
ADMIN_CHAT_ID = 7103520365

# 📢 Central Group Chat ID for all OTP drops
OTP_GROUP_CHAT_ID = -1004315686306

# ⚠️ PASTE YOUR FULL UNMASKED API KEY HERE
THIRDWAVE_API_KEY = "tw_live_5e1666d46397c359b5ba2eda85b40fdf384c2ffd37ebb519290f358ef4260416"
THIRDWAVE_BASE_URL = "https://clients.thirdwave.im"

WAITING_FOR_BANK_DETAILS = 1

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

user_balances = {}
active_user_sessions = {}  # Tracks {user_id: {"chat_id": chat_id, "numbers": [...]}}
seen_sms_ids = set()      # Prevents double-processing the same OTP

NUMBER_INVENTORY = {
    "🇮🇶 Iraq": {
        "🍕 Talabat": [
            "9647779310223", "9647779314601", "9647779319334", "9647779310639", "9647779310062",
            "9647779319284", "9647779316918", "9647779313922", "9647779310842", "9647779310106",
            "9647779311658", "9647779311789", "9647779315042", "9647779311906", "9647779314244",
            "9647779317183", "9647779319503", "9647779311996", "9647779318376", "9647779316175"
        ]
    },
    "🇿🇼 Zimbabwe": {
        "⚡ Bolt": [
            "263789759098", "263789060662", "263789623481", "263789776652", "263789045983",
            "263789419486", "263789174501", "263789811491", "263789180662", "263789326994",
            "263789526742", "263789495171", "26378990332", "263789638646"
        ]
    },
    "🇽🇰 Kosovo": {
        "⚡ Bolt": [
            "38345384829", "38345351529", "38345334490", "38345389732", "38345348894",
            "383453917", "38345361704", "38345313811", "38345355381", "38345322156",
            "38345331166", "38345304477", "38345341181", "38345360208", "38345395194",
            "38345311352", "38345345110", "38345323551", "38345397530", "38345314002",
            "38345391029", "38345367506", "38345392579", "38345336696", "38345391744",
            "38345328804", "38345372555", "38345321606", "38345316016", "38345344931",
            "38345386245", "38345369253", "38345345883", "38345367817", "38345338902",
            "38345355437", "38345398382", "38345361566", "38345355842", "38345310155",
            "38345322327", "38345324640", "38345347267", "38345327200", "38345375881",
            "38345331258", "38345320180", "38345395724", "38345323285", "38345353709",
            "38345391163"
        ]
    },
    "🇳🇬 Nigeria": {
        "⚡ Bolt": [
            "2348181452348", "2348187643800", "2348183594062", "2348183780422", "2348183428358",
            "2348185044740", "2348186204908", "2348180535680", "2348182952785", "2348188845315",
            "2348189635864", "2348185740451", "2348188674728", "2348185559194", "2348183666568",
            "2348181052998", "2348181610765", "2348186333898", "2348184931279"
        ],
        "🔥 Tinder": [
            "2348083926782", "2348080337105", "2348084209101", "2348081111592", "2348081130205",
            "2348083563337", "2348088777573", "2348086160640", "2348085827378", "2348088137785",
            "2348082595862", "2348086703880", "2348086061218", "2348082006731", "2348080769076",
            "2348082869810", "2348085926696", "2348089887170", "2348081991774", "2348088334121",
            "2348085648448", "2348081368011", "2348087624495", "2348085081650", "2348084300985",
            "2348085463571", "2348087907275", "2348082550343", "2348086429467", "2348083187450",
            "2348081940566", "2348085365066", "2348081421885", "2348084889974", "2348087301113",
            "2348088542224", "2348080382741", "2348089323741", "2348084073299", "2348086589265",
            "2348085606910", "2348086449330", "2348085057405", "2348085699788", "2348085748642",
            "2348089453705", "2348088595749", "2348082174553", "2348082461457", "2348082944666",
            "2348083468939", "2348087714761", "2348080810322", "2348084391932", "2348088002606",
            "2348085957995", "2348080194422", "2348086475351", "2348083390436", "2348087195007",
            "2348085203235", "2348084478777", "2348080153661", "2348088771613", "2348081353860",
            "2348089200320", "2348089299384", "2348083587033", "2348087009556", "2348085046604",
            "2348081708497", "2348080047684", "2348081312469", "2348089736947", "2348083657958",
            "2348081397448", "2348088448568", "2348087665511", "2348089013590", "2348082380181",
            "2348080874736", "2348080440499", "2348084914632", "2348089017763", "2348081846974",
            "2348089902229", "2348086332779", "2348082037211", "2348086553893", "2348081734165",
            "2348083659924"
        ]
    },
    "🇦🇿 Azerbaijan": {
        "⚡ Bolt": [
            "994505807655", "994505346041", "994505755298", "994505204380", "994505008749",
            "994505643332", "994505315255", "994505361151", "994505144133", "994505637094"
        ]
    }
}


# Flatten list of all inventory numbers for global matching
ALL_SYSTEM_NUMBERS = [
    num for country in NUMBER_INVENTORY.values() 
    for service in country.values() 
    for num in service
]


def generate_numbers_payload(country_key: str, service_key: str):
    available = NUMBER_INVENTORY.get(country_key, {}).get(service_key, [])
    selected_numbers = random.sample(available, min(len(available), 3)) if available else ["N/A"]
    
    msg_text = (
        f"🌍 **Country:** {country_key}\n"
        f"🛠️ **Service:** {service_key}\n\n"
        f"📱 **Assigned Numbers (Tap to copy):**\n"
        + "\n".join([f"`{num}`" for num in selected_numbers]) + "\n\n"
        f"⌛ **Status:** Waiting for SMS via Thirdwave...\n"
        f"💲 **Earn per OTP:** $0.01"
    )
    
    inline_keyboard = [
        [InlineKeyboardButton("🔄 Change Numbers", callback_data=f"change_{country_key}_{service_key}")],
        [InlineKeyboardButton("🔙 Back to Services", callback_data=f"country_{country_key}")]
    ]
    
    return msg_text, InlineKeyboardMarkup(inline_keyboard), selected_numbers


# Continuous Global Async Polling Loop
async def global_thirdwave_scanner(app: Application):
    headers = {
        "Authorization": f"Bearer {THIRDWAVE_API_KEY}",
        "Content-Type": "application/json"
    }
    url = f"{THIRDWAVE_BASE_URL}/api/v1/traffic"
    
    print("⚡ Continuous Asynchronous Scanner Initialized...")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    rows = data.get("rows") or data.get("data") or data.get("traffic") or [] if isinstance(data, dict) else (data if isinstance(data, list) else [])

                    print(f"📡 Thirdwave Checked: {len(rows)} records found.")

                    for row in rows:
                        if not isinstance(row, dict):
                            continue

                        raw_dest = str(
                            row.get("destination") or row.get("dest_number") or row.get("msisdn") or 
                            row.get("number") or row.get("receiver") or row.get("dest") or row.get("to") or ""
                        )
                        clean_dest = re.sub(r"\D", "", raw_dest)
                        
                        msg_text = str(
                            row.get("message") or row.get("text") or row.get("sms") or 
                            row.get("body") or row.get("msg") or row.get("content") or ""
                        ).strip()

                        if not clean_dest or not msg_text:
                            continue

                        sms_signature = f"{clean_dest}_{msg_text}"
                        if sms_signature in seen_sms_ids:
                            continue

                        dest_tail = clean_dest[-10:] if len(clean_dest) >= 10 else clean_dest
                        
                        # Match against all system inventory
                        for sys_num in ALL_SYSTEM_NUMBERS:
                            sys_tail = sys_num[-10:] if len(sys_num) >= 10 else sys_num
                            
                            if dest_tail == sys_tail or sys_num in clean_dest:
                                seen_sms_ids.add(sms_signature)
                                print(f"🎯 OTP MATCH FOUND! Number: {clean_dest} | SMS: {msg_text}")
                                
                                match = re.search(r'\b\d{4,6}\b', msg_text)
                                otp_code = match.group(0) if match else "SMS Received"

                                # Send to central group
                                try:
                                    await app.bot.send_message(
                                        chat_id=OTP_GROUP_CHAT_ID,
                                        text=(
                                            f"📥 **GENERAL OTP RECEIVED**\n\n"
                                            f"📱 **Number:** `{clean_dest}`\n"
                                            f"🔑 **Code:** `{otp_code}`\n"
                                            f"💬 **Full SMS:** `{msg_text}`"
                                        ),
                                        parse_mode="Markdown"
                                    )
                                except Exception as e:
                                    print(f"❌ Central Group Error: {e}")

                                # Route to matched user session
                                for uid, sess in list(active_user_sessions.items()):
                                    user_tails = [n[-10:] for n in sess["numbers"]]
                                    if dest_tail in user_tails or any(n in clean_dest for n in sess["numbers"]):
                                        user_balances[uid] = round(user_balances.get(uid, 0.0) + 0.01, 2)
                                        try:
                                            await app.bot.send_message(
                                                chat_id=sess["chat_id"],
                                                text=(
                                                    f"🎉 **OTP RECEIVED!**\n\n"
                                                    f"📱 **Number:** `{clean_dest}`\n"
                                                    f"🔑 **Code:** `{otp_code}`\n\n"
                                                    f"💰 **+$0.01** added to balance! (Total: **${user_balances[uid]:.2f}**)"
                                                ),
                                                parse_mode="Markdown"
                                            )
                                        except Exception as e:
                                            print(f"❌ Private User Error: {e}")
                                break

            except Exception as e:
                print(f"⚠️ Polling Exception: {e}")

            await asyncio.sleep(4)


async def show_country_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(country, callback_data=f"country_{country}")] for country in NUMBER_INVENTORY.keys()]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text("🌐 **Select a Country:**", parse_mode="Markdown", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text("🌐 **Select a Country:**", parse_mode="Markdown", reply_markup=reply_markup)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reply_keyboard = [
        ["🦄 GET NUMBER", "🔴 LIVE TRAFFIC"],
        ["CHECKER SET", "💰 BALANCE"],
        ["♾️ REFER AND EARN", "💸 WITHDRAW"],
        ["💀 SUPPORT", "📈 STATUS"]
    ]
    markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
    await update.message.reply_text("Welcome to Lekdigital OTP Earning Zone!", reply_markup=markup)
    return ConversationHandler.END


async def handle_menu_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    user_id = update.effective_user.id

    if "GET NUMBER" in text:
        await show_country_menu(update, context)
    elif "BALANCE" in text:
        bal = user_balances.get(user_id, 0.0)
        await update.message.reply_text(f"💳 **Your Balance:** ${bal:.2f}", parse_mode="Markdown")
    elif "WITHDRAW" in text:
        bal = user_balances.get(user_id, 0.0)
        if bal <= 0:
            await update.message.reply_text("❌ Balance is **$0.00**.", parse_mode="Markdown")
            return ConversationHandler.END
        await update.message.reply_text("💰 Reply with your **Bank Details**:", parse_mode="Markdown")
        return WAITING_FOR_BANK_DETAILS
    else:
        await update.message.reply_text(f"Selected: {text}")

    return ConversationHandler.END


async def process_withdrawal_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    bank_details = update.message.text
    amount = user_balances.get(user.id, 0.0)

    await update.message.reply_text("✅ **Withdrawal Request Submitted!**", parse_mode="Markdown")
    
    admin_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accept", callback_data=f"approve_{user.id}_{amount}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user.id}_{amount}")]
    ])
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🚨 **WITHDRAWAL REQUEST** 🚨\nUser: {user.full_name}\nAmount: ${amount:.2f}\nDetails: `{bank_details}`",
            parse_mode="Markdown",
            reply_markup=admin_markup
        )
    except Exception as e:
        logging.error(f"Admin alert failed: {e}")

    return ConversationHandler.END


async def handle_callback_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("approve_") or data.startswith("reject_"):
        parts = data.split("_")
        action, user_id, amount = parts[0], int(parts[1]), float(parts[2])
        if action == "approve":
            user_balances[user_id] = max(0.0, user_balances.get(user_id, 0.0) - amount)
            await query.edit_message_text(f"{query.message.text}\n\n✅ **APPROVED**")
        else:
            await query.edit_message_text(f"{query.message.text}\n\n❌ **REJECTED**")

    elif data.startswith("country_"):
        country_key = data.replace("country_", "")
        services = NUMBER_INVENTORY.get(country_key, {}).keys()
        keyboard = [[InlineKeyboardButton(srv, callback_data=f"srv_{country_key}_{srv}")] for srv in services]
        keyboard.append([InlineKeyboardButton("🔙 Back to Countries", callback_data="back_countries")])
        await query.edit_message_text(f"📍 Country: **{country_key}**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("srv_") or data.startswith("change_"):
        prefix, country_key, service_key = data.split("_", 2)
        msg_text, markup, selected_numbers = generate_numbers_payload(country_key, service_key)
        await query.edit_message_text(msg_text, parse_mode="Markdown", reply_markup=markup)
        
        # Register session in active tracker
        active_user_sessions[query.from_user.id] = {
            "chat_id": query.message.chat.id,
            "numbers": selected_numbers
        }

    elif data == "back_countries":
        await show_country_menu(update, context)


async def post_init(application: Application):
    # Spawns global background scanner upon startup
    asyncio.create_task(global_thirdwave_scanner(application))


def main() -> None:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .build()
    )
    
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💸 WITHDRAW$"), handle_menu_selection)],
        states={WAITING_FOR_BANK_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_withdrawal_details)]},
        fallbacks=[CommandHandler("start", start)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_selection))
    application.add_handler(CallbackQueryHandler(handle_callback_navigation))

    print("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
