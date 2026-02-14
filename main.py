from fastapi import FastAPI, Request
from datetime import datetime, timezone, timedelta
import requests
import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

# =========================
# ENV (Render)
# =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")  # string ok
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

VN_TZ = timezone(timedelta(hours=7))

# Reset to False whenever Render restarts/redeploys
SYSTEM_ENABLED = False

# =========================
# PLAN C — Best simple defaults
# =========================
BURST_WINDOW_SECONDS = 30          # khẩn cấp trong 30s đầu
BURST_NOTIFY_EVERY_SECONDS = 5     # nhắn mỗi 5s trong 30s đầu
QUIET_RESET_SECONDS = 25           # im >= 25s => coi như ngưng, lần sau là đợt mới
MIN_ALERT_GAP_SECONDS = 1          # lọc nhiễu: bỏ qua alert lặp < 1s

# Google Sheet name (bạn dùng đúng cái này)
SPREADSHEET_NAME = "BabyCryLogs"

# =========================
# Simple reliability
# =========================
HTTP = requests.Session()
REQ_TIMEOUT_SEC = 8

# Cache Google Sheet objects (đỡ auth lại mỗi lần)
_GS_CLIENT = None
_GS_SHEET = None

# =========================
# Runtime state (in-memory)
# =========================
LAST_ALERT_AT = None
LAST_SEEN_AT = None
BURST_END_AT = None
LAST_NOTIFY_AT = None

EPISODE_ACKED = False  # bấm "✅ Đã biết" => stop spam trong burst của đợt hiện tại


