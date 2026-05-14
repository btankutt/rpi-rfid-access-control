# Hardware Setup

This document is the bench-side reference for wiring the supported RFID
readers and the relay/lock module to a Raspberry Pi. It complements
[architecture.md](architecture.md), which describes the software side.

> Pin numbers below use **BCM numbering** (the same scheme the code uses
> via `GPIO.setmode(GPIO.BCM)`). Refer to <https://pinout.xyz/> to find
> the matching physical pin on your Pi model.

---

## Bill of Materials

| Component | Notes |
| --- | --- |
| Raspberry Pi Zero 2 W / 3 / 4 | Pi 4 recommended for production |
| MicroSD card, 16 GB+, Class 10 | Use a card with **high TBW** rating to survive years of logging |
| 5 V / 2.5 A power supply (Pi) | **Separate rail from the lock** — see *Power architecture* |
| RFID reader | One of: MFRC522, PN532, RS-232 industrial |
| 1-channel opto-isolated relay module | 5 V coil, AC/DC SPDT contacts |
| Electromagnetic lock or strike | 12 V or 24 V, current per spec |
| Lock power supply | Sized for the lock's inrush current |
| Jumper wires | Female-to-female; keep < 30 cm on SPI |
| (Optional) Door switch | Reed switch or microswitch for tamper detection |
| (Optional) Status LED + buzzer | Visual / audio feedback for users |

---

## Power Architecture

The single most common cause of a "the Pi randomly reboots when the
door opens" report is **brownout from lock inrush**. Avoid this by:

```
            ┌────────────────────────┐
            │  Mains  (AC)           │
            └─────────┬──────────────┘
                      │
       ┌──────────────┴──────────────┐
       │                             │
       ▼                             ▼
  ┌──────────┐                  ┌──────────┐
  │ 5V/2.5A  │  -- Pi only      │ 12V PSU  │  -- Lock only
  │  PSU     │                  │  (sized) │
  └────┬─────┘                  └────┬─────┘
       │                             │
       ▼                             ▼
   Raspberry Pi                Lock + relay contact side
```

The Pi's 3.3 V rail powers the *coil* side of the opto-isolated relay
(low current, no inrush). The relay's contact side switches the lock's
own 12 V (or 24 V) supply. The two supplies share **no common ground**
through the relay, which is the whole point of opto isolation.

---

## MFRC522 (SPI hobby module)

Cheap and easy — fine for hobby and prototyping. Reads UID only, so
**not appropriate for high-security** deployments (UID can be cloned).

### Wiring

| MFRC522 pin | Pi BCM pin | Physical pin | Notes |
| --- | --- | --- | --- |
| SDA (CS) | GPIO 8 (CE0) | 24 | SPI chip-select |
| SCK | GPIO 11 (SCLK) | 23 | SPI clock |
| MOSI | GPIO 10 (MOSI) | 19 | SPI data Pi→reader |
| MISO | GPIO 9 (MISO) | 21 | SPI data reader→Pi |
| IRQ | *(unused)* | — | Optional; not used by `mfrc522` lib |
| GND | GND | 6 / 9 / 14 / etc. | |
| RST | GPIO 25 | 22 | Reset |
| **3.3 V** | 3.3 V | 1 or 17 | **Do not use 5 V — will destroy the IC** |

### Enable SPI

```bash
sudo raspi-config
# → Interface Options → SPI → Enable → Reboot
```

### Gotchas

- Keep SPI traces **< 30 cm**. Longer runs introduce signal-integrity
  errors that surface as "random failed reads".
- The Chinese clone modules sometimes ship with the **antenna PCB
  separated**. Reflow the joint or use an OEM Adafruit/Sparkfun module.
- Some Pi cases block the antenna; mount the reader **outside** the case.

---

## PN532 (NFC, recommended for security)

Supports MIFARE Classic, MIFARE DESFire EV1+, and FeliCa with
cryptographic authentication. UID cloning is mitigated by using
authenticated reads.

### Wiring (I²C — recommended)

| PN532 pin | Pi BCM pin | Physical pin |
| --- | --- | --- |
| SDA | GPIO 2 (SDA1) | 3 |
| SCL | GPIO 3 (SCL1) | 5 |
| VCC | 3.3 V | 1 or 17 |
| GND | GND | 6 or 9 |
| IRQ | *(optional GPIO)* | — |

On the PN532 module, set the **SEL0 / SEL1 DIP switches to (1, 0)** for
I²C mode. (For SPI use (0, 1); for HSU use (0, 0).)

### Enable I²C

```bash
sudo raspi-config
# → Interface Options → I2C → Enable → Reboot
sudo apt install i2c-tools
i2cdetect -y 1     # PN532 should show at 0x24
```

### Set this reader in your `.env`

```env
READER_TYPE=pn532
```

---

## RS-232 Industrial Readers

