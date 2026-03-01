from fastapi import FastAPI, Request
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import requests
import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

# =========================
# ENV (Render)
# =========================
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

# ✅ THAY ĐỔI 1: Multi CHAT_ID
# Trên Render, set env var CHAT_IDS = "111111111,222222222"
# (bố một ID, mẹ một ID, cách nhau dấu phẩy, không có dấu cách)
CHAT_IDS: list[str] = [
    cid.strip()
    for cid in (os.getenv("CHAT_IDS") or os.getenv("CHAT_ID") or "").split(",")
    if cid.strip()
]

VN_TZ = timezone(timedelta(hours=7))

# =========================
# PLAN C — config
# =========================
BURST_WINDOW_SECONDS       = 60   # mở burst 60s sau lần đầu phát hiện
BURST_NOTIFY_EVERY_SECONDS = 8    # nhắn lại mỗi 8s trong burst
QUIET_RESET_SECONDS        = 10   # im >= 10s => hết đợt, lần sau là đợt mới
MIN_ALERT_GAP_SECONDS      = 1    # lọc nhiễu: bỏ qua alert lặp < 1s

SPREADSHEET_NAME = "BabyCryLogs"

# =========================
# Simple reliability
# =========================
HTTP = requests.Session()
REQ_TIMEOUT_SEC = 8

# Cache Google Sheet objects
_GS_CLIENT = None
_GS_SHEET  = None   # sheet chính (logs)
_ST_SHEET  = None   # sheet "state" (tab thứ 2)

# =========================
# Runtime state (in-memory)
# =========================
LAST_ALERT_AT  = None
LAST_SEEN_AT   = None
BURST_END_AT   = None
LAST_NOTIFY_AT = None
EPISODE_ACKED  = False

# ✅ THAY ĐỔI 2: SYSTEM_ENABLED đọc từ sheet khi khởi động
# (sẽ được gán ở cuối file sau khi các hàm được định nghĩa)
SYSTEM_ENABLED = False


# =========================
# Reply keyboard
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
# Google Sheet helpers — log sheet
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
    _GS_SHEET  = _GS_CLIENT.open(SPREADSHEET_NAME).sheet1
    return _GS_SHEET


def reset_sheet_cache():
    global _GS_CLIENT, _GS_SHEET, _ST_SHEET
    _GS_CLIENT = None
    _GS_SHEET  = None
    _ST_SHEET  = None


# =========================
# ✅ THAY ĐỔI 2: State sheet helpers
# Tab thứ 2 tên "state", chỉ dùng ô A1 để lưu "1" hoặc "0"
# =========================
def get_state_sheet():
    global _GS_CLIENT, _ST_SHEET
    if _ST_SHEET is not None:
        return _ST_SHEET
    if not GOOGLE_CREDENTIALS:
        raise RuntimeError("GOOGLE_CREDENTIALS missing")
    # Nếu _GS_CLIENT chưa có thì gọi get_sheet() trước để khởi tạo
    if _GS_CLIENT is None:
        get_sheet()
    _ST_SHEET = _GS_CLIENT.open(SPREADSHEET_NAME).worksheet("state")
    return _ST_SHEET


def load_system_state() -> bool:
    """Đọc trạng thái bật/tắt từ sheet khi server khởi động."""
    try:
        st = get_state_sheet()
        val = st.acell("A1").value
        print(f"✅ Loaded SYSTEM_ENABLED = {val!r} from sheet")
        return val == "1"
    except Exception as e:
        print(f"⚠️ Could not load state from sheet ({e}), defaulting to False")
        return False


def save_system_state(enabled: bool):
    """Ghi trạng thái bật/tắt vào sheet để sống qua restart."""
    try:
        st = get_state_sheet()
        st.update([["1" if enabled else "0"]], "A1")
    except Exception as e:
        print(f"⚠️ Could not save state to sheet: {e}")
        reset_sheet_cache()


# =========================
# Sheet log helpers
# =========================
def append_to_sheet(now: datetime) -> bool:
    try:
        for _ in range(2):
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
    try:
        sheet = get_sheet()
        rows  = sheet.get_all_values()[1:]
        today = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        return [row[1] for row in rows if len(row) >= 2 and row[0] == today]
    except Exception as e:
        print("❌ Sheet read error:", str(e))
        reset_sheet_cache()
        return []


