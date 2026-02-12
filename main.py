from fastapi import FastAPI, Request
import requests
import os

app = FastAPI()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

@app.get("/")
def home():
    return {"status": "Server is running"}

@app.post("/alert")
async def alert(request: Request):
    data = await request.json()
    message = f"🚨 PHÁT HIỆN TIẾNG KHÓC\n\nMức âm: {level}\nThời gian: {time}"

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": message
    })

    return {"success": True}
