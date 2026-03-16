# HR Studio Monitor — CLAUDE.md

## Čo je tento projekt

Lokálny HR monitoring systém pre spinning štúdio (max 20 bicyklov).
Číta dáta z ANT+ hrudných pásov v reálnom čase a zobrazuje ich na TV displeji.
Integruje sa s externým rezervačným systémom cez REST API + webhook.

**Súvisiaci projekt:** Rezervačný systém (samostatný repozitár) — poskytuje
rider profily (meno, vek, váha, pohlavie) cez `GET /api/v1/studio/riders/today`.

---

## Architektúra

```
[ANT+ pásy] → [gateway] → [WebSocket] → [frontend dashboard]
                              ↑
[Cloud rezervák] → [sync service] → [lokálna SQLite cache]
```

**Lokálny stack (Docker Compose):**
- `gateway`  — Python, číta ANT+ dongles, broadcastuje HR cez WebSocket
- `api`      — FastAPI, REST endpoints, session management, webhook prijímač
- `sync`     — Python service, sťahuje rider profily z cloud rezerváku
- `frontend` — statický HTML/JS dashboard (nginx), WebSocket klient
- `db`       — PostgreSQL (session history, logs)

**Cloud rezervačný systém (externý):**
- Poskytuje endpoint `GET /api/v1/studio/riders/today`
- Posiela webhooky pri zmene rezervácie na `POST /webhook/reservation-change`
- Rider model obsahuje: `name`, `birth_year`, `weight_kg`, `gender`, `max_hr_override`, `bike_number`

---

## Technické rozhodnutia a dôvody

### ANT+ vs BLE
Zvolený ANT+. Jeden dongle = 8 súčasných kanálov, takže pre 20 pásov = 3 dongles.
BLE má limit ~7 spojení a horšiu spoľahlivosť pri väčšom počte zariadení.

### Pevné device ID (nie scan mode)
Každý ANT+ pás má pridelené `ant_device_id` v DB. Channel sa otvára s konkrétnym
ID — nescannujeme okolie. Dôvod: spinning štúdio, každý bicykel má "svoje" miesto.

### Lokálna SQLite cache
HR monitor musí fungovať aj bez internetu. Sync service stiahne rider profily
pred hodinou a uloží do SQLite. Ak vypadne cloud, cache zostáva.

### MEP výpočet (Tanaka formula)
Max HR sa nepýtame od ridera — vypočítame z veku:
- Muži:  `208 - (0.7 × vek)`
- Ženy:  `206 - (0.88 × vek)`
Rider môže mať `max_hr_override` ak pozná svoje reálne MEP z testovania.

### HR zóny (Myzone-kompatibilné)
- Zóna 0 (šedá):   < 50% MEP
- Zóna 1 (modrá):  50–59% MEP
- Zóna 2 (zelená): 60–69% MEP
- Zóna 3 (žltá):   70–79% MEP
- Zóna 4 (červená): ≥ 80% MEP

### Multi-dongle — každý v samostatnom vlákne
`threading.Thread(target=node.start, daemon=True)` — jeden thread per dongle.
Broadcast do asyncio event loopu cez `asyncio.run_coroutine_threadsafe()`.

---

## Databázová schéma (lokálna)

```sql
-- Fyzické bicykle v štúdiu
CREATE TABLE bikes (
    id          INTEGER PRIMARY KEY,
    position    INTEGER UNIQUE NOT NULL,  -- miesto 1–20
    label       TEXT NOT NULL,
    dongle_port TEXT,                     -- napr. /dev/ttyUSB0
    ant_channel INTEGER                   -- 0–7 na danom dongle
);

-- ANT+ pásy priradené k bicyklom
CREATE TABLE straps (
    id            INTEGER PRIMARY KEY,
    ant_device_id INTEGER UNIQUE NOT NULL,
    bike_id       INTEGER REFERENCES bikes(id),
    label         TEXT
);

-- Cache rider profilov z cloud rezerváku (prepísaná pred každou hodinou)
CREATE TABLE riders_cache (
    bike_position  INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    max_hr         INTEGER NOT NULL,
    weight_kg      REAL,
    reservation_id INTEGER,
    synced_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Session história
CREATE TABLE sessions (
    id          INTEGER PRIMARY KEY,
    started_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    ended_at    DATETIME,
    instructor  TEXT
);

-- Záznamy HR počas session (pre post-workout štatistiky)
CREATE TABLE session_data (
    session_id  INTEGER REFERENCES sessions(id),
    bike_position INTEGER,
    rider_name  TEXT,
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
    hr          INTEGER,
    zone        INTEGER
);
```

---

## Štruktúra projektu

