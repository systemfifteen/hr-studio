import asyncio
import logging
import os
import sqlite3

import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sync import sync_riders, sync_status, load_from_cache, _init_cache_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api")

CACHE_DB           = os.getenv("LOCAL_CACHE_DB", "/data/local_cache.db")
WEBHOOK_SECRET     = os.getenv("WEBHOOK_SECRET", "")
GATEWAY_SCAN_URL   = os.getenv("GATEWAY_SCAN_URL", "http://localhost:8766/scan")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_cache_db()
    asyncio.create_task(sync_loop_task())
    yield


app = FastAPI(title="HR Studio API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


async def sync_loop_task():
    from sync import sync_loop
    await sync_loop()


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_db():
    return sqlite3.connect(CACHE_DB)


# ── Status & sync ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/status")
def status():
    return sync_status()


@app.get("/riders")
def riders():
    return load_from_cache()


@app.post("/sync")
async def manual_sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(sync_riders)
    return {"ok": True, "msg": "Sync spustený na pozadí"}


# ── BLE scan (proxy na gateway) ───────────────────────────────────────────────

@app.get("/scan")
async def scan_ble():
    """Spustí BLE scan cez gateway a vráti nájdené HR zariadenia."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(GATEWAY_SCAN_URL)
            return resp.json()
    except Exception as e:
        raise HTTPException(503, f"Gateway scan nedostupný: {e}")


# ── Bikes CRUD ────────────────────────────────────────────────────────────────

class BikeCreate(BaseModel):
    position: int
    label:    str | None = None


@app.get("/bikes")
def list_bikes():
    db = get_db()
    rows = db.execute("""
        SELECT b.id, b.position, b.label,
               s.id, s.ble_address, s.label
        FROM   bikes b
        LEFT JOIN straps s ON s.bike_id = b.id
        ORDER BY b.position
    """).fetchall()
    db.close()
    return [
        {
            "id":       r[0],
            "position": r[1],
            "label":    r[2],
            "strap":    {"id": r[3], "ble_address": r[4], "label": r[5]} if r[3] else None,
        }
        for r in rows
    ]


@app.post("/bikes", status_code=201)
def create_bike(data: BikeCreate):
    db = get_db()
    try:
        row = db.execute(
            "INSERT INTO bikes (position, label) VALUES (?, ?) RETURNING id",
            (data.position, data.label or f"Bike {data.position}"),
        ).fetchone()
        db.commit()
        return {"ok": True, "id": row[0], "position": data.position}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Bicykel s touto pozíciou už existuje")
    finally:
        db.close()


@app.delete("/bikes/{bike_id}", status_code=204)
def delete_bike(bike_id: int):
    db = get_db()
    db.execute("DELETE FROM straps WHERE bike_id=?", (bike_id,))
    db.execute("DELETE FROM bikes WHERE id=?", (bike_id,))
    db.commit()
    db.close()


# ── Straps CRUD ───────────────────────────────────────────────────────────────

class StrapAssign(BaseModel):
    ble_address: str
    label:       str | None = None


@app.put("/bikes/{bike_id}/strap")
def assign_strap(bike_id: int, data: StrapAssign):
    db = get_db()
    bike = db.execute("SELECT id FROM bikes WHERE id=?", (bike_id,)).fetchone()
    if not bike:
        db.close()
        raise HTTPException(404, "Bicykel nenájdený")

    # Uvoľni adresu ak je priradená inému bicyklu
    db.execute(
        "UPDATE straps SET bike_id=NULL WHERE ble_address=? AND bike_id!=?",
        (data.ble_address, bike_id),
    )
    existing = db.execute("SELECT id FROM straps WHERE bike_id=?", (bike_id,)).fetchone()
    if existing:
        db.execute(
            "UPDATE straps SET ble_address=?, label=? WHERE bike_id=?",
            (data.ble_address, data.label, bike_id),
        )
    else:
        db.execute(
            "INSERT INTO straps (ble_address, bike_id, label) VALUES (?, ?, ?)"
            " ON CONFLICT(ble_address) DO UPDATE SET bike_id=?, label=?",
            (data.ble_address, bike_id, data.label, bike_id, data.label),
        )
    db.commit()
    db.close()
    return {"ok": True, "bike_id": bike_id, "ble_address": data.ble_address}


@app.delete("/bikes/{bike_id}/strap", status_code=204)
def remove_strap(bike_id: int):
    db = get_db()
    db.execute("DELETE FROM straps WHERE bike_id=?", (bike_id,))
    db.commit()
    db.close()


# ── Webhook od cloud rezerváku ────────────────────────────────────────────────

class WebhookPayload(BaseModel):
    event:      str
    rider_name: str | None = None
    bike:       int | None = None
    date:       str | None = None


@app.post("/webhook/reservation-change")
async def reservation_changed(
    payload: WebhookPayload,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str = Header(...),
):
    if x_webhook_secret != WEBHOOK_SECRET:
        raise HTTPException(403, "Forbidden")
    if payload.event in ("created", "updated", "cancelled"):
        background_tasks.add_task(sync_riders, payload.date)
    return {"ok": True}
