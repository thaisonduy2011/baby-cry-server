from fastapi import FastAPI
from datetime import datetime, timezone, timedelta
import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

VN_TZ = timezone(timedelta(hours=7))


def append_to_sheet():
    try:
        print("=== START APPEND ===")

        if not GOOGLE_CREDENTIALS:
            print("❌ GOOGLE_CREDENTIALS is empty")
            return {"error": "no credentials"}

        creds_dict = json.loads(GOOGLE_CREDENTIALS)

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]

        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        sheet = client.open("BabyCryLogs").sheet1

        now = datetime.now(VN_TZ)

        sheet.append_row([
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S")
        ])

        print("✅ SUCCESS WRITE")

        return {"success": True}

    except Exception as e:
        print("❌ ERROR:", str(e))
        return {"error": str(e)}


@app.post("/test-sheet")
def test_sheet():
    return append_to_sheet()
