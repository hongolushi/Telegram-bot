import os
import re
import time
import random
import hashlib
import asyncio
import html
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
            rules TEXT DEFAULT 'لا توجد قوانين محددة حالياً.',
            lock_mode INTEGER DEFAULT 0,
            media_lock INTEGER DEFAULT 0,
            slowmode INTEGER DEFAULT 0
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
            messages INTEGER DEFAULT 0,
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
        await db.execute("""CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT DEFAULT '',
            price INTEGER NOT NULL DEFAULT 0, item_type TEXT DEFAULT 'custom', duration_days INTEGER DEFAULT 0,
            stock INTEGER DEFAULT -1, limited INTEGER DEFAULT 0, vip_only INTEGER DEFAULT 0, active INTEGER DEFAULT 1
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS shop_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, user_id INTEGER, item_id INTEGER,
            status TEXT DEFAULT 'pending', created_at REAL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS auctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, name TEXT, description TEXT DEFAULT '',
            start_price INTEGER DEFAULT 0, highest_bid INTEGER DEFAULT 0, highest_user INTEGER DEFAULT 0,
            ends_at REAL, active INTEGER DEFAULT 1
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS user_vip (
            chat_id INTEGER, user_id INTEGER, expires_at REAL DEFAULT 0, PRIMARY KEY(chat_id,user_id)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS user_boosts (
            chat_id INTEGER, user_id INTEGER, xp_until REAL DEFAULT 0, coins_until REAL DEFAULT 0,
            luck_until REAL DEFAULT 0, PRIMARY KEY(chat_id,user_id)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS inventory (
            chat_id INTEGER, user_id INTEGER, item_id INTEGER, quantity INTEGER DEFAULT 1,
            PRIMARY KEY(chat_id,user_id,item_id)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS market_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, seller_id INTEGER, item_id INTEGER,
            price INTEGER NOT NULL, quantity INTEGER DEFAULT 1, active INTEGER DEFAULT 1, created_at REAL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS achievements (
            chat_id INTEGER, user_id INTEGER, achievement TEXT, created_at REAL,
            PRIMARY KEY(chat_id,user_id,achievement)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS economy_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, operator_id INTEGER, target_id INTEGER,
            kind TEXT, amount INTEGER, created_at REAL
        )""")
        await db.commit()

    async with aiosqlite.connect(DB_FILE) as db:
        cols = {r[1] for r in await (await db.execute("PRAGMA table_info(groups)")).fetchall()}
        for col, typ in [("lock_mode", "INTEGER DEFAULT 0"), ("media_lock", "INTEGER DEFAULT 0"), ("slowmode", "INTEGER DEFAULT 0")]:
            if col not in cols:
                await db.execute(f"ALTER TABLE groups ADD COLUMN {col} {typ}")
        await db.commit()

    async with aiosqlite.connect(DB_FILE) as db:
        user_cols = {r[1] for r in await (await db.execute("PRAGMA table_info(users)")).fetchall()}
        if "messages" not in user_cols:
            await db.execute("ALTER TABLE users ADD COLUMN messages INTEGER DEFAULT 0")
        if "points" not in user_cols:
            await db.execute("ALTER TABLE users ADD COLUMN points INTEGER DEFAULT 0")
        if "coins" not in user_cols:
            await db.execute("ALTER TABLE users ADD COLUMN coins INTEGER DEFAULT 0")
        if "streak" not in user_cols:
            await db.execute("ALTER TABLE users ADD COLUMN streak INTEGER DEFAULT 0")
        async with db.execute("PRAGMA table_info(shop_items)") as cur:
            shop_cols = {r[1] for r in await cur.fetchall()}
        for col, typ in [("title_text", "TEXT DEFAULT ''"), ("badge_text", "TEXT DEFAULT ''")]:
            if col not in shop_cols:
                await db.execute(f"ALTER TABLE shop_items ADD COLUMN {col} {typ}")
        await db.execute("CREATE TABLE IF NOT EXISTS notes (chat_id INTEGER, name TEXT, value TEXT, PRIMARY KEY(chat_id,name))")
        await db.commit()


async def ensure_group(chat_id):
    await db_execute(
        "INSERT OR IGNORE INTO groups(chat_id,admin_code) VALUES(?,?)", (chat_id, hashlib.sha256(b"123456").hexdigest())
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
    return await get_role(message.chat.id, message.from_user.id) in ("owner", "admin", "helper", "moderator")

async def is_helper(message):
    return await get_role(message.chat.id, message.from_user.id) in ("owner", "admin", "helper", "moderator")

async def require_moderation(message, action):
    role = await get_role(message.chat.id, message.from_user.id)
    # Admin/Owner: all moderation actions. Moderator: Helper's old stronger member-management set.
    if role in ("owner", "admin"):
        return True
    if role == "moderator" and action in ("ban", "unban", "kick", "mute", "unmute", "warn", "unwarn", "delete"):
        return True
    if role == "helper" and action in ("mute", "unmute", "warn", "unwarn", "delete"):
        return True
    await message.answer("❌ لا تملك صلاحية هذا الأمر.")
    return False

async def require_staff(message):
    role = await get_role(message.chat.id, message.from_user.id)
    if role not in ("owner", "admin", "helper", "moderator"):
        await message.answer("❌ هذا الأمر خاص بفريق الإدارة.")
        return False
    return True

async def require_helper(message):
    role = await get_role(message.chat.id, message.from_user.id)
    if role not in ("owner", "admin", "helper"):
        await message.answer("❌ هذا الأمر يحتاج Helper أو أعلى.")
        return False
    return True


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


ROLE_NAMES = {"owner":"👑 Owner", "admin":"🛡️ Admin", "helper":"🔧 Helper", "moderator":"🛠️ Moderator", "member":"👤 Member"}

HELP = """
🤖 *أوامر البوت*

👑 *الإدارة*
/admin CODE
/admins
/addadmin — بالرد على العضو
/deladmin — بالرد على العضو
/addmod — بالرد على العضو
/delmod — بالرد على العضو
/addhelper — بالرد على العضو
/delhelper — بالرد على العضو
/vip — بالرد على العضو
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

💰 *النقاط والاقتصاد*
/points /balance /top
/daily /giftpoints /giftcoins

🛒 *المتجر*
/shop /buy /giftitem /market /marketadd /marketbuy /inventory /achievements
/shopadd /shopdel /shopprice /auctioncreate /bid /marketadd /marketbuy /giftvip

ℹ️ /about
"""

HELP_HTML = """
🤖 <b>أوامر البوت</b>

👑 <b>الإدارة</b>
/admin CODE
/admins
/addadmin — بالرد على العضو
/deladmin — بالرد على العضو
/addmod — بالرد على العضو
/delmod — بالرد على العضو
/addhelper — بالرد على العضو
/delhelper — بالرد على العضو
/vip — بالرد على العضو
/setcode CODE

👮 <b>الأعضاء</b>
/ban /unban /kick
/mute /unmute
/warn /unwarn /warnings

🛡️ <b>الحماية</b>
/antilink on|off
/antispam on|off
/antiflood on|off
/autowarn on|off
/setwarns 3

👋 <b>الترحيب والقوانين</b>
/welcome on|off
/setwelcome النص
/rules
/setrules النص
/delrules

📊 <b>المعلومات</b>
/stats /info /id /members
/banned /muted /settings

🎮 <b>الألعاب</b>
/dice /coin /8ball /rps
/guess

💰 <b>النقاط والاقتصاد</b>
/points /balance /top
/daily /giftpoints /giftcoins

🛒 <b>المتجر</b>
/shop /buy /giftitem /market /marketadd /marketbuy /inventory /achievements
/shopadd /shopdel /shopprice /auctioncreate /bid /giftvip

ℹ️ /about
"""


@router.message(CommandStart())
async def start(message: types.Message):
    # start=panel_<GROUP_ID> يفتح لوحة خاصة بصاحبها فقط.
    payload = ''
    if message.text and ' ' in message.text:
        payload = message.text.split(' ', 1)[1].strip()
    if payload.startswith('panel_') and message.chat.type == 'private':
        try:
            group_id = int(payload[6:])
        except ValueError:
            group_id = 0
        if group_id and await send_private_panel(message.from_user.id, group_id):
            return
        await message.answer('❌ لا تملك صلاحية فتح لوحة هذه المجموعة.')
        return

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


@router.message(Command("setcode", "changecode"))
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
    await owner_role_command(message, "moderator", "Moderator 🛠️")

@router.message(Command("addhelper"))
async def addhelper(message: types.Message):
    await owner_role_command(message, "helper", "Helper 🔧")


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

@router.message(Command("delhelper"))
async def delhelper(message: types.Message):
    await remove_role_command(message, "helper", "Helper")


@router.message(Command("admins"))
async def admins(message: types.Message):
    await save_user(message)
    rows = await db_execute("""
        SELECT user_id,name,role FROM users
        WHERE chat_id=? AND role IN ('admin','helper','moderator')
        ORDER BY role,name
    """, (message.chat.id,), fetch=True)
    text = f"👑 *Owner:* `{OWNER_ID}`\n\n"
    if not rows:
        text += "لا يوجد Admins أو Moderators إضافيون."
    for uid, name, role in rows:
        text += f"{ROLE_NAMES.get(role, role)} {name} — `{role}`\n"
    await message.answer(text, parse_mode="Markdown")


async def moderation_action(message, action):
    if not await require_moderation(message, action):
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
    if not await require_moderation(message, "warn"):
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
    await message.answer(f"📊 <b>الإحصائيات</b>\n\n👥 الأعضاء المسجلون: {users[0]}\n🚫 المحظورون: {banned[0]}\n🔇 المكتومون: {muted[0]}", parse_mode="HTML")


@router.message(Command("members"))
async def members(message):
    await ensure_group(message.chat.id)
    # Telegram لا يوفّر API لجلب كل أعضاء المجموعة دفعة واحدة.
    # لكن يمكننا مزامنة المشرفين الحاليين، بينما يتم تسجيل بقية الأعضاء
    # تلقائياً عند انضمامهم أو عند إرسالهم أي رسالة.
    try:
        admins = await bot.get_chat_administrators(message.chat.id)
        for admin in admins:
            u = admin.user
            await db_execute("""
                INSERT INTO users(chat_id,user_id,username,name)
                VALUES(?,?,?,?)
                ON CONFLICT(chat_id,user_id) DO UPDATE SET
                username=excluded.username,name=excluded.name
            """, (message.chat.id, u.id, u.username or "", u.full_name))
    except TelegramBadRequest:
        pass
    row = await db_execute("SELECT COUNT(*) FROM users WHERE chat_id=?", (message.chat.id,), fetchone=True)
    await message.answer(
        f"👥 <b>الأعضاء المسجلون</b>\n\nالعدد الحالي في قاعدة بيانات البوت: <b>{row[0]}</b>\n\nℹ️ يتم تسجيل الأعضاء تلقائياً عند الانضمام أو عند إرسال رسالة.",
        parse_mode="HTML"
    )


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

# لوحات التحكم الخاصة: كل لوحة تظهر في الخاص لصاحبها فقط.
panel_sessions = {}  # (private_chat_id, message_id) -> group_chat_id
pending_targets = {}  # (group_chat_id, operator_user_id) -> target_user_id
private_panel_group = {}  # user_id -> last group used for the panel
pending_panel_actions = {}  # user_id -> panel text input action

async def panel_group_id(call: types.CallbackQuery):
    # أولاً نستخدم جلسة اللوحة المرتبطة بالرسالة.
    if call.message:
        gid = panel_sessions.get((call.message.chat.id, call.message.message_id))
        if gid:
            return gid
    # احتياطياً: إذا أعاد Render تشغيل الخدمة وضاعت الجلسة من الذاكرة،
    # نستخدم آخر مجموعة فتح منها المستخدم اللوحة.
    if call.from_user:
        return private_panel_group.get(call.from_user.id)
    return None

async def panel_is_admin(call: types.CallbackQuery):
    gid = await panel_group_id(call)
    if not gid or not call.from_user:
        return False
    return await get_role(gid, call.from_user.id) in ("owner", "admin", "helper", "moderator")

async def send_private_panel(user_id, group_id):
    if not group_id:
        return False
    role = await get_role(group_id, user_id)
    if role not in ("owner", "admin", "helper", "moderator"):
        return False
    await ensure_group(group_id)
    kb = await dashboard_for(None, role)
    msg = await bot.send_message(user_id, '🚀 <b>لوحة التحكم الخاصة</b>\n\nهذه اللوحة تظهر لك أنت فقط.\nاختر القسم:', reply_markup=kb, parse_mode='HTML')
    panel_sessions[(user_id, msg.message_id)] = group_id
    private_panel_group[user_id] = group_id
    return True

# ==================== SUPER DASHBOARD ====================

async def dashboard_for(message, role=None):
    role = role or (await get_role(await panel_group_id(message), message.from_user.id) if isinstance(message, types.CallbackQuery) else "admin")
    rows = [
        [InlineKeyboardButton(text='👥 الأعضاء', callback_data='dash_members:0'), InlineKeyboardButton(text='🛡️ الحماية', callback_data='dash_security')],
        [InlineKeyboardButton(text='👋 الترحيب', callback_data='dash_welcome'), InlineKeyboardButton(text='📜 القوانين', callback_data='dash_rules')],
        [InlineKeyboardButton(text='📊 الإحصائيات', callback_data='dash_stats'), InlineKeyboardButton(text='🎮 الألعاب', callback_data='dash_games')],
    ]
    if role in ('owner','admin'):
        rows.append([InlineKeyboardButton(text='👑 المشرفون', callback_data='dash_admins'), InlineKeyboardButton(text='💰 الاقتصاد والمتجر', callback_data='dash_economy')])
        rows.append([InlineKeyboardButton(text='⚙️ الإعدادات', callback_data='dash_settings')])
    elif role in ('helper','moderator'):
        rows.append([InlineKeyboardButton(text='💰 الاقتصاد', callback_data='dash_economy')])
    rows.append([InlineKeyboardButton(text='🆘 المساعدة', callback_data='dash_help')])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.message(Command('panel', 'dashboard', 'menu'))
async def super_panel(message: types.Message):
    if message.chat.type in ('group', 'supergroup'):
        if not await require_admin(message):
            return
        private_panel_group[message.from_user.id] = message.chat.id
        me = await bot.get_me()
        url = f'https://t.me/{me.username}?start=panel_{message.chat.id}'
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔐 فتح اللوحة الخاصة', url=url)]])
        await message.answer('🔒 لوحة التحكم لا تظهر إلا لك.\nاضغط الزر لفتحها في الخاص مع البوت.', reply_markup=kb)
        return

    group_id = private_panel_group.get(message.from_user.id)
    if not group_id:
        await message.answer('❌ افتح `/panel` من داخل المجموعة أولاً.', parse_mode='Markdown')
        return
    if not await send_private_panel(message.from_user.id, group_id):
        await message.answer('❌ ليس لديك صلاحية Admin في هذه المجموعة.')

async def edit_dashboard(call, text, keyboard):
    try:
        await call.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=keyboard, parse_mode='HTML')

