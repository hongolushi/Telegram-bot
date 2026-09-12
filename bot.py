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


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not OWNER_ID:
    raise RuntimeError("OWNER_ID is missing")


DB_FILE = "bot.db"

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)


# =========================================================
# DATABASE
# =========================================================

async def db_execute(query, params=(), fetch=False, fetchone=False):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(query, params)

        if fetch:
            result = await cursor.fetchall()
        elif fetchone:
            result = await cursor.fetchone()
        else:
            result = None

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
        """
        INSERT OR IGNORE INTO groups(chat_id)
        VALUES(?)
        """,
        (chat_id,)
    )


async def save_user(message: types.Message):
    if not message.from_user:
        return

    await ensure_group(message.chat.id)

    user = message.from_user

    await db_execute(
        """
        INSERT INTO users(
            chat_id,
            user_id,
            username,
            name
        )
        VALUES(?,?,?,?)
        ON CONFLICT(chat_id,user_id)
        DO UPDATE SET
            username=excluded.username,
            name=excluded.name
        """,
        (
            message.chat.id,
            user.id,
            user.username or "",
            user.full_name
        )
    )


# =========================================================
# ROLES
# =========================================================

async def get_role(chat_id, user_id):

    if user_id == OWNER_ID:
        return "owner"

    row = await db_execute(
        """
        SELECT role
        FROM users
        WHERE chat_id=? AND user_id=?
        """,
        (chat_id, user_id),
        fetchone=True
    )

    if not row:
        return "member"

    return row[0]


async def set_role(chat_id, user_id, role):

    await db_execute(
        """
        INSERT INTO users(
            chat_id,
            user_id,
            role
        )
        VALUES(?,?,?)
        ON CONFLICT(chat_id,user_id)
        DO UPDATE SET role=excluded.role
        """,
        (
            chat_id,
            user_id,
            role
        )
    )


async def is_owner(message):
    return message.from_user and message.from_user.id == OWNER_ID


async def is_admin(message):

    role = await get_role(
        message.chat.id,
        message.from_user.id
    )

    return role in ("owner", "admin")


async def is_moderator(message):

    role = await get_role(
        message.chat.id,
        message.from_user.id
    )

    return role in ("owner", "admin", "moderator")


async def require_admin(message):

    if not await is_admin(message):
        await message.answer(
            "❌ هذا الأمر خاص بالـ Admins.\n\n"
            "إذا عندك كود الإدارة استعمل:\n"
            "`/admin CODE`",
            parse_mode="Markdown"
        )
        return False

    return True


async def require_owner(message):

    if not await is_owner(message):
        await message.answer(
            "❌ هذا الأمر خاص بالـ Owner فقط."
        )
        return False

    return True


# =========================================================
# HELP
# =========================================================

HELP_TEXT = """
🤖 *أوامر البوت*

👑 *الإدارة*
/admin CODE - دخول الإدارة
/admins - قائمة الإدارة
/addadmin - إضافة Admin
/deladmin - حذف Admin
/addmod - إضافة Moderator
/delmod - حذف Moderator
/setcode CODE - تغيير كود الإدارة

👮 *الأعضاء*
/ban - حظر
/unban - فك الحظر
/kick - طرد
/mute - كتم
/unmute - فك الكتم
/warn - تحذير
/unwarn - حذف تحذير
/warnings - عدد التحذيرات

🛡️ *الحماية*
/antilink on
/antilink off
/antispam on
/antispam off
/antiflood on
/antiflood off
/autowarn on
/autowarn off
/setwarns 3

👋 *الترحيب والقوانين*
/welcome on
/welcome off
/setwelcome النص
/rules
/setrules النص

📊 *المعلومات*
/stats
/info
/id
/members
/banned
/muted
/settings

🎮 *الألعاب*
/dice
/coin
/8ball
/rps
/guess

ℹ️ /about
"""


@router.message(Command("help"))
async def help_command(message: types.Message):
    await save_user(message)

    await message.answer(
        HELP_TEXT,
        parse_mode="Markdown"
    )


# =========================================================
# START
# =========================================================

@router.message(CommandStart())
async def start_command(message: types.Message):

    await save_user(message)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📖 الأوامر",
                    callback_data="help"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛡️ دخول الإدارة",
                    callback_data="admin"
                )
            ]
        ]
    )

    await message.answer(
        "🤖 *مرحباً بك في البوت!*\n\n"
        "🛡️ حماية وإدارة المجموعة\n"
        "🎮 ألعاب وترفيه\n"
        "📊 إحصائيات\n"
        "👮 إدارة الأعضاء",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "help")
async def callback_help(callback: types.CallbackQuery):

    await callback.message.answer(
        HELP_TEXT,
        parse_mode="Markdown"
    )

    await callback.answer()


@router.callback_query(F.data == "admin")
async def callback_admin(callback: types.CallbackQuery):

    await callback.message.answer(
        "🔐 للحصول على رتبة Admin:\n\n"
        "`/admin CODE`",
        parse_mode="Markdown"
    )

    await callback.answer()


