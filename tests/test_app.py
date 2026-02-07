import os
from datetime import datetime, timedelta

import pytz
import pytest
from fastapi.testclient import TestClient
from icalendar import Calendar

import app


def _base_config():
    return {
        "exchange": {
            "server": "example.com",
            "email": "user@example.com",
            "username": "user",
            "password": "pass",
            "auth_type": "NTLM",
        },
        "calendar": {
            "name": "TestCal",
            "days_past": 1,
            "days_future": 1,
            "sync_interval_minutes": 15,
            "default_timezone": "Europe/Berlin",
        },
        "server": {
            "host": "127.0.0.1",
            "port": 8080,
            "calendar_url_path": "/cal/{calendar_name}.ics",
            "token": "secret-token",
            "secure_healthcheck": True,
        },
    }


class DummyOrganizer:
    def __init__(self, email_address):
        self.email_address = email_address


class DummyItem:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


@pytest.fixture(autouse=True)
def _reset_globals():
    app.calendar_cache = None
    app.last_sync_time = None
    app.config = _base_config()
    yield
    app.calendar_cache = None
    app.last_sync_time = None


def test_health_requires_bearer_token():
    client = TestClient(app.app)
    res = client.get("/health")
    assert res.status_code == 404


def test_health_ok_with_bearer_token(monkeypatch):
    client = TestClient(app.app)
    ts = datetime(2024, 1, 2, 3, 4, 5, tzinfo=pytz.UTC)
    app.last_sync_time = ts
    monkeypatch.setenv("APP_VERSION", "1.2.3")

    res = client.get("/health", headers={"Authorization": "Bearer secret-token"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "healthy"
    assert body["last_sync"] == ts.isoformat()
    assert body["version"] == "1.2.3"


def test_calendar_wrong_path_is_404():
    client = TestClient(app.app)
    res = client.get("/wrong.ics?token=secret-token")
    assert res.status_code == 404


def test_calendar_requires_token():
    client = TestClient(app.app)
    app.calendar_cache = b"BEGIN:VCALENDAR\nEND:VCALENDAR\n"
    res = client.get("/cal/TestCal.ics")
    assert res.status_code == 404


def test_calendar_serves_ics_with_token():
    client = TestClient(app.app)
    app.calendar_cache = b"BEGIN:VCALENDAR\nEND:VCALENDAR\n"
    res = client.get("/cal/TestCal.ics?token=secret-token")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/calendar")
    assert res.headers["content-disposition"].endswith("TestCal.ics")
    assert b"BEGIN:VCALENDAR" in res.content


def test_calendar_503_when_cache_empty():
    client = TestClient(app.app)
    res = client.get("/cal/TestCal.ics?token=secret-token")
    assert res.status_code == 503


def test_convert_exchange_item_to_ical_event_all_day():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=pytz.UTC)
    end = datetime(2024, 1, 2, 0, 0, tzinfo=pytz.UTC)
    item = DummyItem(
        start=start,
        end=end,
        uid="uid-123",
        item_id="item-123",
        subject="Subject",
        text_body="A" * 2000,
        location="Room 1",
        is_all_day=True,
        categories=["Cat"],
        importance="High",
        datetime_created=None,
        last_modified_time=None,
        is_cancelled=False,
        organizer=DummyOrganizer("org@example.com"),
    )

    event = app.convert_exchange_item_to_ical_event(item)
    assert str(event["summary"]) == "Subject"
    assert str(event["location"]) == "Room 1"
    assert event["dtstart"].dt == start.date()
    assert event["dtend"].dt == end.date()
    assert str(event["organizer"]) == "mailto:org@example.com"
    assert event["priority"] == 1
    assert event["status"] == "CONFIRMED"
    assert len(str(event["description"])) == 1000


def test_fetch_calendar_events_with_mocked_exchange(monkeypatch):
    cfg = _base_config()

    class DummyCalendar:
        def view(self, start, end):
            return [
                DummyItem(
                    start=start,
                    end=start + timedelta(hours=1),
                    uid="u1",
                    item_id="i1",
                    subject="Meeting",
                    text_body=None,
                    location=None,
                    is_all_day=False,
                    categories=None,
                    importance=None,
                    datetime_created=None,
                    last_modified_time=None,
                    is_cancelled=False,
                    organizer=None,
                    recurrence=None,
                )
            ]

    class DummyAccount:
        calendar = DummyCalendar()

    monkeypatch.setattr(app, "connect_to_exchange", lambda _cfg: DummyAccount())
    monkeypatch.setattr(app, "CalendarItem", DummyItem)

    ical = app.fetch_calendar_events(cfg)
    cal = Calendar.from_ical(ical)
    events = [c for c in cal.walk() if c.name == "VEVENT"]
    assert len(events) == 1
    assert str(events[0]["summary"]) == "Meeting"