@router.callback_query(F.data == 'dash_home')
async def dash_home(call: types.CallbackQuery):
    if not call.message or not await panel_is_admin(call):
        await call.answer('❌ ليس لديك صلاحية.', show_alert=True); return
    await call.answer()
    await edit_dashboard(call, '🚀 <b>لوحة التحكم الخارقة</b>\nاختر القسم:', await dashboard_for(call.message, await get_role(await panel_group_id(call), call.from_user.id)))

@router.callback_query(F.data.startswith('dash_members:'))
async def dash_members(call: types.CallbackQuery):
    if not call.message or not await panel_is_admin(call):
        await call.answer('❌ ليس لديك صلاحية.', show_alert=True); return
    await call.answer()
    page=int(call.data.split(':')[1])
    rows=await db_execute('SELECT user_id,name,username,role FROM users WHERE chat_id=? ORDER BY name LIMIT 8 OFFSET ?', ((await panel_group_id(call)),page*8), fetch=True)
    buttons=[]
    for uid,name,username,role in rows:
        icon='👑' if role=='admin' else '🔧' if role=='helper' else '🛠️' if role=='moderator' else '👤'
        buttons.append([InlineKeyboardButton(text=f'{icon} {(name or "عضو")[:30]}', callback_data=f'member:{uid}')])
    nav=[]
    if page>0: nav.append(InlineKeyboardButton(text='⬅️ السابق',callback_data=f'dash_members:{page-1}'))
    if len(rows)==8: nav.append(InlineKeyboardButton(text='التالي ➡️',callback_data=f'dash_members:{page+1}'))
    if nav: buttons.append(nav)
    buttons.append([InlineKeyboardButton(text='🔙 الرئيسية',callback_data='dash_home')])
    await edit_dashboard(call,f'👥 <b>اختيار عضو</b>\nالصفحة {page+1}\nاضغط على الشخص:',InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith('member:'))
async def member_card(call: types.CallbackQuery):
    if not call.message or not await panel_is_admin(call):
        await call.answer('❌ ليس لديك صلاحية.', show_alert=True); return
    await call.answer()
    uid=int(call.data.split(':')[1])
    pending_targets[((await panel_group_id(call)),call.from_user.id)]=uid
    row=await db_execute('SELECT name,username,role,warnings,messages FROM users WHERE chat_id=? AND user_id=?',((await panel_group_id(call)),uid),fetchone=True)
    if not row:
        await call.message.answer('❌ العضو غير موجود في قاعدة البيانات.'); return
    name,username,role,warns,msgs=row
    text=f'👤 <b>{html.escape(name or "عضو")}</b>\n🆔 <code>{uid}</code>\n🏷️ {ROLE_NAMES.get(role,role)}\n⚠️ تحذيرات: {warns}\n💬 رسائل: {msgs}'
    if username: text += f'\n🔗 @{html.escape(username)}'
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔨 حظر',callback_data=f'quick:ban:{uid}'),InlineKeyboardButton(text='🚪 طرد',callback_data=f'quick:kick:{uid}')],
        [InlineKeyboardButton(text='🔇 كتم',callback_data=f'quick:mute:{uid}'),InlineKeyboardButton(text='🔊 فك الكتم',callback_data=f'quick:unmute:{uid}')],
        [InlineKeyboardButton(text='⚠️ تحذير',callback_data=f'quick:warn:{uid}'),InlineKeyboardButton(text='♻️ إزالة تحذير',callback_data=f'quick:unwarn:{uid}')],
        [InlineKeyboardButton(text='🔓 فك الحظر',callback_data=f'quick:unban:{uid}'),InlineKeyboardButton(text='🔄 تحديث',callback_data=f'member:{uid}')],
        [InlineKeyboardButton(text='💰 الاقتصاد',callback_data=f'membereco:{uid}'),InlineKeyboardButton(text='💎 VIP',callback_data=f'membervip:{uid}')],
        [InlineKeyboardButton(text='⬅️ الأعضاء',callback_data='dash_members:0')]
    ])
    await edit_dashboard(call,text,kb)

