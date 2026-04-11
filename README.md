# Palgate Gate Opener

Telegram bot to open a Palgate smart gate via the reverse-engineered API.

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file (see `.env.example`):
```
BOT_TOKEN=your_telegram_bot_token
ADMIN_USER_ID=your_telegram_user_id
DEVICE_ID=your_gate_device_id
OUTPUT_NUM=1
```

## Usage

### 1. Link your device (one-time)
```bash
python palgate_link.py
```
Scan the QR code in the PalGate app → **Menu → Linked Devices → Link a Device**.  
Saves session credentials (`PHONE_NUMBER`, `SESSION_TOKEN`, `TOKEN_TYPE`) to `.env`.

### 2. Run the Telegram bot
```bash
python palgate_bot.py
```

| Command | Who | Description |
|---|---|---|
| `/opengate` | all approved | Open the gate |
| `/requestaccess` | anyone | Send access request to admin |
| `/adduser <id>` | admin | Approve a user directly |
| `/removeuser <id>` | admin | Revoke access |
| `/listusers` | admin | List approved users |
| `/help` | all | Show available commands |

## Files

| File | Purpose |
|---|---|
| `palgate_link.py` | One-time device linking — writes session to `.env` |
| `palgate_bot.py` | Telegram bot |
| `.env` | Config + session credentials (gitignored) |
| `palgate_users.json` | Approved Telegram users (gitignored) |

## Notes

- Based on the reverse-engineered [pylgate](https://github.com/DonutByte/pylgate) library
- API: `https://api1.pal-es.com/v1/bt/`
- Derived tokens expire in ~5s — regenerated fresh on every request
- PalGate supports up to 2 linked devices; linking a 3rd revokes the oldest
