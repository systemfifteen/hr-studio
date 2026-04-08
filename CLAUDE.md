# HR Studio Monitor — CLAUDE.md

## Čo je tento projekt

Live HR monitoring systém pre spinning štúdio (max 20 bicyklov).
Číta BLE (Myzone MZ-1) alebo ANT+ hrudné pásy, zobrazuje HR na TV dashboarde, ukladá session dáta, exportuje FIT súbory.

**Live URL (server):** https://hrstudio.system15.win
**Notebook (štúdio):** 192.168.1.145 — tu beží BLE gateway pre reálne hodiny
**Server:** 192.168.1.105 (Coolify) — záložná/testovacia inštancia

---

## Architektúra

```
[BLE pásy MZ-1]  ──BLE──▶ ┐
[ANT+ pásy]  ──USB dongle──▶ [gateway/] ──WebSocket 8765──▶ [frontend/]
                                  │
                                  └──REST 8766 (nginx /api/)──▶ admin panel
                                  │
                               [SQLite /data/local_cache.db]
```

**Stack (Docker Compose):**
- `gateway/` — Python + bleak (BLE) + openant (ANT+), WebSocket broadcast, Admin REST API (port 8766)
- `frontend/` — statický HTML/JS, nginx (port 80), WebSocket klient + admin panel

**Žiadny PostgreSQL, žiadna separate API služba** — všetko v gateway + SQLite.

---

## Databázová schéma (aktuálna)

```sql
CREATE TABLE bikes (
    id INTEGER PRIMARY KEY,
    position INTEGER UNIQUE NOT NULL,  -- miesto 1–20
    label TEXT NOT NULL
);

CREATE TABLE straps (
    id INTEGER PRIMARY KEY,
    ble_address TEXT UNIQUE NOT NULL,
    ble_name TEXT,
    label TEXT,                        -- číslo nálepky (1–20)
    bike_id INTEGER REFERENCES bikes(id)
);

CREATE TABLE riders_catalog (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    birth_year INTEGER NOT NULL,
    weight_kg REAL,
    gender TEXT NOT NULL DEFAULT 'M',
    max_hr_override INTEGER,
    birth_date TEXT                    -- YYYY-MM-DD, presnejší ako birth_year
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- default: INSERT INTO settings VALUES('hr_formula', 'tanaka')

CREATE TABLE riders_cache (
    bike_position INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    max_hr INTEGER NOT NULL,
    weight_kg REAL,
    catalog_id INTEGER
);

CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME
);

CREATE TABLE session_data (
    session_id INTEGER REFERENCES sessions(id),
    bike_position INTEGER,
    rider_name TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    hr INTEGER,
    zone INTEGER
);
```

---

## HR Formulae (hr_utils.py)

```python
# Tanaka (default)
men:   208 - (0.7 × age)
women: 206 - (0.88 × age)

# Classic
220 - age

# Prepínač cez settings tabuľku: key='hr_formula', value='tanaka'|'classic'
```

**JS age calc (bug-free):**
```javascript
const [y, m, d] = birth_date.split('-').map(Number);
// NIE new Date("YYYY-MM-DD") — parsuje ako UTC → zlý deň v SEČ
```

---

## Zóny (Myzone-kompatibilné)

| Zóna | % max HR | Farba | MEPs/min |
|------|----------|-------|----------|
| 0 | < 50% | tmavosivá | 0 |
| 1 | 50–59% | sivá | 1 |
| 2 | 60–69% | modrá | 2 |
| 3 | 70–79% | zelená | 3 |
| 4 | 80–89% | žltá | 4 |
| 5 | ≥ 90% | červená | 4 |

---

## Štruktúra projektu

