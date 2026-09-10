import os
import telebot
from telebot.types import ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton

# =========================
# الإعدادات
# =========================

TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
SECRET_CODE = os.getenv("SECRET_CODE")

if not TOKEN or not GROUP_ID or not SECRET_CODE:
    raise ValueError("❌ تأكد من إعداد BOT_TOKEN و GROUP_ID و SECRET_CODE")

bot = telebot.TeleBot(TOKEN)

# =========================
# صلاحيات العضو بعد التحقق
# =========================

OPEN_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True
)

# =========================
# الحصول على ID القروب
# =========================

@bot.message_handler(commands=["id"])
def get_group_id(message):
    bot.reply_to(
        message,
        f"🆔 ID القروب هو:\n{message.chat.id}"
    )

# =========================
# تقييد الأعضاء الجدد
# =========================

@bot.message_handler(content_types=["new_chat_members"])
def new_member(message):

    bot_info = bot.get_me()

    # نتأكد أن الحدث من القروب المطلوب
    if message.chat.id != GROUP_ID:
        return

    for user in message.new_chat_members:

        # لا نقيّد البوت نفسه
        if user.id == bot_info.id:
            continue

        try:
            bot.restrict_chat_member(
                GROUP_ID,
                user.id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_audios=False,
                    can_send_documents=False,
                    can_send_photos=False,
                    can_send_videos=False,
                    can_send_video_notes=False,
                    can_send_voice_notes=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False
                )
            )

            keyboard = InlineKeyboardMarkup()

            keyboard.add(
                InlineKeyboardButton(
                    "🔐 إدخال الكود",
                    url=f"https://t.me/{bot_info.username}?start=verify"
                )
            )

            bot.send_message(
                GROUP_ID,
                f"👋 أهلاً {user.first_name}!\n\n"
                "🔒 تم تقييدك مؤقتًا.\n"
                "اضغط على الزر وأرسل الكود للبوت لفتح صلاحياتك.",
                reply_markup=keyboard
            )

        except Exception as e:
            print("❌ خطأ في تقييد العضو:", e)

# =========================
# /start في الخاص
# =========================

@bot.message_handler(commands=["start"])
def start(message):

    if message.chat.type != "private":
        return

    bot.send_message(
        message.chat.id,
        "🔐 أرسل الكود الآن:"
    )

# =========================
# التحقق من الكود
# =========================

@bot.message_handler(
    func=lambda message:
    message.chat.type == "private" and message.text is not None
)
def check_code(message):

    if message.text.strip() != SECRET_CODE:
        bot.send_message(
            message.chat.id,
            "❌ الكود غير صحيح، حاول مرة أخرى."
        )
        return

    user_id = message.from_user.id

    try:
        # التأكد أن الشخص موجود في القروب
        member = bot.get_chat_member(GROUP_ID, user_id)

        if member.status in ["left", "kicked"]:
            bot.send_message(
                message.chat.id,
                "❌ أنت لست عضوًا في القروب."
            )
            return

        # فتح الصلاحيات
        bot.restrict_chat_member(
            GROUP_ID,
            user_id,
            permissions=OPEN_PERMISSIONS
        )

        bot.send_message(
            message.chat.id,
            "✅ الكود صحيح!\n\n"
            "🎉 تم فتح صلاحياتك في القروب."
        )

    except Exception as e:

        print("❌ خطأ:", e)

        bot.send_message(
            message.chat.id,
            "❌ لم أستطع فتح صلاحياتك.\n\n"
            "تأكد أن البوت مشرف في القروب "
            "ولديه صلاحية تقييد الأعضاء."
        )

# =========================
# تشغيل البوت
# =========================

print("🤖 البوت يعمل 24/24...")

bot.infinity_polling(
    skip_pending=True,
    timeout=30,
    long_polling_timeout=30
)
