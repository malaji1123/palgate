# Palgate Gate Opener

A Telegram bot that lets you open a Palgate smart gate remotely, with user access management and a full activity log.

## How it works

1. **`palgate_link.py`** links this machine to your PalGate account via QR code (like adding a new phone). It calls the PalGate API to get a session token and saves it to `.env`.
2. **`palgate_bot.py`** runs a Telegram bot. Approved users send `/opengate` and the bot calls the PalGate API to trigger the gate. All users and gate events are stored in a local SQLite database.

The bot uses the reverse-engineered [pylgate](https://github.com/DonutByte/pylgate) library to authenticate with the PalGate API (`https://api1.pal-es.com/v1/bt/`). Each API call requires a freshly derived token (expires in ~5s), so it is regenerated on every request.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create a Telegram bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** BotFather gives you (looks like `123456:ABC-DEF...`)
4. To find your own **Telegram user ID**, message [@userinfobot](https://t.me/userinfobot)

### 3. Configure `.env`

Create a `.env` file in the project root (see `.env.example`):

```env
# Telegram
BOT_TOKEN=123456:ABC-DEF...          # from BotFather
ADMIN_USER_ID=123456789              # your Telegram user ID

# Gate device
DEVICE_ID=your_gate_device_id        # printed on the PalGate device
OUTPUT_NUM=1                         # output number (default 1)

# Session — filled automatically by palgate_link.py
PHONE_NUMBER=
SESSION_TOKEN=
TOKEN_TYPE=
```

> `DEVICE_ID` is the serial number printed on your PalGate device (e.g. `4G300204754`).

### 4. Link your device (one-time)

```bash
python palgate_link.py
```

A QR code will appear in the terminal. In the PalGate app go to:
**Menu → Linked Devices → Link a Device → scan the QR code**

The script will save `PHONE_NUMBER`, `SESSION_TOKEN`, and `TOKEN_TYPE` to your `.env` automatically.

> PalGate supports up to 2 linked devices. Linking a 3rd will revoke the oldest one.

### 5. Run the bot

```bash
python palgate_bot.py
```

---

## Bot commands

| Command | Who | Description |
|---|---|---|
| `/opengate` | approved users | Open the gate |
| `/requestaccess` | anyone | Send an access request to the admin |
| `/adduser <id>` | admin | Approve a user by their Telegram ID |
| `/removeuser <id>` | admin | Revoke a user's access |
| `/listusers` | admin | List all approved users with join dates |
| `/history` | admin | Show last 10 gate open attempts |
| `/help` | all | Show available commands |

When a user sends `/requestaccess`, the admin receives a message with **Approve / Deny** buttons.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | Yes | Telegram bot token from BotFather |
| `ADMIN_USER_ID` | Yes | Your Telegram user ID — has full admin access |
| `DEVICE_ID` | Yes | PalGate device serial number |
| `OUTPUT_NUM` | No | Gate output number (default: `1`) |
| `PHONE_NUMBER` | Yes* | Your phone number — set by `palgate_link.py` |
| `SESSION_TOKEN` | Yes* | Session token hex — set by `palgate_link.py` |
| `TOKEN_TYPE` | Yes* | Token type integer — set by `palgate_link.py` |

*Set automatically when you run `palgate_link.py`.

---

## Files

| File | Purpose |
|---|---|
| `palgate_link.py` | One-time device linking — writes session to `.env` |
| `palgate_bot.py` | Telegram bot |
| `.env` | Config + session credentials (gitignored) |
| `palgate.db` | SQLite database — users & gate events (gitignored) |