@router.callback_query(F.data.startswith('membereco:'))
async def member_economy_panel(call: types.CallbackQuery):
    if not call.message or not await panel_is_admin(call): await call.answer('❌',show_alert=True); return
    uid=int(call.data.split(':')[1]); gid=await panel_group_id(call); await call.answer()
    row=await db_execute("SELECT name,points,coins FROM users WHERE chat_id=? AND user_id=?",(gid,uid),fetchone=True)
    if not row: await call.answer('❌ العضو غير موجود.',show_alert=True); return
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⭐ + Points',callback_data=f'econ:addp:{uid}'),InlineKeyboardButton(text='🪙 + Coins',callback_data=f'econ:addc:{uid}')],
        [InlineKeyboardButton(text='➖ Points',callback_data=f'econ:subp:{uid}'),InlineKeyboardButton(text='➖ Coins',callback_data=f'econ:subc:{uid}')],
        [InlineKeyboardButton(text='🎁 إهداء',callback_data=f'econ:gift:{uid}')],
        [InlineKeyboardButton(text='⬅️ رجوع',callback_data=f'member:{uid}')]
    ])
    await edit_dashboard(call,f'💰 <b>{html.escape(row[0] or "عضو")}</b>\n⭐ Points: {row[1]}\n🪙 Coins: {row[2]}\n\nاختر العملية:',kb)

@router.callback_query(F.data.startswith('econ:'))
async def economy_action_panel(call: types.CallbackQuery):
    if not call.message or call.from_user.id != OWNER_ID: await call.answer('❌ Owner فقط.',show_alert=True); return
    _,action,uid_s=call.data.split(':'); uid=int(uid_s); gid=await panel_group_id(call); await call.answer()
    pending_panel_actions[call.from_user.id]={'action':action,'group_id':gid,'target_id':uid}
    prompts={'addp':'⭐ أرسل كمية الـPoints','addc':'🪙 أرسل كمية الـCoins','subp':'➖ أرسل كمية الـPoints للخصم','subc':'➖ أرسل كمية الـCoins للخصم','gift':'🎁 أرسل الكمية، وسيتم خصمها من رصيدك وإرسالها للعضو'}
    await call.message.answer(prompts.get(action,'أرسل الكمية')+'\nمثال: <code>1000</code>')

@router.callback_query(F.data.startswith('membervip:'))
async def member_vip_panel(call: types.CallbackQuery):
    if not call.message or call.from_user.id != OWNER_ID: await call.answer('❌ Owner فقط.',show_alert=True); return
    uid=int(call.data.split(':')[1]); gid=await panel_group_id(call); await call.answer()
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💎 يوم',callback_data=f'vipset:1:{uid}'),InlineKeyboardButton(text='💎 7 أيام',callback_data=f'vipset:7:{uid}')],
        [InlineKeyboardButton(text='💎 30 يوم',callback_data=f'vipset:30:{uid}'),InlineKeyboardButton(text='♾️ دائم',callback_data=f'vipset:0:{uid}')],
        [InlineKeyboardButton(text='❌ إزالة VIP',callback_data=f'vipset:-1:{uid}')],
        [InlineKeyboardButton(text='⬅️ رجوع',callback_data=f'member:{uid}')]
    ])
    await edit_dashboard(call,'💎 <b>إدارة VIP</b>\nاختر المدة:',kb)

@router.callback_query(F.data.startswith('vipset:'))
async def vip_set_panel(call: types.CallbackQuery):
    if not call.message or call.from_user.id != OWNER_ID: await call.answer('❌ Owner فقط.',show_alert=True); return
    _,days_s,uid_s=call.data.split(':'); days=int(days_s); uid=int(uid_s); gid=await panel_group_id(call)
    if days==-1:
        await db_execute("DELETE FROM user_vip WHERE chat_id=? AND user_id=?",(gid,uid)); await call.answer('❌ تمت إزالة VIP')
    else:
        expires=0 if days==0 else time.time()+days*86400
        await db_execute("INSERT INTO user_vip(chat_id,user_id,expires_at) VALUES(?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET expires_at=excluded.expires_at",(gid,uid,expires))
        await call.answer('💎 تم منح VIP')
    call.data=f'member:{uid}'; await member_card(call)

@router.callback_query(F.data.startswith('quick:'))
async def quick_action(call: types.CallbackQuery):
    if not call.message or not await panel_is_admin(call):
        await call.answer('❌ ليس لديك صلاحية.',show_alert=True); return
    _,action,uid_s=call.data.split(':'); uid=int(uid_s)
    role=await get_role(await panel_group_id(call),call.from_user.id)
    allowed={
        'owner': {'ban','unban','kick','mute','unmute','warn','unwarn'},
        'admin': {'ban','unban','kick','mute','unmute','warn','unwarn'},
        'moderator': {'ban','unban','kick','mute','unmute','warn','unwarn'},
        'helper': {'mute','unmute','warn','unwarn'}
    }
    if action not in allowed.get(role,set()):
        await call.answer('❌ هذه الصلاحية ليست متاحة لرتبتك.',show_alert=True); return
    if uid==OWNER_ID:
        await call.answer('❌ لا يمكن تنفيذ ذلك على Owner.',show_alert=True); return
    try:
        if action=='ban':
            await bot.ban_chat_member((await panel_group_id(call)),uid); await db_execute('INSERT OR IGNORE INTO banned VALUES(?,?)',((await panel_group_id(call)),uid))
        elif action=='unban':
            await bot.unban_chat_member((await panel_group_id(call)),uid,only_if_banned=True); await db_execute('DELETE FROM banned WHERE chat_id=? AND user_id=?',((await panel_group_id(call)),uid))
        elif action=='kick':
            await bot.ban_chat_member((await panel_group_id(call)),uid); await bot.unban_chat_member((await panel_group_id(call)),uid,only_if_banned=True)
        elif action=='mute':
            await bot.restrict_chat_member((await panel_group_id(call)),uid,permissions=ChatPermissions(can_send_messages=False)); await db_execute('INSERT OR IGNORE INTO muted VALUES(?,?)',((await panel_group_id(call)),uid))
        elif action=='unmute':
            await bot.restrict_chat_member((await panel_group_id(call)),uid,permissions=ChatPermissions(can_send_messages=True,can_send_audios=True,can_send_documents=True,can_send_photos=True,can_send_videos=True,can_send_video_notes=True,can_send_voice_notes=True,can_send_polls=True,can_send_other_messages=True,can_add_web_page_previews=True)); await db_execute('DELETE FROM muted WHERE chat_id=? AND user_id=?',((await panel_group_id(call)),uid))
        elif action=='warn': await db_execute('UPDATE users SET warnings=warnings+1 WHERE chat_id=? AND user_id=?',((await panel_group_id(call)),uid))
        elif action=='unwarn': await db_execute('UPDATE users SET warnings=CASE WHEN warnings>0 THEN warnings-1 ELSE 0 END WHERE chat_id=? AND user_id=?',((await panel_group_id(call)),uid))
        await call.answer('✅ تم التنفيذ')
        # Refresh the same member card without requiring a new message.
        call.data=f'member:{uid}'
        await member_card(call)
    except Exception:
        await call.answer('❌ فشل التنفيذ. تأكد أن البوت أدمن بالصلاحيات اللازمة.',show_alert=True)

@router.callback_query(F.data == 'dash_security')
async def dash_security(call: types.CallbackQuery):
    if not call.message or not await panel_is_admin(call): await call.answer('❌',show_alert=True); return
    await call.answer()
    row=await db_execute('SELECT anti_link,anti_spam,anti_flood,auto_warn,welcome,lock_mode,media_lock FROM groups WHERE chat_id=?',((await panel_group_id(call)),),fetchone=True)
    if not row: row=(1,1,1,1,1,0,0)
    cols=['anti_link','anti_spam','anti_flood','auto_warn','welcome','lock_mode','media_lock']
    labels=['🔗 Anti-Link','🚫 Anti-Spam','🌊 Anti-Flood','⚠️ Auto-Warn','👋 Welcome','🔒 Lock','🖼️ Media-Lock']
    kb=[]
    for label,col,val in zip(labels,cols,row): kb.append([InlineKeyboardButton(text=f'{label}: {"🟢 ON" if val else "🔴 OFF"}',callback_data=f'toggle:{col}')])
    kb.append([InlineKeyboardButton(text='🔙 الرئيسية',callback_data='dash_home')])
    await edit_dashboard(call,'🛡️ <b>مركز الحماية</b>\nاضغط لتشغيل/إيقاف:',InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith('toggle:'))
async def dashboard_toggle(call: types.CallbackQuery):
    if not call.message or not await panel_is_admin(call): await call.answer('❌',show_alert=True); return
    col=call.data.split(':',1)[1]
    allowed={'anti_link','anti_spam','anti_flood','auto_warn','welcome','lock_mode','media_lock'}
    if col not in allowed: await call.answer(); return
    row=await db_execute(f'SELECT {col} FROM groups WHERE chat_id=?',((await panel_group_id(call)),),fetchone=True)
    new=0 if row and row[0] else 1
    await db_execute(f'UPDATE groups SET {col}=? WHERE chat_id=?',(new,(await panel_group_id(call))))
    await call.answer('تم التغيير ✅')
    call.data='dash_security'
    await dash_security(call)

@router.callback_query(F.data == 'dash_admins')
async def dash_admins(call: types.CallbackQuery):
    if not call.message or not await panel_is_admin(call): await call.answer('❌',show_alert=True); return
    await call.answer()
    rows=await db_execute("SELECT name,username,user_id,role FROM users WHERE chat_id=? AND role IN ('admin','helper','moderator') ORDER BY role,name",((await panel_group_id(call)),),fetch=True)
    text=f'👑 <b>المشرفون</b>\n\n👑 Owner: <code>{OWNER_ID}</code>\n'
    for name,un,uid,role in rows: text+=f'{ROLE_NAMES.get(role,role)} {name}'+(f' (@{un})' if un else '')+f' — <code>{uid}</code>\n'
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='➕ إضافة Admin',callback_data='role:addadmin')],[InlineKeyboardButton(text='➕ إضافة Helper',callback_data='role:addhelper')], [InlineKeyboardButton(text='➕ إضافة Moderator',callback_data='role:addmod')],[InlineKeyboardButton(text='➖ إزالة Admin',callback_data='role:deladmin')],[InlineKeyboardButton(text='➖ إزالة Helper',callback_data='role:delhelper')], [InlineKeyboardButton(text='➖ إزالة Moderator',callback_data='role:delmod')],[InlineKeyboardButton(text='🔐 تغيير الكود',callback_data='code_help')],[InlineKeyboardButton(text='🔙 الرئيسية',callback_data='dash_home')]])
    await edit_dashboard(call,text,kb)