```
hr-studio/
├── CLAUDE.md
├── docker-compose.yml              ← full stack (gateway+api+frontend+db) — server
├── docker-compose.standalone.yml   ← gateway+frontend, SQLite — notebook
├── docker-compose.frontend.yml     ← pre Coolify deploy
│
├── gateway/
│   ├── main.py             ← asyncio, WebSocket server, Admin REST API
│   ├── ble_manager.py      ← BleManager, asyncio.Lock (1 spojenie naraz)
│   ├── ble_strap.py        ← BleStrap, Myzone MZ-1 protokol
│   ├── admin_api.py        ← REST API: bikes, straps, riders, sessions, FIT export
│   ├── session_manager.py  ← session start/stop, session_data zápis
│   ├── fit_export.py       ← FIT súbor export (fit-tool==0.9.15)
│   ├── hr_utils.py         ← calc_age(), calc_max_hr(), calc_zone(), calc_calories()
│   ├── studio_manager.py   ← načíta bikes/straps z DB, riadi BLE
│   └── requirements.txt
│
├── frontend/
│   ├── index.html          ← fullscreen dashboard (kiosk)
│   ├── dashboard.js        ← WebSocket klient, grid rendering, zóny, animácie
│   ├── admin.html          ← admin panel (bikes, straps, riders, sessions)
│   └── nginx.conf
│
├── setup.sh                ← plne automatická inštalácia na čistý Debian
├── setup-kiosk.sh          ← X11, Chromium kiosk, autologin
└── installer/              ← USB bootovací ISO builder (Debian preseed)
```

---

## Admin API (nginx proxy /api/ → port 8766)

```
GET/POST  /api/bikes                    → CRUD bicyklov
DELETE    /api/bikes/{id}
PUT/DEL   /api/bikes/{id}/strap         → priradenie pásu

GET/POST  /api/rider-catalog            → trvalý katalóg jazdcov
PUT/DEL   /api/rider-catalog/{id}

PUT/DEL   /api/riders/{position}        → riders_cache (pre aktuálnu hodinu)

GET       /api/straps/status            → live HR/battery per strap, transport: "ble"|"ant"
GET       /api/scan                     → BLE scan 10s, auto-register MYZONE-*
GET       /api/scan-ant                 → ANT+ HR scan (wildcard 0x78, 8 kanálov, 20s)
GET       /api/scan-ant-all             → ANT+ wildcard scan VŠETKY typy (0x00) — FE-C, CAD, HR...

GET/POST  /api/strap-catalog            → katalóg fyzických pásov (label, BLE name, MAC, ANT+ ID)
PUT/DEL   /api/strap-catalog/{id}

POST      /api/session/start|stop
GET       /api/session                  → aktuálna session
GET       /api/sessions                 → história
GET       /api/sessions/{id}/riders/{pos}/export.fit  → FIT súbor download
GET       /api/sessions/{id}/export.csv → CSV export celej session

POST      /api/dashboard-reload         → broadcast refresh
POST      /api/fix-hdmi                 → xrandr cez hdmi-helper.py (port 8767)
POST      /api/restart-bt               → systemctl restart bluetooth (port 8768)
POST      /api/restart-ant              → reštartuje ANT+ StudioManager (reload kanálov)
GET       /api/settings                 → hr_formula
PUT       /api/settings                 → uloží hr_formula
GET       /api/network                  → sieťový stav (eth0/wlp2s0)

GET/PUT   /api/timer                    → získa/nastaví konfiguráciu interval timera (rounds, work_min, rest_min)
POST      /api/timer/start              → štartuje timer, broadcastuje timer_update
POST      /api/timer/pause              → pauzuje timer
POST      /api/timer/resume             → obnoví timer
POST      /api/timer/stop               → zastaví timer (reset do idle)
GET       /api/zone-history             → posledných 10 min zone dát per bike_position z session_data
```

---

## Interval timer

**Súbor:** `gateway/interval_timer.py`

Spravuje interval program — e.g. 3× (25 min work + 5 min rest). Stav: `idle` / `running` / `paused`.

