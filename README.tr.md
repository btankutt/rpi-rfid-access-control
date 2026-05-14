# RPi RFID Kart Geçiş Sistemi

> Raspberry Pi tabanlı, üretime hazır, tek kapılı RFID kart geçiş sistemi.
> Dağıtık çok-kapılı sistemleri yöneterek edinilen üretim ortamı tecrübesiyle inşa edildi.

[![Lisans: Apache 2.0](https://img.shields.io/badge/Lisans-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)

🇬🇧 [English README](README.md)

---

## Genel Bakış

Tek kapılı uygulamalar için asyncio tabanlı, kendi kendine yeten bir RFID
kart geçiş sistemi. Mevcut kod tabanı **MVP çekirdeğini** kapsar:
yapılandırma, kalıcı depolama, kapı kontrolü ve kart okuma boru hattı.
Sonraki adımlarda denetim genişletmeleri, web yönetim arayüzü ve rate
limiter bu temele eklenecek (uzun vadeli plan için bkz.
[CLAUDE.md](CLAUDE.md), bileşen haritası için
[docs/architecture.md](docs/architecture.md)).

Bu proje, **25 kapılı dağıtık RFID kart geçiş sistemi** işleten
**5+ yıllık tecrübeli** bir mühendis tarafından inşa edilmiştir.

---

## MVP'deki Modüller

| Modül | Amaç |
| --- | --- |
| `src/config.py` | pydantic-settings ile env'den ayar yükleme |
| `src/database.py` | SQLAlchemy 2.0 async modelleri (`User`, `AccessLog`) + CRUD |
| `src/readers/` | RFID okuyucu soyutlaması (Mock, MFRC522, PN532, RS-232) |
| `src/door_controller.py` | Röle soyutlaması: `MockDoorController` + `GPIODoorController` |
| `src/main.py` | Giriş noktası: reader loop, sinyal yönetimi, `--simulate-card` |

---

## Hızlı Başlangıç (5 dakika, donanım gerektirmez)

```bash
# 1. Klonla
git clone https://github.com/btankutt/rpi-rfid-access-control.git
cd rpi-rfid-access-control

# 2. Bağımlılıkları kur
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Mock mod için yapılandır (default değerler zaten mock dostu)
cp .env.example .env

# 4. Reader loop'u çalıştır
python -m src.main
```

Sistem **mock modda** başlar — Raspberry Pi veya RFID donanımı gerekmez.

### Tek-atımlık duman testi (sunucu açmadan)

CI hatları veya hızlı doğrulama için tek bir yetkilendirme kararı:

```bash
python -m src.main --simulate-card A1B2C3D4
# {"granted": false, "reason": "UNKNOWN_CARD", "user_id": null}
# Çıkış kodu: 1 (DENIED). 0 ise GRANTED.
```

GRANTED görmek için önce kartı seed'le:

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

## Donanım Kurulumu

Fiziksel donanım dağıtımları için
[docs/hardware-setup.md](docs/hardware-setup.md) — kablolama, pin
çıkışları, fail-safe vs fail-secure yapılandırması, sorun giderme listesi.

---

## Yapılandırma

Tüm yapılandırma `.env` dosyası üzerinden ortam değişkenleri ile yapılır:

```env
USE_MOCK_HARDWARE=true
READER_TYPE=mfrc522                 # mfrc522 | pn532 | rs232 | mock
RELAY_GPIO_PIN=17
DATABASE_PATH=./data/access.db
DOOR_OPEN_DURATION_SECONDS=5.0
FAIL_SAFE_MODE=true
LOG_LEVEL=INFO
LOG_FILE=./logs/access.log
```

---

## Testleri Çalıştırma

```bash
pytest --cov=src tests/
ruff check src/ tests/
mypy src/ --ignore-missing-imports
```

CI (GitHub Actions, Python 3.9–3.12 matrisi) her push'ta bunları çalıştırır.

---

## MVP'nin Ötesi — Üretim Modülleri

Aşağıdaki modüller halihazırda implement edilmiş ve test edilmiştir, temel okuyucu → kapı akışının ötesinde üretim sınıfı yetenekler sağlar:

| Modül | Amaç |
|-------|------|
| `src/access_manager.py` | Zaman penceresi kısıtları, rol bazlı erişim ve son kullanım tarihli kartlar ile yetkilendirme orkestrasyonu |
| `src/audit_logger.py` | Gerçek zamanlı yönetici paneline canlı yayın için pub/sub destekli, ekleme-yapılabilir-silinemez olay kaydı |
| `src/rate_limiter.py` | Kart UID başına yapılandırılabilir eşikle kayan-pencere brute-force koruması |
| `src/web/` | bcrypt kimlik doğrulama, CSRF token, Starlette oturumları, REST API ve WebSocket olay akışı içeren FastAPI yönetici arayüzü |
| `scripts/hash_password.py` | Yönetici kimlik bilgileri kurulumu için bcrypt parola hash üretici |
| `docs/hardware-setup.md` | Kablolama şemaları, fail-safe vs fail-secure yapılandırma, sorun giderme rehberi |

Her modülün dokümantasyonu genişletilmektedir; mevcut kullanım örüntüleri için commit geçmişine ve satır içi docstring'lere bakın.

---

## Yol Haritası

MVP tek kapılı çalışan bir sistemi sağlar. Planlanan eklemeler:

- [ ] AccessManager: zaman penceresi kısıtları, süresi dolan kartlar, rol kontrolleri
- [ ] AuditLogger: gerçek zamanlı admin dashboard'ları için WebSocket pub/sub
- [ ] Rate limiter: ardışık başarısız okumalara karşı brute-force koruması
- [ ] Web yönetim arayüzü: kullanıcı CRUD, log görüntüleyici (FastAPI + Jinja2)
- [ ] Opsiyonel kapı sensörü ile kurcalama tespiti
- [ ] LDAP / Active Directory entegrasyonu
- [ ] OSDP protokol desteği

---

## Lisans

Apache Lisansı 2.0 — [LICENSE](LICENSE) dosyasını inceleyiniz.

---

## Yazar

**Barış Tankut** — Gömülü Sistemler & Algoritmik Ticaret Geliştiricisi
12+ yıllık yazılım & donanım entegrasyonu profesyonel tecrübesi.
5+ yıllık dağıtık IoT kart geçiş sistemleri uzmanlığı.

- GitHub: [@btankutt](https://github.com/btankutt)
- IoT, gömülü sistemler ve trading sistemleri alanlarında danışmanlık & freelance çalışmaya açık

---

## Katkı

Pull request'ler kabul edilir. Önemli değişiklikler için önce ne değiştirmek istediğinizi tartışmak üzere issue açınız.

Lütfen testlerin geçtiğinden emin olun ve yeni özellikler için yeni testler ekleyin.

```bash
pytest --cov=src tests/
```