@router.callback_query(F.data.startswith('role:'))
async def role_help(call: types.CallbackQuery):
    if call.from_user.id!=OWNER_ID: await call.answer('❌ Owner فقط.',show_alert=True); return
    await call.answer(); await call.message.answer(f'👤 رد على رسالة الشخص ثم اكتب `/{call.data.split(":")[1]}`.',parse_mode='Markdown')

@router.callback_query(F.data == 'code_help')
async def code_help(call: types.CallbackQuery):
    if call.from_user.id!=OWNER_ID: await call.answer('❌ Owner فقط.',show_alert=True); return
    await call.answer(); await call.message.answer('🔐 استخدم `/setcode NEWCODE` أو `/changecode NEWCODE`.',parse_mode='Markdown')

@router.callback_query(F.data == 'dash_welcome')
async def dash_welcome(call: types.CallbackQuery):
    if not call.message or not await panel_is_admin(call): await call.answer('❌',show_alert=True); return
    await call.answer(); row=await db_execute('SELECT welcome,welcome_text FROM groups WHERE chat_id=?',((await panel_group_id(call)),),fetchone=True)
    val=row[0] if row else 1; txt=row[1] if row else 'أهلاً بك {name} 👋'
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f'👋 {"إيقاف" if val else "تشغيل"}',callback_data='toggle:welcome')],[InlineKeyboardButton(text='✏️ تغيير النص',callback_data='welcome_help')],[InlineKeyboardButton(text='🔙 الرئيسية',callback_data='dash_home')]])
    await edit_dashboard(call,f'👋 <b>الترحيب</b>\nالحالة: {"🟢 ON" if val else "🔴 OFF"}\n\n{txt}\n\nالمتغيرات: {{name}} {{username}} {{title}}',kb)

@router.callback_query(F.data == 'welcome_help')
async def welcome_help(call: types.CallbackQuery):
    await call.answer(); await call.message.answer('✏️ استخدم `/setwelcome نص الترحيب`.',parse_mode='Markdown')

@router.callback_query(F.data == 'dash_rules')
async def dash_rules(call: types.CallbackQuery):
    if not call.message or not await panel_is_admin(call): await call.answer('❌',show_alert=True); return
    await call.answer(); row=await db_execute('SELECT rules FROM groups WHERE chat_id=?',((await panel_group_id(call)),),fetchone=True)
    rules=row[0] if row else 'لا توجد قوانين محددة حالياً.'
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✏️ تعديل',callback_data='rules_help')],[InlineKeyboardButton(text='🗑️ حذف',callback_data='rules_del')],[InlineKeyboardButton(text='🔙 الرئيسية',callback_data='dash_home')]])
    await edit_dashboard(call,f'📜 <b>القوانين</b>\n\n{rules}',kb)

@router.callback_query(F.data == 'rules_help')
async def rules_help(call: types.CallbackQuery):
    await call.answer(); await call.message.answer('✏️ استخدم `/setrules نص القوانين`.',parse_mode='Markdown')

@router.callback_query(F.data == 'rules_del')
async def rules_del(call: types.CallbackQuery):
    if not call.message or not await panel_is_admin(call): await call.answer('❌',show_alert=True); return
    await db_execute("UPDATE groups SET rules='لا توجد قوانين محددة حالياً.' WHERE chat_id=?",((await panel_group_id(call)),)); await call.answer('🗑️ تم الحذف')

@router.callback_query(F.data == 'dash_stats')
async def dash_stats(call: types.CallbackQuery):
    if not call.message or not await panel_is_admin(call): await call.answer('❌',show_alert=True); return
    await call.answer()
    total=(await db_execute('SELECT COUNT(*) FROM users WHERE chat_id=?',((await panel_group_id(call)),),fetchone=True))[0]
    msgs=(await db_execute('SELECT COALESCE(SUM(messages),0) FROM users WHERE chat_id=?',((await panel_group_id(call)),),fetchone=True))[0]
    warns=(await db_execute('SELECT COALESCE(SUM(warnings),0) FROM users WHERE chat_id=?',((await panel_group_id(call)),),fetchone=True))[0]
    banned=(await db_execute('SELECT COUNT(*) FROM banned WHERE chat_id=?',((await panel_group_id(call)),),fetchone=True))[0]
    muted=(await db_execute('SELECT COUNT(*) FROM muted WHERE chat_id=?',((await panel_group_id(call)),),fetchone=True))[0]
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔄 تحديث',callback_data='dash_stats')],[InlineKeyboardButton(text='🔙 الرئيسية',callback_data='dash_home')]])
    await edit_dashboard(call,f'📊 <b>الإحصائيات</b>\n\n👥 أعضاء: {total}\n💬 رسائل: {msgs}\n⚠️ تحذيرات: {warns}\n🚫 محظورون: {banned}\n🔇 مكتومون: {muted}',kb)