```python
# Konfigurácia
timer.set_config(rounds=3, work_min=25, rest_min=5)
# Expanduje na: [work25, rest5, work25, rest5, work25, rest5]

# Elapsed výpočet — presný aj po pause/resume:
# running: elapsed = elapsed_at_pause + (now - start_epoch)
# paused:  elapsed = elapsed_at_pause

# Perzistencia:
timer.save_to_db(db)   # settings key = 'interval_timer', JSON
timer.load_from_db(db)
```

**WebSocket broadcast** — každá zmena stavu emituje `timer_update`:
```json
{
  "type": "timer_update",
  "configured": true,
  "state": "running",
  "rounds": 3,
  "work_min": 25,
  "rest_min": 5,
  "intervals": [{"type":"work","duration":1500}, ...],
  "elapsed": 142.3
}
```

**Dashboard rendering (`frontend/dashboard.js`):**
- `timerActive(state)`: true ak configured && state != "idle"
- `renderGrid()`: timer cell vložený ako prvý, `grid-column: span 2; grid-row: span 2`; column count += 4 (effective)
- `renderTimerSVG()`: SVG viewBox="-55 -55 110 110"; outer ring r=48 (OW=6) = farebné segmenty intervalov; inner ring r=36 (IW=12) = stroke-dasharray countdown; elapsed segmenty outer ringu = `#333`
- `timerTick = setInterval(renderTimerSVG, 500)` — beží len keď timer aktívny

---

## Dashboard state persistence

**LocalStorage** (`hr-studio-riders-v1`):
- Ukladá: `calories`, `meps` pre každého jazdca
- Obnovuje: len ak aktívna session (`active=true`) a `started_at` zhoduje s uloženým
- Ukladá každých 15s + `beforeunload`

**Zone history** — `GET /api/zone-history`:
- Volá sa po každom `initial_state` WebSocket správe (podmienka: aktívna session)
- SQLite query: `CAST((julianday(ts) - julianday('1970-01-01')) * 86400000 AS INTEGER)` → UTC ms timestamp

**UTC timezone pitfall (SQLite → JS):**
```javascript
// SQLite CURRENT_TIMESTAMP vracia UTC bez 'Z' — new Date() parsuje ako local!
new Date(d.started_at.replace(' ', 'T') + 'Z')  // správne
new Date(d.started_at)                            // NESPRÁVNE — +2h offset v SEČ
```

---

## Deploy

### Notebook (spinning štúdio)
```bash
# Rebuild + deploy:
docker compose -f docker-compose.standalone.yml build gateway frontend
docker compose -f docker-compose.standalone.yml up -d

# Git pull + rebuild:
echo 'hrstudio' | sudo -S git -C /opt/hr-studio pull
```

### Server (Coolify)
```bash
# Trigger redeploy cez API:
COOLIFY_TOKEN="..."
curl -X POST "http://localhost:8000/api/v1/deploy?uuid=uduo7vv26va660q740j4wwi7" \
  -H "Authorization: Bearer $COOLIFY_TOKEN"

# Po rebuilde importuj DB zálohu:
docker run --rm \
  -v uduo7vv26va660q740j4wwi7_cache-data:/data \
  -v /path/to/backup:/backup \
  alpine cp /backup/hr_data.db /data/local_cache.db
docker restart gateway-uduo7vv26va660q740j4wwi7-*
```

### Záloha DB z notebooku na server
```bash
sshpass -p 'hrstudio' ssh adminhrstudio@192.168.1.145 \
  "docker run --rm -v hr-studio_hr_data:/data alpine sh -c 'cat /data/local_cache.db'" \
  > backup/hr_data.db
# Poznámka: súbor v volume sa volá local_cache.db (nie hr_data.db)
```

---

## Kľúčové poznatky BLE

