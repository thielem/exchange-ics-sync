#!/usr/bin/env python3
"""
Exchange to ICS Calendar Sync Service
Connects to Exchange on-premise server and serves calendar as ICS feed
"""

import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread, Lock
import time
import asyncio

import yaml
import hmac
from importlib import metadata

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
import uvicorn
from exchangelib import (
    Credentials, Configuration, Account, DELEGATE,
    EWSTimeZone, EWSDateTime, CalendarItem
)
from icalendar import Calendar, Event
import pytz

from exchangelib.winzone import MS_TIMEZONE_TO_IANA_MAP, CLDR_TO_MS_TIMEZONE_MAP
MS_TIMEZONE_TO_IANA_MAP[''] = "Europe/Berlin"

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Changed to DEBUG for detailed logging
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Exchange ICS Sync Service",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def _not_found():
    """Return a generic 404 response that leaks no information."""
    return Response(status_code=404, content=None, media_type=None)


def _verify_bearer_token(request: Request) -> bool:
    """Check the Authorization: Bearer header against the configured token.
    Uses constant-time comparison to prevent timing attacks.
    """
    expected = config.get('server', {}).get('token')
    if not expected:
        return False
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return hmac.compare_digest(auth[7:], expected)


@app.exception_handler(StarletteHTTPException)
async def _http_exc_handler(request: Request, exc: StarletteHTTPException):
    return _not_found()


@app.exception_handler(RequestValidationError)
async def _validation_exc_handler(request: Request, exc: RequestValidationError):
    return _not_found()


@app.exception_handler(Exception)
async def _generic_exc_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
    return _not_found()

# Global variables for caching
calendar_cache = None
cache_lock = Lock()
last_sync_time = None
config = None


def _get_app_version() -> str:
    """Return app version from env or package metadata."""
    env_version = os.getenv("APP_VERSION")
    if env_version:
        return env_version
    try:
        return metadata.version("exchange-ics-sync")
    except metadata.PackageNotFoundError:
        return "unknown"


def load_config():
    """Load configuration from config.yaml"""
    config_path = os.getenv('CONFIG_PATH', '/app/config.yaml')

    try:
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)

        # Override with environment variables if present
        if os.getenv('EXCHANGE_SERVER'):
            cfg['exchange']['server'] = os.getenv('EXCHANGE_SERVER')
        if os.getenv('EXCHANGE_EMAIL'):
            cfg['exchange']['email'] = os.getenv('EXCHANGE_EMAIL')
        if os.getenv('EXCHANGE_USERNAME'):
            cfg['exchange']['username'] = os.getenv('EXCHANGE_USERNAME')
        if os.getenv('EXCHANGE_PASSWORD'):
            cfg['exchange']['password'] = os.getenv('EXCHANGE_PASSWORD')
        if os.getenv('SERVER_PORT'):
            cfg['server']['port'] = int(os.getenv('SERVER_PORT'))
        if os.getenv('SERVER_HOST'):
            cfg['server']['host'] = os.getenv('SERVER_HOST')
        if os.getenv('SERVER_TOKEN'):
            cfg['server']['token'] = os.getenv('SERVER_TOKEN')
        if os.getenv('CALENDAR_PATH'):
            cfg['server']['calendar_url_path'] = os.getenv('CALENDAR_PATH')

        logger.info("Configuration loaded successfully")
        return cfg
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_path}")
        sys.exit(1)
    except yaml.YAMLError as e:
        logger.error(f"Error parsing configuration file: {e}")
        sys.exit(1)


def connect_to_exchange(cfg):
    """Connect to Exchange server and return Account object"""
    try:
        # Build credentials
        username = cfg['exchange']['username']

        credentials = Credentials(
            username=username,
            password=cfg['exchange']['password']
        )

        # Configure Exchange connection
        exchange_config = Configuration(
            server=cfg['exchange']['server'],
            credentials=credentials,
        )

        # Connect to account
        account = Account(
            primary_smtp_address=cfg['exchange']['email'],
            config=exchange_config,
            autodiscover=False,
            access_type=DELEGATE
        )

        logger.info(f"Successfully connected to Exchange account: {cfg['exchange']['email']}")
        return account

    except Exception as e:
        logger.error(f"Failed to connect to Exchange: {e}")
        try:
            log_creds = {**cfg['exchange']}
            log_creds["password"] = "****"
            logger.error(f"Exchange config used: {log_creds}")
        except Exception:
            pass
        raise


def convert_exchange_item_to_ical_event(item):
    """Convert an Exchange CalendarItem to an iCalendar Event"""
    event = Event()

    # Basic properties
    # ensure start_utc is a timezone-aware UTC datetime
    start_uid = item.start.strftime('%Y%m%dT%H%M%S')
    base = item.uid or f"{item.item_id}@exchange"
    uid = f"{base}-{start_uid}"
    event.add('uid', uid)

    event.add('uid', uid)
    event.add('summary', item.subject or '(No Subject)')

    if item.text_body:
        event.add('description', item.text_body[:1000])

    if item.location:
        event.add('location', item.location)

    # Date/time handling
    if item.is_all_day:
        event.add('dtstart', item.start.date())
        event.add('dtend', item.end.date())
    else:
        event.add('dtstart', item.start)
        event.add('dtend', item.end)

    if item.categories:
        event.add('categories', item.categories)

    if item.importance:
        priority_map = {'Low': 9, 'Normal': 5, 'High': 1}
        event.add('priority', priority_map.get(item.importance, 5))

    # Timestamps
    event.add('created', item.datetime_created or datetime.now(pytz.UTC))
    event.add('last-modified', item.last_modified_time or datetime.now(pytz.UTC))
    event.add('dtstamp', datetime.now(pytz.UTC))

    # Status
    event.add('status', 'CANCELLED' if item.is_cancelled else 'CONFIRMED')

    # Organizer
    if item.organizer:
        event.add('organizer', f"mailto:{item.organizer.email_address}")
    return event