# =========================================================
# ID
# =========================================================

@router.message(Command("id"))
async def id_command(message: types.Message):

    await save_user(message)

    user_id = message.from_user.id

    await message.answer(
        f"👤 User ID: `{user_id}`\n"
        f"💬 Chat ID: `{message.chat.id}`",
        parse_mode="Markdown"
    )


# =========================================================
# ADMIN LOGIN
# =========================================================

@router.message(Command("admin"))
async def admin_login(message: types.Message):

    await save_user(message)

    args = message.text.split(maxsplit=1)

    if len(args) < 2:
        await message.answer(
            "❌ اكتب الكود هكذا:\n\n"
            "`/admin CODE`",
            parse_mode="Markdown"
        )
        return

    code = args[1].strip()

    row = await db_execute(
        """
        SELECT admin_code
        FROM groups
        WHERE chat_id=?
        """,
        (message.chat.id,),
        fetchone=True
    )

    if not row or not row[0]:

        await message.answer(
            "⚠️ لم يتم تعيين كود للإدارة بعد.\n"
            "الـ Owner يستعمل:\n"
            "`/setcode CODE`",
            parse_mode="Markdown"
        )
        return

    hashed = hashlib.sha256(
        code.encode()
    ).hexdigest()

    if hashed != row[0]:

        await message.answer(
            "❌ الكود خاطئ."
        )
        return

    await set_role(
        message.chat.id,
        message.from_user.id,
        "admin"
    )

    await message.answer(
        "✅ تم تسجيلك كـ Admin بنجاح 👑"
    )


# =========================================================
# SET CODE
# =========================================================

@router.message(Command("setcode"))
async def setcode_command(message: types.Message):

    if not await require_owner(message):
        return

    args = message.text.split(maxsplit=1)

    if len(args) < 2:
        await message.answer(
            "❌ مثال:\n`/setcode 123456`",
            parse_mode="Markdown"
        )
        return

    code = args[1].strip()

    if len(code) < 4:
        await message.answer(
            "❌ الكود يجب أن يكون 4 أحرف/أرقام على الأقل."
        )
        return

    hashed = hashlib.sha256(
        code.encode()
    ).hexdigest()

    await ensure_group(message.chat.id)

    await db_execute(
        """
        UPDATE groups
        SET admin_code=?
        WHERE chat_id=?
        """,
        (
            hashed,
            message.chat.id
        )
    )

    await message.answer(
        "✅ تم تغيير كود الإدارة بنجاح."
    )


# =========================================================
# ADD ADMIN
# =========================================================

@router.message(Command("addadmin"))
async def addadmin_command(message: types.Message):

    if not await require_owner(message):
        return

    if not message.reply_to_message:
        await message.answer(
            "❌ ردّ على رسالة الشخص ثم اكتب:\n"
            "`/addadmin`",
            parse_mode="Markdown"
        )
        return

    target = message.reply_to_message.from_user

    await set_role(
        message.chat.id,
        target.id,
        "admin"
    )

    await message.answer(
        f"👑 تم تعيين {target.full_name} كـ Admin."
    )


# =========================================================
# DELETE ADMIN
# =========================================================

@router.message(Command("deladmin"))
async def deladmin_command(message: types.Message):

    if not await require_owner(message):
        return

    if not message.reply_to_message:
        await message.answer(
            "❌ ردّ على رسالة الـ Admin ثم اكتب:\n"
            "`/deladmin`",
            parse_mode="Markdown"
        )
        return

    target = message.reply_to_message.from_user

    if target.id == OWNER_ID:
        await message.answer(
            "❌ لا يمكن إزالة الـ Owner."
        )
        return

    await set_role(
        message.chat.id,
        target.id,
        "member"
    )

    await message.answer(
        f"✅ تم حذف رتبة Admin من {target.full_name}."
    )


# =========================================================
# MODERATOR
# =========================================================

@router.message(Command("addmod"))
async def addmod_command(message: types.Message):

    if not await require_owner(message):
        return

    if not message.reply_to_message:
        await message.answer(
            "❌ ردّ على رسالة الشخص ثم اكتب:\n"
            "`/addmod`",
            parse_mode="Markdown"
        )
        return

    target = message.reply_to_message.from_user

    await set_role(
        message.chat.id,
        target.id,
        "moderator"
    )

    await message.answer(
        f"🛡️ تم تعيين {target.full_name} كـ Moderator."
    )


@router.message(Command("delmod"))
async def delmod_command(message: types.Message):

    if not await require_owner(message):
        return

    if not message.reply_to_message:
        await message.answer(
            "❌ ردّ على رسالة الـ Moderator ثم اكتب:\n"
            "`/delmod`",
            parse_mode="Markdown"
        )
        return

    target = message.reply_to_message.from_user

    await set_role(
        message.chat.id,
        target.id,
        "member"
    )

    await message.answer(
        f"✅ تم حذف رتبة Moderator من {target.full_name}."
    )


