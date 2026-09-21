"""
palgate_bot.py — Telegram bot that opens the gate with user management.

Commands (users):
    /opengate      — open the gate
    /requestaccess — ask admin to grant you access
    /help          — show available commands

Commands (admin only):
    /adduser <id>    — directly approve a user by ID
    /removeuser <id> — revoke a user's access
    /listusers       — show all approved users with join dates
    /history         — show last 10 gate opens
    /opengate        — open the gate
    /help            — show all commands

Setup:
    1. Create a bot via @BotFather
    2. Set BOT_TOKEN, ADMIN_USER_ID, DEVICE_ID in .env
    3. pip install -r requirements.txt
    4. python palgate_bot.py
"""

import asyncio
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
import pylgate
from dotenv import load_dotenv
from pylgate.types import TokenType
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, Application,
)

_env_file = os.getenv("ENV_FILE")
if _env_file and os.path.exists(_env_file):
    load_dotenv(_env_file)
elif os.path.exists("./.amir.env"):
    load_dotenv("./.amir.env")
else:
    load_dotenv()

BOT_TOKEN     = os.getenv("BOT_TOKEN", "")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
DEVICE_ID     = os.getenv("DEVICE_ID", "")
OUTPUT_NUM    = int(os.getenv("OUTPUT_NUM", "1"))
HTTP_PORT     = int(os.getenv("HTTP_PORT", "8080"))
API_KEY       = os.getenv("API_KEY", "")

DB_FILE  = Path(__file__).parent / "palgate.db"
BASE_URL = "https://api1.pal-es.com/v1/bt/"

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Global references for async notification from the sync HTTP server thread
_app_instance: Application | None = None
_loop_instance: asyncio.AbstractEventLoop | None = None


# ── Database ──────────────────────────────────────────────────────────────────

def db_connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    return con


def db_init():
    with db_connect() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                full_name  TEXT,
                approved   INTEGER NOT NULL DEFAULT 0,
                added_at   TEXT,
                added_by   INTEGER
            );

            CREATE TABLE IF NOT EXISTS gate_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                username   TEXT,
                opened_at  TEXT NOT NULL,
                success    INTEGER NOT NULL,
                response   TEXT
            );
        """)


# ── User store ────────────────────────────────────────────────────────────────

def upsert_user(user_id: int, username: str | None, full_name: str | None):
    """Insert user if not exists, update name fields if they changed."""
    with db_connect() as con:
        con.execute("""
            INSERT INTO users (user_id, username, full_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username  = excluded.username,
                full_name = excluded.full_name
        """, (user_id, username, full_name))


def approve_user(user_id: int, approved_by: int):
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as con:
        con.execute("""
            UPDATE users SET approved = 1, added_at = ?, added_by = ?
            WHERE user_id = ?
        """, (now, approved_by, user_id))


def revoke_user(user_id: int):
    with db_connect() as con:
        con.execute("UPDATE users SET approved = 0 WHERE user_id = ?", (user_id,))


def is_approved(user_id: int) -> bool:
    if user_id == ADMIN_USER_ID:
        return True
    with db_connect() as con:
        row = con.execute("SELECT approved FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return bool(row and row["approved"])


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_USER_ID


def get_approved_users() -> list[sqlite3.Row]:
    with db_connect() as con:
        return con.execute("""
            SELECT user_id, username, full_name, added_at
            FROM users WHERE approved = 1
            ORDER BY added_at
        """).fetchall()


# ── Gate event log ────────────────────────────────────────────────────────────

def log_gate_event(user_id: int, username: str | None, success: bool, response: str):
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as con:
        con.execute("""
            INSERT INTO gate_events (user_id, username, opened_at, success, response)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, username, now, int(success), response))


def get_recent_events(limit: int = 10) -> list[sqlite3.Row]:
    with db_connect() as con:
        return con.execute("""
            SELECT user_id, username, opened_at, success, response
            FROM gate_events
            ORDER BY opened_at DESC
            LIMIT ?
        """, (limit,)).fetchall()


# ── Gate ──────────────────────────────────────────────────────────────────────

