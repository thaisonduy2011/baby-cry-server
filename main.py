from fastapi import FastAPI
from datetime import datetime, date, timezone, timedelta
from database import engine, SessionLocal
from models import CrySession, Base
import requests
import os

# ====== SETUP ======
Base.metadata.create_all(bind=engine)
app = FastAPI()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

VN_TZ = timezone(timedelta(hours=7))

current_session_id = None  # lưu trạng thái đang khóc


# ====== TELEGRAM ======
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text
    })


# ====== API ======
@app.get("/")
def home():
    return {"status": "Server is running"}


@app.post("/cry/start")
def cry_start():
    global current_session_id
    db = SessionLocal()

    if current_session_id is not None:
        db.close()
        return {"status": "already crying"}

    now = datetime.now(VN_TZ)

    session = CrySession(start_time=now)
    db.add(session)
    db.commit()
    db.refresh(session)

    current_session_id = session.id
    db.close()

    send_telegram(
        "🚨 BÉ BẮT ĐẦU KHÓC\n"
        f"🕒 Thời gian: {now.strftime('%H:%M:%S %d/%m/%Y')}"
    )

    return {"status": "cry started"}


@app.post("/cry/stop")
def cry_stop():
    global current_session_id
    db = SessionLocal()

    if current_session_id is None:
        db.close()
        return {"status": "not crying"}

    session = db.query(CrySession).get(current_session_id)
    session.end_time = datetime.now(VN_TZ)
    db.commit()

    duration = (session.end_time - session.start_time).total_seconds()
    minutes = round(duration / 60, 2)

    send_telegram(
        "✅ BÉ NGỪNG KHÓC\n"
        f"⏳ Thời gian khóc: {minutes} phút"
    )

    current_session_id = None
    db.close()

    return {"status": "cry stopped"}


@app.get("/today")
def today_stats():
    db = SessionLocal()
    today = date.today()

    sessions = db.query(CrySession).filter(
        CrySession.start_time >= datetime(today.year, today.month, today.day, tzinfo=VN_TZ)
    ).all()

    count = len(sessions)
    total_seconds = 0

    for s in sessions:
        if s.end_time:
            total_seconds += (s.end_time - s.start_time).total_seconds()

    db.close()

    return {
        "date": str(today),
        "cry_count": count,
        "total_cry_time_seconds": int(total_seconds),
        "total_cry_time_minutes": round(total_seconds / 60, 2)
    }