# =========================================================
# ADMINS LIST
# =========================================================

@router.message(Command("admins"))
async def admins_command(message: types.Message):

    await save_user(message)

    rows = await db_execute(
        """
        SELECT user_id, name, role
        FROM users
        WHERE chat_id=?
        AND role IN ('admin','moderator')
        ORDER BY role
        """,
        (message.chat.id,),
        fetch=True
    )

    text = "👑 *الإدارة الحالية*\n\n"
    text += f"👑 Owner: `{OWNER_ID}`\n"

    for user_id, name, role in rows:

        icon = "🛡️" if role == "admin" else "🔧"

        text += (
            f"{icon} {name} — `{role}`\n"
        )

    await message.answer(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# TARGET HELPER
# =========================================================

async def get_target(message):

    if not message.reply_to_message:
        return None

    return message.reply_to_message.from_user


# =========================================================
# BAN
# =========================================================

@router.message(Command("ban"))
async def ban_command(message: types.Message):

    if not await require_admin(message):
        return

    target = await get_target(message)

    if not target:
        await message.answer(
            "❌ ردّ على رسالة الشخص ثم اكتب `/ban`."
        )
        return

    if target.id == OWNER_ID:
        await message.answer(
            "❌ لا يمكن حظر الـ Owner."
        )
        return

    try:

        await bot.ban_chat_member(
            message.chat.id,
            target.id
        )

        await db_execute(
            """
            INSERT OR IGNORE INTO banned
            VALUES(?,?)
            """,
            (
                message.chat.id,
                target.id
            )
        )

        await message.answer(
            f"🚫 تم حظر {target.full_name}."
        )

    except TelegramBadRequest:
        await message.answer(
            "❌ لا أستطيع حظر هذا الشخص. تأكد أن البوت Admin."
        )


# =========================================================
# UNBAN
# =========================================================

@router.message(Command("unban"))
async def unban_command(message: types.Message):

    if not await require_admin(message):
        return

    target = await get_target(message)

    if not target:
        await message.answer(
            "❌ ردّ على رسالة الشخص ثم اكتب `/unban`."
        )
        return

    try:

        await bot.unban_chat_member(
            message.chat.id,
            target.id,
            only_if_banned=True
        )

        await db_execute(
            """
            DELETE FROM banned
            WHERE chat_id=? AND user_id=?
            """,
            (
                message.chat.id,
                target.id
            )
        )

        await message.answer(
            f"✅ تم فك الحظر عن {target.full_name}."
        )

    except TelegramBadRequest:
        await message.answer(
            "❌ لم أستطع فك الحظر."
        )


# =========================================================
# KICK
# =========================================================

@router.message(Command("kick"))
async def kick_command(message: types.Message):

    if not await require_admin(message):
        return

    target = await get_target(message)

    if not target:
        await message.answer(
            "❌ ردّ على رسالة الشخص ثم اكتب `/kick`."
        )
        return

    if target.id == OWNER_ID:
        await message.answer(
            "❌ لا يمكن طرد الـ Owner."
        )
        return

    try:

        await bot.ban_chat_member(
            message.chat.id,
            target.id
        )

        await bot.unban_chat_member(
            message.chat.id,
            target.id
        )

        await message.answer(
            f"👢 تم طرد {target.full_name}."
        )

    except TelegramBadRequest:
        await message.answer(
            "❌ لا أستطيع طرد هذا الشخص."
        )


# =========================================================
# MUTE
# =========================================================

@router.message(Command("mute"))
async def mute_command(message: types.Message):

    if not await require_admin(message):
        return

    target = await get_target(message)

    if not target:
        await message.answer(
            "❌ ردّ على رسالة الشخص ثم اكتب `/mute`."
        )
        return

    if target.id == OWNER_ID:
        await message.answer(
            "❌ لا يمكن كتم الـ Owner."
        )
        return

    try:

        permissions = ChatPermissions(
            can_send_messages=False
        )

        await bot.restrict_chat_member(
            message.chat.id,
            target.id,
            permissions=permissions
        )

        await db_execute(
            """
            INSERT OR IGNORE INTO muted
            VALUES(?,?)
            """,
            (
                message.chat.id,
                target.id
            )
        )

        await message.answer(
            f"🔇 تم كتم {target.full_name}."
        )

    except TelegramBadRequest:
        await message.answer(
            "❌ لا أستطيع كتم هذا الشخص."
        )


# =========================================================
# UNMUTE
# =========================================================

@router.message(Command("unmute"))
async def unmute_command(message: types.Message):

    if not await require_admin(message):
        return

    target = await get_target(message)

    if not target:
        await message.answer(
            "❌ ردّ على رسالة الشخص ثم اكتب `/unmute`."
        )
        return

    try:

        permissions = ChatPermissions(
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

        await bot.restrict_chat_member(
            message.chat.id,
            target.id,
            permissions=permissions
        )

    
