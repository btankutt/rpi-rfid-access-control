"""
Application entry point.

Wires the configured components together and runs three concurrent
tasks:
1. The uvicorn HTTP server (admin UI + REST + WebSocket).
2. The reader poll loop, forwarding each `CardRead` to the
   `AccessManager`.
3. (Implicitly) the FastAPI lifespan, which owns schema init and
   hardware initialize/shutdown.

Run with:
    python -m src.main
"""

from __future__ import annotations

import asyncio
import logging
import signal
from logging.handlers import RotatingFileHandler
from typing import Any

import uvicorn

from src.access_manager import AccessManager
from src.audit_logger import AuditLogger
from src.config import Settings, get_settings
from src.database import Database, build_sqlite_url
from src.door_controller import create_door_controller
from src.rate_limiter import RateLimiter
from src.readers import create_reader
from src.web.app import AppState, create_app

logger = logging.getLogger(__name__)


def setup_logging(settings: Settings) -> None:
    """Configure root logging with a console + rotating-file handler.

    Called once at startup. The application emits everything through
    `logging.getLogger(__name__)`, so this is the single place to tune
    formatting and destinations.
    """
    settings.ensure_directories()
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")

    # Clear any prior handlers (idempotent under reloads/tests).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    fh = RotatingFileHandler(
        settings.log_file,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)


def build_state(settings: Settings) -> AppState:
    """Construct every component from the loaded settings.

    The choice between mock and real hardware is centralized here so
    nothing else in the codebase needs to branch on `use_mock_hardware`.
    """
    db = Database(build_sqlite_url(str(settings.database_path)))

    reader_type = "mock" if settings.use_mock_hardware else settings.reader_type
    reader_kwargs: dict[str, Any] = {}
    if reader_type == "rs232":
        reader_kwargs = {
            "port": settings.rs232_port,
            "baudrate": settings.rs232_baudrate,
        }
    reader = create_reader(reader_type, **reader_kwargs)

    door_type = "mock" if settings.use_mock_hardware else "gpio"
    if door_type == "gpio":
        door_kwargs: dict[str, Any] = {
            "pin": settings.relay_gpio_pin,
            "default_duration_seconds": settings.door_open_duration_seconds,
            "fail_safe": settings.fail_safe_mode,
        }
    else:
        door_kwargs = {
            "default_duration_seconds": settings.door_open_duration_seconds
        }
    door = create_door_controller(door_type, **door_kwargs)

    audit = AuditLogger(db)
    limiter = RateLimiter(
        max_failures=settings.rate_limit_failed_attempts,
        window_seconds=settings.rate_limit_window_seconds,
    )
    am = AccessManager(
        database=db,
        door=door,
        audit=audit,
        rate_limiter=limiter,
        door_open_duration=settings.door_open_duration_seconds,
    )
    return AppState(
        settings=settings,
        database=db,
        reader=reader,
        door=door,
        audit=audit,
        rate_limiter=limiter,
        access_manager=am,
    )


async def reader_loop(
    state: AppState, stop: asyncio.Event, poll_timeout: float = 1.0
) -> None:
    """Poll the reader; dispatch each card read to the AccessManager.

    Errors in any single iteration are logged and the loop backs off for
    one second — we never want a transient hardware glitch to take the
    door offline. Cancellation propagates immediately.
    """
    logger.info("Reader loop starting")
    while not stop.is_set():
        try:
            card = await state.reader.read_card(timeout=poll_timeout)
            if card is not None:
                await state.access_manager.handle_card_read(card)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reader loop error — backing off 1s")
            await asyncio.sleep(1.0)
    logger.info("Reader loop stopped")


async def main_async() -> None:
    settings = get_settings()
    setup_logging(settings)
    logger.info(
        "Starting RPi RFID Access Control (mock=%s, reader=%s, port=%d)",
        settings.use_mock_hardware,
        settings.reader_type,
        settings.web_port,
    )

    state = build_state(settings)
    app = create_app(state)

    stop_event = asyncio.Event()

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.web_host,
            port=settings.web_port,
            log_config=None,
            access_log=False,
        )
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows event loops do not support add_signal_handler;
            # uvicorn installs its own KeyboardInterrupt handler.
            pass

    await asyncio.gather(
        server.serve(),
        reader_loop(state, stop_event),
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
