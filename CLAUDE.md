# Project Context for Claude Code

This file provides persistent context for Claude Code sessions on this project.

## Project Overview

**Name:** rpi-rfid-access-control
**Author:** Barış Tankut ([@btankutt](https://github.com/btankutt))
**Purpose:** Production-grade single-door RFID access control system for Raspberry Pi
**Status:** Active development — showcase repository for portfolio

## Strategic Context

This is a **showcase repository**, not a literal port of any production system.

- The author has **5+ years of professional experience** operating a 25-door distributed RFID access control system at their workplace
- That production system's source code is the workplace's IP and **must NOT** be copied here
- This repository must be written **from scratch**, applying the architectural patterns and lessons learned, but never copying actual code
- All credentials, paths, hardware specifics from the production system are off-limits
- The purpose is to demonstrate senior-level competency to potential freelance clients and remote employers

## Technical Stack

- **Language:** Python 3.9+
- **Web framework:** FastAPI (chosen for modern async-first API + auto-generated OpenAPI docs)
- **Database:** SQLite + aiosqlite + SQLAlchemy 2.0 (async ORM)
- **Templating:** Jinja2 (admin UI)
- **Real-time:** WebSocket (built into FastAPI)
- **Authentication:** bcrypt + session cookies
- **Testing:** pytest + pytest-asyncio + pytest-cov
- **Linting:** ruff + mypy
- **CI/CD:** GitHub Actions (Python 3.9–3.12 matrix)
- **License:** Apache 2.0

## Code Style Conventions

- **Type hints** on all function signatures (`from __future__ import annotations`)
- **Docstrings** for all public classes and methods (Google style)
- **Logging** via stdlib `logging` module — never `print()`
- **Configuration** via environment variables (loaded with `pydantic-settings`)
- **Async-first** — use `async def` and `aiosqlite` for I/O; wrap blocking calls in `loop.run_in_executor()`
- **Abstract base classes** for hardware abstraction (so mock implementations work without real hardware)
- **Frozen dataclasses** for value objects (e.g., `CardRead`)
- **Factory functions** for instantiating hardware-dependent classes from config

## Hardware Abstraction Pattern

Every hardware-touching component must follow this pattern:

1. Define an `ABC` with abstract methods
2. Provide a `Mock*` implementation that runs without hardware (for tests + dev)
3. Provide real hardware implementation(s) — lazy-import hardware libraries inside `initialize()` so the module imports cleanly on non-Pi systems
4. Provide a `create_*()` factory function that selects implementation by config

Example: see `src/readers/__init__.py` for the `RFIDReader` pattern (Mock, MFRC522, PN532, RS232).

Apply the same pattern when adding:
- `DoorController` (GPIO relay)
- `TamperMonitor` (door switch sensor)
- `StatusIndicator` (LED, buzzer, LCD)

## Project Structure

```
rpi-rfid-access-control/
├── README.md, README.tr.md          # English + Turkish (keep both updated)
├── LICENSE                          # Apache 2.0
├── CLAUDE.md                        # This file
├── .env.example                     # Config template
├── .gitignore
├── requirements.txt
├── pytest.ini
├── .github/workflows/tests.yml      # CI
├── docs/                            # Architecture, hardware setup
├── src/
│   ├── main.py                      # Entry point
│   ├── config.py                    # Settings (pydantic-settings)
│   ├── readers/                     # RFID reader abstraction ✅ DONE
│   ├── door_controller.py           # Relay + lock control (TODO)
│   ├── access_manager.py            # Authorization logic (TODO)
│   ├── database.py                  # SQLAlchemy models + queries (TODO)
│   ├── audit_logger.py              # Immutable event log (TODO)
│   ├── rate_limiter.py              # Brute-force protection (TODO)
│   └── web/
│       ├── app.py                   # FastAPI app
│       ├── routes.py                # REST + WebSocket
│       ├── auth.py                  # bcrypt + sessions
│       └── templates/               # Jinja2 admin UI
├── tests/                           # pytest tests for each module
└── scripts/
    ├── hash_password.py             # bcrypt helper for admin password
    └── install.sh                   # Production deployment script
```

## Development Workflow

- **Commit messages:** Conventional Commits format (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`)
- **Branch strategy:** `main` for stable, `develop` for active work, feature branches for larger changes
- **Pull requests:** Required for merging to `main` once collaborators join. Solo development can push to `develop` directly.
- **Pre-commit:** Run `ruff check src/ tests/` and `pytest` before every commit
- **CI:** GitHub Actions runs tests on every push — must be green before merging

## Bilingual Documentation

- **Primary language:** English (for global audience)
- **Secondary:** Turkish in `README.tr.md` (kept in sync with English README)
- Code comments and docstrings: **English only**
- Commit messages: **English only**

## What to Ask the User Before Making Changes

If a request is ambiguous, ask before guessing. Specifically:

- **Adding new dependencies** — confirm before adding to `requirements.txt`
- **Database schema changes** — confirm before modifying existing tables
- **API breaking changes** — confirm before changing public REST endpoints
- **Removing features** — never silently remove; always confirm

## Things to Avoid

- ❌ Copying code from the user's workplace production system
- ❌ Inline hardware imports at module level (must be lazy inside `initialize()`)
- ❌ Synchronous blocking calls in async code paths
- ❌ Storing passwords in plaintext anywhere
- ❌ Hardcoding paths, URLs, ports, or credentials (use config/env)
- ❌ Using `print()` for logging
- ❌ Adding placeholder TODO comments without a tracking issue
- ❌ Breaking the public CardRead / RFIDReader API without discussion

## Related Repositories (Planned)

Part of a broader portfolio:
- `pinescript-toolkit` — TradingView Pine Script indicators
- `telegram-trading-signal-bot` — Trading signal Telegram bot
- `mt5-bot-framework` — MetaTrader 5 trading bot framework
- `iot-relay-controller` — Web/Telegram-controlled relay system
- `multi-pi-fleet-manager` — Distributed Pi fleet coordinator (future)

Cross-reference these in READMEs where relevant.

## Author's Background (for context)

12+ years of professional software/hardware development experience.
- Last 5 years: distributed IoT systems, RFID access control, embedded systems
- Parallel: algorithmic trading systems development (MT5, Pine Script, Python bots)
- Open to consulting and freelance work in IoT, embedded systems, trading systems