def read_last_from_sheet():
    try:
        sheet = get_sheet()
        rows  = sheet.get_all_values()
        for row in reversed(rows):
            if len(row) >= 2 and row[0] and row[1]:
                return row[0], row[1]
        return None
    except Exception as e:
        print("❌ Sheet last-row error:", str(e))
        reset_sheet_cache()
        return None


# =========================
# ✅ THAY ĐỔI 1: Telegram gửi đến nhiều người
# =========================
def send_telegram(text: str, attach_keyboard: bool = False) -> bool:
    if not TELEGRAM_TOKEN or not CHAT_IDS:
        print("❌ Missing TELEGRAM_TOKEN or CHAT_IDS")
        return False

    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    success = True

    for chat_id in CHAT_IDS:
        try:
            payload = {"chat_id": chat_id, "text": text}
            if attach_keyboard:
                payload["reply_markup"] = reply_keyboard()
            r = HTTP.post(url, json=payload, timeout=REQ_TIMEOUT_SEC)
            if not (200 <= r.status_code < 300):
                print(f"❌ Telegram error for {chat_id}: {r.status_code}")
                success = False
        except Exception as e:
            print(f"❌ Telegram error for {chat_id}: {e}")
            success = False

    return success


# =========================
# ✅ THAY ĐỔI 3: APScheduler — tổng kết cuối ngày tự động
# Tự gửi lúc 21:00 giờ VN mỗi tối, không cần bấm /today
# =========================
def daily_summary():
    times = read_today_from_sheet()
    if not times:
        send_telegram("🌙 Tổng kết hôm nay: Bé không khóc lần nào. Ngủ ngon! 😴")
    else:
        msg = f"🌙 TỔNG KẾT HÔM NAY — BÉ KHÓC {len(times)} LẦN:\n"
        for i, t in enumerate(times, 1):
            msg += f"  {i}. {t}\n"
        send_telegram(msg)


scheduler = BackgroundScheduler(timezone="Asia/Ho_Chi_Minh")
scheduler.add_job(daily_summary, "cron", hour=21, minute=0)
scheduler.start()


# =========================
# Startup: load state từ sheet
# =========================
@app.on_event("startup")
def on_startup():
    global SYSTEM_ENABLED
    SYSTEM_ENABLED = load_system_state()


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
        EPISODE_ACKED  = False
        BURST_END_AT   = now + timedelta(seconds=BURST_WINDOW_SECONDS)
        LAST_NOTIFY_AT = None

        append_to_sheet(now)
        send_telegram(f"🚨 BÉ ĐANG KHÓC\nThời gian: {now.strftime('%H:%M:%S')}", attach_keyboard=True)
        LAST_NOTIFY_AT = now
        LAST_SEEN_AT   = now
        return {"success": True, "new_episode": True}

    LAST_SEEN_AT = now

    if EPISODE_ACKED:
        return {"success": True, "new_episode": False, "notified": False, "acked": True}

    if BURST_END_AT is not None and now <= BURST_END_AT:
        if LAST_NOTIFY_AT is None or (now - LAST_NOTIFY_AT).total_seconds() >= BURST_NOTIFY_EVERY_SECONDS:
            send_telegram(f"Bé vẫn đang khóc...\n{now.strftime('%H:%M:%S')}", attach_keyboard=True)
            LAST_NOTIFY_AT = now
            return {"success": True, "new_episode": False, "notified": True}

    return {"success": True, "new_episode": False, "notified": False}


@app.post("/telegram")
async def telegram_webhook(request: Request):
    global SYSTEM_ENABLED
    global EPISODE_ACKED

    data = await request.json()
    if "message" not in data:
        return {"ok": True}

    text = (data["message"].get("text", "") or "").strip()

    if text in ("/start", "🟢 Bật"):
        SYSTEM_ENABLED = True
        save_system_state(True)   # ✅ lưu vào sheet
        send_telegram("🟢 HỆ THỐNG ĐÃ BẬT", attach_keyboard=True)

    elif text in ("/stop", "🔴 Tắt"):
        SYSTEM_ENABLED = False
        save_system_state(False)  # ✅ lưu vào sheet
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
