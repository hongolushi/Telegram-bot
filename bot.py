import os
import re
import time
import random
import hashlib
import asyncio
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI
from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatPermissions,
)
from aiogram.exceptions import TelegramBadRequest


BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))
DB_FILE = "bot.db"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not OWNER_ID:
    raise RuntimeError("OWNER_ID is missing")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)


async def db_execute(query, params=(), fetch=False, fetchone=False):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute(query, params)
        result = None
        if fetch:
            result = await cur.fetchall()
        elif fetchone:
            result = await cur.fetchone()
        await db.commit()
        return result


async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            admin_code TEXT DEFAULT '',
            welcome INTEGER DEFAULT 1,
            anti_spam INTEGER DEFAULT 1,
            anti_link INTEGER DEFAULT 1,
            anti_flood INTEGER DEFAULT 1,
            auto_warn INTEGER DEFAULT 1,
            warn_limit INTEGER DEFAULT 3,
            welcome_text TEXT DEFAULT 'أهلاً بك {name} 👋',
            rules TEXT DEFAULT 'لا توجد قوانين محددة حالياً.'
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER,
            user_id INTEGER,
            username TEXT,
            name TEXT,
            role TEXT DEFAULT 'member',
            warnings INTEGER DEFAULT 0,
            PRIMARY KEY(chat_id, user_id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS banned (
            chat_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY(chat_id, user_id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS muted (
            chat_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY(chat_id, user_id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS spam (
            chat_id INTEGER,
            user_id INTEGER,
            last_message REAL,
            count INTEGER DEFAULT 0,
            PRIMARY KEY(chat_id, user_id)
        )
        """)
        await db.commit()


async def ensure_group(chat_id):
    await db_execute(
        "INSERT OR IGNORE INTO groups(chat_id) VALUES(?)", (chat_id,)
    )


async def save_user(message: types.Message):
    if not message.from_user:
        return
    await ensure_group(message.chat.id)
    u = message.from_user
    await db_execute("""
        INSERT INTO users(chat_id,user_id,username,name)
        VALUES(?,?,?,?)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
        username=excluded.username,name=excluded.name
    """, (message.chat.id, u.id, u.username or "", u.full_name))


async def get_role(chat_id, user_id):
    if user_id == OWNER_ID:
        return "owner"
    row = await db_execute(
        "SELECT role FROM users WHERE chat_id=? AND user_id=?",
        (chat_id, user_id), fetchone=True
    )
    return row[0] if row else "member"


async def set_role(chat_id, user_id, role):
    await db_execute("""
        INSERT INTO users(chat_id,user_id,role)
        VALUES(?,?,?)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET role=excluded.role
    """, (chat_id, user_id, role))


async def is_owner(message):
    return bool(message.from_user and message.from_user.id == OWNER_ID)


async def is_admin(message):
    return await get_role(message.chat.id, message.from_user.id) in ("owner", "admin")


async def is_moderator(message):
    return await get_role(message.chat.id, message.from_user.id) in ("owner", "admin", "moderator")


async def require_admin(message):
    if not await is_admin(message):
        await message.answer("❌ هذا الأمر خاص بالـ Admins.\nاستعمل `/admin CODE` للحصول على الإدارة.", parse_mode="Markdown")
        return False
    return True


async def require_owner(message):
    if not await is_owner(message):
        await message.answer("❌ هذا الأمر خاص بالـ Owner فقط.")
        return False
    return True


async def get_target(message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    return None


def on_off(value):
    return "🟢 ON" if value else "🔴 OFF"


HELP = """
🤖 *أوامر البوت*

👑 *الإدارة*
/admin CODE
/admins
/addadmin — بالرد على العضو
/deladmin — بالرد على العضو
/addmod — بالرد على العضو
/delmod — بالرد على العضو
/setcode CODE

👮 *الأعضاء*
/ban /unban /kick
/mute /unmute
/warn /unwarn /warnings

🛡️ *الحماية*
/antilink on|off
/antispam on|off
/antiflood on|off
/autowarn on|off
/setwarns 3

👋 *الترحيب والقوانين*
/welcome on|off
/setwelcome النص
/rules
/setrules النص
/delrules

📊 *المعلومات*
/stats /info /id /members
/banned /muted /settings

🎮 *الألعاب*
/dice /coin /8ball /rps
/guess

ℹ️ /about
"""


@router.message(CommandStart())
async def start(message: types.Message):
    await save_user(message)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 الأوامر", callback_data="help")],
        [InlineKeyboardButton(text="🛡️ دخول الإدارة", callback_data="admin")]
    ])
    await message.answer(
        "🤖 *مرحباً بك!*\n\n🛡️ حماية وإدارة\n🎮 ألعاب وترفيه\n📊 إحصائيات",
        reply_markup=kb, parse_mode="Markdown"
    )


@router.message(Command("help"))
async def help_cmd(message: types.Message):
    await save_user(message)
    await message.answer(HELP, parse_mode="Markdown")


@router.callback_query(F.data == "help")
async def cb_help(callback: types.CallbackQuery):
    await callback.message.answer(HELP, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "admin")
async def cb_admin(callback: types.CallbackQuery):
    await callback.message.answer("🔐 للحصول على Admin:\n`/admin CODE`", parse_mode="Markdown")
    await callback.answer()


@router.message(Command("id"))
async def id_cmd(message: types.Message):
    await save_user(message)
    await message.answer(
        f"👤 User ID: `{message.from_user.id}`\n💬 Chat ID: `{message.chat.id}`",
        parse_mode="Markdown"
    )


@router.message(Command("admin"))
async def admin_login(message: types.Message):
    await save_user(message)
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ مثال: `/admin 123456`", parse_mode="Markdown")
        return
    row = await db_execute(
        "SELECT admin_code FROM groups WHERE chat_id=?", (message.chat.id,), fetchone=True
    )
    if not row or not row[0]:
        await message.answer("⚠️ لم يتم تعيين الكود.\nالـ Owner يستعمل `/setcode CODE`.", parse_mode="Markdown")
        return
    hashed = hashlib.sha256(args[1].strip().encode()).hexdigest()
    if hashed != row[0]:
        await message.answer("❌ الكود خاطئ.")
        return
    await set_role(message.chat.id, message.from_user.id, "admin")
    await message.answer("✅ تم تسجيلك كـ Admin 👑")


@router.message(Command("setcode"))
async def setcode(message: types.Message):
    if not await require_owner(message):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or len(args[1].strip()) < 4:
        await message.answer("❌ مثال: `/setcode 123456`", parse_mode="Markdown")
        return
    code = args[1].strip()
    await ensure_group(message.chat.id)
    hashed = hashlib.sha256(code.encode()).hexdigest()
    await db_execute("UPDATE groups SET admin_code=? WHERE chat_id=?", (hashed, message.chat.id))
    await message.answer("✅ تم تغيير كود الإدارة.")


async def owner_role_command(message, role, label):
    if not await require_owner(message):
        return
    target = await get_target(message)
    if not target:
        await message.answer(f"❌ رد على رسالة الشخص ثم اكتب `/{'addadmin' if role=='admin' else 'addmod'}`.", parse_mode="Markdown")
        return
    if target.id == OWNER_ID:
        await message.answer("❌ هذا هو الـ Owner.")
        return
    await set_role(message.chat.id, target.id, role)
    await message.answer(f"✅ تم تعيين {target.full_name} كـ {label}.")


@router.message(Command("addadmin"))
async def addadmin(message: types.Message):
    await owner_role_command(message, "admin", "Admin 👑")


@router.message(Command("addmod"))
async def addmod(message: types.Message):
    await owner_role_command(message, "moderator", "Moderator 🛡️")


async def remove_role_command(message, role, label):
    if not await require_owner(message):
        return
    target = await get_target(message)
    if not target:
        await message.answer("❌ رد على رسالة الشخص ثم اكتب الأمر.")
        return
    if target.id == OWNER_ID:
        await message.answer("❌ لا يمكن إزالة الـ Owner.")
        return
    current = await get_role(message.chat.id, target.id)
    if current != role:
        await message.answer("❌ هذا الشخص لا يملك هذه الرتبة.")
        return
    await set_role(message.chat.id, target.id, "member")
    await message.answer(f"✅ تم حذف رتبة {label}.")


@router.message(Command("deladmin"))
async def deladmin(message: types.Message):
    await remove_role_command(message, "admin", "Admin")


@router.message(Command("delmod"))
async def delmod(message: types.Message):
    await remove_role_command(message, "moderator", "Moderator")


@router.message(Command("admins"))
async def admins(message: types.Message):
    await save_user(message)
    rows = await db_execute("""
        SELECT user_id,name,role FROM users
        WHERE chat_id=? AND role IN ('admin','moderator')
        ORDER BY role,name
    """, (message.chat.id,), fetch=True)
    text = f"👑 *Owner:* `{OWNER_ID}`\n\n"
    if not rows:
        text += "لا يوجد Admins أو Moderators إضافيون."
    for uid, name, role in rows:
        text += f"{'👑' if role=='admin' else '🛡️'} {name} — `{role}`\n"
    await message.answer(text, parse_mode="Markdown")


async def moderation_action(message, action):
    if not await require_admin(message):
        return
    target = await get_target(message)
    if not target:
        await message.answer("❌ رد على رسالة الشخص ثم اكتب الأمر.")
        return
    if target.id == OWNER_ID:
        await message.answer("❌ لا يمكن تنفيذ هذا الأمر على الـ Owner.")
        return
    try:
        if action == "ban":
            await bot.ban_chat_member(message.chat.id, target.id)
            await db_execute("INSERT OR IGNORE INTO banned VALUES(?,?)", (message.chat.id, target.id))
            text = f"🚫 تم حظر {target.full_name}."
        elif action == "unban":
            await bot.unban_chat_member(message.chat.id, target.id, only_if_banned=True)
            await db_execute("DELETE FROM banned WHERE chat_id=? AND user_id=?", (message.chat.id, target.id))
            text = f"✅ تم فك الحظر عن {target.full_name}."
        elif action == "kick":
            await bot.ban_chat_member(message.chat.id, target.id)
            await bot.unban_chat_member(message.chat.id, target.id)
            text = f"👢 تم طرد {target.full_name}."
        elif action == "mute":
            await bot.restrict_chat_member(
                message.chat.id, target.id,
                permissions=ChatPermissions(can_send_messages=False)
            )
            await db_execute("INSERT OR IGNORE INTO muted VALUES(?,?)", (message.chat.id, target.id))
            text = f"🔇 تم كتم {target.full_name}."
        else:
            await bot.restrict_chat_member(
                message.chat.id, target.id,
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
            await db_execute("DELETE FROM muted WHERE chat_id=? AND user_id=?", (message.chat.id, target.id))
            text = f"🔊 تم فك كتم {target.full_name}."
        await message.answer(text)
    except TelegramBadRequest:
        await message.answer("❌ لم أستطع تنفيذ الأمر. تأكد أن البوت Admin ولديه الصلاحيات اللازمة.")


@router.message(Command("ban"))
async def ban(message): await moderation_action(message, "ban")


@router.message(Command("unban"))
async def unban(message): await moderation_action(message, "unban")


@router.message(Command("kick"))
async def kick(message): await moderation_action(message, "kick")


@router.message(Command("mute"))
async def mute(message): await moderation_action(message, "mute")


@router.message(Command("unmute"))
async def unmute(message): await moderation_action(message, "unmute")


@router.message(Command("warn"))
async def warn(message: types.Message):
    if not await require_admin(message):
        return
    target = await get_target(message)
    if not target or target.id == OWNER_ID:
        await message.answer("❌ رد على عضو، ولا يمكن تحذير الـ Owner.")
        return
    await save_user(message)
    row = await db_execute(
        "SELECT warnings FROM users WHERE chat_id=? AND user_id=?",
        (message.chat.id, target.id), fetchone=True
    )
    warnings = (row[0] if row else 0) + 1
    await db_execute("""
        INSERT INTO users(chat_id,user_id,name,warnings)
        VALUES(?,?,?,?)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET warnings=excluded.warnings
    """, (message.chat.id, target.id, target.full_name, warnings))
    group = await db_execute("SELECT warn_limit,auto_warn FROM groups WHERE chat_id=?", (message.chat.id,), fetchone=True)
    limit, auto = (group if group else (3, 1))
    text = f"⚠️ تحذير لـ {target.full_name}: {warnings}/{limit}"
    if auto and warnings >= limit:
        try:
            await bot.ban_chat_member(message.chat.id, target.id)
            await db_execute("INSERT OR IGNORE INTO banned VALUES(?,?)", (message.chat.id, target.id))
            text += "\n🚫 وصل للحد وتم حظره تلقائياً."
        except TelegramBadRequest:
            text += "\n❌ تعذر الحظر التلقائي."
    await message.answer(text)


@router.message(Command("unwarn"))
async def unwarn(message: types.Message):
    if not await require_admin(message): return
    target = await get_target(message)
    if not target:
        await message.answer("❌ رد على العضو.")
        return
    await db_execute(
        "UPDATE users SET warnings=CASE WHEN warnings>0 THEN warnings-1 ELSE 0 END WHERE chat_id=? AND user_id=?",
        (message.chat.id, target.id)
    )
    await message.answer("✅ تم حذف تحذير واحد.")


@router.message(Command("warnings"))
async def warnings(message: types.Message):
    target = await get_target(message) or message.from_user
    row = await db_execute("SELECT warnings FROM users WHERE chat_id=? AND user_id=?", (message.chat.id, target.id), fetchone=True)
    await message.answer(f"⚠️ تحذيرات {target.full_name}: {row[0] if row else 0}")


async def set_group_bool(message, field):
    if not await require_admin(message): return
    args = message.text.split()
    if len(args) != 2 or args[1].lower() not in ("on", "off"):
        await message.answer("❌ استعمل on أو off.")
        return
    value = 1 if args[1].lower() == "on" else 0
    await ensure_group(message.chat.id)
    await db_execute(f"UPDATE groups SET {field}=? WHERE chat_id=?", (value, message.chat.id))
    await message.answer(f"✅ تم تغيير الإعداد إلى {on_off(value)}")


@router.message(Command("antilink"))
async def antilink(message): await set_group_bool(message, "anti_link")


@router.message(Command("antispam"))
async def antispam(message): await set_group_bool(message, "anti_spam")


@router.message(Command("antiflood"))
async def antiflood(message): await set_group_bool(message, "anti_flood")


@router.message(Command("autowarn"))
async def autowarn(message): await set_group_bool(message, "auto_warn")


@router.message(Command("welcome"))
async def welcome(message): await set_group_bool(message, "welcome")


@router.message(Command("setwarns"))
async def setwarns(message):
    if not await require_admin(message): return
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit() or not 1 <= int(args[1]) <= 20:
        await message.answer("❌ مثال: `/setwarns 3`", parse_mode="Markdown")
        return
    await ensure_group(message.chat.id)
    await db_execute("UPDATE groups SET warn_limit=? WHERE chat_id=?", (int(args[1]), message.chat.id))
    await message.answer(f"✅ حد التحذيرات أصبح {args[1]}.")


@router.message(Command("setwelcome"))
async def setwelcome(message):
    if not await require_admin(message): return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ مثال: `/setwelcome أهلاً {name} 👋`", parse_mode="Markdown")
        return
    await db_execute("UPDATE groups SET welcome_text=? WHERE chat_id=?", (args[1], message.chat.id))
    await message.answer("✅ تم حفظ رسالة الترحيب.")


@router.message(Command("rules"))
async def rules(message):
    await ensure_group(message.chat.id)
    row = await db_execute("SELECT rules FROM groups WHERE chat_id=?", (message.chat.id,), fetchone=True)
    await message.answer("📜 *القوانين*\n\n" + (row[0] if row else "لا توجد قوانين."), parse_mode="Markdown")


@router.message(Command("setrules"))
async def setrules(message):
    if not await require_admin(message): return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ اكتب القوانين بعد الأمر.")
        return
    await db_execute("UPDATE groups SET rules=? WHERE chat_id=?", (args[1], message.chat.id))
    await message.answer("✅ تم حفظ القوانين.")


@router.message(Command("delrules"))
async def delrules(message):
    if not await require_admin(message): return
    await db_execute("UPDATE groups SET rules=? WHERE chat_id=?", ("لا توجد قوانين محددة حالياً.", message.chat.id))
    await message.answer("✅ تم حذف القوانين المخصصة.")


@router.message(Command("del"))
async def delete_message(message):
    if not await require_admin(message): return
    if not message.reply_to_message:
        await message.answer("❌ رد على الرسالة التي تريد حذفها.")
        return
    try:
        await bot.delete_message(message.chat.id, message.reply_to_message.message_id)
        await bot.delete_message(message.chat.id, message.message_id)
    except TelegramBadRequest:
        await message.answer("❌ لا أستطيع حذف الرسالة. تأكد من صلاحيات البوت.")


@router.message(Command("pin"))
async def pin(message):
    if not await require_admin(message): return
    if not message.reply_to_message:
        await message.answer("❌ رد على الرسالة التي تريد تثبيتها.")
        return
    try:
        await bot.pin_chat_message(message.chat.id, message.reply_to_message.message_id)
        await message.answer("📌 تم تثبيت الرسالة.")
    except TelegramBadRequest:
        await message.answer("❌ تعذر التثبيت.")


@router.message(Command("unpin"))
async def unpin(message):
    if not await require_admin(message): return
    try:
        if message.reply_to_message:
            await bot.unpin_chat_message(message.chat.id, message.reply_to_message.message_id)
        else:
            await bot.unpin_chat_message(message.chat.id)
        await message.answer("✅ تم فك التثبيت.")
    except TelegramBadRequest:
        await message.answer("❌ تعذر فك التثبيت.")


@router.message(Command("stats"))
async def stats(message):
    await ensure_group(message.chat.id)
    users = await db_execute("SELECT COUNT(*) FROM users WHERE chat_id=?", (message.chat.id,), fetchone=True)
    banned = await db_execute("SELECT COUNT(*) FROM banned WHERE chat_id=?", (message.chat.id,), fetchone=True)
    muted = await db_execute("SELECT COUNT(*) FROM muted WHERE chat_id=?", (message.chat.id,), fetchone=True)
    await message.answer(f"📊 *الإحصائيات*\n\n👥 الأعضاء المسجلون: {users[0]}\n🚫 المحظورون: {banned[0]}\n🔇 المكتومون: {muted[0]}", parse_mode="Markdown")


@router.message(Command("members"))
async def members(message):
    row = await db_execute("SELECT COUNT(*) FROM users WHERE chat_id=?", (message.chat.id,), fetchone=True)
    await message.answer(f"👥 الأعضاء المسجلون في البوت: {row[0]}")


@router.message(Command("banned"))
async def banned(message):
    rows = await db_execute("SELECT user_id FROM banned WHERE chat_id=? LIMIT 50", (message.chat.id,), fetch=True)
    await message.answer("🚫 عدد المحظورين: " + str(len(rows)))


@router.message(Command("muted"))
async def muted(message):
    rows = await db_execute("SELECT user_id FROM muted WHERE chat_id=? LIMIT 50", (message.chat.id,), fetch=True)
    await message.answer("🔇 عدد المكتومين: " + str(len(rows)))


@router.message(Command("info"))
async def info(message):
    target = await get_target(message) or message.from_user
    role = await get_role(message.chat.id, target.id)
    row = await db_execute("SELECT warnings FROM users WHERE chat_id=? AND user_id=?", (message.chat.id, target.id), fetchone=True)
    await message.answer(
        f"👤 *معلومات العضو*\n\nالاسم: {target.full_name}\n"
        f"ID: `{target.id}`\nالرتبة: `{role}`\nالتحذيرات: {row[0] if row else 0}",
        parse_mode="Markdown"
    )


@router.message(Command("settings"))
async def settings(message):
    await ensure_group(message.chat.id)
    row = await db_execute("""
        SELECT welcome,anti_spam,anti_link,anti_flood,auto_warn,warn_limit
        FROM groups WHERE chat_id=?
    """, (message.chat.id,), fetchone=True)
    w, s, l, f, a, limit = row
    await message.answer(
        f"⚙️ *إعدادات المجموعة*\n\n"
        f"👋 Welcome: {on_off(w)}\n"
        f"🛡️ Anti-spam: {on_off(s)}\n"
        f"🔗 Anti-link: {on_off(l)}\n"
        f"🌊 Anti-flood: {on_off(f)}\n"
        f"⚠️ Auto-warn: {on_off(a)}\n"
        f"🔢 Warn limit: {limit}",
        parse_mode="Markdown"
    )


@router.message(Command("about"))
async def about(message):
    await message.answer("🤖 بوت إدارة وحماية للمجموعات.\n⚡ يعمل بـ Python + aiogram + SQLite.")


@router.message(Command("dice"))
async def dice(message): await message.answer(f"🎲 النتيجة: {random.randint(1,6)}")


@router.message(Command("coin"))
async def coin(message): await message.answer("🪙 " + random.choice(["وجه", "كتابة"]))


@router.message(Command("8ball"))
async def ball(message):
    await message.answer("🎱 " + random.choice(["نعم ✅","لا ❌","ممكن 🤔","أكيد 🔥","اسأل لاحقاً ⏳"]))


@router.message(Command("rps"))
async def rps(message):
    choices = ["حجر 🪨", "ورق 📄", "مقص ✂️"]
    await message.answer("✊ أنا اخترت: " + random.choice(choices))


@router.message(Command("guess"))
async def guess(message):
    n = random.randint(1,10)
    await message.answer(f"🎯 لعبة التخمين!\nخمن رقماً من 1 إلى 10.\n(رقم الجولة: {n})")


# Anti-link / anti-flood / anti-spam and welcome
@router.message()
async def protection_and_welcome(message: types.Message):
    if not message.from_user:
        return

    await save_user(message)

    if message.new_chat_members:
        row = await db_execute("SELECT welcome,welcome_text FROM groups WHERE chat_id=?", (message.chat.id,), fetchone=True)
        if row and row[0]:
            for member in message.new_chat_members:
                text = row[1].replace("{name}", member.full_name).replace("{username}", "@" + member.username if member.username else member.full_name)
                await message.answer(text)
        return

    if message.chat.type not in ("group", "supergroup"):
        return

    if await is_admin(message):
        return

    row = await db_execute("""
        SELECT anti_link,anti_spam,anti_flood,auto_warn,warn_limit
        FROM groups WHERE chat_id=?
    """, (message.chat.id,), fetchone=True)
    if not row:
        return

    anti_link, anti_spam, anti_flood, auto_warn, warn_limit = row

    if anti_link and message.text:
        if re.search(r"(https?://|www\.|t\.me/|telegram\.me/)", message.text, re.I):
            try:
                await bot.delete_message(message.chat.id, message.message_id)
                await message.answer("🔗 ممنوع إرسال الروابط هنا.")
            except TelegramBadRequest:
                pass
            return

    now = time.time()
    if anti_spam or anti_flood:
        rec = await db_execute(
            "SELECT last_message,count FROM spam WHERE chat_id=? AND user_id=?",
            (message.chat.id, message.from_user.id), fetchone=True
        )
        if rec:
            last, count = rec
            delta = now - last
            count = count + 1 if delta < 4 else 1
        else:
            delta, count = 99, 1
        await db_execute("""
            INSERT INTO spam(chat_id,user_id,last_message,count)
            VALUES(?,?,?,?)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET
            last_message=excluded.last_message,count=excluded.count
        """, (message.chat.id, message.from_user.id, now, count))

        limit = 6 if anti_flood else 12
        if (anti_spam or anti_flood) and delta < 4 and count >= limit:
            try:
                await bot.delete_message(message.chat.id, message.message_id)
            except TelegramBadRequest:
                pass
            if auto_warn:
                await db_execute("""
                    UPDATE users SET warnings=warnings+1
                    WHERE chat_id=? AND user_id=?
                """, (message.chat.id, message.from_user.id))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Delete any old webhook before polling. Telegram does not allow
    # getUpdates (long polling) while a webhook is active.
    await bot.delete_webhook(drop_pending_updates=True)

    task = asyncio.create_task(dp.start_polling(bot))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {"status": "online", "bot": "Telegram Manager Bot"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