@router.callback_query(F.data == 'dash_games')
async def dash_games(call: types.CallbackQuery):
    await call.answer()
    if call.message: await edit_dashboard(call,'🎮 <b>الألعاب والترفيه</b>',InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🎲 نرد',callback_data='game:dice'),InlineKeyboardButton(text='🪙 عملة',callback_data='game:coin')],[InlineKeyboardButton(text='🎱 8Ball',callback_data='game:8ball'),InlineKeyboardButton(text='✊ RPS',callback_data='game:rps')],[InlineKeyboardButton(text='🎯 تخمين',callback_data='game:guess'),InlineKeyboardButton(text='🧠 Quiz',callback_data='game:quiz')],[InlineKeyboardButton(text='😂 نكتة',callback_data='game:joke'),InlineKeyboardButton(text='💡 معلومة',callback_data='game:fact')],[InlineKeyboardButton(text='🏆 رتبتي',callback_data='game:rank')],[InlineKeyboardButton(text='🔙 الرئيسية',callback_data='dash_home')]]))

@router.callback_query(F.data.startswith('game:'))
async def dashboard_game(call: types.CallbackQuery):
    await call.answer()
    game=call.data.split(':',1)[1]
    if game=='dice': out=f'🎲 النتيجة: {random.randint(1,6)}'
    elif game=='coin': out='🪙 '+random.choice(['وجه','كتابة'])
    elif game=='8ball': out='🎱 '+random.choice(['نعم ✅','لا ❌','ممكن 🤔','أكيد 🔥'])
    elif game=='rps': out='✊ '+random.choice(['حجر 🪨','ورق 📄','مقص ✂️'])
    elif game=='guess': out='🎯 استخدم `/guess` لبدء لعبة التخمين.'
    elif game=='quiz': out='🧠 استخدم `/quiz` لبدء الاختبار.'
    elif game=='joke': out=random.choice(['😂 واحد قال لصاحبه: عندك وقت؟ قاله: عندي ساعة.','😂 واحد نسى كلمة السر… نساها.'])
    elif game=='fact': out=random.choice(['💡 للأخطبوط ثلاثة قلوب.','💡 الضوء من الشمس يصل للأرض في حوالي 8 دقائق و20 ثانية.'])
    else: out='🏆 استخدم `/rank` لمعرفة رتبتك.'
    await call.message.answer(out)

@router.callback_query(F.data == 'dash_settings')
async def dash_settings(call: types.CallbackQuery):
    if not call.message or not await panel_is_admin(call): await call.answer('❌',show_alert=True); return
    await call.answer(); row=await db_execute('SELECT warn_limit FROM groups WHERE chat_id=?',((await panel_group_id(call)),),fetchone=True); limit=row[0] if row else 3
    await edit_dashboard(call,f'⚙️ <b>الإعدادات</b>\n\n⚠️ حد التحذيرات: {limit}\n🐢 Slowmode: /slowmode 10',InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔢 تغيير حد التحذيرات',callback_data='warn_help')],[InlineKeyboardButton(text='🐢 Slowmode',callback_data='slow_help')],[InlineKeyboardButton(text='🔙 الرئيسية',callback_data='dash_home')]]))

@router.callback_query(F.data == 'warn_help')
async def warn_help(call: types.CallbackQuery): await call.answer(); await call.message.answer('🔢 مثال: `/setwarns 3`',parse_mode='Markdown')
@router.callback_query(F.data == 'slow_help')
async def slow_help(call: types.CallbackQuery): await call.answer(); await call.message.answer('🐢 مثال: `/slowmode 10`',parse_mode='Markdown')

@router.callback_query(F.data == 'dash_help')
async def dash_help(call: types.CallbackQuery):
    await call.answer(); await edit_dashboard(call,HELP_HTML,InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔙 الرئيسية',callback_data='dash_home')]]))

@router.message(Command("achievements"))
async def achievements_cmd(message):
    await save_user(message)
    row=await db_execute("SELECT points,messages,streak FROM users WHERE chat_id=? AND user_id=?",(message.chat.id,message.from_user.id),fetchone=True)
    points,messages,streak=row if row else (0,0,0)
    achieved=[]
    if messages>=100: achieved.append('🥉 البداية')
    if messages>=500: achieved.append('🥈 نشيط')
    if messages>=5000: achieved.append('🥇 أسطورة')
    if points>=10000: achieved.append('💎 10K Points')
    if streak>=7: achieved.append('🔥 7 أيام')
    await message.answer('🏆 <b>إنجازاتك</b>\n\n'+('\n'.join(achieved) if achieved else 'لم تفتح إنجازات بعد.'))

@router.message(Command("inventory"))
async def inventory_cmd(message):
    await save_user(message); rows=await db_execute("SELECT i.item_id,i.quantity,s.name FROM inventory i LEFT JOIN shop_items s ON s.id=i.item_id WHERE i.chat_id=? AND i.user_id=? AND i.quantity>0",(message.chat.id,message.from_user.id),fetch=True)
    await message.answer('🎒 <b>مخزوني</b>\n\n'+('\n'.join(f'#{r[0]} {r[2] or "عنصر"} ×{r[1]}' for r in rows) if rows else 'المخزون فارغ.'))

@router.message(Command('quiz'))
async def quiz_cmd(message: types.Message):
    q,a=random.choice([('ما عاصمة تونس؟','تونس'),('كم عدد أيام الأسبوع؟','7'),('ما أكبر كوكب؟','المشتري')])
    await db_execute('INSERT OR REPLACE INTO notes(chat_id,name,value) VALUES(?,?,?)',(message.chat.id,'quiz_answer',a))
    await message.answer(f'🧠 <b>Quiz</b>\n{q}\n\nأرسل الإجابة.')

@router.message(Command('joke'))
async def joke_cmd(message: types.Message): await message.answer(random.choice(['😂 مرة واحد نسي كلمة السر… نساها.','😂 واحد قال لصاحبه: عندك وقت؟ قاله: عندي ساعة.']))

@router.message(Command('fact'))
async def fact_cmd(message: types.Message): await message.answer(random.choice(['💡 للأخطبوط ثلاثة قلوب.','💡 الضوء من الشمس يصل إلى الأرض في حوالي 8 دقائق و20 ثانية.']))

@router.message(Command('rank'))
async def rank_cmd(message: types.Message):
    await save_user(message); row=await db_execute('SELECT messages,warnings,role,points,coins,streak FROM users WHERE chat_id=? AND user_id=?',(message.chat.id,message.from_user.id),fetchone=True)
    msgs=row[0] if row else 0; warns=row[1] if row else 0; role=row[2] if row else 'member'; points=row[3] if row else 0; coins=row[4] if row else 0; streak=row[5] if row else 0; level=max(1,points//100+1)
    await message.answer(f'🏆 <b>بطاقة الرتبة</b>\n{ROLE_NAMES.get(role,role)}\n⭐ المستوى: {level}\n⭐ Points: {points}\n🪙 Coins: {coins}\n💬 الرسائل: {msgs}\n🔥 Streak: {streak}\n⚠️ التحذيرات: {warns}\n💎 VIP: {"نعم" if await vip_active(message.chat.id,message.from_user.id) else "لا"}')


# ==================== ECONOMY / VIP / SHOP ====================
async def vip_active(chat_id, user_id):
    row = await db_execute("SELECT expires_at FROM user_vip WHERE chat_id=? AND user_id=?", (chat_id,user_id), fetchone=True)
    return bool(row and (row[0] == 0 or row[0] > time.time()))

async def ensure_economy_user(message):
    await save_user(message)
    await db_execute("UPDATE users SET points=COALESCE(points,0), coins=COALESCE(coins,0) WHERE chat_id=? AND user_id=?", (message.chat.id,message.from_user.id))

@router.message(Command("points","balance"))
async def points_cmd(message):
    await ensure_economy_user(message)
    row=await db_execute("SELECT points,coins FROM users WHERE chat_id=? AND user_id=?",(message.chat.id,message.from_user.id),fetchone=True)
    await message.answer(f"⭐ Points: <b>{row[0]}</b>\
🪙 Coins: <b>{row[1]}</b>\
💎 VIP: {'نعم' if await vip_active(message.chat.id,message.from_user.id) else 'لا'}")

@router.message(Command("top"))
async def top_cmd(message):
    await save_user(message)
    rows=await db_execute("SELECT name,points,coins FROM users WHERE chat_id=? ORDER BY points DESC LIMIT 10",(message.chat.id,),fetch=True)
    text="🏆 <b>Top 10</b>\
\
"+"\
".join(f"{i}. {n or 'عضو'} — ⭐ {p} | 🪙 {c}" for i,(n,p,c) in enumerate(rows,1))
    await message.answer(text)

@router.message(Command("daily"))
async def daily_cmd(message):
    await ensure_economy_user(message)
    # one daily reward tracked through notes table per user
    key=f"daily_{message.from_user.id}"
    row=await db_execute("SELECT value FROM notes WHERE chat_id=? AND name=?",(message.chat.id,key),fetchone=True)
    now=int(time.time()); last=int(row[0]) if row else 0
    if now-last < 86400:
        await message.answer(f"⏳ ارجع بعد {86400-(now-last)} ثانية."); return
    bonus=10; coins=25
    await db_execute("UPDATE users SET points=points+?, coins=coins+?, streak=streak+1 WHERE chat_id=? AND user_id=?",(bonus,coins,message.chat.id,message.from_user.id))
    await db_execute("INSERT OR REPLACE INTO notes(chat_id,name,value) VALUES(?,?,?)",(message.chat.id,key,str(now)))
    await message.answer(f"🎁 مكافأتك اليومية: +{bonus} ⭐ و +{coins} 🪙")

async def transfer_currency(message, target, amount, col, label, icon):
    if amount<=0: await message.answer("❌ الكمية يجب أن تكون أكبر من 0."); return
    if target.id==message.from_user.id: await message.answer("❌ لا يمكنك الإهداء لنفسك."); return
    await save_user(message)
    await db_execute("INSERT OR IGNORE INTO users(chat_id,user_id,username,name) VALUES(?,?,?,?,?)"[:0] if False else "INSERT OR IGNORE INTO users(chat_id,user_id,username,name) VALUES(?,?,?,?)",(message.chat.id,target.id,target.username or '',target.full_name))
    row=await db_execute(f"SELECT {col} FROM users WHERE chat_id=? AND user_id=?",(message.chat.id,message.from_user.id),fetchone=True)
    if not row or row[0]<amount: await message.answer(f"❌ رصيدك من {label} غير كافٍ."); return
    await db_execute(f"UPDATE users SET {col}={col}-? WHERE chat_id=? AND user_id=?",(amount,message.chat.id,message.from_user.id))
    await db_execute(f"UPDATE users SET {col}={col}+? WHERE chat_id=? AND user_id=?",(amount,message.chat.id,target.id))
    await message.answer(f"🎁 {message.from_user.full_name} أهدى {target.full_name} {icon} <b>{amount}</b> {label}.")

@router.message(Command("giftpoints"))
async def giftpoints(message):
    target=await get_target(message); args=message.text.split()
    if not target or len(args)<2 or not args[-1].isdigit(): await message.answer("❌ رد على رسالة العضو واكتب `/giftpoints 100`.",parse_mode="Markdown"); return
    await transfer_currency(message,target,int(args[-1]),"points","Points","⭐")

@router.message(Command("giftcoins"))
async def giftcoins(message):
    target=await get_target(message); args=message.text.split()
    if not target or len(args)<2 or not args[-1].isdigit(): await message.answer("❌ رد على رسالة العضو واكتب `/giftcoins 100`.",parse_mode="Markdown"); return
    await transfer_currency(message,target,int(args[-1]),"coins","Coins","🪙")

@router.message(Command("givepoints","givecoins","removepoints","removecoins"))
async def owner_economy_command(message):
    if not await require_owner(message): return
    target=get_target(message); args=message.text.split()
    if not target or len(args)<2 or not args[-1].lstrip('-').isdigit(): await message.answer("❌ رد على العضو واكتب الأمر مع الكمية."); return
    amount=int(args[-1]); cmd=args[0].split('@')[0].lower()
    col='points' if 'points' in cmd else 'coins'; sign=1 if cmd.startswith('/give') else -1
    if amount<0: amount=abs(amount)
    await db_execute(f"UPDATE users SET {col}=MAX(0,{col}+?) WHERE chat_id=? AND user_id=?",(sign*amount,message.chat.id,target.id))
    await db_execute("INSERT INTO economy_log(chat_id,operator_id,target_id,kind,amount,created_at) VALUES(?,?,?,?,?,?)",(message.chat.id,message.from_user.id,target.id,cmd,amount,time.time()))
    await message.answer(f"✅ تم {'إعطاء' if sign>0 else 'خصم'} {amount} من {col} لـ {target.full_name}.")

@router.message(Command("vip"))
async def vip_cmd(message):
    if not await require_owner(message): return
    target=get_target(message); args=message.text.split()
    if not target or len(args)<2: await message.answer("❌ رد على العضو واكتب `/vip 30` أو `/vip permanent`.",parse_mode="Markdown"); return
    days=args[-1].lower(); expires=0 if days in ('permanent','دائم','forever') else time.time()+max(1,int(days))*86400
    await db_execute("INSERT INTO user_vip(chat_id,user_id,expires_at) VALUES(?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET expires_at=excluded.expires_at",(message.chat.id,target.id,expires))
    await message.answer(f"💎 تم منح {target.full_name} VIP {'دائم' if expires==0 else f'لمدة {days} يوم'}.")

@router.message(Command("delvip"))
async def delvip_cmd(message):
    if not await require_owner(message): return
    target=get_target(message)
    if not target: await message.answer("❌ رد على العضو."); return
    await db_execute("DELETE FROM user_vip WHERE chat_id=? AND user_id=?",(message.chat.id,target.id)); await message.answer("❌ تم إزالة VIP.")

@router.message(Command("giftvip"))
async def giftvip_cmd(message):
    if not await require_owner(message): return
    target=get_target(message); args=message.text.split()
    if not target or len(args)<2 or not args[-1].isdigit(): await message.answer('❌ رد على العضو واكتب `/giftvip 30`.',parse_mode='Markdown'); return
    days=int(args[-1]);
    if days<=0: await message.answer('❌ المدة يجب أن تكون أكبر من 0.'); return
    expires=time.time()+days*86400
    await db_execute("INSERT INTO user_vip(chat_id,user_id,expires_at) VALUES(?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET expires_at=excluded.expires_at",(message.chat.id,target.id,expires))
    await message.answer(f'💎 تم إهداء VIP إلى {target.full_name} لمدة {days} يوم.')

@router.message(Command("shopadd"))
async def shopadd_cmd(message):
    if not await require_owner(message): return
    raw=message.text.split(maxsplit=1)
    if len(raw)<2: await message.answer('❌ الاسم | السعر | الوصف | النوع | المخزون | vip'); return
    parts=[x.strip() for x in raw[1].split('|')]
    if len(parts)<2 or not parts[1].isdigit(): await message.answer('❌ الصيغة خاطئة.'); return
    name=parts[0]; price=int(parts[1]); desc=parts[2] if len(parts)>2 else ''; typ=parts[3] if len(parts)>3 else 'custom'; stock=int(parts[4]) if len(parts)>4 and parts[4].lstrip('-').isdigit() else -1; vip=1 if len(parts)>5 and parts[5].lower() in ('vip','1','yes','نعم') else 0
    await db_execute("INSERT INTO shop_items(name,description,price,item_type,stock,vip_only,active) VALUES(?,?,?,?,?,?,1)",(name,desc,price,typ,stock,vip))
    newid=(await db_execute("SELECT id FROM shop_items ORDER BY id DESC LIMIT 1",fetchone=True))[0]
    await message.answer(f'✅ تمت إضافة المنتج {name} برقم #{newid}')

@router.message(Command("shopdel"))
async def shopdel_cmd(message):
    if not await require_owner(message): return
    args=message.text.split();
    if len(args)<2 or not args[1].isdigit(): await message.answer('❌ `/shopdel ID`',parse_mode='Markdown'); return
    await db_execute("UPDATE shop_items SET active=0 WHERE id=?",(int(args[1]),)); await message.answer('🗑️ تم إخفاء المنتج.')

@router.message(Command("shopprice"))
async def shopprice_cmd(message):
    if not await require_owner(message): return
    args=message.text.split();
    if len(args)<3 or not args[1].isdigit() or not args[2].isdigit(): await message.answer('❌ `/shopprice ID PRICE`',parse_mode='Markdown'); return
    await db_execute("UPDATE shop_items SET price=? WHERE id=?",(int(args[2]),int(args[1]))); await message.answer('✅ تم تغيير السعر.')

@router.message(Command("auctioncreate"))
async def auctioncreate_cmd(message):
    if not await require_owner(message): return
    raw=message.text.split(maxsplit=1); parts=[x.strip() for x in raw[1].split('|')] if len(raw)>1 else []
    if len(parts)<3 or not parts[1].isdigit() or not parts[2].isdigit(): await message.answer('❌ الاسم | السعر الابتدائي | الدقائق | الوصف'); return
    await db_execute("INSERT INTO auctions(chat_id,name,description,start_price,highest_bid,ends_at,active) VALUES(?,?,?,?,?,?,1)",(message.chat.id,parts[0],parts[3] if len(parts)>3 else '',int(parts[1]),int(parts[1]),time.time()+int(parts[2])*60))
    await message.answer('🏆 تم إنشاء المزاد.')

@router.message(Command("bid"))
async def bid_cmd(message):
    await ensure_economy_user(message); args=message.text.split()
    if len(args)<3 or not args[1].isdigit() or not args[2].isdigit(): await message.answer('❌ `/bid ID AMOUNT`',parse_mode='Markdown'); return
    aid=int(args[1]); amount=int(args[2]); auc=await db_execute("SELECT highest_bid,highest_user,ends_at,active,name FROM auctions WHERE id=? AND chat_id=?",(aid,message.chat.id),fetchone=True)
    if not auc or not auc[3] or auc[2]<=time.time(): await message.answer('❌ المزاد منتهي.'); return
    if amount<=auc[0]: await message.answer(f'❌ يجب أن يكون العرض أكبر من {auc[0]}.'); return
    bal=await db_execute("SELECT coins FROM users WHERE chat_id=? AND user_id=?",(message.chat.id,message.from_user.id),fetchone=True)
    if not bal or bal[0]<amount: await message.answer('❌ Coins غير كافية.'); return
    # refund previous bidder, charge current bidder
    if auc[1] and auc[1]!=message.from_user.id:
        await db_execute("UPDATE users SET coins=coins+? WHERE chat_id=? AND user_id=?",(auc[0],message.chat.id,auc[1]))
    await db_execute("UPDATE users SET coins=coins-? WHERE chat_id=? AND user_id=?",(amount,message.chat.id,message.from_user.id))
    await db_execute("UPDATE auctions SET highest_bid=?,highest_user=? WHERE id=?",(amount,message.from_user.id,aid))
    await message.answer(f'🏆 تم تسجيل عرضك {amount} 🪙 على {auc[4]}.')

@router.message(Command("market"))
async def market_cmd(message):
    await save_user(message); rows=await db_execute("SELECT m.id,s.name,m.price,m.quantity FROM market_listings m JOIN shop_items s ON s.id=m.item_id WHERE m.chat_id=? AND m.active=1 ORDER BY m.id DESC LIMIT 20",(message.chat.id,),fetch=True)
    await message.answer('🏪 <b>سوق الأعضاء</b>\n\n'+('\n'.join(f'#{r[0]} {r[1]} — 🪙 {r[2]} ×{r[3]}' for r in rows) if rows else 'السوق فارغ.')+'\n\nشراء: /marketbuy ID')

@router.message(Command("marketadd"))
async def marketadd_cmd(message):
    await ensure_economy_user(message); args=message.text.split()
    if len(args)<3 or not args[1].isdigit() or not args[2].isdigit(): await message.answer('❌ `/marketadd ITEM_ID PRICE`',parse_mode='Markdown'); return
    item_id=int(args[1]); price=int(args[2]); inv=await db_execute("SELECT quantity FROM inventory WHERE chat_id=? AND user_id=? AND item_id=?",(message.chat.id,message.from_user.id,item_id),fetchone=True)
    if not inv or inv[0]<1: await message.answer('❌ لا تملك هذا المنتج في مخزونك.'); return
    await db_execute("UPDATE inventory SET quantity=quantity-1 WHERE chat_id=? AND user_id=? AND item_id=?",(message.chat.id,message.from_user.id,item_id))
    await db_execute("INSERT INTO market_listings(chat_id,seller_id,item_id,price,quantity,created_at) VALUES(?,?,?,?,1,?)",(message.chat.id,message.from_user.id,item_id,price,time.time()))
    await message.answer('🏪 تم عرض المنتج للبيع.')

@router.message(Command("marketbuy"))
async def marketbuy_cmd(message):
    await ensure_economy_user(message); args=message.text.split()
    if len(args)<2 or not args[1].isdigit(): await message.answer('❌ `/marketbuy ID`',parse_mode='Markdown'); return
    listing=await db_execute("SELECT seller_id,item_id,price,quantity,active FROM market_listings WHERE id=? AND chat_id=?",(int(args[1]),message.chat.id),fetchone=True)
    if not listing or not listing[4] or listing[3]<1: await message.answer('❌ العرض غير موجود.'); return
    seller,item_id,price,qty,_=listing
    if seller==message.from_user.id: await message.answer('❌ لا يمكنك شراء عرضك.'); return
    bal=await db_execute("SELECT coins FROM users WHERE chat_id=? AND user_id=?",(message.chat.id,message.from_user.id),fetchone=True)
    if not bal or bal[0]<price: await message.answer('❌ Coins غير كافية.'); return
    await db_execute("UPDATE users SET coins=coins-? WHERE chat_id=? AND user_id=?",(price,message.chat.id,message.from_user.id))
    await db_execute("UPDATE users SET coins=coins+? WHERE chat_id=? AND user_id=?",(price,message.chat.id,seller))
    await db_execute("INSERT INTO inventory(chat_id,user_id,item_id,quantity) VALUES(?,?,?,1) ON CONFLICT(chat_id,user_id,item_id) DO UPDATE SET quantity=quantity+1",(message.chat.id,message.from_user.id,item_id))
    await db_execute("UPDATE market_listings SET quantity=quantity-1,active=CASE WHEN quantity<=1 THEN 0 ELSE 1 END WHERE id=?",(int(args[1]),))
    await message.answer('✅ تم الشراء وإضافة المنتج إلى مخزونك.')

@router.message(Command("shop"))
async def shop_cmd(message):
    await save_user(message)
    rows=await db_execute("SELECT id,name,description,price,stock,limited,vip_only FROM shop_items WHERE active=1 ORDER BY id DESC",fetch=True)
    if not rows: await message.answer("🛒 المتجر فارغ حاليًا. يمكن للـOwner إضافة المنتجات من الـPanel."); return
    text="🛒 <b>المتجر</b>\
\
"
    for i,n,d,price,stock,limited,viponly in rows:
        if viponly and not await vip_active(message.chat.id,message.from_user.id): continue
        text+=f"#{i} <b>{n}</b> — 🪙 {price}\
{d}\
{'🔴 محدود' if limited else ''} {'📦 '+str(stock) if stock>=0 else '♾️'}\
\
"
    await message.answer(text)

@router.message(Command("buy"))
async def buy_cmd(message):
    await ensure_economy_user(message); args=message.text.split()
    if len(args)<2 or not args[1].isdigit(): await message.answer("❌ مثال: `/buy 1`",parse_mode="Markdown"); return
    item=await db_execute("SELECT name,price,stock,limited,vip_only,active FROM shop_items WHERE id=?",(int(args[1]),),fetchone=True)
    if not item or not item[5]: await message.answer("❌ المنتج غير موجود."); return
    name,price,stock,limited,viponly,active=item
    if viponly and not await vip_active(message.chat.id,message.from_user.id): await message.answer("💎 هذا المنتج للـVIP فقط."); return
    row=await db_execute("SELECT coins FROM users WHERE chat_id=? AND user_id=?",(message.chat.id,message.from_user.id),fetchone=True)
    if row[0]<price: await message.answer("❌ Coins غير كافية."); return
    if stock==0: await message.answer("❌ المنتج نفد."); return
    await db_execute("UPDATE users SET coins=coins-? WHERE chat_id=? AND user_id=?",(price,message.chat.id,message.from_user.id))
    if stock>0: await db_execute("UPDATE shop_items SET stock=stock-1 WHERE id=?",(int(args[1]),))
    typ=await db_execute("SELECT item_type,duration_days,title_text,badge_text FROM shop_items WHERE id=?",(int(args[1]),),fetchone=True)
    typ=typ or ('custom',0,'','')
    itype,duration,title,badge=typ
    if itype=='vip':
        exp=0 if duration<=0 else time.time()+duration*86400
        await db_execute("INSERT INTO user_vip(chat_id,user_id,expires_at) VALUES(?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET expires_at=excluded.expires_at",(message.chat.id,message.from_user.id,exp))
        await message.answer(f'💎 حصلت على VIP: <b>{name}</b>')
    elif itype in ('title','badge','custom'):
        await db_execute("INSERT INTO inventory(chat_id,user_id,item_id,quantity) VALUES(?,?,?,1) ON CONFLICT(chat_id,user_id,item_id) DO UPDATE SET quantity=quantity+1",(message.chat.id,message.from_user.id,int(args[1])))
        if itype=='title': await message.answer(f'🏷️ حصلت على اللقب <b>{title or name}</b>.')
        elif itype=='badge': await message.answer(f'🏅 حصلت على الشارة <b>{badge or name}</b>.')
        else: await message.answer(f'📦 تم شراء <b>{name}</b> وإضافته لمخزونك.')
    elif itype in ('boost_xp','boost_coins','luck'):
        until=time.time()+max(1,duration or 1)*86400
        col={'boost_xp':'xp_until','boost_coins':'coins_until','luck':'luck_until'}[itype]
        await db_execute(f"INSERT INTO user_boosts(chat_id,user_id,{col}) VALUES(?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET {col}=excluded.{col}",(message.chat.id,message.from_user.id,until))
        await message.answer(f'⚡ تم تفعيل {name}.')
    else:
        await db_execute("INSERT INTO shop_orders(chat_id,user_id,item_id,created_at) VALUES(?,?,?,?)",(message.chat.id,message.from_user.id,int(args[1]),time.time()))
        await message.answer(f"✅ تم شراء <b>{name}</b>.\n📦 طلبك قيد المراجعة من الـOwner.")

@router.message(Command("giftitem"))
async def giftitem_cmd(message):
    await ensure_economy_user(message); target=get_target(message); args=message.text.split()
    if not target or len(args)<2 or not args[-1].isdigit(): await message.answer("❌ رد على العضو واكتب `/giftitem ID`.",parse_mode="Markdown"); return
    if target.id==message.from_user.id: await message.answer('❌ لا يمكنك إهداء نفسك.'); return
    item_id=int(args[-1]); item=await db_execute("SELECT name,price,stock,active,vip_only FROM shop_items WHERE id=?",(item_id,),fetchone=True)
    if not item or not item[3]: await message.answer("❌ المنتج غير موجود."); return
    if item[4] and not await vip_active(message.chat.id,target.id): await message.answer('💎 هذا المنتج يمكن إهداؤه فقط لعضو VIP.'); return
    if item[2]==0: await message.answer('❌ المنتج نفد.'); return
    row=await db_execute("SELECT coins FROM users WHERE chat_id=? AND user_id=?",(message.chat.id,message.from_user.id),fetchone=True)
    if not row or row[0]<item[1]: await message.answer("❌ Coins غير كافية."); return
    await db_execute("UPDATE users SET coins=coins-? WHERE chat_id=? AND user_id=?",(item[1],message.chat.id,message.from_user.id))
    if item[2]>0: await db_execute("UPDATE shop_items SET stock=stock-1 WHERE id=?",(item_id,))
    await db_execute("INSERT INTO inventory(chat_id,user_id,item_id,quantity) VALUES(?,?,?,1) ON CONFLICT(chat_id,user_id,item_id) DO UPDATE SET quantity=quantity+1",(message.chat.id,target.id,item_id))
    await message.answer(f"🎁 تم إهداء <b>{item[0]}</b> إلى {target.full_name}.")


@router.callback_query(F.data == 'dash_economy')
async def dash_economy(call):
    if not call.message or not await panel_is_admin(call): await call.answer('❌',show_alert=True); return
    gid=await panel_group_id(call); await call.answer()
    rows=await db_execute("SELECT COUNT(*),COALESCE(SUM(points),0),COALESCE(SUM(coins),0) FROM users WHERE chat_id=?",(gid,),fetchone=True)
    text=f"💰 <b>الاقتصاد والمتجر</b>\
\
👥 أعضاء الاقتصاد: {rows[0]}\
⭐ مجموع Points: {rows[1]}\
🪙 مجموع Coins: {rows[2]}\
\
🎁 الإهداء متاح للأعضاء.\
💎 الـOwner يستطيع منح VIP.\
🛒 المتجر والمزادات والمنتجات المحدودة تحت إدارة الـOwner."
    buttons=[[InlineKeyboardButton(text='🛒 المتجر',callback_data='shop_manage')],[InlineKeyboardButton(text='🎁 الطلبات',callback_data='shop_orders')],[InlineKeyboardButton(text='🏆 المزادات',callback_data='auction_manage')],[InlineKeyboardButton(text='🏪 سوق الأعضاء',callback_data='market_manage')],[InlineKeyboardButton(text='💎 VIP',callback_data='vip_manage')],[InlineKeyboardButton(text='🔙 الرئيسية',callback_data='dash_home')]]
    if await get_role(gid, call.from_user.id) not in ('owner','admin'):
        buttons=[[InlineKeyboardButton(text='📊 اقتصاد الأعضاء',callback_data='econ_stats')],[InlineKeyboardButton(text='🔙 الرئيسية',callback_data='dash_home')]]
    kb=InlineKeyboardMarkup(inline_keyboard=buttons)
    await edit_dashboard(call,text,kb)

@router.callback_query(F.data == 'econ_stats')
async def econ_stats(call):
    if not call.message or not await panel_is_admin(call): await call.answer('❌',show_alert=True); return
    gid=await panel_group_id(call); await call.answer()
    row=await db_execute("SELECT COUNT(*),COALESCE(SUM(points),0),COALESCE(SUM(coins),0) FROM users WHERE chat_id=?",(gid,),fetchone=True)
    await edit_dashboard(call,f'💰 <b>اقتصاد المجموعة</b>\n\n👥 {row[0]} أعضاء\n⭐ {row[1]} Points\n🪙 {row[2]} Coins',InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔙 الرئيسية',callback_data='dash_home')]]))

@router.callback_query(F.data.in_({'shop_manage','shop_orders','auction_manage','vip_manage','market_manage'}))
async def economy_owner_sections(call):
    if not call.message or call.from_user.id!=OWNER_ID: await call.answer('❌ Owner فقط.',show_alert=True); return
    await call.answer(); action=call.data
    if action=='shop_manage':
        rows=await db_execute("SELECT id,name,price,stock,limited,vip_only,active,item_type FROM shop_items ORDER BY id DESC LIMIT 20",fetch=True)
        text='🛒 <b>إدارة المتجر</b>\n\n' + ('\n'.join(f'#{r[0]} {r[1]} — 🪙 {r[2]} | stock={r[3]} | {r[7]}' for r in rows) if rows else 'المتجر فارغ.')
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='➕ إضافة منتج',callback_data='shop_add_help')],[InlineKeyboardButton(text='🗑️ حذف منتج',callback_data='shop_del_help')],[InlineKeyboardButton(text='💰 تغيير السعر',callback_data='shop_price_help')],[InlineKeyboardButton(text='🔙 الاقتصاد',callback_data='dash_economy')]])
        await edit_dashboard(call,text,kb)
    elif action=='shop_orders':
        rows=await db_execute("SELECT o.id,o.user_id,o.item_id,o.status,s.name,o.chat_id FROM shop_orders o JOIN shop_items s ON s.id=o.item_id WHERE o.status IN ('pending','gift_pending') ORDER BY o.id DESC LIMIT 15",fetch=True)
        text='🎁 <b>طلبات المتجر</b>\n\n'+('\n'.join(f'#{r[0]} — {r[4]} — User {r[1]} — {r[3]}' for r in rows) if rows else 'لا توجد طلبات معلقة.')
        buttons=[]
        for r in rows: buttons.append([InlineKeyboardButton(text=f'✅ قبول #{r[0]}',callback_data=f'order:ok:{r[0]}'),InlineKeyboardButton(text=f'❌ رفض #{r[0]}',callback_data=f'order:no:{r[0]}')])
        buttons.append([InlineKeyboardButton(text='🔙 الاقتصاد',callback_data='dash_economy')])
        await edit_dashboard(call,text,InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action=='auction_manage':
        rows=await db_execute("SELECT id,name,start_price,highest_bid,highest_user,ends_at FROM auctions WHERE active=1 ORDER BY id DESC LIMIT 10",fetch=True)
        text='🏆 <b>المزادات</b>\n\n'+('\n'.join(f'#{r[0]} {r[1]} — يبدأ {r[2]} — أعلى {r[3]} — ينتهي بعد {max(0,int(r[5]-time.time()))}ث' for r in rows) if rows else 'لا توجد مزادات نشطة.')
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='➕ إنشاء مزاد',callback_data='auction_help')],[InlineKeyboardButton(text='🔙 الاقتصاد',callback_data='dash_economy')]])
        await edit_dashboard(call,text,kb)
    elif action=='market_manage':
        rows=await db_execute("SELECT id,seller_id,item_id,price,quantity FROM market_listings WHERE chat_id=? AND active=1 ORDER BY id DESC LIMIT 15",(await panel_group_id(call),),fetch=True)
        text='🏪 <b>سوق الأعضاء</b>\n\n'+('\n'.join(f'#{r[0]} seller={r[1]} item={r[2]} — 🪙 {r[3]} ×{r[4]}' for r in rows) if rows else 'لا توجد عروض نشطة.')
        await edit_dashboard(call,text,InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔙 الاقتصاد',callback_data='dash_economy')]]))
    else:
        await edit_dashboard(call,'💎 <b>VIP</b>\nاختر عضوًا من قسم الأعضاء ثم اضغط VIP.\nيمكن أيضًا استخدام أزرار VIP من بطاقة العضو.',InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='👥 الأعضاء',callback_data='dash_members:0')],[InlineKeyboardButton(text='🔙 الاقتصاد',callback_data='dash_economy')]]))

