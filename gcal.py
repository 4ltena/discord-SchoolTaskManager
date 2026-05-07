import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build


def build_service():
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON is not set in .env")
    creds = service_account.Credentials.from_service_account_info(
        json.loads(creds_json), scopes=["https://www.googleapis.com/auth/calendar"]
    )
    return build("calendar", "v3", credentials=creds)


_TAG_KEY = "task_manager"
_TAG_VAL = "true"


class CalendarClient:
    def __init__(self):
        self.tz = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tokyo"))
        self.cal_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
        self.svc = build_service()

    def add_task(self, subject: str, name: str, due_dt: datetime) -> dict:
        is_timed = bool(due_dt.hour or due_dt.minute or due_dt.second)
        if is_timed:
            start = {"dateTime": due_dt.isoformat(), "timeZone": str(self.tz)}
            end_dt = due_dt + timedelta(hours=1)
            end = {"dateTime": end_dt.isoformat(), "timeZone": str(self.tz)}
        else:
            date_str = due_dt.strftime("%Y-%m-%d")
            next_date = (due_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            start = {"date": date_str}
            end = {"date": next_date}

        body = {
            "summary": f"{subject}-{name}",
            "start": start,
            "end": end,
            "extendedProperties": {
                "private": {_TAG_KEY: _TAG_VAL, "subject": subject}
            },
        }
        return self.svc.events().insert(calendarId=self.cal_id, body=body).execute()

    def list_tasks(
        self,
        time_min: datetime,
        time_max: datetime | None = None,
        subject: str | None = None,
        max_results: int = 100,
    ) -> list:
        params = dict(
            calendarId=self.cal_id,
            timeMin=time_min.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            privateExtendedProperty=f"{_TAG_KEY}={_TAG_VAL}",
            maxResults=max_results,
        )
        if time_max:
            params["timeMax"] = time_max.isoformat()

        items = self.svc.events().list(**params).execute().get("items", [])

        if subject:
            items = [
                e for e in items
                if e.get("extendedProperties", {}).get("private", {}).get("subject") == subject
            ]
        return items

    def get_task(self, event_id: str) -> dict:
        return self.svc.events().get(calendarId=self.cal_id, eventId=event_id).execute()

    def delete_task(self, event_id: str) -> None:
        self.svc.events().delete(calendarId=self.cal_id, eventId=event_id).execute()

    def update_task(
        self,
        event_id: str,
        name: str | None = None,
        subject: str | None = None,
        notes: str | None = None,
    ) -> dict:
        ev = self.get_task(event_id)
        current_subj = ev.get("extendedProperties", {}).get("private", {}).get("subject", "")
        current_summary = ev.get("summary", "")
        prefix = f"{current_subj}-"
        current_name = current_summary[len(prefix):] if current_summary.startswith(prefix) else current_summary

        new_subj = subject if subject is not None else current_subj
        new_name = name if name is not None else current_name
        ev["summary"] = f"{new_subj}-{new_name}"

        if subject is not None:
            priv = ev.setdefault("extendedProperties", {}).setdefault("private", {})
            priv["subject"] = subject
            priv[_TAG_KEY] = _TAG_VAL
        if notes is not None:
            ev["description"] = notes
        return self.svc.events().update(
            calendarId=self.cal_id, eventId=event_id, body=ev
        ).execute()

    def search_by_name(self, query: str, time_min: datetime) -> list:
        params = dict(
            calendarId=self.cal_id,
            timeMin=time_min.isoformat(),
            q=query,
            singleEvents=True,
            orderBy="startTime",
            privateExtendedProperty=f"{_TAG_KEY}={_TAG_VAL}",
            maxResults=50,
        )
        return self.svc.events().list(**params).execute().get("items", [])
