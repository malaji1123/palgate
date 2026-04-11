"""
palgate_bot.py — Telegram bot that opens the gate with user management.

Commands (users):
    /opengate     — open the gate
    /requestaccess — ask admin to grant you access
    /help         — show available commands

Commands (admin only):
    /adduser <id>    — directly approve a user by ID
    /removeuser <id> — revoke a user's access
    /listusers       — show all approved users
    /opengate        — open the gate
    /help            — show all commands

Setup:
    1. Create a bot via @BotFather
    2. Set BOT_TOKEN and ADMIN_USER_ID below
    3. pip install "python-telegram-bot>=20"
    4. python palgate_bot.py
"""

import json
import logging
from pathlib import Path
from urllib.parse import urljoin

import os

import requests
import pylgate
from dotenv import load_dotenv
from pylgate.types import TokenType
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, Application,
)

load_dotenv()

BOT_TOKEN     = os.environ["BOT_TOKEN"]
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
DEVICE_ID     = os.environ["DEVICE_ID"]
OUTPUT_NUM    = int(os.getenv("OUTPUT_NUM", "1"))

USERS_FILE = Path(__file__).parent / "palgate_users.json"
BASE_URL     = "https://api1.pal-es.com/v1/bt/"

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)


# ── User store ────────────────────────────────────────────────────────────────

def load_users() -> set[int]:
    if not USERS_FILE.exists():
        return set()
    return set(json.loads(USERS_FILE.read_text()))


def save_users(users: set[int]):
    USERS_FILE.write_text(json.dumps(list(users), indent=2))


def is_approved(user_id: int) -> bool:
    return user_id == ADMIN_USER_ID or user_id in load_users()


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_USER_ID


# ── Gate ──────────────────────────────────────────────────────────────────────