@router.callback_query(F.data == 'shop_add_help')
async def shop_add_help(call):
    if call.from_user.id!=OWNER_ID: await call.answer('❌ Owner فقط.',show_alert=True); return
    await call.answer(); await call.message.answer('➕ أضف منتجًا بالأمر الخاص: `/shopadd الاسم | السعر | الوصف | النوع | المخزون | vip`\nالأنواع: custom,title,badge,vip,boost_xp,boost_coins,luck,extra_game',parse_mode='Markdown')

@router.callback_query(F.data == 'shop_del_help')
async def shop_del_help(call):
    if call.from_user.id!=OWNER_ID: await call.answer('❌ Owner فقط.',show_alert=True); return
    await call.answer(); await call.message.answer('🗑️ `/shopdel ID`',parse_mode='Markdown')

@router.callback_query(F.data == 'shop_price_help')
async def shop_price_help(call):
    if call.from_user.id!=OWNER_ID: await call.answer('❌ Owner فقط.',show_alert=True); return
    await call.answer(); await call.message.answer('💰 `/shopprice ID PRICE`',parse_mode='Markdown')

@router.callback_query(F.data == 'auction_help')
async def auction_help(call):
    if call.from_user.id!=OWNER_ID: await call.answer('❌ Owner فقط.',show_alert=True); return
    await call.answer(); await call.message.answer('🏆 `/auctioncreate الاسم | السعر الابتدائي | المدة بالدقائق | الوصف`\nثم الأعضاء يستعملون `/bid ID AMOUNT`',parse_mode='Markdown')

