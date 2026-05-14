# RPi RFID Kart Geçiş Sistemi

> Raspberry Pi tabanlı, üretime hazır, tek kapılı RFID kart geçiş sistemi.
> Dağıtık çok-kapılı sistemleri yöneterek edinilen üretim ortamı tecrübesiyle inşa edildi.

[![Lisans: Apache 2.0](https://img.shields.io/badge/Lisans-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-modern-009688.svg)](https://fastapi.tiangolo.com/)

🇬🇧 [English README](README.md)

---

## Genel Bakış

Tek kapılı uygulamalar için tasarlanmış, hazır-üretim kalitesinde tam kapsamlı bir RFID kart geçiş çözümü. Kurulum sonrası doğrudan kullanılabilir: kurcalamaya dayanıklı, ağ kesintilerine karşı dayanıklı, sürekli internet bağlantısı gerektirmez.

Bu proje, **25 kapılı dağıtık RFID kart geçiş sistemi** işleten **5+ yıllık tecrübeli** bir mühendis tarafından inşa edilmiştir. Buradaki kalıplar ve teknik tercihler eğitim örneklerinden değil, gerçek üretim ortamlarından alınmıştır.

---

## Temel Özellikler

### Donanım Desteği
- **Üç farklı RFID okuyucu türü** dahili olarak desteklenir:
  - MFRC522 (SPI bağlantılı, hobi seviyesi modül)
  - PN532 (NFC destekli, kriptografik kimlik doğrulama destekli)
  - Endüstriyel RS-232 okuyucular (Wiegand-Seri köprü)
- **Mock donanım modu** — fiziksel cihaz olmadan geliştirme
- **GPIO röle kontrolü** — elektromanyetik kilit tetiklemesi
- **İsteğe bağlı çevre birimleri**: LED durum göstergesi, sesli geri bildirim için buzzer, LCD ekran

### Yazılım
- **Yetki motoru**: rol tabanlı erişim (yönetici / operatör / kullanıcı), zaman bazlı kısıtlar, son kullanma tarihli kartlar
- **Kalıcı depolama**: otomatik yedekleme destekli SQLite
- **Tam denetim kaydı**: her okuma denemesi meta veri ile loglanır (zaman damgası, kart UID, karar, sebep)
- **Web yönetim arayüzü** (FastAPI): kullanıcı yönetimi, log görüntüleyici, sistem sağlığı
- **REST API**: dış sistemlerle programatik entegrasyon
- **Kimlik doğrulama**: bcrypt ile hash'lenmiş yönetici kimlik bilgileri, oturum bazlı UI auth
- **Gerçek zamanlı güncellemeler**: canlı olay akışı için WebSocket desteği

### Üretim Ortamı Hazırlığı
- **Fail-safe / fail-secure** modlar — enerji kesintisi yönetimi
- **Kurcalama tespiti** — isteğe bağlı kapı sensörü
- **Ağ dayanıklılığı**: çevrimdışı öncelikli, bağlantı geldiğinde senkronizasyon
- **Sağlık izleme**: heartbeat endpoint, systemd watchdog entegrasyonu
- **Docker desteği** — tekrarlanabilir kurulum
- **CI/CD**: her push'ta GitHub Actions ile otomatik test
- **Test kapsamı**: pytest ile %90+ kod kapsama hedefi

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

# 3. Mock mod için yapılandır
cp .env.example .env
# .env dosyasını düzenle — USE_MOCK_HARDWARE=true yap

# 4. Çalıştır
python -m src.main

# 5. Tarayıcıyı aç
# Web arayüzü:  http://localhost:8000
# API dokümanı: http://localhost:8000/docs
```

Sistem **mock modda** başlar — Raspberry Pi veya RFID donanımı gerekmez. Web arayüzündeki "Simulate Card Read" butonu ile tüm akışı uçtan uca test edebilirsiniz.

### Tek-atımlık duman testi (sunucu açmadan)

CI hatları veya hızlı bir doğrulama için, HTTP sunucusunu başlatmadan
tek bir yetkilendirme kararı çalıştırabilirsiniz:

```bash
python -m src.main --simulate-card A1B2C3D4
# {"granted": false, "reason": "UNKNOWN_CARD", "user_id": null}
# Çıkış kodu: 1 (DENIED). 0 ise GRANTED.
```

---

## Donanım Kurulum Listesi

Asgari donanım listesi:

| Bileşen | Not |
|---------|------|
| Raspberry Pi Zero 2 W (veya Pi 3/4) | Üretim ortamı için Pi 4 önerilir |
| MicroSD kart | En az 16 GB, Class 10 |
| Güç adaptörü | 5V, 2.5A veya daha yüksek |
| MFRC522 RFID modülü | SPI; sadece 3.3V — 5V ile beslemeyin |
| 1-kanal röle modülü | AC yük için opto-izoleli önerilir |
| RFID kart/anahtarlık | MIFARE Classic 1K uyumlu |
| Atlama kabloları | Pi GPIO için dişi-dişi |
| **Opsiyonel:** 12V solenoid/elektromanyetik kilit | İhtiyaca göre fail-safe (NO) veya fail-secure (NC) |
| **Opsiyonel:** LED, buzzer | Görsel/sesli geri bildirim |

Endüstriyel RS-232 okuyucular (HID, Wiegand) için kurum sınıfı kurulum dokümanını [docs/hardware-setup.md](docs/hardware-setup.md) içinde bulabilirsiniz.

---

## Üretim Ortamı için Dikkat Edilecekler

Gerçek dağıtımlarda öğrenilmiş, dokümandan görülemeyen detaylar:

- **SPI sinyal kararlılığı**: MFRC522 hatları 30 cm'den kısa olmalı, aksi gürültü problemi. Daha uzun mesafe için kılıflı kablo veya RS-485'e geçiş gerekir.
- **Röle izolasyonu**: AC yük için her zaman opto-izoleli röle modülü kullanın. Ucuz modüller Pi'nin GPIO'suna geri besleme yapabilir.
- **Güç mimarisi**: Pi ve kilit **ayrı güç hatlarında** olmalı. Kilit anahtarlamasından gelen ani akım Pi'yi düşürebilir.
- **Fail-safe vs fail-secure**: Mevzuata göre yapılandırın. Yangın yönetmelikleri çıkış kapıları için **fail-safe** (elektrik kesilince açık), sadece giriş kapıları için **fail-secure** (kesilince kapalı) ister.
- **Ağ dayanıklılığı**: Bulut bağlantısı asla varsayılmamalı. Sistem çevrimdışı çalışmalı, senkronizasyon ikinci özellik.
- **Kurcalama tespiti**: Kapı sensörü zorlama girişi tespit eder. Denetim kayıtlarıyla birleştirilince güvenlik denetimi için kanıt sağlar.
- **Kart kopyalama**: MFRC522 sadece UID okur — UID kopyalanabilir. Yüksek güvenlik için PN532 + kriptografik kimlik doğrulama (DESFire EV1+) kullanın.
- **Yedekleme stratejisi**: SQLite çok sağlam ama yedekler **cihaz dışında** olmalı (NAS'a rsync vb.). Cihaz içi yedek SD kart arızasına karşı korumaz.

---

## Yol Haritası

- [ ] LDAP / Active Directory entegrasyonu
- [ ] Çok-kapılı koordinasyon protokolü (kardeş proje: `multi-pi-fleet-manager`)
- [ ] Mobil uygulama (React Native) — yönetici işlemleri için
- [ ] OSDP protokol desteği (endüstri standardı)
- [ ] Personel devam takibi raporlama modülü

---

## Lisans

Apache Lisansı 2.0 — [LICENSE](LICENSE) dosyasını inceleyiniz.

İzinli açık kaynak: telif hakkı bildirimini koruduğunuz ve patent hibesindeki değişiklikleri açıkladığınız sürece bu yazılımı (ticari kullanım dahil) kullanabilir, değiştirebilir ve dağıtabilirsiniz.

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