def open_gate() -> tuple[bool, str]:
    phone_val = os.getenv("PHONE_NUMBER")
    token_val = os.getenv("SESSION_TOKEN")
    ttype_val = os.getenv("TOKEN_TYPE")

    if not (phone_val and token_val and ttype_val and DEVICE_ID):
        return False, "Missing PalGate configuration (PHONE_NUMBER, SESSION_TOKEN, TOKEN_TYPE, DEVICE_ID)"

    phone = int(phone_val)
    token = bytes.fromhex(token_val)
    ttype = TokenType(int(ttype_val))

    headers = {
        "User-Agent": "okhttp/4.9.3",
        "X-Bt-Token": pylgate.generate_token(token, phone, ttype),
    }
    url  = urljoin(BASE_URL, f"device/{DEVICE_ID}/open-gate")
    resp = requests.get(url, headers=headers, params={"outputNum": OUTPUT_NUM}, timeout=10)
    data = resp.json()

    if resp.status_code == 200 and data.get("status") == "ok":
        return True, "Gate opened!"
    return False, f"Failed: {resp.status_code} {resp.text}"


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.username, user.full_name)

    if is_admin(user.id):
        await set_admin_menu(context.bot)
        await update.message.reply_text("Palgate bot ready (admin). Use /help to see all commands.")
    elif is_approved(user.id):
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
            "/history — show last 10 gate opens\n"
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
    user = update.effective_user
    if not is_approved(user.id):
        await update.message.reply_text("Not authorized. Use /requestaccess to request access.")
        return

    log.info(f"Gate open by {user.username} ({user.id})")
    await update.message.reply_text("Opening gate...")
    try:
        success, result = open_gate()
        log_gate_event(user.id, user.username, success, result)
        await update.message.reply_text(result)
    except Exception as e:
        log.error(f"Gate open failed: {e}")
        log_gate_event(user.id, user.username, False, str(e))
        await update.message.reply_text(f"Error: {e}")


