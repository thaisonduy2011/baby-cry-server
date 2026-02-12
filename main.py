from fastapi import FastAPI, Request
from datetime import datetime, timezone, timedelta
import requests
import os
import json

import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

# ===== ENV =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

VN_TZ = timezone(timedelta(hours=7))

SYSTEM_ENABLED = False


# ===== TELEGRAM =====
def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ Missing TELEGRAM_TOKEN or CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    try:
        r = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": text
        })
        print("Telegram status:", r.status_code)
    except Exception as e:
        print("Telegram error:", e)


# ===== GOOGLE SHEET =====
def read_today_from_sheet():
    if not GOOGLE_CREDENTIALS:
        return []

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    sheet = client.open("BabyCryLogs").sheet1
    rows = sheet.get_all_values()[1:]  # bỏ header

    today = datetime.now(VN_TZ).strftime("%Y-%m-%d")
    times = []

    for row in rows:
        if len(row) >= 2 and row[0] == today:
            times.append(row[1])

    return times



# ===== HOME =====
@app.get("/")
def home():
    return {
        "status": "running",
        "system_enabled": SYSTEM_ENABLED
    }


# ===== ALERT =====
@app.post("/alert")
def alert():
    global SYSTEM_ENABLED

    if not SYSTEM_ENABLED:
        return {"success": False, "reason": "system stopped"}

    print("🚨 ALERT RECEIVED")

    write_google_sheet()

    send_telegram(
        f"BÉ ĐANG KHÓC\nThời gian: {datetime.now(VN_TZ).strftime('%H:%M:%S')}"
    )

    return {"success": True}


# ===== TELEGRAM WEBHOOK =====
@app.post("/telegram")
async def telegram_webhook(request: Request):
    global SYSTEM_ENABLED

    data = await request.json()

    if "message" not in data:
        return {"ok": True}

    text = data["message"].get("text", "")

    if text == "/start":
        SYSTEM_ENABLED = True
        send_telegram("🟢 HỆ THỐNG ĐÃ BẬT")

    elif text == "/stop":
        SYSTEM_ENABLED = False
        send_telegram("🔴 HỆ THỐNG ĐÃ TẮT")

    elif text == "/status":
        status_text = "🟢 ĐANG BẬT" if SYSTEM_ENABLED else "🔴 ĐANG TẮT"
        send_telegram(f"Trạng thái hiện tại: {status_text}")

    elif text == "/clear":
        send_telegram("Xóa tin nhắn thủ công trong Telegram (bot không thể tự xóa toàn bộ).")

    elif text == "/today":
        send_telegram("Xem lịch sử trong Google Sheets: BabyCryLogs")

    else:
        send_telegram("Lệnh hợp lệ:\n/start\n/stop\n/status\n/today")

    return {"ok": True}