def fetch_calendar_events(cfg):
    """Fetch calendar events from Exchange"""
    try:
        account = connect_to_exchange(cfg)

        # Calculate date range
        days_past = cfg['calendar'].get('days_past', 30)
        days_future = cfg['calendar'].get('days_future', 365)

        # Use account's default timezone
        tz = EWSTimeZone(cfg['calendar']['default_timezone'])
        now = EWSDateTime.now(tz=tz)

        logger.info(f"Fetching events from {now} to {now + timedelta(days=days_future)}")

        items = account.calendar.view(
            start=now,
            end=now + timedelta(days=days_future)
        )

        # Create iCalendar
        cal = Calendar()
        cal.add('prodid', '-//Exchange ICS Sync//EN')
        cal.add('version', '2.0')
        cal.add('calscale', 'GREGORIAN')
        cal.add('method', 'PUBLISH')
        cal.add('x-wr-calname', cfg['calendar'].get('name', 'Exchange Calendar'))
        cal.add('x-wr-timezone', str(tz))

        event_count = 0
        for item in items:
            if not isinstance(item, CalendarItem):
                continue

            logger.debug(f"Processing event: '{item.subject}' (has recurrence: {item.recurrence is not None})")

            event = convert_exchange_item_to_ical_event(item)
            cal.add_component(event)
            event_count += 1

        logger.info(f"Successfully fetched {event_count} calendar events")
        return cal.to_ical()

    except Exception as e:
        logger.error(f"Error fetching calendar events: {e}")
        raise


def sync_calendar_worker(cfg):
    """Background worker to periodically sync calendar"""
    global calendar_cache, last_sync_time

    sync_interval = cfg['calendar'].get('sync_interval_minutes', 15) * 60

    while True:
        try:
            logger.info("Starting calendar sync...")
            ical_data = fetch_calendar_events(cfg)

            with cache_lock:
                calendar_cache = ical_data
                last_sync_time = datetime.now(pytz.UTC)

            logger.info(f"Calendar sync completed. Next sync in {sync_interval/60} minutes")

        except Exception as e:
            logger.error(f"Calendar sync failed: {e}")

        time.sleep(sync_interval)




@app.get("/health")
async def health(request: Request):
    """Health check endpoint — optionally requires Bearer token."""
    if config.get('server', {}).get('secure_healthcheck', True):
        if not _verify_bearer_token(request):
            return _not_found()

    return JSONResponse({
        'status': 'healthy',
        'last_sync': last_sync_time.isoformat() if last_sync_time else None,
        'version': _get_app_version(),
    })


@app.get("/{path:path}")
async def catch_all(request: Request, path: str):
    """Serve the ICS calendar at the configured path. Everything else is 404."""
    # Get configured path and calendar name
    calendar_url_path = config['server'].get('calendar_url_path', '/cal/{calendar_name}.ics')
    calendar_name = config['calendar'].get('name', 'calendar')
    expected_path = calendar_url_path.replace('{calendar_name}', calendar_name).lstrip('/')

    # Wrong path → 404 (checked before auth so the path itself isn't revealed)
    if path != expected_path:
        return _not_found()

    # Verify token (Bearer header or ?token= query param)
    expected_token = config.get('server', {}).get('token')
    if not expected_token:
        logger.warning("No token configured in server settings")
        return _not_found()

    provided_token = None
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        provided_token = auth[7:]
    else:
        provided_token = request.query_params.get("token")

    if not provided_token or not hmac.compare_digest(provided_token, expected_token):
        logger.warning("Invalid or missing token in calendar request")
        return _not_found()

    # Serve the calendar
    with cache_lock:
        if calendar_cache is None:
            return Response(
                status_code=503,
                content="Service temporarily unavailable",
                media_type="text/plain",
            )

        return Response(
            content=calendar_cache,
            media_type='text/calendar',
            headers={
                'Content-Disposition': f'attachment; filename={calendar_name}.ics',
                'Cache-Control': f"max-age={config['calendar'].get('sync_interval_minutes', 15) * 60}",
            },
        )


def main():
    """Main entry point"""
    global config

    # Load configuration
    config = load_config()

    # Initial sync
    logger.info("Performing initial calendar sync...")
    try:
        ical_data = fetch_calendar_events(config)
        with cache_lock:
            global calendar_cache, last_sync_time
            calendar_cache = ical_data
            last_sync_time = datetime.now(pytz.UTC)
        logger.info("Initial sync completed successfully")
    except Exception as e:
        logger.error(f"Initial sync failed: {e}")
        logger.info("Service will continue and retry in background")

    # Start background sync worker
    sync_thread = Thread(target=sync_calendar_worker, args=(config,), daemon=True)
    sync_thread.start()
    logger.info("Background sync worker started")

    # Start FastAPI server
    host = config['server'].get('host', '0.0.0.0')
    port = config['server'].get('port', 8080)

    calendar_url_path = config['server'].get('calendar_url_path', '/cal/{calendar_name}.ics')
    calendar_name = config['calendar'].get('name', 'calendar')
    calendar_path = calendar_url_path.replace('{calendar_name}', calendar_name)

    logger.info(f"Starting server on {host}:{port}")
    logger.info(f"Calendar will be available at: http://{host}:{port}{calendar_path}")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        server_header=False,
    )


if __name__ == '__main__':
    main()
