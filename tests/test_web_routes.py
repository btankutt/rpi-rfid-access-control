"""End-to-end HTTP integration tests for the FastAPI app.

Uses httpx.ASGITransport so the app runs in-process — no uvicorn or
network involvement. The fixtures wire a fresh in-memory-ish stack
(temp SQLite + Mock reader/door) per test for isolation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.access_manager import AccessManager
from src.audit_logger import AuditLogger
from src.config import Settings, get_settings
from src.database import Card, Database, User, build_sqlite_url
from src.door_controller import MockDoorController
from src.rate_limiter import RateLimiter
from src.readers import MockRFIDReader
from src.web.app import AppState, create_app
from src.web.auth import hash_password

# Pre-compute the bcrypt hash once — bcrypt's cost factor of 12 makes
# per-test hashing painfully slow.
_ADMIN_PASSWORD = "supersecret123"
_ADMIN_HASH = hash_password(_ADMIN_PASSWORD)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.setenv("ADMIN_USERNAME", "rootadmin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", _ADMIN_HASH)
    monkeypatch.setenv("SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("USE_MOCK_HARDWARE", "true")
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    return Settings()  # type: ignore[call-arg]


@pytest_asyncio.fixture
async def state(settings: Settings, tmp_path: Path) -> AppState:
    db = Database(build_sqlite_url(str(tmp_path / "web.db")))
    reader = MockRFIDReader()
    door = MockDoorController(default_duration_seconds=0.01)
    audit = AuditLogger(db)
    limiter = RateLimiter(max_failures=3, window_seconds=60)
    am = AccessManager(
        database=db,
        door=door,
        audit=audit,
        rate_limiter=limiter,
        door_open_duration=0.01,
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


@pytest_asyncio.fixture
async def client(state: AppState):
    app = create_app(state)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as c:
        # Trigger startup (lifespan) so init_schema runs
        async with app.router.lifespan_context(app):
            yield c


async def _login(client: AsyncClient) -> None:
    resp = await client.post(
        "/login",
        data={"username": "rootadmin", "password": _ADMIN_PASSWORD},
    )
    assert resp.status_code == 303, resp.text


# =============================================================================
# Auth flow
# =============================================================================


class TestAuthFlow:
    @pytest.mark.asyncio
    async def test_root_redirects_to_login_when_anon(self, client: AsyncClient):
        resp = await client.get("/")
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"

    @pytest.mark.asyncio
    async def test_login_page_renders(self, client: AsyncClient):
        resp = await client.get("/login")
        assert resp.status_code == 200
        assert "Admin Login" in resp.text

    @pytest.mark.asyncio
    async def test_invalid_login_redirects_back(self, client: AsyncClient):
        resp = await client.post(
            "/login", data={"username": "rootadmin", "password": "wrong"}
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login?error=invalid"

    @pytest.mark.asyncio
    async def test_valid_login_then_dashboard(self, client: AsyncClient):
        await _login(client)
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert "Live Events" in resp.text

    @pytest.mark.asyncio
    async def test_dashboard_requires_auth(self, client: AsyncClient):
        resp = await client.get("/dashboard")
        # require_admin raises 401 — FastAPI returns JSON; no redirect
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_clears_session(self, client: AsyncClient):
        await _login(client)
        resp = await client.post("/logout")
        assert resp.status_code == 303
        # Subsequent dashboard access should fail
        resp = await client.get("/dashboard")
        assert resp.status_code == 401


# =============================================================================
# Health
# =============================================================================


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_unauthenticated_ok(self, client: AsyncClient):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["database"] is True


# =============================================================================
# User CRUD
# =============================================================================


class TestUserCRUD:
    @pytest.mark.asyncio
    async def test_list_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/users")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_create_and_list(self, client: AsyncClient):
        await _login(client)

        create_resp = await client.post(
            "/api/users",
            json={"full_name": "Alice", "role": "user"},
        )
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["full_name"] == "Alice"

        list_resp = await client.get("/api/users")
        assert list_resp.status_code == 200
        users = list_resp.json()
        assert any(u["full_name"] == "Alice" for u in users)

    @pytest.mark.asyncio
    async def test_get_404(self, client: AsyncClient):
        await _login(client)
        resp = await client.get("/api/users/999999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_user(self, client: AsyncClient):
        await _login(client)
        created = (
            await client.post(
                "/api/users", json={"full_name": "Bob", "role": "user"}
            )
        ).json()

        resp = await client.put(
            f"/api/users/{created['id']}",
            json={"full_name": "Bob Smith", "role": "operator"},
        )
        assert resp.status_code == 200
        assert resp.json()["full_name"] == "Bob Smith"
        assert resp.json()["role"] == "operator"

    @pytest.mark.asyncio
    async def test_invalid_role_rejected(self, client: AsyncClient):
        await _login(client)
        resp = await client.post(
            "/api/users", json={"full_name": "X", "role": "superhero"}
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_delete_user(self, client: AsyncClient):
        await _login(client)
        created = (
            await client.post("/api/users", json={"full_name": "Carol"})
        ).json()
        resp = await client.delete(f"/api/users/{created['id']}")
        assert resp.status_code == 204
        resp = await client.get(f"/api/users/{created['id']}")
        assert resp.status_code == 404


class TestCards:
    @pytest.mark.asyncio
    async def test_assign_card(self, client: AsyncClient):
        await _login(client)
        user = (
            await client.post("/api/users", json={"full_name": "Dave"})
        ).json()

        resp = await client.post(
            f"/api/users/{user['id']}/cards",
            json={"uid": "ABCD1234", "label": "Primary"},
        )
        assert resp.status_code == 201
        assert resp.json()["uid"] == "ABCD1234"

    @pytest.mark.asyncio
    async def test_duplicate_card_uid_conflict(self, client: AsyncClient):
        await _login(client)
        u = (await client.post("/api/users", json={"full_name": "Eve"})).json()
        await client.post(
            f"/api/users/{u['id']}/cards", json={"uid": "DUP"}
        )
        resp = await client.post(
            f"/api/users/{u['id']}/cards", json={"uid": "DUP"}
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_revoke_card(self, client: AsyncClient):
        await _login(client)
        u = (await client.post("/api/users", json={"full_name": "Frank"})).json()
        card = (
            await client.post(
                f"/api/users/{u['id']}/cards", json={"uid": "GONE"}
            )
        ).json()

        resp = await client.delete(f"/api/cards/{card['id']}")
        assert resp.status_code == 204


# =============================================================================
# Simulate endpoint + end-to-end flow
# =============================================================================


class TestSimulate:
    @pytest.mark.asyncio
    async def test_simulate_unknown_card_denied(self, client: AsyncClient):
        await _login(client)
        resp = await client.post("/api/simulate", json={"uid": "NOPE"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["granted"] is False
        assert body["reason"] == "UNKNOWN_CARD"

    @pytest.mark.asyncio
    async def test_simulate_known_card_granted_and_door_opens(
        self, client: AsyncClient, state: AppState
    ):
        await _login(client)

        # Seed a user + card directly
        async with state.database.session() as session:
            user = User(full_name="Grace")
            session.add(user)
            await session.flush()
            session.add(Card(uid="OPEN", user_id=user.id))
            await session.commit()

        resp = await client.post("/api/simulate", json={"uid": "OPEN"})
        assert resp.status_code == 200
        assert resp.json()["granted"] is True
        # Door should have an open event recorded
        assert isinstance(state.door, MockDoorController)
        assert len(state.door.open_events) == 1


class TestLogsApi:
    @pytest.mark.asyncio
    async def test_logs_returned_in_descending_order(
        self, client: AsyncClient, state: AppState
    ):
        await _login(client)

        for i in range(3):
            await state.audit.log(
                card_uid=f"L{i}",
                decision="GRANTED",
                reason="ok",
                reader_type="mock",
            )

        resp = await client.get("/api/logs?limit=10")
        assert resp.status_code == 200
        events = resp.json()
        # Most-recent first
        assert events[0]["card_uid"] == "L2"
        assert events[-1]["card_uid"] == "L0"

    @pytest.mark.asyncio
    async def test_logs_filter_by_uid(self, client: AsyncClient, state: AppState):
        await _login(client)
        for uid in ["X", "Y", "X"]:
            await state.audit.log(
                card_uid=uid, decision="GRANTED", reason="ok", reader_type="mock"
            )
        resp = await client.get("/api/logs?card_uid=X")
        assert resp.status_code == 200
        assert all(e["card_uid"] == "X" for e in resp.json())
        assert len(resp.json()) == 2

    @pytest.mark.asyncio
    async def test_logs_invalid_limit_rejected(self, client: AsyncClient):
        await _login(client)
        resp = await client.get("/api/logs?limit=99999")
        assert resp.status_code == 400


# =============================================================================
# HTML pages render for authenticated user
# =============================================================================


class TestHtmlPages:
    @pytest.mark.asyncio
    async def test_users_page_renders(self, client: AsyncClient, state: AppState):
        await _login(client)
        async with state.database.session() as session:
            session.add(User(full_name="Henry"))
            await session.commit()
        resp = await client.get("/users")
        assert resp.status_code == 200
        assert "Henry" in resp.text

    @pytest.mark.asyncio
    async def test_logs_page_renders(self, client: AsyncClient, state: AppState):
        await _login(client)
        await state.audit.log(
            card_uid="VIEW",
            decision="GRANTED",
            reason="ok",
            reader_type="mock",
        )
        resp = await client.get("/logs")
        assert resp.status_code == 200
        assert "VIEW" in resp.text