```
hr-studio/
├── CLAUDE.md               ← tento súbor
├── docker-compose.yml
├── .env.example
│
├── gateway/                ← ANT+ → WebSocket bridge
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py             ← vstupný bod, spúšťa node + asyncio
│   ├── studio_manager.py   ← MultiDongleManager, načíta config z DB
│   ├── strap_channel.py    ← StrapChannel, on_data, on_timeout
│   └── hr_utils.py         ← calc_max_hr, calc_zone, calc_calories
│
├── api/                    ← FastAPI backend
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   ├── routers/
│   │   ├── session.py      ← start/stop session, live status
│   │   ├── bikes.py        ← CRUD bicyklov a pásov
│   │   └── webhook.py      ← prijíma zmeny z cloud rezerváku
│   ├── models.py           ← SQLAlchemy modely
│   └── sync.py             ← sync_riders(), sync_loop()
│
├── frontend/               ← Dashboard
│   ├── Dockerfile          ← nginx
│   ├── index.html          ← fullscreen dashboard
│   ├── dashboard.js        ← WebSocket klient, grid rendering
│   ├── zones.js            ← farby, zóna logika
│   └── style.css
│
└── docs/
    ├── hardware.md         ← odporúčaný hardware, dongle setup
    ├── ant_device_ids.md   ← ako zistiť device ID pásu
    └── reservation_api.md  ← dokumentácia cloud API kontraktu
```

---

## ENV premenné

```bash
# .env (nikdy do gitu!)

# Cloud rezervačný systém
CLOUD_REZERVAK_URL=https://rezervak.poetika.online/api/v1/studio
STUDIO_API_KEY=generuj-cez-openssl-rand-hex-32
WEBHOOK_SECRET=generuj-cez-openssl-rand-hex-32

# Lokálna DB
DATABASE_URL=postgresql://studio:pass@db:5432/studio
LOCAL_CACHE_DB=/data/local_cache.db

# ANT+ dongles (čiarkou oddelené porty)
ANT_DONGLE_PORTS=/dev/ttyUSB0,/dev/ttyUSB1,/dev/ttyUSB2

# Sync interval v sekundách
SYNC_INTERVAL_SECONDS=300

# WebSocket port
WS_PORT=8765
```

---

## Kľúčové flows

### 1. Štart hodiny (happy path)
```
instructor otvorí admin panel
  → klikne "Načítať riderov"
  → sync service zavolá GET /api/v1/studio/riders/today
  → uloží do riders_cache
  → gateway načíta cache, otvorí ANT+ kanály
  → dashboard zobrazí karty s menami
  → instructor klikne "Štart session"
```

### 2. Rider sa pripojí (pás sa nasadí)
```
pás začne vysielať broadcast
  → StrapChannel.on_data() zachytí HR
  → broadcast_ws({type: "hr_update", position, name, hr, pct, zone})
  → frontend aktualizuje kartu (farba, % , BPM)
```

### 3. Výpadok internetu počas hodiny
```
sync service nemôže dosiahnuť cloud
  → loguje warning, NIC INÉ sa nedeje
  → riders_cache zostáva z posledného úspešného syncu
  → HR monitoring beží normálne
```

### 4. Zmena rezervácie počas dňa
```
cloud rezervák odošle POST /webhook/reservation-change
  → webhook.py overí X-Webhook-Secret header
  → triggerne okamžitý sync_riders()
  → broadcast_ws({type: "riders_updated"})
  → dashboard refresh kariet (bez reload stránky)
```

---

## Čo ešte treba dorobiť

- [ ] Admin panel (web UI pre priradenie pásov k bicyklom)
- [ ] Post-workout súhrn (zóny čas, kalórie, priemerný HR per rider)
- [ ] Integrácia s rezervačným systémom — pridať polia `birth_year`, `weight_kg`,
      `gender`, `max_hr_override`, `bike_number` do rider modelu tam
- [ ] Watchdog pre vypadnuté pásy (timeout detekcia)
- [ ] Kalórie výpočet (Keytel formula, potrebuje váhu + HR)
- [ ] MEP test flow (voliteľný — rider môže urobiť 20min test a override uložiť)
- [ ] SSL / lokálna sieť konfig (odporúčam Tailscale pre remote prístup)

---

## Hardware notes

- **Odporúčaný**: mini PC s Intel N100, 8–16 GB RAM (napr. Beelink EQ12, ~150 €)
- **Alternatíva**: Raspberry Pi 5 (8 GB) — funguje, ale ARM (niektoré Docker image
  treba buildovať lokálne)
- **ANT+ dongles**: Dynastream ANTUSB-m alebo Garmin USB ANT Stick
  — každý zvládne max 8 simultánnych kanálov
- **Pre 20 pásov**: 3 dongles
- **OS**: Ubuntu 24.04 LTS alebo Raspberry Pi OS (Debian Bookworm)
- Docker group_add: `dialout` pre prístup k USB serial zariadeniam

---

## Užitočné príkazy

```bash
# Spustenie celého stacku
docker compose up -d

# Logy gateway (ANT+ príjem)
docker compose logs -f gateway

# Manuálny sync riderov
docker compose exec api python -c "import asyncio; from sync import sync_riders; asyncio.run(sync_riders())"

# Zistenie ANT+ device ID pásu (scan mode — len pre setup)
docker compose exec gateway python tools/scan_devices.py

# Backup lokálnej cache
docker compose exec api sqlite3 /data/local_cache.db .dump > backup_$(date +%Y%m%d).sql
```
