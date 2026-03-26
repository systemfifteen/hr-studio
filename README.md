# HR Studio Monitor

Live HR monitoring systém pre spinning štúdio. Číta BLE hrudné pásy (Myzone MZ-1) a zobrazuje heart rate dáta na TV dashboarde v reálnom čase.

**Live demo:** https://hrstudio.system15.win
**GitHub:** https://github.com/systemfifteen/hr-studio

---

## Čo to robí

- Zobrazuje HR každého jazdca na farebnej karte (zóna 0–5, Myzone-kompatibilné farby)
- Vypočítava MEPs (Myzone Effort Points), kalórie, % max HR
- Zobrazuje 10-minútový history pásik zón pod menom jazdca
- Zaznamenáva session dáta do SQLite (post-workout súhrn)
- Admin panel — správa bicyklov, pásov, jazdcov, start/stop session
- Kiosk režim — Chromium fullscreen na TV, no mouse cursor
- Batéria notebooku + sieťový stav v headeri

---

## Architektúra

```
[Myzone MZ-1 pásy]
       │ BLE (GATT, Heart Rate Service 0x180D)
       ▼
┌─────────────────┐
│  gateway/        │  Python + bleak
│  BLE Manager     │  → pripája sa k pásom
│  WebSocket :8765 │  → broadcastuje HR dáta
│  Admin API :8766 │  → REST pre admin panel
└────────┬────────┘
         │ ws://localhost:8765
         ▼
┌─────────────────┐
│  frontend/       │  nginx :80
│  dashboard.html  │  → live HR grid (TV)
│  admin.html      │  → správa systému
└─────────────────┘
         │
         ▼
┌─────────────────┐
│  SQLite          │  /data/local_cache.db
│  bikes           │  fyzické bicykle
│  straps          │  BLE pásy
│  riders_cache    │  aktuálni jazdci (session)
│  riders_catalog  │  trvalý zoznam jazdcov
│  sessions        │  história hodín
│  session_data    │  HR záznamy per rider
└─────────────────┘
```

---

## Hardware

| Komponent | Detail |
|-----------|--------|
| **Notebook/PC** | Intel laptop s NVMe, WiFi — sieť `No_internet`, IP `192.168.1.145` |
| **BT dongle** | Asus BT540 (USB), `hci0`, MAC `A0:AD:9F:73:9C:F0` |
| **HR pásy** | Myzone MZ-1 — BLE GATT, Heart Rate Service (UUID `0x180D`) |
| **TV** | HDMI na notebook (`HDMI-1`), 1920×1080 @ 60Hz |

### BLE špecifiká Myzone MZ-1

- Pás advertisuje **len keď má skin contact** (mokré elektródy priamo na koži)
- Po nasadení ~20s inicializácia → HR=0 je normálne, `connected=True` sa nastavuje hneď
- MZ-1 podporuje len **1 BLE spojenie naraz** — ak je pripojený na iný host, treba ho tam odpojiť
- Po redeployi/reštarte gateway treba pás fyzicky sundat a znova nasadit (BlueZ zombie spojenie)
- Batéria: zaokrúhlená na 10% (MZ-1 reporting nepresný)
- `CONNECT_TIMEOUT=30s`, `RECONNECT_DELAY=5s` → max 35s od nasadenia po zobrazenie HR

### Multi-strap BLE

BlueZ zvláda len jedno `connect()` naraz. Systém používa `asyncio.Lock` — každý strap ho dostane pri štarte, lock sa drží len počas `client.connect()` (nie počas celého spojenia). Po připojení sú všetky pásy súbežné.

---

## Docker Compose

```
gateway    — network_mode: host (potrebné pre BlueZ/D-Bus)
frontend   — port 80:80, extra_hosts: gateway:host-gateway
```

`extra_hosts: gateway:host-gateway` je nutné, pretože `gateway` beží v host network a Docker DNS ho nevie nájsť z bridge network `frontend` kontajnera.

**Standalone deploy** (notebook, priamy port 80):
```bash
docker compose -f docker-compose.standalone.yml up -d
```

**Prod deploy** (Coolify, port cez reverse proxy):
```bash
docker compose -f docker-compose.frontend.yml up -d
```

---

## Inštalácia

### 1. Automatická (odporúčaná)

```bash
# Klonovanie a základný setup (Docker, BT, systemd service)
sudo bash setup.sh

# Kiosk setup (X11, Chromium, autologin kiosk user)
sudo bash setup-kiosk.sh
```

`setup.sh` nainštaluje Docker, povolí Bluetooth, stiahne repo do `/opt/hr-studio`, vytvorí `systemd` service `hr-studio` a spustí stack.

### 2. USB installer (čistý Debian)

```bash
# Vybuduje hr-studio.iso s preseed (Debian 13 Trixie, unattended install)
bash installer/build-iso.sh

# Zapísanie na USB (nahradiť /dev/sdX správnym diskom!)
sudo dd if=hr-studio.iso of=/dev/sdX bs=4M status=progress
```