Wiegand-to-serial bridges or readers with native RS-232 output (HID,
Suprema, ZKTeco). These typically operate at 9600 or 19200 baud and
emit hex-encoded UIDs followed by CR/LF.

### Required: TTL ↔ RS-232 converter

The Pi's UART is TTL (3.3 V); RS-232 swings ±12 V. Use a **MAX3232**
or DB9 USB-serial adapter to translate. Direct connection will burn
the Pi's UART.

### Wiring (USB-serial adapter, simplest)

```
[ Reader DB9 ]  --serial cable--  [ USB-serial dongle ]  ---USB---  [ Pi ]
```

The dongle appears as `/dev/ttyUSB0` (or `COMx` on Windows). Set in
`.env`:

```env
READER_TYPE=rs232
RS232_PORT=/dev/ttyUSB0
RS232_BAUDRATE=9600
```

### Verifying

```bash
sudo apt install minicom
minicom -D /dev/ttyUSB0 -b 9600
# Present a card — you should see ASCII hex appear.
```

If garbage appears, you have the baud rate or parity wrong.

---

## Relay + Lock Wiring

Pi GPIO drives the relay coil; the relay's contacts switch the lock's
own power supply.

```
Pi GPIO 17  ─────────► relay IN (coil +)
Pi GND      ─────────► relay GND (coil −)
Pi 5V       ─────────► relay VCC (coil supply for some boards)

       ┌──── COM (common)
Relay  ├──── NO  (normally open) ────► lock + (fail-secure)
       └──── NC  (normally closed) ───► lock + (fail-safe)
       │
Lock GND ──────────────────────────────► lock −
Lock + 12 V from its own PSU ──────────► relay COM
```

### Fail-safe vs fail-secure

| Mode | Use NO or NC contact | Idle state | On power loss | Code config |
| --- | --- | --- | --- | --- |
| Fail-safe (egress doors, life safety) | Use **NC** | Lock energized (held closed) | Door unlocks | `FAIL_SAFE_MODE=true` |
| Fail-secure (entry-only doors) | Use **NO** | Lock de-energized (mechanically locked) | Door stays locked | `FAIL_SAFE_MODE=false` |

**Consult local building codes.** Most jurisdictions require fail-safe
on egress paths.

### Active-high vs active-low relays

Many cheap relay modules are **active-low** — they energize when the
input is pulled LOW. If your wiring is correct but the lock pulses on
boot (the Pi briefly drives the pin LOW during init), set
`active_high=False` when constructing the `GPIODoorController`.

---

## Optional Peripherals

### Door switch (tamper detection)

```
Door switch:  GPIO 18 ──[switch]── GND
```

Enable in `.env`:

```env
DOOR_SWITCH_GPIO_PIN=18
```

A door opening without a preceding authorized card read is logged as a
tamper event. Pair this with the audit-log alerting to catch forced
entries.

### Status LED + buzzer

| Component | BCM pin | Notes |
| --- | --- | --- |
| Status LED (green) | GPIO 23 | Solid = system OK, blink = denied |
| Buzzer | GPIO 24 | Short beep = granted, long = denied |

(Driver code for these is not in this repo yet; the architecture supports
adding them as additional `AuditLogger` subscribers.)

---

## Troubleshooting Cheatsheet

| Symptom | Likely cause |
| --- | --- |
| Pi reboots when door opens | Brownout — separate the lock's PSU from the Pi's |
| Random failed reads | SPI cable too long, or noise on the antenna |
| Reader returns the wrong UID byte order | Vendor uses MSB-first vs LSB-first — confirm in datasheet |
| Relay clicks but lock doesn't move | Relay contact rating too low for the lock's current |
| Lock holds open instead of pulsing | Wrong NO/NC choice for your fail mode |
| Pi sees "permission denied" on /dev/ttyUSB0 | User isn't in the `dialout` group |
| `RPi.GPIO` "channel in use" warning | Stale state from a crashed prior run — `sudo systemctl restart` clears it |

---

## Verifying End-to-End Before Going Live

1. **Mock mode first.** Set `USE_MOCK_HARDWARE=true`, run `python -m
   src.main`, open the admin UI, and use the *Simulate Card Read*
   button. Confirm grants/denies appear in the live event stream.
2. **Reader alone, no relay.** Set `USE_MOCK_HARDWARE=false`,
   `READER_TYPE=<your reader>`. Disconnect the relay. Present cards;
   the audit log should show `UNKNOWN_CARD` denials. Enroll one card
   via the admin UI, present again — log should show a `GRANTED`
   decision *but no physical door movement* (relay is disconnected).
3. **Connect the relay last.** Reconnect, confirm a granted read makes
   the door click once for the configured `DOOR_OPEN_DURATION_SECONDS`
   and then re-lock.

If step 1 or 2 fail, the problem is software/configuration. If only
step 3 fails, the problem is the relay/lock wiring.
