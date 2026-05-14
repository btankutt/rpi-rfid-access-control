"""
HTTP and WebSocket routes for the admin UI and REST API.

Convention: HTML pages live under `/`, REST endpoints under `/api`, and
the live event stream under `/ws/events`. Authentication is enforced by
the `require_admin` dependency on every protected endpoint — HTML routes
catch the 401 and redirect to /login; API routes return the 401 as JSON.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from src.access_manager import AccessManager
from src.audit_logger import AccessEvent, AuditLogger
from src.database import Card, Database, User
from src.readers import CardRead, MockRFIDReader
from src.web.auth import (
    SESSION_KEY_CSRF,
    current_admin,
    login_session,
    logout_session,
    require_admin,
    verify_credentials,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Dependency wiring
# =============================================================================


def get_state(request: Request):
    """Return the AppState attached to the application."""
    return request.app.state.app_state


def get_db(request: Request) -> Database:
    return get_state(request).database


def get_audit(request: Request) -> AuditLogger:
    return get_state(request).audit


def get_access_manager(request: Request) -> AccessManager:
    return get_state(request).access_manager


# =============================================================================
# Pydantic schemas
# =============================================================================


class UserIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="user", pattern="^(admin|operator|user)$")
    email: Optional[str] = Field(default=None, max_length=120)
    active: bool = True
    allowed_hours_start: Optional[_dt.time] = None
    allowed_hours_end: Optional[_dt.time] = None
    expires_at: Optional[_dt.datetime] = None
    notes: Optional[str] = Field(default=None, max_length=500)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    role: str
    email: Optional[str]
    active: bool
    allowed_hours_start: Optional[_dt.time]
    allowed_hours_end: Optional[_dt.time]
    expires_at: Optional[_dt.datetime]
    notes: Optional[str]
    created_at: _dt.datetime


class CardIn(BaseModel):
    uid: str = Field(min_length=1, max_length=64)
    label: Optional[str] = Field(default=None, max_length=60)
    active: bool = True


class CardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uid: str
    user_id: int
    label: Optional[str]
    active: bool
    created_at: _dt.datetime


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: _dt.datetime
    card_uid: str
    user_id: Optional[int]
    decision: str
    reason: str
    reader_type: str


class SimulateIn(BaseModel):
    uid: str = Field(min_length=1, max_length=64)


# =============================================================================
# HTML routes
# =============================================================================


@router.get("/", response_class=HTMLResponse)
async def root(request: Request) -> RedirectResponse:
    target = "/dashboard" if current_admin(request) else "/login"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: Optional[str] = None):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "login.html", {"error": error}
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    state = get_state(request)
    if not verify_credentials(username, password, state.settings):
        # Don't reveal whether the username or password was wrong.
        return RedirectResponse(
            "/login?error=invalid", status_code=status.HTTP_303_SEE_OTHER
        )
    login_session(request, username)
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(request: Request, _admin: str = Depends(require_admin)):
    logout_session(request)
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, admin: str = Depends(require_admin)):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "admin": admin,
            "csrf_token": request.session.get(SESSION_KEY_CSRF, ""),
            "is_mock_mode": get_state(request).settings.use_mock_hardware,
        },
    )


@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    admin: str = Depends(require_admin),
    db: Database = Depends(get_db),
):
    async with db.session() as session:
        result = await session.execute(select(User).order_by(User.full_name))
        users = list(result.scalars().all())
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "users.html", {"admin": admin, "users": users}
    )


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    admin: str = Depends(require_admin),
    audit: AuditLogger = Depends(get_audit),
):
    events = await audit.recent_events(limit=200)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "logs.html", {"admin": admin, "events": events}
    )


# =============================================================================
# REST API
# =============================================================================


@router.get("/api/health")
async def health(request: Request) -> dict:
    """Liveness probe — no auth required, by design.

    Reports whether the database is reachable so an orchestrator can
    decide whether to restart the process.
    """
    db = get_db(request)
    try:
        async with db.session() as session:
            await session.execute(select(User).limit(1))
        db_ok = True
    except Exception:
        logger.exception("Health check: database query failed")
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
    }


@router.get("/api/users", response_model=list[UserOut])
async def list_users(
    _admin: str = Depends(require_admin), db: Database = Depends(get_db)
):
    async with db.session() as session:
        result = await session.execute(select(User).order_by(User.id))
        return list(result.scalars().all())


@router.post("/api/users", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserIn,
    _admin: str = Depends(require_admin),
    db: Database = Depends(get_db),
):
    async with db.session() as session:
        user = User(**payload.model_dump())
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@router.get("/api/users/{user_id}", response_model=UserOut)
async def get_user(
    user_id: int,
    _admin: str = Depends(require_admin),
    db: Database = Depends(get_db),
):
    async with db.session() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        return user


@router.put("/api/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    payload: UserIn,
    _admin: str = Depends(require_admin),
    db: Database = Depends(get_db),
):
    async with db.session() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        for field, value in payload.model_dump().items():
            setattr(user, field, value)
        await session.commit()
        await session.refresh(user)
        return user


@router.delete("/api/users/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    _admin: str = Depends(require_admin),
    db: Database = Depends(get_db),
):
    async with db.session() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        await session.delete(user)
        await session.commit()


@router.get("/api/users/{user_id}/cards", response_model=list[CardOut])
async def list_user_cards(
    user_id: int,
    _admin: str = Depends(require_admin),
    db: Database = Depends(get_db),
):
    async with db.session() as session:
        result = await session.execute(
            select(Card).where(Card.user_id == user_id).order_by(Card.id)
        )
        return list(result.scalars().all())


@router.post("/api/users/{user_id}/cards", response_model=CardOut, status_code=201)
async def assign_card(
    user_id: int,
    payload: CardIn,
    _admin: str = Depends(require_admin),
    db: Database = Depends(get_db),
):
    async with db.session() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        card = Card(user_id=user_id, **payload.model_dump())
        session.add(card)
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise HTTPException(
                status_code=409, detail="card UID already exists"
            ) from e
        await session.refresh(card)
        return card


@router.delete("/api/cards/{card_id}", status_code=204)
async def revoke_card(
    card_id: int,
    _admin: str = Depends(require_admin),
    db: Database = Depends(get_db),
):
    async with db.session() as session:
        result = await session.execute(delete(Card).where(Card.id == card_id))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="card not found")
        await session.commit()


@router.get("/api/logs", response_model=list[AuditOut])
async def list_logs(
    limit: int = 100,
    card_uid: Optional[str] = None,
    _admin: str = Depends(require_admin),
    audit: AuditLogger = Depends(get_audit),
):
    if limit <= 0 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be 1..1000")
    if card_uid:
        events = await audit.events_for_uid(card_uid, limit=limit)
    else:
        events = await audit.recent_events(limit=limit)
    return events


@router.post("/api/simulate")
async def simulate_card_read(
    payload: SimulateIn,
    request: Request,
    _admin: str = Depends(require_admin),
    access_manager: AccessManager = Depends(get_access_manager),
):
    """Trigger a card-read event without physical hardware.

    Only available when running in mock mode — otherwise the
    legitimate reader could race with simulated reads and produce
    confusing audit entries.
    """
    state = get_state(request)
    if not state.settings.use_mock_hardware:
        raise HTTPException(
            status_code=400,
            detail="simulate endpoint is only available in mock mode",
        )

    if isinstance(state.reader, MockRFIDReader):
        # Also feed the mock reader so any waiting reader-loop sees it.
        state.reader.trigger_read(payload.uid)

    card_read = CardRead(
        uid=payload.uid,
        timestamp=_dt.datetime.now(_dt.timezone.utc),
        reader_type="mock",
    )
    decision = await access_manager.handle_card_read(card_read)
    return {
        "granted": decision.granted,
        "reason": decision.reason,
        "user_id": decision.user_id,
    }


# =============================================================================
# WebSocket: live event stream
# =============================================================================


@router.websocket("/ws/events")
async def events_stream(websocket: WebSocket):
    """Streams every access event to authenticated admins in real time.

    Authentication uses the same session cookie as HTTP routes. Browsers
    automatically send cookies on WebSocket upgrade requests, so no
    special token handshake is needed.
    """
    # Auth via session cookie
    session = websocket.scope.get("session", {})
    if not session.get("admin_username"):
        await websocket.close(code=1008, reason="not authenticated")
        return

    state = websocket.app.state.app_state
    await websocket.accept()
    state.websocket_clients.add(websocket)

    queue: asyncio.Queue[AccessEvent] = asyncio.Queue()

    async def push(event: AccessEvent) -> None:
        await queue.put(event)

    state.audit.subscribe(push)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event.to_dict())
    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected")
    except Exception:
        logger.exception("WebSocket loop error")
    finally:
        state.audit.unsubscribe(push)
        state.websocket_clients.discard(websocket)
