import json
import logging
import sqlite3
from datetime import datetime

from bleak import BleakScanner
from hr_utils import calc_max_hr

logger = logging.getLogger(__name__)

HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
SCAN_TIMEOUT = 10.0


class AdminApi:
    def __init__(self, cache_db: str, manager, broadcast_fn, session_mgr=None):
        self.cache_db     = cache_db
        self.manager      = manager
        self.broadcast_fn = broadcast_fn
        self.session_mgr  = session_mgr

    def _db(self):
        return sqlite3.connect(self.cache_db)

    async def handle(self, method: str, path: str, body: bytes) -> tuple[int, object]:
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        # OPTIONS preflight
        if method == "OPTIONS":
            return 204, {}

        if method == "GET" and path == "/status":
            return 200, self._status()

        if method == "GET" and path == "/bikes":
            return 200, self._get_bikes()

        if method == "POST" and path == "/bikes":
            return self._add_bike(data)

        if method == "POST" and path == "/reload":
            return await self._reload()

        if method == "POST" and path == "/sync":
            return await self._reload()   # sync = reload pre teraz

        if method == "GET" and path == "/scan":
            return await self._scan()

        # Riders
        if method == "GET" and path == "/riders":
            return 200, self._get_riders()

        if path.startswith("/riders/"):
            try:
                position = int(path.split("/")[2])
            except (IndexError, ValueError):
                return 400, {"error": "invalid position"}
            if method == "PUT":
                return await self._upsert_rider(position, data)
            if method == "DELETE":
                return await self._delete_rider(position)

        # Session
        if method == "GET" and path == "/session":
            cur = self.session_mgr.get_current() if self.session_mgr else None
            return 200, cur or {"active": False}

        if method == "POST" and path == "/session/start":
            if not self.session_mgr:
                return 503, {"error": "session manager not ready"}
            label = data.get("label")
            return 200, self.session_mgr.start(label)

        if method == "POST" and path == "/session/stop":
            if not self.session_mgr:
                return 503, {"error": "session manager not ready"}
            result = self.session_mgr.stop()
            await self.broadcast_fn({"type": "session_stopped",
                                     "summary": result.get("summary", [])})
            return 200, result

        if method == "GET" and path == "/sessions":
            if not self.session_mgr:
                return 503, {"error": "session manager not ready"}
            return 200, self.session_mgr.list_sessions()

        # /bikes/{id}
        parts = path.split("/")
        if len(parts) >= 3 and parts[1] == "bikes":
            try:
                bike_id = int(parts[2])
            except ValueError:
                return 400, {"error": "invalid id"}

            if len(parts) == 3:
                if method == "DELETE":
                    return self._delete_bike(bike_id)

            if len(parts) == 4 and parts[3] == "strap":
                if method == "PUT":
                    return await self._assign_strap(bike_id, data)
                if method == "DELETE":
                    return await self._remove_strap(bike_id)

        return 404, {"error": "not found"}

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _status(self):
        db = self._db()
        count = db.execute("SELECT COUNT(*) FROM riders_cache").fetchone()[0]
        db.close()
        return {"last_sync_ok": 0, "cache_count": count}

    def _get_riders(self):
        db   = self._db()
        rows = db.execute(
            "SELECT bike_position, name, max_hr, weight_kg, birth_year, gender "
            "FROM riders_cache ORDER BY bike_position"
        ).fetchall()
        db.close()
        return [
            {"bike_position": r[0], "name": r[1], "max_hr": r[2],
             "weight_kg": r[3], "birth_year": r[4], "gender": r[5]}
            for r in rows
        ]

    async def _upsert_rider(self, position: int, data: dict):
        name       = (data.get("name") or "").strip()
        birth_year = data.get("birth_year")
        weight_kg  = data.get("weight_kg")
        gender     = (data.get("gender") or "M").upper()
        if not name:
            return 400, {"error": "name required"}
        if not birth_year:
            return 400, {"error": "birth_year required"}
        max_hr = data.get("max_hr") or calc_max_hr(int(birth_year), gender)
        db = self._db()
        db.execute("""
            INSERT INTO riders_cache(bike_position, name, max_hr, weight_kg, birth_year, gender)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(bike_position) DO UPDATE SET
                name=excluded.name, max_hr=excluded.max_hr,
                weight_kg=excluded.weight_kg, birth_year=excluded.birth_year,
                gender=excluded.gender, synced_at=CURRENT_TIMESTAMP
        """, (position, name, max_hr, weight_kg, int(birth_year), gender))
        db.commit()
        db.close()
        await self._reload()
        return 200, {"ok": True, "max_hr": max_hr}

    async def _delete_rider(self, position: int):
        db = self._db()
        db.execute("DELETE FROM riders_cache WHERE bike_position=?", (position,))
        db.commit()
        db.close()
        await self._reload()
        return 200, {"ok": True}

    def _get_bikes(self):
        db = self._db()
        bikes = db.execute(
            "SELECT id, position, label FROM bikes ORDER BY position"
        ).fetchall()
        result = []
        for bike_id, position, label in bikes:
            strap = db.execute(
                "SELECT id, ble_address, label FROM straps "
                "WHERE bike_id=? AND ble_address IS NOT NULL",
                (bike_id,),
            ).fetchone()
            rider = db.execute(
                "SELECT name, max_hr, weight_kg, birth_year, gender "
                "FROM riders_cache WHERE bike_position=?",
                (position,),
            ).fetchone()
            result.append({
                "id":       bike_id,
                "position": position,
                "label":    label,
                "strap":    {"id": strap[0], "ble_address": strap[1], "label": strap[2]}
                            if strap else None,
                "rider":    {"name": rider[0], "max_hr": rider[1], "weight_kg": rider[2],
                             "birth_year": rider[3], "gender": rider[4]}
                            if rider else None,
            })
        db.close()
        return result

    def _add_bike(self, data):
        pos   = data.get("position")
        label = data.get("label") or f"Bike {pos}"
        if not pos:
            return 400, {"error": "position required"}
        try:
            db = self._db()
            db.execute("INSERT INTO bikes(position, label) VALUES(?,?)", (pos, label))
            db.commit()
            db.close()
            return 201, {"ok": True}
        except sqlite3.IntegrityError:
            return 409, {"error": "position exists"}

    def _delete_bike(self, bike_id):
        db = self._db()
        db.execute("DELETE FROM straps WHERE bike_id=?", (bike_id,))
        db.execute("DELETE FROM bikes WHERE id=?", (bike_id,))
        db.commit()
        db.close()
        return 200, {"ok": True}

    async def _assign_strap(self, bike_id, data):
        addr = (data.get("ble_address") or "").strip().upper()
        if not addr:
            return 400, {"error": "ble_address required"}
        db = self._db()
        db.execute("DELETE FROM straps WHERE bike_id=?", (bike_id,))
        db.execute(
            "INSERT INTO straps(ble_address, bike_id, label) VALUES(?,?,?)",
            (addr, bike_id, ""),
        )
        db.commit()
        db.close()
        await self._reload()
        return 200, {"ok": True}

    async def _remove_strap(self, bike_id):
        db = self._db()
        db.execute("DELETE FROM straps WHERE bike_id=?", (bike_id,))
        db.commit()
        db.close()
        await self._reload()
        return 200, {"ok": True}

    async def _reload(self):
        if self.manager:
            await self.manager.reload()
            await self.broadcast_fn({"type": "riders_updated"})
        return 200, {"ok": True}

    async def _scan(self):
        logger.info(f"BLE scan — hľadám HR zariadenia ({SCAN_TIMEOUT}s)...")
        devices = await BleakScanner.discover(
            timeout=SCAN_TIMEOUT,
            service_uuids=[HEART_RATE_SERVICE_UUID],
        )
        result = [
            {"name": d.name or "Unknown", "address": d.address, "rssi": d.rssi}
            for d in sorted(devices, key=lambda x: -(x.rssi or -999))
        ]
        logger.info(f"Scan hotový — {len(result)} HR zariadení")
        return 200, result