# =========================
# Reply keyboard (nút to kiểu BotFather)
# =========================
def reply_keyboard():
    return {
        "keyboard": [
            [{"text": "✅ Đã biết"}],
            [{"text": "📊 Hôm nay"}, {"text": "🕒 Gần nhất"}],
            [{"text": "🟢 Bật"}, {"text": "🔴 Tắt"}],
            [{"text": "ℹ️ Trạng thái"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


# =========================
# Google Sheet helpers
# =========================
def get_sheet():
    global _GS_CLIENT, _GS_SHEET

    if _GS_SHEET is not None:
        return _GS_SHEET

    if not GOOGLE_CREDENTIALS:
        raise RuntimeError("GOOGLE_CREDENTIALS missing")

    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    _GS_CLIENT = gspread.authorize(creds)
    _GS_SHEET = _GS_CLIENT.open(SPREADSHEET_NAME).sheet1
    return _GS_SHEET


def reset_sheet_cache():
    global _GS_CLIENT, _GS_SHEET
    _GS_CLIENT = None
    _GS_SHEET = None


def append_to_sheet(now: datetime) -> bool:
    """1 row = 1 'đợt khóc' (để /today đếm đẹp, không bị spam)."""
    try:
        for _ in range(2):  # retry once
            try:
                sheet = get_sheet()
                sheet.append_row([now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")])
                return True
            except Exception as e:
                print("❌ Sheet append error:", str(e))
                reset_sheet_cache()
        return False
    except Exception as e:
        print("❌ Sheet write error:", str(e))
        return False


def read_today_from_sheet():
    """Trả về list time của hôm nay (mỗi dòng = 1 đợt)."""
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()[1:]  # skip header row
        today = datetime.now(VN_TZ).strftime("%Y-%m-%d")

        times = []
        for row in rows:
            if len(row) >= 2 and row[0] == today:
                times.append(row[1])
        return times
    except Exception as e:
        print("❌ Sheet read error:", str(e))
        reset_sheet_cache()
        return []


def read_last_from_sheet():
    """Lấy dòng log gần nhất (date,time) hoặc None."""
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()
        for row in reversed(rows):
            if len(row) >= 2 and row[0] and row[1]:
                return row[0], row[1]
        return None
    except Exception as e:
        print("❌ Sheet last-row error:", str(e))
        reset_sheet_cache()
        return None


# =========================
# Telegram
# =========================
def send_telegram(text: str, attach_keyboard: bool = False) -> bool:
    try:
        if not TELEGRAM_TOKEN or not CHAT_ID:
            print("❌ Missing TELEGRAM_TOKEN or CHAT_ID")
            return False

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": text}

        if attach_keyboard:
            payload["reply_markup"] = reply_keyboard()

        r = HTTP.post(url, json=payload, timeout=REQ_TIMEOUT_SEC)
        return 200 <= r.status_code < 300
    except Exception as e:
        print("❌ Telegram error:", str(e))
        return False


# =========================
# Routes
# =========================
@app.get("/")
def home():
    return {"status": "running", "system_enabled": SYSTEM_ENABLED}


@app.head("/")
def head_home():
    return {"ok": True}


@app.post("/alert")
def alert():
    """
    PLAN C + nút '✅ Đã biết'
    - Đợt mới:
        * log 1 dòng sheet
        * nhắn Telegram ngay + hiện nút
        * mở burst 30s
    - Trong burst:
        * nhắn mỗi 5s (chỉ khi device gọi /alert liên tục)
        * nếu bấm ✅ Đã biết => stop spam ngay
    - Sau burst: không nhắn nữa
    - Nếu im >= 25s: coi như hết đợt, lần sau là đợt mới
    """
    global SYSTEM_ENABLED
    global LAST_ALERT_AT, LAST_SEEN_AT, BURST_END_AT, LAST_NOTIFY_AT
    global EPISODE_ACKED

    if not SYSTEM_ENABLED:
        return {"success": False, "reason": "system stopped"}

    now = datetime.now(VN_TZ)

    # Noise/bounce filter
    if LAST_ALERT_AT is not None and (now - LAST_ALERT_AT).total_seconds() < MIN_ALERT_GAP_SECONDS:
        return {"success": True, "deduped": True}
    LAST_ALERT_AT = now

    # New episode?
    is_new_episode = (
        LAST_SEEN_AT is None or
        (now - LAST_SEEN_AT).total_seconds() >= QUIET_RESET_SECONDS
    )

    if is_new_episode:
        EPISODE_ACKED = False
        BURST_END_AT = now + timedelta(seconds=BURST_WINDOW_SECONDS)
        LAST_NOTIFY_AT = None

        # Log once per episode (keeps /today clean)
        append_to_sheet(now)

        # Immediate Telegram + big button keyboard
        send_telegram(f"🚨 BÉ ĐANG KHÓC\nThời gian: {now.strftime('%H:%M:%S')}", attach_keyboard=True)
        LAST_NOTIFY_AT = now
        LAST_SEEN_AT = now
        return {"success": True, "new_episode": True}

    # Same episode
    LAST_SEEN_AT = now

    # If user acknowledged -> stop burst notifications immediately
    if EPISODE_ACKED:
        return {"success": True, "new_episode": False, "notified": False, "acked": True}

    # Burst window notify
    if BURST_END_AT is not None and now <= BURST_END_AT:
        if LAST_NOTIFY_AT is None or (now - LAST_NOTIFY_AT).total_seconds() >= BURST_NOTIFY_EVERY_SECONDS:
            send_telegram(f"Bé vẫn đang khóc...\n{now.strftime('%H:%M:%S')}", attach_keyboard=True)
            LAST_NOTIFY_AT = now
            return {"success": True, "new_episode": False, "notified": True}

    # After burst: no Telegram
    return {"success": True, "new_episode": False, "notified": False}


@app.post("/telegram")
async def telegram_webhook(request: Request):
    global SYSTEM_ENABLED
    global EPISODE_ACKED

    data = await request.json()
    if "message" not in data:
        return {"ok": True}

    text = (data["message"].get("text", "") or "").strip()

    # Support both slash commands and button texts
    if text in ("/start", "🟢 Bật"):
        SYSTEM_ENABLED = True
        send_telegram("🟢 HỆ THỐNG ĐÃ BẬT", attach_keyboard=True)

    elif text in ("/stop", "🔴 Tắt"):
        SYSTEM_ENABLED = False
        send_telegram("🔴 HỆ THỐNG ĐÃ TẮT", attach_keyboard=True)

    elif text in ("/status", "ℹ️ Trạng thái"):
        status_text = "🟢 ĐANG BẬT" if SYSTEM_ENABLED else "🔴 ĐANG TẮT"
        send_telegram(f"Trạng thái hiện tại: {status_text}", attach_keyboard=True)

    elif text in ("/today", "📊 Hôm nay"):
        times = read_today_from_sheet()
        if not times:
            send_telegram("Hôm nay chưa có lần khóc nào.", attach_keyboard=True)
        else:
            msg = f"HÔM NAY BÉ KHÓC {len(times)} LẦN:\n"
            for i, t in enumerate(times, 1):
                msg += f"{i}. {t}\n"
            send_telegram(msg, attach_keyboard=True)

    elif text in ("/last", "🕒 Gần nhất"):
        last = read_last_from_sheet()
        if not last:
            send_telegram("Chưa có log nào trong sheet.", attach_keyboard=True)
        else:
            d, t = last
            send_telegram(f"Lần khóc gần nhất: {d} {t}", attach_keyboard=True)

    elif text in ("/ack", "✅ Đã biết"):
        EPISODE_ACKED = True
        send_telegram("✅ OK, mình sẽ không nhắn liên tục nữa.", attach_keyboard=True)

    else:
        send_telegram(
            "Lệnh hợp lệ:\n/start\n/stop\n/status\n/today\n/last\n/ack",
            attach_keyboard=True
        )

    return {"ok": True}
