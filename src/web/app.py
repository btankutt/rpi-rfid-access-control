"""
FastAPI application factory + shared runtime state.

`AppState` is the single bundle of components shared across the reader
loop and the HTTP handlers — keeping it explicit (rather than module-
level globals) makes testing tractable: a test can wire its own
`AppState` from in-memory components and call `create_app(state)` to
get an isolated app.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.access_manager import AccessManager
from src.audit_logger import AuditLogger
from src.config import Settings
from src.database import Database
from src.door_controller import DoorController
from src.rate_limiter import RateLimiter
from src.readers import RFIDReader
from src.web.auth import install_session_middleware

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"


@dataclass
class AppState:
    """All long-lived runtime components, bundled for explicit DI."""

    settings: Settings
    database: Database
    reader: RFIDReader
    door: DoorController
    audit: AuditLogger
    rate_limiter: RateLimiter
    access_manager: AccessManager
    websocket_clients: set["WebSocket"] = field(default_factory=set)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown wiring.

    On startup we initialize the database schema and the reader/door
    hardware. On shutdown we close the database and tear down hardware.
    The reader's *polling loop* is started by `main.py`, not here —
    keeping the HTTP layer free of background tasks makes the lifespan
    easier to reason about.
    """
    state: AppState = app.state.app_state
    logger.info("FastAPI startup: initializing components")
    await state.database.init_schema()
    await state.reader.initialize()
    await state.door.initialize()
    try:
        yield
    finally:
        logger.info("FastAPI shutdown: tearing down components")
        await state.reader.shutdown()
        await state.door.shutdown()
        await state.database.close()


def create_app(state: AppState) -> FastAPI:
    """Build a FastAPI app bound to the given runtime state.

    The returned app:
    - Has the `AppState` accessible via `app.state.app_state` and via
      the `get_state` dependency (see `src.web.routes`).
    - Has session middleware installed using `state.settings.session_secret`.
    - Mounts `static/` and includes the `routes` router.
    """
    from src.web import routes  # local import to avoid cycle

    app = FastAPI(
        title="RPi RFID Access Control",
        description="Production-grade single-door RFID access control system.",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.state.app_state = state
    install_session_middleware(app, state.settings)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.templates = templates

    app.include_router(routes.router)
    logger.info("FastAPI app created")
    return app