def open_gate() -> str:
    phone = int(os.environ["PHONE_NUMBER"])
    token = bytes.fromhex(os.environ["SESSION_TOKEN"])
    ttype = TokenType(int(os.environ["TOKEN_TYPE"]))

    headers = {
        "User-Agent": "okhttp/4.9.3",
        "X-Bt-Token": pylgate.generate_token(token, phone, ttype),
    }
    url  = urljoin(BASE_URL, f"device/{DEVICE_ID}/open-gate")
    resp = requests.get(url, headers=headers, params={"outputNum": OUTPUT_NUM}, timeout=10)
    data = resp.json()

    if resp.status_code == 200 and data.get("status") == "ok":
        return "Gate opened!"
    return f"Failed: {resp.status_code} {resp.text}"


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_admin(user_id):
        await set_admin_menu(context.bot)
        await update.message.reply_text("Palgate bot ready (admin). Use /help to see all commands.")
    elif is_approved(user_id):
        await update.message.reply_text("Palgate bot ready. Use /help to see commands.")
    else:
        await update.message.reply_text(
            "You don't have access yet.\nUse /requestaccess to ask the admin for permission."
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_admin(user_id):
        text = (
            "*Admin commands:*\n"
            "/opengate — open the gate\n"
            "/adduser `<id>` — approve a user by Telegram ID\n"
            "/removeuser `<id>` — revoke a user's access\n"
            "/listusers — show all approved users\n"
            "/help — show this message"
        )
    elif is_approved(user_id):
        text = (
            "*Commands:*\n"
            "/opengate — open the gate\n"
            "/help — show this message"
        )
    else:
        text = (
            "*Commands:*\n"
            "/requestaccess — ask admin to grant you access\n"
            "/help — show this message"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_opengate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_approved(user_id):
        await update.message.reply_text("Not authorized. Use /requestaccess to request access.")
        return

    log.info(f"Gate open by {update.effective_user.username} ({user_id})")
    await update.message.reply_text("Opening gate...")
    try:
        result = open_gate()
        await update.message.reply_text(result)
    except Exception as e:
        log.error(f"Gate open failed: {e}")
        await update.message.reply_text(f"Error: {e}")


async def cmd_requestaccess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id   = update.effective_user.id
    username  = update.effective_user.username or update.effective_user.full_name

    if is_approved(user_id):
        await update.message.reply_text("You already have access.")
        return

    await update.message.reply_text("Request sent to admin. You'll be notified when approved.")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data=f"approve:{user_id}"),
            InlineKeyboardButton("Deny",    callback_data=f"deny:{user_id}"),
        ]
    ])
    await context.bot.send_message(
        chat_id=ADMIN_USER_ID,
        text=f"Access request from *{username}* (ID: `{user_id}`)",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def callback_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()

    action, uid_str = query.data.split(":")
    uid = int(uid_str)

    if action == "approve":
        users = load_users()
        users.add(uid)
        save_users(users)
        await query.edit_message_text(f"User {uid} approved.")
        await context.bot.send_message(chat_id=uid, text="Your access has been approved! Use /opengate to open the gate.")
        log.info(f"Admin approved user {uid}")

    elif action == "deny":
        await query.edit_message_text(f"User {uid} denied.")
        await context.bot.send_message(chat_id=uid, text="Your access request was denied.")
        log.info(f"Admin denied user {uid}")


async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /adduser <telegram_id>")
        return

    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID must be a number.")
        return

    users = load_users()
    users.add(uid)
    save_users(users)
    await update.message.reply_text(f"User {uid} added.")
    try:
        await context.bot.send_message(chat_id=uid, text="You've been granted access to the gate bot. Use /opengate.")
    except Exception:
        pass  # user may not have started the bot yet


async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /removeuser <telegram_id>")
        return

    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID must be a number.")
        return

    users = load_users()
    users.discard(uid)
    save_users(users)
    await update.message.reply_text(f"User {uid} removed.")


async def cmd_listusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return

    users = load_users()
    if not users:
        await update.message.reply_text("No approved users (besides admin).")
        return

    lines = "\n".join(str(uid) for uid in sorted(users))
    await update.message.reply_text(f"*Approved users:*\n{lines}", parse_mode="Markdown")


# ── Bot menu setup ────────────────────────────────────────────────────────────

ADMIN_COMMANDS = [
    BotCommand("opengate",    "Open the gate"),
    BotCommand("adduser",     "Approve a user by ID"),
    BotCommand("removeuser",  "Revoke a user's access"),
    BotCommand("listusers",   "List approved users"),
    BotCommand("help",        "Show all commands"),
]

USER_COMMANDS = [
    BotCommand("opengate",      "Open the gate"),
    BotCommand("requestaccess", "Request access from admin"),
    BotCommand("help",          "Show available commands"),
]


async def set_admin_menu(bot):
    from telegram import BotCommandScopeChat
    try:
        await bot.set_my_commands(commands=ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=ADMIN_USER_ID))
        log.info("Admin menu set.")
    except Exception as e:
        log.warning(f"Could not set admin menu (admin hasn't started the bot yet?): {e}")


async def post_init(app: Application):
    await app.bot.set_my_commands(USER_COMMANDS)
    await set_admin_menu(app.bot)
    log.info("Bot commands set.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not all(os.getenv(k) for k in ("PHONE_NUMBER", "SESSION_TOKEN", "TOKEN_TYPE")):
        print("No session found in .env. Run palgate_link.py first.")
        return

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",         cmd_start))
    app.add_handler(CommandHandler("help",          cmd_help))
    app.add_handler(CommandHandler("opengate",      cmd_opengate))
    app.add_handler(CommandHandler("requestaccess", cmd_requestaccess))
    app.add_handler(CommandHandler("adduser",       cmd_adduser))
    app.add_handler(CommandHandler("removeuser",    cmd_removeuser))
    app.add_handler(CommandHandler("listusers",     cmd_listusers))
    app.add_handler(CallbackQueryHandler(callback_access, pattern=r"^(approve|deny):"))

    log.info(f"Bot started. Admin: {ADMIN_USER_ID}")
    app.run_polling()


if __name__ == "__main__":
    main()
