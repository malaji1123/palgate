"""
palgate_link.py — Run ONCE to get your session token via QR code.

1. Run this script — a QR code appears in the terminal
2. Open PalGate app → Menu → Linked Devices → Link a Device → scan QR
3. Session token is saved to .env

Requirements:
    pip install "git+https://github.com/DonutByte/pylgate.git@main" qrcode requests python-dotenv
"""

import time
import uuid
from pathlib import Path
from urllib.parse import urljoin

import qrcode
import requests
import pylgate
from dotenv import set_key
from pylgate.types import TokenType

BASE_URL = "https://api1.pal-es.com/v1/bt/"
ENV_FILE = Path(__file__).parent / ".env"


def basic_headers():
    return {"User-Agent": "okhttp/4.9.3"}


def auth_headers(phone: int, token: bytes, ttype: TokenType):
    return {**basic_headers(), "X-Bt-Token": pylgate.generate_token(token, phone, ttype)}


def validate(resp: requests.Response):
    data = resp.json()
    if not resp.ok or data.get("err") or data.get("status") != "ok":
        raise RuntimeError(f"Request failed: {data}")
    return data


def link_device():
    unique_id = uuid.uuid4()

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(f'{{"id": "{unique_id}"}}')
    qr.make(fit=True)
    qr.print_ascii(invert=True)

    print("Waiting for app scan...")
    resp = requests.get(urljoin(BASE_URL, f"un/secondary/init/{unique_id}"), headers=basic_headers())
    data = validate(resp)

    phone   = int(data["user"]["id"])
    token   = bytes.fromhex(data["user"]["token"])
    ttype   = TokenType(int(data["secondary"]))
    return phone, token, ttype


def check_status(phone, token, ttype):
    resp = requests.get(urljoin(BASE_URL, "secondary/status"), headers=auth_headers(phone, token, ttype))
    validate(resp)


def check_token(phone, token, ttype):
    ts = int(time.time())
    resp = requests.get(urljoin(BASE_URL, f"user/check-token?ts={ts}&ts_diff=0"), headers=auth_headers(phone, token, ttype))
    validate(resp)


def main():
    print("=== Palgate Device Linking ===\n")
    print("Open PalGate app → Menu → Linked Devices → Link a Device → scan the QR below:\n")

    phone, token, ttype = link_device()

    print("Checking status...")
    check_status(phone, token, ttype)

    print("Verifying derived token...")
    check_token(phone, token, ttype)

    print("\nLinked successfully!")
    print(f"Phone      : {phone}")
    print(f"Token type : {ttype} (TokenType.{ttype.name})")
    print(f"Session    : {token.hex()}")

    set_key(str(ENV_FILE), "PHONE_NUMBER",  str(phone))
    set_key(str(ENV_FILE), "SESSION_TOKEN", token.hex())
    set_key(str(ENV_FILE), "TOKEN_TYPE",    str(int(ttype)))
    print(f"\nSaved to {ENV_FILE}")
    print("Now run palgate_opener.py to open the gate.")


if __name__ == "__main__":
    main()