@router.callback_query(F.data.startswith('order:'))
async def order_action(call):
    if call.from_user.id!=OWNER_ID: await call.answer('❌ Owner فقط.',show_alert=True); return
    _,act,oid=call.data.split(':'); oid=int(oid)
    row=await db_execute("SELECT chat_id,user_id,item_id,status FROM shop_orders WHERE id=?",(oid,),fetchone=True)
    if not row or row[3] not in ('pending','gift_pending'): await call.answer('الطلب منتهي.',show_alert=True); return
    if act=='ok':
        await db_execute("UPDATE shop_orders SET status='approved' WHERE id=?",(oid,)); await call.answer('✅ تم القبول')
        try: await bot.send_message(row[0],f'🎁 تم قبول طلبك #{oid} ✅')
        except: pass
    else:
        item=await db_execute("SELECT price FROM shop_items WHERE id=?",(row[2],),fetchone=True)
        if item: await db_execute("UPDATE users SET coins=coins+? WHERE chat_id=? AND user_id=?",(item[0],row[0],row[1]))
        await db_execute("UPDATE shop_orders SET status='rejected' WHERE id=?",(oid,)); await call.answer('❌ تم الرفض واسترجاع Coins')
    call.data='shop_orders'; await economy_owner_sections(call)

@router.message(Command("lock"))
async def lock_cmd(message: types.Message):
    if not await require_admin(message): return
    await ensure_group(message.chat.id)
    await db_execute("UPDATE groups SET lock_mode=1 WHERE chat_id=?", (message.chat.id,))
    try:
        await bot.set_chat_permissions(message.chat.id, permissions=ChatPermissions(can_send_messages=False))
        await message.answer("🔒 تم قفل المجموعة.")
    except TelegramBadRequest as e:
        await message.answer(f"❌ لم أستطع القفل: {e}")

