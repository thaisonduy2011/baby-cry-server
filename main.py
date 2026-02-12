from fastapi import FastAPI, Request
from datetime import datetime, date, timezone, timedelta
import requests
import os
import json

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ====== APP ======
app = FastAPI()

# ====== ENV ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

VN_TZ = timezone(timedelta(hours=7))
SYSTEM_ENABLED = False  # mặc định tắt

# ====== TELEGRAM ======
def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": chat_id,
        "text": text
    })

# ====== GOOGLE SHEET ======
def write_google_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    sheet = client.open("BabyCryLogs").sheet1

    now = datetime.now(VN_TZ)
    sheet.append_row([
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M:%S")
    ])

# ====== BASIC ======
@app.get("/")
def home():
    return {
        "status": "Server is running",
        "system_enabled": SYSTEM_ENABLED
    }

# ====== ESP32 / TEST TAY ======
@app.post("/alert")
def alert():
    global SYSTEM_ENABLED

    if not SYSTEM_ENABLED:
        return {"success": False, "reason": "system stopped"}

    print("=== ALERT RECEIVED ===")

    try:
        print("=== TRY WRITE GOOGLE SHEET ===")
        write_google_sheet()
        print("=== WRITE GOOGLE SHEET SUCCESS ===")
    except Exception as e:
        print("!!! GOOGLE SHEET ERROR !!!")
        print(e)

    send_telegram(
        CHAT_ID,
        f"BÉ ĐANG KHÓC\nThời gian: {datetime.now(VN_TZ).strftime('%H:%M:%S')}"
    )

    return {"success": True}

# ====== TELEGRAM WEBHOOK ======
@app.post("/telegram")
async def telegram_webhook(request: Request):
    global SYSTEM_ENABLED

    data = await request.json()

    if "message" not in data:
        return {"ok": True}

    text = data["message"].get("text", "")
    chat_id = data["message"]["chat"]["id"]

    # /start
    if text == "/start":
        SYSTEM_ENABLED = True
        send_telegram(chat_id, "HỆ THỐNG ĐÃ BẬT")

    # /stop
    elif text == "/stop":
        SYSTEM_ENABLED = False
        send_telegram(chat_id, "HỆ THỐNG ĐÃ TẮT")

    # /today
    elif text == "/today":
        send_telegram(chat_id, "Xem lịch sử trong Google Sheets: BabyCryLogs")

    else:
        send_telegram(chat_id, "Lệnh hợp lệ:\n/start\n/stop\n/today")

    return {"ok": True}
