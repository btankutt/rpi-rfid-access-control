"""
Application entry point.

Wires the four components (config, database, reader, door) together,
then runs an asyncio loop that polls the reader and dispatches each
card read to the access-decision pipeline.

Run with::

    python -m src.main                    # full reader loop
    python -m src.main --simulate-card X  # one-shot smoke test
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from src.config import Settings, get_settings
from src.database import (
    close_db,
    get_user_by_uid,
    init_db,
    init_engine,
    log_access_attempt,
)
from src.door_controller import DoorController, create_door_controller
from src.readers import CardRead, RFIDReader, create_reader

logger = logging.getLogger(__name__)


def setup_logging(settings: Settings) -> None:
    """Configure root logging with a console + rotating-file handler.

    The function is idempotent — repeated calls do not pile up handlers.
    """
    Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(settings.log_level)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")

    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    fh = RotatingFileHandler(
        settings.log_file, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)


def _reader_kwargs(settings: Settings) -> dict:
    """Reader-specific kwargs from settings."""
    if settings.reader_type == "rs232" and not settings.use_mock_hardware:
        return {"port": settings.rs232_port, "baudrate": settings.rs232_baudrate}
    return {}


def _build_components(settings: Settings) -> tuple[RFIDReader, DoorController]:
    """Construct the reader and door controller from settings."""
    reader_type = "mock" if settings.use_mock_hardware else settings.reader_type
    reader = create_reader(reader_type, **_reader_kwargs(settings))

    if settings.use_mock_hardware:
        door = create_door_controller(
            use_mock=True,
            default_duration_seconds=settings.door_open_duration_seconds,
        )
    else:
        door = create_door_controller(
            use_mock=False,
            pin=settings.relay_gpio_pin,
            default_duration_seconds=settings.door_open_duration_seconds,
            fail_safe_mode=settings.fail_safe_mode,
        )
    return reader, door


async def process_card(card: CardRead, door: DoorController) -> dict:
    """Handle one card read end-to-end.

    Looks up the cardholder, decides allow/deny, writes the audit row,
    and (on grant) triggers the door open pulse. The decision dict is
    returned so callers (e.g. `--simulate-card`) can inspect or print it.
    """
    user = await get_user_by_uid(card.uid)
    if user is None:
        await log_access_attempt(
            card_uid=card.uid, decision="DENIED", reason="UNKNOWN_CARD"
        )
        logger.info("DENIED uid=%s reason=UNKNOWN_CARD", card.uid)
        return {"granted": False, "reason": "UNKNOWN_CARD", "user_id": None}

    if not user.is_active:
        await log_access_attempt(
            card_uid=card.uid,
            decision="DENIED",
            reason="USER_INACTIVE",
            user_id=user.id,
        )
        logger.info("DENIED uid=%s reason=USER_INACTIVE", card.uid)
        return {"granted": False, "reason": "USER_INACTIVE", "user_id": user.id}

    await log_access_attempt(
        card_uid=card.uid, decision="GRANTED", reason="OK", user_id=user.id
    )
    logger.info("GRANTED uid=%s user=%s", card.uid, user.name)
    # Fire-and-forget door open so the reader can continue polling.
    asyncio.create_task(door.open())
    return {"granted": True, "reason": "OK", "user_id": user.id}


async def reader_loop(
    reader: RFIDReader,
    door: DoorController,
    stop: asyncio.Event,
    poll_timeout: float = 1.0,
) -> None:
    """Poll the reader forever; dispatch each card read."""
    logger.info("Reader loop starting")
    while not stop.is_set():
        try:
            card = await reader.read_card(timeout=poll_timeout)
            if card is None:
                continue
            await process_card(card, door)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reader loop error — backing off 1s")
            await asyncio.sleep(1.0)
    logger.info("Reader loop stopped")


async def _simulate_one(uid: str, door: DoorController) -> int:
    """One-shot card read for smoke tests; prints decision as JSON."""
    card = CardRead(
        uid=uid,
        timestamp=_dt.datetime.now(_dt.timezone.utc),
        reader_type="simulated",
    )
    decision = await process_card(card, door)
    print(json.dumps(decision))
    # Let any fire-and-forget door task complete before returning.
    await asyncio.sleep(0)
    return 0 if decision["granted"] else 1


async def main_async(simulate_card: Optional[str] = None) -> int:
    settings = get_settings()
    setup_logging(settings)
    logger.info(
        "Starting (mock=%s, reader=%s)",
        settings.use_mock_hardware,
        settings.reader_type,
    )

    init_engine(settings.database_path)
    await init_db()

    reader, door = _build_components(settings)
    await reader.initialize()
    await door.initialize()

    try:
        if simulate_card:
            return await _simulate_one(simulate_card, door)

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                # Windows event loops don't support add_signal_handler.
                pass

        await reader_loop(reader, door, stop_event)
        return 0
    finally:
        await reader.shutdown()
        await door.shutdown()
        await close_db()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rpi-rfid-access-control",
        description=(
            "RPi RFID Access Control — runs the reader poll loop, or "
            "with --simulate-card performs a one-shot smoke test."
        ),
    )
    parser.add_argument(
        "--simulate-card",
        metavar="UID",
        dest="simulate_card",
        help=(
            "Inject a single card-read for UID and exit. Prints the "
            "decision as JSON; exits 0 on GRANTED, 1 on DENIED."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_arg_parser().parse_args(argv)
    exit_code = asyncio.run(main_async(args.simulate_card))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