@router.message(Command("unlock"))
async def unlock_cmd(message: types.Message):
    if not await require_admin(message): return
    await ensure_group(message.chat.id)
    await db_execute("UPDATE groups SET lock_mode=0 WHERE chat_id=?", (message.chat.id,))
    try:
        await bot.set_chat_permissions(message.chat.id, permissions=ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True))
        await message.answer("🔓 تم فتح المجموعة.")
    except TelegramBadRequest as e:
        await message.answer(f"❌ لم أستطع الفتح: {e}")

@router.message(F.chat.type == 'private')
async def private_panel_input(message: types.Message):
    action = pending_panel_actions.get(message.from_user.id)
    if not action or not message.text or not message.text.strip().isdigit():
        return
    amount=int(message.text.strip())
    if amount<=0: await message.answer('❌ يجب أن تكون الكمية أكبر من 0.'); return
    gid=action['group_id']; uid=action['target_id']; kind=action['action']
    if kind in ('addp','subp','addc','subc'):
        col='points' if kind.endswith('p') else 'coins'; sign=1 if kind.startswith('add') else -1
        await db_execute(f"UPDATE users SET {col}=MAX(0,{col}+?) WHERE chat_id=? AND user_id=?",(sign*amount,gid,uid))
        await db_execute("INSERT INTO economy_log(chat_id,operator_id,target_id,kind,amount,created_at) VALUES(?,?,?,?,?,?)",(gid,message.from_user.id,uid,kind,amount,time.time()))
        await message.answer(f"✅ تم {'إضافة' if sign>0 else 'خصم'} {amount} من {col}.")
    elif kind=='gift':
        row=await db_execute("SELECT coins FROM users WHERE chat_id=? AND user_id=?",(gid,message.from_user.id),fetchone=True)
        if not row or row[0]<amount: await message.answer('❌ رصيدك غير كافٍ.'); pending_panel_actions.pop(message.from_user.id,None); return
        await db_execute("UPDATE users SET coins=coins-? WHERE chat_id=? AND user_id=?",(amount,gid,message.from_user.id))
        await db_execute("UPDATE users SET coins=coins+? WHERE chat_id=? AND user_id=?",(amount,gid,uid))
        await message.answer(f'🎁 تم إهداء {amount} 🪙 للعضو.')
    pending_panel_actions.pop(message.from_user.id,None)

@router.chat_member()
async def register_chat_member(event: types.ChatMemberUpdated):
    if not event.chat or event.chat.type not in ("group", "supergroup"):
        return
    new_status = event.new_chat_member.status
    old_status = event.old_chat_member.status
    # تسجيل من انضم فعلياً، مع تجاهل البوت نفسه والحالات التي تعني خروجه.
    joined = new_status in ("member", "administrator", "creator", "restricted") and old_status in ("left", "kicked")
    if not joined:
        return
    u = event.new_chat_member.user
    if u.is_bot:
        return
    await ensure_group(event.chat.id)
    await db_execute("""
        INSERT INTO users(chat_id,user_id,username,name)
        VALUES(?,?,?,?)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
        username=excluded.username,name=excluded.name
    """, (event.chat.id, u.id, u.username or "", u.full_name))


@router.message(F.new_chat_members)
async def register_new_members(message: types.Message):
    if message.chat.type not in ("group", "supergroup"):
        return
    await ensure_group(message.chat.id)
    for member in message.new_chat_members:
        if member.is_bot:
            continue
        await db_execute("""
            INSERT INTO users(chat_id,user_id,username,name)
            VALUES(?,?,?,?)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET
            username=excluded.username,name=excluded.name
        """, (message.chat.id, member.id, member.username or "", member.full_name))
        row = await db_execute("SELECT welcome,welcome_text FROM groups WHERE chat_id=?", (message.chat.id,), fetchone=True)
        if row and row[0]:
            text = row[1].replace("{name}", member.full_name).replace("{username}", "@" + member.username if member.username else member.full_name)
            await message.answer(text)


@router.message()
async def protection_and_welcome(message: types.Message):
    if not message.from_user:
        return

    await save_user(message)

    # Economy XP/Points: 1 point per message, with basic anti-spam already active below.
    if message.chat.type in ('group','supergroup') and not message.new_chat_members:
        boost=await db_execute("SELECT xp_until FROM user_boosts WHERE chat_id=? AND user_id=?",(message.chat.id,message.from_user.id),fetchone=True)
        xp=2 if boost and boost[0]>time.time() else 1
        await db_execute("UPDATE users SET messages=messages+1, points=points+? WHERE chat_id=? AND user_id=?", (xp,message.chat.id,message.from_user.id))

    if message.new_chat_members:
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


async def settle_auctions_loop():
    while True:
        try:
            rows=await db_execute("SELECT id,chat_id,name,highest_bid,highest_user FROM auctions WHERE active=1 AND ends_at<=?",(time.time(),),fetch=True)
            for aid,gid,name,bid,user_id in rows:
                await db_execute("UPDATE auctions SET active=0 WHERE id=? AND active=1",(aid,))
                if user_id:
                    try: await bot.send_message(gid,f'🏆 انتهى المزاد #{aid} على <b>{name}</b>\n🎉 الفائز: <code>{user_id}</code>\n💰 العرض: {bid} 🪙')
                    except Exception: pass
        except Exception:
            pass
        await asyncio.sleep(20)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Delete any old webhook before polling. Telegram does not allow
    # getUpdates (long polling) while a webhook is active.
    await bot.delete_webhook(drop_pending_updates=True)

    task = asyncio.create_task(dp.start_polling(bot))
    auction_task = asyncio.create_task(settle_auctions_loop())
    try:
        yield
    finally:
        task.cancel(); auction_task.cancel()
        for t in (task, auction_task):
            try:
                await t
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
