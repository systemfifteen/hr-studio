import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

CLOUD_URL          = os.getenv("CLOUD_REZERVAK_URL", "")
API_KEY            = os.getenv("STUDIO_API_KEY", "")
CACHE_DB           = os.getenv("LOCAL_CACHE_DB", "/data/local_cache.db")
SYNC_INTERVAL      = int(os.getenv("SYNC_INTERVAL_SECONDS", 300))
GATEWAY_RELOAD_URL = os.getenv("GATEWAY_RELOAD_URL", "")

_last_sync_ok:  float = 0.0
_last_sync_at:  float = 0.0


def calc_max_hr(birth_year: int, gender: str = None) -> int:
    age = datetime.now().year - birth_year
    if gender and gender.upper() == "F":
        return round(206 - 0.88 * age)
    return round(208 - 0.7 * age)


def _init_cache_db():
    db = sqlite3.connect(CACHE_DB)
    db.execute("""
        CREATE TABLE IF NOT EXISTS riders_cache (
            bike_position  INTEGER PRIMARY KEY,
            name           TEXT NOT NULL,
            max_hr         INTEGER NOT NULL,
            weight_kg      REAL,
            birth_year     INTEGER,
            gender         TEXT,
            reservation_id INTEGER,
            synced_at      TEXT
        )
    """)
    # Migrácia pre existujúce DB — pridaj stĺpce ak chýbajú
    for col, typedef in [("birth_year", "INTEGER"), ("gender", "TEXT")]:
        try:
            db.execute(f"ALTER TABLE riders_cache ADD COLUMN {col} {typedef}")
        except Exception:
            pass
    db.execute("""
        CREATE TABLE IF NOT EXISTS bikes (
            id          INTEGER PRIMARY KEY,
            position    INTEGER UNIQUE NOT NULL,
            label       TEXT NOT NULL,
            dongle_port TEXT,
            ant_channel INTEGER
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS straps (
            id            INTEGER PRIMARY KEY,
            ble_address   TEXT UNIQUE,
            bike_id       INTEGER REFERENCES bikes(id),
            label         TEXT
        )
    """)
    # Migrácia pre existujúce DB
    for col, typedef in [("ble_address", "TEXT")]:
        try:
            db.execute(f"ALTER TABLE straps ADD COLUMN {col} {typedef}")
        except Exception:
            pass
    db.commit()
    db.close()


async def sync_riders(date: str = None) -> bool:
    global _last_sync_ok, _last_sync_at
    _last_sync_at = time.time()

    if not CLOUD_URL or not API_KEY:
        logger.warning("CLOUD_REZERVAK_URL alebo STUDIO_API_KEY nie je nastavený")
        return False

    target_date = date or str(datetime.today().date())

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{CLOUD_URL}/riders/today",
                headers={"X-Studio-Key": API_KEY},
                params={"date": target_date},
            )
            resp.raise_for_status()
            riders = resp.json()

        db = sqlite3.connect(CACHE_DB)
        db.execute("DELETE FROM riders_cache")
        now_str = datetime.now().isoformat()

        for r in riders:
            max_hr = (
                r.get("max_hr_override")
                or calc_max_hr(r["birth_year"], r.get("gender"))
            )
            db.execute("""
                INSERT INTO riders_cache
                    (bike_position, name, max_hr, weight_kg, birth_year, gender, reservation_id, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r["bike_position"],
                r["name"],
                max_hr,
                r.get("weight_kg"),
                r.get("birth_year"),
                r.get("gender"),
                r.get("reservation_id"),
                now_str,
            ))

        db.commit()
        db.close()

        _last_sync_ok = time.time()
        logger.info(f"Sync OK — {len(riders)} riderov ({target_date})")

        if GATEWAY_RELOAD_URL:
            try:
                async with httpx.AsyncClient(timeout=3.0) as gc:
                    await gc.post(GATEWAY_RELOAD_URL)
                logger.info("Gateway reload notifikácia odoslaná")
            except Exception as e:
                logger.warning(f"Gateway reload zlyhalo (ignorujem): {e}")

        return True

    except httpx.HTTPStatusError as e:
        logger.error(f"Sync HTTP chyba: {e.response.status_code}")
    except httpx.RequestError as e:
        logger.warning(f"Sync sieťová chyba (offline?): {e}")
    except Exception as e:
        logger.error(f"Sync neznáma chyba: {e}")

    return False


def load_from_cache() -> list:
    try:
        db   = sqlite3.connect(CACHE_DB)
        rows = db.execute(
            "SELECT bike_position, name, max_hr, weight_kg, birth_year, gender FROM riders_cache ORDER BY bike_position"
        ).fetchall()
        db.close()
        return [
            {"bike_position": r[0], "name": r[1], "max_hr": r[2],
             "weight_kg": r[3], "birth_year": r[4], "gender": r[5]}
            for r in rows
        ]
    except Exception as e:
        logger.error(f"Chyba čítania cache: {e}")
        return []


async def sync_loop():
    """Beží ako background task — sync každých N sekúnd."""
    _init_cache_db()
    await sync_riders()   # prvý sync hneď pri štarte
    while True:
        await asyncio.sleep(SYNC_INTERVAL)
        await sync_riders()


def sync_status() -> dict:
    return {
        "last_sync_at":  _last_sync_at,
        "last_sync_ok":  _last_sync_ok,
        "cache_count":   len(load_from_cache()),
        "cloud_url":     CLOUD_URL or "nie je nastavený",
    }