async def cmd_requestaccess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.username, user.full_name)

    if is_approved(user.id):
        await update.message.reply_text("You already have access.")
        return

    await update.message.reply_text("Request sent to admin. You'll be notified when approved.")

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Approve", callback_data=f"approve:{user.id}"),
        InlineKeyboardButton("Deny",    callback_data=f"deny:{user.id}"),
    ]])
    display = user.username or user.full_name
    await context.bot.send_message(
        chat_id=ADMIN_USER_ID,
        text=f"Access request from *{display}* (ID: `{user.id}`)",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def callback_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, uid_str = query.data.split(":")
    uid = int(uid_str)

    if action == "approve":
        approve_user(uid, ADMIN_USER_ID)
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

    upsert_user(uid, None, None)
    approve_user(uid, update.effective_user.id)
    await update.message.reply_text(f"User {uid} added.")
    try:
        await context.bot.send_message(chat_id=uid, text="You've been granted access to the gate bot. Use /opengate.")
    except Exception:
        pass


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

    revoke_user(uid)
    await update.message.reply_text(f"User {uid} removed.")


async def cmd_listusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return

    rows = get_approved_users()
    if not rows:
        await update.message.reply_text("No approved users (besides admin).")
        return

    lines = []
    for r in rows:
        name  = f"@{r['username']}" if r["username"] else r["full_name"] or "—"
        date  = r["added_at"][:10] if r["added_at"] else "?"
        lines.append(f"`{r['user_id']}` {name} — since {date}")

    await update.message.reply_text("*Approved users:*\n" + "\n".join(lines), parse_mode="Markdown")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Not authorized.")
        return

    rows = get_recent_events()
    if not rows:
        await update.message.reply_text("No gate events yet.")
        return

    lines = []
    for r in rows:
        name   = f"@{r['username']}" if r["username"] else str(r["user_id"])
        dt     = r["opened_at"][:16].replace("T", " ")
        status = "✓" if r["success"] else "✗"
        lines.append(f"{status} {dt}  {name}")

    await update.message.reply_text("*Last gate opens:*\n`" + "\n".join(lines) + "`", parse_mode="Markdown")


# ── Bot menu setup ────────────────────────────────────────────────────────────

ADMIN_COMMANDS = [
    BotCommand("opengate",    "Open the gate"),
    BotCommand("adduser",     "Approve a user by ID"),
    BotCommand("removeuser",  "Revoke a user's access"),
    BotCommand("listusers",   "List approved users with dates"),
    BotCommand("history",     "Show last 10 gate opens"),
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
        log.warning(f"Could not set admin menu: {e}")


async def post_init(app: Application):
    global _app_instance, _loop_instance
    _app_instance = app
    try:
        _loop_instance = asyncio.get_running_loop()
    except RuntimeError:
        _loop_instance = None
    await app.bot.set_my_commands(USER_COMMANDS)
    await set_admin_menu(app.bot)
    log.info("Bot commands set.")


# ── HTTP API (curl support) ──────────────────────────────────────────────────

class GateHttpRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _is_authenticated(self) -> bool:
        configured_key = os.getenv("API_KEY", "")
        if not configured_key:
            return True  # If no API_KEY is set in env, allow access

        # Check X-Api-Key header
        if self.headers.get("X-Api-Key") == configured_key:
            return True

        # Check Authorization: Bearer <key>
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:].strip() == configured_key:
            return True

        # Check query parameter ?key=<key>
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if qs.get("key", [None])[0] == configured_key:
            return True

        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/health", "/status"):
            self._send_json(200, {"status": "ok", "service": "palgate_bot"})
            return
        if parsed.path in ("/opengate", "/open"):
            self._handle_open_gate()
            return
        self._send_json(404, {"status": "error", "message": "Endpoint not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/opengate", "/open"):
            self._handle_open_gate()
            return
        self._send_json(404, {"status": "error", "message": "Endpoint not found"})

    def _handle_open_gate(self):
        if not self._is_authenticated():
            self._send_json(401, {
                "status": "error",
                "message": "Unauthorized. Provide API key via header 'X-Api-Key' or '?key=...'"
            })
            return

        log.info("Gate open triggered via HTTP API (curl)")
        try:
            success, result = open_gate()
            log_gate_event(0, "curl_api", success, result)

            if _app_instance and _loop_instance and ADMIN_USER_ID:
                try:
                    msg = f"🚪 Gate opened via API (curl): {result}" if success else f"⚠️ Gate open failed via API: {result}"
                    asyncio.run_coroutine_threadsafe(
                        _app_instance.bot.send_message(chat_id=ADMIN_USER_ID, text=msg),
                        _loop_instance,
                    )
                except Exception as ex:
                    log.warning(f"Could not send Telegram notification for API call: {ex}")

            status_code = 200 if success else 500
            self._send_json(status_code, {"status": "ok" if success else "failed", "message": result})
        except Exception as e:
            log.error(f"HTTP gate open failed: {e}")
            log_gate_event(0, "curl_api", False, str(e))
            self._send_json(500, {"status": "error", "message": str(e)})

    def log_message(self, format, *args):
        log.info("HTTP %s - " + format, self.address_string(), *args)


def start_http_server(port: int, host: str = "0.0.0.0") -> HTTPServer:
    server = HTTPServer((host, port), GateHttpRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(f"HTTP API server listening on http://{host}:{port}")
    return server


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not all(os.getenv(k) for k in ("PHONE_NUMBER", "SESSION_TOKEN", "TOKEN_TYPE")):
        print("No session found in .env. Run palgate_link.py first.")
        return

    db_init()

    # Start HTTP API server for curl / direct access
    if HTTP_PORT > 0:
        try:
            start_http_server(HTTP_PORT)
        except Exception as e:
            log.error(f"Failed to start HTTP server on port {HTTP_PORT}: {e}")

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
    app.add_handler(CommandHandler("history",       cmd_history))
    app.add_handler(CallbackQueryHandler(callback_access, pattern=r"^(approve|deny):"))

    log.info(f"Bot started. Admin: {ADMIN_USER_ID}")
    app.run_polling()


if __name__ == "__main__":
    main()