**Pred inštaláciou z USB:**
- V BIOSe prepnúť disk z **Intel RST → AHCI** (inak kernel nevidí NVMe)
- Boot z USB, inštalácia je plne automatická (preseed)
- Prihlasovacie údaje: `adminhrstudio` / `<zmeň heslo po inštalácii!>`

### 3. Manuálne

```bash
git clone https://github.com/systemfifteen/hr-studio.git /opt/hr-studio
cd /opt/hr-studio
docker compose -f docker-compose.standalone.yml up --build -d
```

---

## Update

```bash
# Automatický update (git pull + rebuild):
systemctl restart hr-studio

# Alebo manuálne na notebooku:
sudo git -C /opt/hr-studio pull
docker compose build gateway frontend
docker compose up -d gateway frontend
```

---

## Admin panel

URL: `http://<IP>/admin.html`

### Funkcie

| Sekcia | Popis |
|--------|-------|
| **Bikes** | Správa 18 fyzických bicyklov (position = číslo z FitReserve, label = štítok na biku) |
| **Straps** | Priradenie BLE pásov k bicyklom, BLE scanner (10s scan) |
| **Rider catalog** | Trvalý zoznam jazdcov — meno, rok narodenia, váha, pohlavie, max HR override |
| **Rider assignment** | Priradenie jazdca k bicyklu (z katalógu alebo manuálne) |
| **Session** | Štart / stop hodiny, záznamy |
| **Fix HDMI** | Aktivuje HDMI výstup na TV (`xrandr --output HDMI-1 --mode 1920x1080 --same-as eDP-1`) |
| **Reload TV** | Pošle `riders_updated` broadcast — dashboard sa refreshne bez reloadu stránky |

### BT Status indikátor

V headeri adminu je live BT status — stav každého pripojeného/odpojeného pásu, HR, batéria. Poll každých 5s.

---

## HR Zóny (Myzone-kompatibilné)

| Zóna | % max HR | Farba | MEPs/min |
|------|----------|-------|----------|
| 0 | < 50% | tmavosivá | 0 |
| 1 | 50–59% | sivá | 1 |
| 2 | 60–69% | modrá | 2 |
| 3 | 70–79% | zelená | 3 |
| 4 | 80–89% | žltá | 4 |
| 5 | ≥ 90% | červená | 4 |

**Max HR výpočet (Tanaka formula):**
- Muži: `208 - (0.7 × vek)`
- Ženy: `206 - (0.88 × vek)`

Jazdec môže mať `max_hr_override` v katalógu ak pozná svoje skutočné MEP z testovania.

---

## Admin API (port 8766, cez nginx `/api/`)

```
GET/POST    /api/bikes
DELETE      /api/bikes/{id}
PUT/DELETE  /api/bikes/{id}/strap       — auto-reload gateway
PUT/DELETE  /api/riders/{position}      — auto-reload gateway (podporuje catalog_id)
GET/POST    /api/rider-catalog
PUT/DELETE  /api/rider-catalog/{id}
GET         /api/straps/status          — live connected/hr/battery per strap
GET         /api/scan                   — BLE scan 10s
POST        /api/session/start|stop
GET         /api/session
GET         /api/sessions
POST        /api/dashboard-reload       — broadcast refresh na všetky dashboardy
POST        /api/fix-hdmi               — aktivuje HDMI výstup cez hdmi-helper.py
GET         /api/network                — sieťový stav (eth0/wlp2s0/offline)
```

---

## Databáza

SQLite v Docker volume `/data/local_cache.db`. Pri prvom spustení sa automaticky aplikuje `gateway/seed.sql`.

**Manuálne operácie:**
```bash
# Priamy prístup
docker compose exec gateway sqlite3 /data/local_cache.db "SELECT * FROM bikes;"

# Ak existuje prázdna DB (seed sa nespustil automaticky):
docker compose exec gateway sqlite3 /data/local_cache.db < /app/seed.sql

# Backup
docker compose exec gateway sqlite3 /data/local_cache.db .dump > backup_$(date +%Y%m%d).sql
```

---

## HDMI / TV setup

TV sa pripája cez HDMI na notebook. Ak sa TV zapne neskôr alebo HDMI vypadne:

**Cez admin panel:** tlačidlo `📺 Fix HDMI` → zavolá `/api/fix-hdmi` → `hdmi-helper.py` service → xrandr ako kiosk user.

**Manuálne (SSH):**
```bash
ssh adminhrstudio@192.168.1.145
sudo -u kiosk env DISPLAY=:0 XAUTHORITY=/home/kiosk/.Xauthority \
    xrandr --output HDMI-1 --mode 1920x1080 --rate 60 --same-as eDP-1
```

**hdmi-helper.py** je malý HTTP service (port `127.0.0.1:8767`) bežiaci ako `systemd` service `hdmi-helper`. Potrebuje sudoers NOPASSWD pre xrandr ako kiosk user.

---

## Kiosk

