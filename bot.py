import os
import telebot
from telebot.types import ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request

# =========================
# الإعدادات من Render
# =========================
TOKEN = os.environ["BOT_TOKEN"]
GROUP_ID = int(os.environ["GROUP_ID"])
SECRET_CODE = os.environ["SECRET_CODE"]

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# =========================
# الصفحة الرئيسية لـ Render
# =========================
@app.route("/", methods=["GET"])
def home():
    return "🤖 Telegram Bot is running!", 200


# =========================
# استقبال تحديثات Telegram
# =========================
@app.route("/webhook", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(
        request.get_data().decode("utf-8")
    )
    bot.process_new_updates([update])
    return "OK", 200


# =========================
# أمر /id لمعرفة ID القروب
# =========================
@bot.message_handler(commands=["id"])
def get_group_id(message):
    bot.reply_to(
        message,
        f"🆔 ID القروب هو:\n{message.chat.id}"
    )


# =========================
# عند دخول عضو جديد
# =========================
@bot.message_handler(content_types=["new_chat_members"])
def new_member(message):

    for user in message.new_chat_members:

        # لا تقيّد البوت نفسه
        if user.id == bot.get_me().id:
            continue

        try:
            # تقييد العضو
            bot.restrict_chat_member(
                chat_id=message.chat.id,
                user_id=user.id,
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

            # زر إدخال الكود
            bot_username = bot.get_me().username

            keyboard = InlineKeyboardMarkup()

            keyboard.add(
                InlineKeyboardButton(
                    "🔐 إدخال الكود",
                    url=f"https://t.me/{bot_username}?start=verify"
                )
            )

            # رسالة للعضو
            bot.send_message(
                message.chat.id,
                f"👋 أهلاً {user.first_name}!\n\n"
                "🔒 تم تقييدك مؤقتًا.\n"
                "اضغط على الزر وأرسل الكود للبوت لفتح صلاحياتك.",
                reply_markup=keyboard
            )

        except Exception as e:
            print("❌ خطأ عند تقييد العضو:", e)


# =========================
# أمر /start في الخاص
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
    func=lambda message: message.chat.type == "private",
    content_types=["text"]
)
def check_code(message):

    if message.text.strip() == SECRET_CODE:

        try:
            # التأكد أن العضو موجود في القروب
            member = bot.get_chat_member(
                GROUP_ID,
                message.from_user.id
            )

            # إذا غادر أو تم طرده
            if member.status in ["left", "kicked"]:
                bot.send_message(
                    message.chat.id,
                    "❌ أنت لست عضوًا في القروب."
                )
                return

            # فتح صلاحيات العضو
            bot.restrict_chat_member(
                chat_id=GROUP_ID,
                user_id=message.from_user.id,
                permissions=ChatPermissions(
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
            )

            bot.send_message(
                message.chat.id,
                "✅ الكود صحيح!\n"
                "🎉 تم فتح صلاحياتك في القروب."
            )

        except Exception as e:

            print("❌ خطأ عند فتح الصلاحيات:", e)

            bot.send_message(
                message.chat.id,
                "❌ لم أستطع فتح صلاحياتك.\n\n"
                "تأكد أن البوت مشرف في القروب "
                "ولديه صلاحية تقييد الأعضاء."
            )

    else:

        bot.send_message(
            message.chat.id,
            "❌ الكود غير صحيح، حاول مرة أخرى."
        )


# =========================
# تشغيل Webhook على Render
# =========================

RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

if RENDER_URL:
    try:
        bot.remove_webhook()
        bot.set_webhook(
            url=f"{RENDER_URL}/webhook"
        )
        print("✅ Webhook تم تفعيله")
        print(f"🌐 URL: {RENDER_URL}/webhook")

    except Exception as e:
        print("❌ خطأ في تفعيل Webhook:", e)

else:
    print("⚠️ RENDER_EXTERNAL_URL غير موجود")


# =========================
# تشغيل Flask
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
            )