- MZ-1 advertisuje **len keď má skin contact** (mokré elektródy)
- ~20s inicializácia po nasadení → HR=0 ignorované
- BlueZ zvláda 1 BLE spojenie naraz → `asyncio.Lock` v BleManager
- Po redeploy treba dať pás dole (BlueZ zombie spojenie)
- `network_mode: host` na gateway (potrebné pre BLE cez D-Bus)
- `extra_hosts: gateway:host-gateway` vo frontend (keďže gateway je v host network)

---

## Hardware (notebook — spinning štúdio)

- **Notebook:** Dell Latitude 7390, i7-8650U
- **BT dongle:** Asus BT540 (USB, hci0, MAC A0:AD:9F:73:9C:F0)
- **ANT+ dongle:** Dynastream ANTUSB2 Stick (0x0fcf:0x1008) — zasunutý do notebooku
- **Freeze fix:** `i915.enable_psr=0 nvme_core.default_ps_max_latency_us=0` v GRUB
- **WiFi:** wpasupplicant (NIE NetworkManager!) — skripty: wifi-home.sh, wifi-studio.sh, wifi-iphone.sh

---

## ANT+ integrácia

**Dongle:** Dynastream ANTUSB2 Stick (VID: 0x0fcf, PID: 0x1008), prístupný cez `privileged: true` v Docker.
**Library:** `openant>=1.2` + `pyusb>=1.0.2` — import `openant.easy.node/channel` (nie `ant.easy.*`).

**Myzone MZ-1 serial → ANT+ device ID:**
```
ant_device_id = serial_number - 3191516
# Príklad: pás 1, serial 3222279 → ANT+ ID 30763 ✓ (overené živým testom)
# Serials 20 pásov: 1→3222279, 2→3222305, 3→3222314, 4→3222302, 5→3222312,
#   6→3222315, 7→3222324, 8→3222303, 9→3222313, 10→3222277, 11→3222316,
#   12→3222278, 13→3222325, 14→3222317, 15→3222322, 16→3222321,
#   17→3222327, 18→3222326, 19→3222318, 20→3222304
```

**Workflow pridania ANT+ pásu:**
1. Admin panel → Katalóg pásov → Edit → zadaj ANT+ ID (alebo cez ANT+ skener)
2. Admin panel → Bicykle a pásy → Zmeniť pás → vyber z katalógu → automaticky prenesie aj ANT+ ID
3. Po uložení sa otvorí ANT+ kanál po reštarte gateway (alebo Restart ANT+ button)

**Stav ANT+ kanálov:** `GET /api/straps/status` — každý záznam má `transport: "ant"` alebo `"ble"`.
Live stĺpec v admin paneli preferuje connected transport (BLE aj ANT+ bežia súčasne ako fallback).

**Dôležité:** `_assign_strap()` zachováva `ant_device_id` pri BLE re-assigne. Seed.sql nesmie obsahovať strap záznamy s BLE adresou — spôsobuje konflikt po reštarte.

**Súbory:**
- `gateway/studio_manager.py` — ANT+ manager (vlákno, openant)
- `gateway/strap_channel.py` — jeden ANT+ kanál = jeden pás

**ANT+ scan — device typy:**
| Typ | Hex | Popis |
|-----|-----|-------|
| HR | 0x78 | Hrudný pás |
| FE-C | 0x11 | Fitness zariadenie (konzola bicykla) |
| PWR | 0x0B | Merač výkonu |
| SPD+CAD | 0x79 | Rýchlosť + kadencia |
| CAD | 0x7A | Kadencia |

---

## Čo ešte treba

- [ ] FIT export end-to-end test (GoldenCheetah ✓, Strava/Garmin Connect?)
- [ ] FitReserve sync — riders pred hodinou cez API
- [ ] Overiť formulu `ant_id = serial - 3191516` s druhým pásom v gym → bulk import pre všetkých 20
- [ ] Zistiť či Spinner NXT konzola vysiela ANT+ (FE-C/CAD) — test cez scan-ant-all počas šliapania
- [ ] Pridať čítanie kadancie (RPM) ak konzola vysiela
