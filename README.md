# RPi RFID Access Control

> Production-grade single-door access control system for Raspberry Pi.
> Built with industry best practices learned from managing distributed multi-door deployments.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Tests](https://github.com/btankutt/rpi-rfid-access-control/actions/workflows/tests.yml/badge.svg)](https://github.com/btankutt/rpi-rfid-access-control/actions)

🇹🇷 [Türkçe README](README.tr.md)

---

## Overview

A self-contained, asyncio-based RFID access control system for single-door
deployments. The current codebase is the MVP core: configuration, persistence,
door control, and the card-read pipeline. Future work will layer auditing
extensions, a web admin UI, and rate limiting on top of this foundation
(see [CLAUDE.md](CLAUDE.md) for the long-term plan and
[docs/architecture.md](docs/architecture.md) for the component map).

Built by an engineer with **5+ years of experience** operating a 25-door
distributed RFID access control system. The patterns and trade-offs in
this repo are drawn from real-world production deployments, not tutorial code.

---

## Modules in this MVP

| Module | Purpose |
| --- | --- |
| `src/config.py` | Environment-driven settings via pydantic-settings |
| `src/database.py` | SQLAlchemy 2.0 async models (`User`, `AccessLog`) + module-level CRUD |
| `src/readers/` | RFID reader abstraction (Mock, MFRC522, PN532, RS-232) |
| `src/door_controller.py` | Relay abstraction with `MockDoorController` and `GPIODoorController` |
| `src/main.py` | Entry point: reader loop, signal handling, `--simulate-card` flag |

---

## Quick Start (5 minutes, no hardware required)

```bash
# 1. Clone
git clone https://github.com/btankutt/rpi-rfid-access-control.git
cd rpi-rfid-access-control

# 2. Install dependencies
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure for mock mode (defaults are already mock-friendly)
cp .env.example .env

# 4. Run the reader loop
python -m src.main
```

The system starts in **mock mode** — no Raspberry Pi or RFID hardware needed.

### One-shot smoke test (no server)

For CI pipelines or quick sanity checks you can run a single authorization
decision and exit:

```bash
python -m src.main --simulate-card A1B2C3D4
# {"granted": false, "reason": "UNKNOWN_CARD", "user_id": null}
# Exit code: 1 (DENIED). Exit 0 means GRANTED.
```

Seed a card first to see a GRANTED decision:

```bash
python -c "
import asyncio
from src.config import get_settings
from src.database import init_engine, init_db, add_user, close_db
async def go():
    init_engine(get_settings().database_path)
    await init_db()
    await add_user(card_uid='A1B2C3D4', name='Test User')
    await close_db()
asyncio.run(go())
"
python -m src.main --simulate-card A1B2C3D4
```

---

## Hardware Setup

For physical-hardware deployments, see [docs/hardware-setup.md](docs/hardware-setup.md)
for wiring, pinouts, fail-safe vs fail-secure configuration, and a
troubleshooting cheatsheet.

Minimum bill of materials:

| Component | Notes |
|-----------|-------|
| Raspberry Pi Zero 2 W (or Pi 3/4) | Pi 4 recommended for production |
| MicroSD card | 16 GB minimum, Class 10 |
| Power adapter | 5V, 2.5A or higher |
| MFRC522 RFID module | SPI; 3.3V only — do not power from 5V |
| 1-channel relay module | Opto-isolated recommended for AC loads |
| RFID cards/tags | MIFARE Classic 1K compatible |

---

## Configuration

All configuration is done via environment variables (`.env` file):

```env
USE_MOCK_HARDWARE=true              # false on production Pi
READER_TYPE=mfrc522                 # mfrc522 | pn532 | rs232 | mock
RELAY_GPIO_PIN=17
DATABASE_PATH=./data/access.db
DOOR_OPEN_DURATION_SECONDS=5.0
FAIL_SAFE_MODE=true                 # true: door unlocks on power loss
LOG_LEVEL=INFO
LOG_FILE=./logs/access.log
```

---

## Running the Tests

```bash
pytest --cov=src tests/
ruff check src/ tests/
mypy src/ --ignore-missing-imports
```

The CI pipeline (GitHub Actions, Python 3.9–3.12 matrix) runs these on every push.

---

## Roadmap

The MVP gets you a working single-door system. Planned extensions
(tracked in CLAUDE.md):

- [ ] AccessManager: time-window restrictions, expirable cards, role checks
- [ ] AuditLogger: WebSocket pub/sub for real-time admin dashboards
- [ ] Rate limiter: brute-force protection on consecutive failed reads
- [ ] Web admin UI: user CRUD, log viewer, system health (FastAPI + Jinja2)
- [ ] Tamper detection via optional door switch sensor
- [ ] LDAP / Active Directory integration
- [ ] OSDP protocol support (industrial standard)

---

## License

Apache License 2.0 — see [LICENSE](LICENSE) file.

---

## Author

**Barış Tankut** — Embedded Systems & Algorithmic Trading Developer
12+ years of professional experience in software & hardware integration.
5+ years specializing in distributed IoT access control systems.

- GitHub: [@btankutt](https://github.com/btankutt)
- Open to consulting & freelance work in IoT, embedded systems, and trading systems

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you'd like to change.

Please ensure tests pass and add new tests for any new functionality.

```bash
pytest --cov=src tests/
```
