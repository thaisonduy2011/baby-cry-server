from fastapi import FastAPI, Request
from datetime import datetime, date, timezone, timedelta
from database import engine, SessionLocal
from models import CryLog, Base
import requests
import os

# ===== SETUP =====
Base.metadata.create_all(bind=engine)
app = FastAPI()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

VN_TZ = timezone(timedelta(hours=7))
SYSTEM_ENABLED = False  # ban đầu TẮT


# ===== TELEGRAM UTILS =====
def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": chat_id,
        "text": text
    })


# ===== BASIC =====
@app.get("/")
def home():
    return {
        "status": "Server is running",
        "system_enabled": SYSTEM_ENABLED
    }


# ===== ESP32 GỌI KHI PHÁT HIỆN KHÓC =====
@app.post("/alert")
def alert():
    global system_enabled

    if not system_enabled:
        return {"success": False, "reason": "system stopped"}

    print("=== ALERT RECEIVED ===")

    # ===== GHI GOOGLE SHEET =====
    try:
        print("=== TRY WRITE GOOGLE SHEET ===")

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]

        credentials_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
        client = gspread.authorize(creds)

        sheet = client.open("BabyCryLogs").sheet1

        now = datetime.now()
        date = now.strftime("%Y-%m-%d")
        time = now.strftime("%H:%M:%S")

        sheet.append_row([date, time])

        print("=== WRITE GOOGLE SHEET SUCCESS ===")

    except Exception as e:
        print("!!! GOOGLE SHEET ERROR !!!")
        print(e)

    # ===== TELEGRAM =====
    send_telegram(
        f"BÉ ĐANG KHÓC\nThời gian: {datetime.now().strftime('%H:%M:%S')}"
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
    chat_id = data["message"]["chat"]["id"]

    db = SessionLocal()
    now = datetime.now(VN_TZ)

    # /start
    if text == "/start":
        SYSTEM_ENABLED = True
        send_telegram(chat_id, "▶️ HỆ THỐNG ĐÃ BẬT")

    # /stop
    elif text == "/stop":
        SYSTEM_ENABLED = False
        send_telegram(chat_id, "⛔ HỆ THỐNG ĐÃ TẮT")

    # /today
    elif text == "/today":
        today = date.today()
        logs = db.query(CryLog).filter(
            CryLog.created_at >= datetime(today.year, today.month, today.day, tzinfo=VN_TZ)
        ).all()

        if not logs:
            reply = "📭 Hôm nay chưa có lần khóc nào."
        else:
            reply = f"HÔM NAY BÉ KHÓC {len(logs)} LẦN:\n"
            for i, log in enumerate(logs, 1):
                t = log.created_at.strftime("%H:%M:%S")
                reply += f"{i}. {t}\n"

        send_telegram(chat_id, reply)

    else:
        send_telegram(chat_id, "❓ Lệnh không hợp lệ\n/start /stop /today")

    db.close()
    return {"ok": True}