- **User:** `kiosk`, autologin cez LightDM
- **Display:** `:0`, `XAUTHORITY=/home/kiosk/.Xauthority`
- **Chromium:** `--start-fullscreen http://localhost` (nie `--kiosk` — treba myš pre navigáciu)
- **Navigácia:** ⚙ tlačidlo na dashboarde → admin panel; ← tlačidlo → späť na dashboard
- **F11:** fullscreen Chromium
- **HDMI po zapnutí TV:** `xrandr --auto` v Openbox autostart; ak TV zapojená za behu, použiť Fix HDMI button

---

## Known issues — notebook (Dell Latitude 7390)

### Freeze fix (i915 + NVMe)

Dell Latitude 7390 s i7-8650U má known bug — Intel PSR (Panel Self Refresh) spôsobuje GPU freeze. Fix v `/etc/default/grub`:

```
GRUB_CMDLINE_LINUX_DEFAULT="quiet i915.enable_psr=0 nvme_core.default_ps_max_latency_us=0"
```

Po zmene: `sudo update-grub && sudo reboot`

### WiFi skripty

```bash
~/wifi-home.sh     # domáca sieť No_internet
~/wifi-iphone.sh   # iPhone hotspot
~/wifi-studio.sh   # spinning štúdio RRUSINA
```

---

## Troubleshooting

### SSH na notebook
```bash
sshpass -p '<heslo>' ssh -o StrictHostKeyChecking=no adminhrstudio@192.168.1.145
# sudo heslo rovnaké ako SSH heslo
```

### Kontajnery nebeží
```bash
docker compose ps
docker compose logs gateway --tail=50
docker compose logs frontend --tail=20
```

### Admin ukazuje prázdne bicykle
`gateway` beží v `network_mode: host`. Skontroluj `docker-compose.yml` — `frontend` musí mať:
```yaml
extra_hosts:
  - "gateway:host-gateway"
```

### BLE pás sa nepripája
1. Skontroluj či pás advertisuje: `hcitool lescan` (musí mať mokré elektródy na tele)
2. Skontroluj BlueZ: `systemctl status bluetooth`
3. Ak gateway píše "InProgress": asyncio.Lock je v kóde — môže byť frontová rada, počkaj 30–60s
4. Po redeployi: sundat pás z tela na ~10s a znova nasadiť (BlueZ zombie spojenie)
5. Ak je pás pripojený na iný host (mobil, iný PC): odpojiť tam, potom skúsiť tu

### BLE InProgress chyby v logoch
BlueZ zvláda len 1 connect naraz. Systém má `asyncio.Lock` — chyby znamenajú že lock nefunguje správne. Skontroluj `ble_manager.py` — `_connect_lock` musí byť zdieľaný medzi všetkými `BleStrap` inštanciami.

### Docker volume wipe (ztratená DB)
```bash
# seed.sql sa spustí automaticky len ak DB súbor neexistuje
# Ak existuje prázdna DB:
docker compose exec gateway sqlite3 /data/local_cache.db < /app/seed.sql
```

### Sieť — WiFi konfigurácia (notebook)
Notebook používa `wpasupplicant + ifupdown`, **NIE** NetworkManager. Nepoužívať `nmcli`.
```bash
cat /etc/network/interfaces
cat /etc/wpa_supplicant/wpa_supplicant.conf
```

---

## Kľúčové príkazy

```bash
# Logy v reálnom čase
journalctl -u hr-studio -f
docker compose logs -f gateway

# Reštart celého stacku
systemctl restart hr-studio

# Len rebuild + reštart gateway/frontend
docker compose build gateway frontend
docker compose up -d gateway frontend

# BT dongle info
hciconfig
hcitool lescan      # scan BLE zariadení (Ctrl+C pre zastavenie)

# SQLite priamo
docker compose exec gateway sqlite3 /data/local_cache.db

# Fix HDMI manuálne
sudo -u kiosk env DISPLAY=:0 XAUTHORITY=/home/kiosk/.Xauthority \
    xrandr --output HDMI-1 --mode 1920x1080 --rate 60 --same-as eDP-1

# Status hdmi-helper
systemctl status hdmi-helper
```

---

## TODO

- [x] FIT súbor export per rider (fit-tool==0.9.15, `GET /api/sessions/{id}/riders/{pos}/export.fit`)
- [ ] FitReserve sync — `/api/v1/studio/riders/today` → riders_cache pred hodinou
- [ ] Strava / Garmin Connect upload cez FitReserve
- [ ] Otestovať s 2+ MZ-1 pásmi súčasne
- [ ] Presný dátum narodenia jazdcov (birth_year → birth_date) — Tanaka formula z presného veku

---

## Sieťová topológia

```
Router (192.168.1.1)
├── Notebook kiosk  192.168.1.145  WiFi wlp2s0 (DHCP rezervovaná)
│   ├── Docker: gateway   → port 8765 (WebSocket), 8766 (Admin API)
│   ├── Docker: frontend  → port 80 (nginx + dashboard + admin)
│   └── hdmi-helper.py    → port 127.0.0.1:8767 (lokálny, nie externý)
└── Server (Coolify)  192.168.1.105
    └── Live: https://hrstudio.system15.win
```
