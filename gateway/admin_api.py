import asyncio
import json
import logging
import sqlite3
from datetime import datetime

from bleak import BleakScanner
from hr_utils import calc_max_hr, calc_age

logger = logging.getLogger(__name__)

HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
SCAN_TIMEOUT = 10.0


class AdminApi:
    def __init__(self, cache_db: str, manager, broadcast_fn, session_mgr=None, ant_manager=None, timer=None):
        self.cache_db     = cache_db
        self.manager      = manager
        self.ant_manager  = ant_manager
        self.broadcast_fn = broadcast_fn
        self.session_mgr  = session_mgr
        self.timer        = timer
        self._hidden_positions: set = set()
        self._ensure_catalog_table()
        self._load_hidden_positions()

    def _ensure_catalog_table(self):
        db = self._db()
        db.execute("""
            CREATE TABLE IF NOT EXISTS strap_catalog (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                label       TEXT NOT NULL,
                ble_name    TEXT,
                ble_address TEXT UNIQUE
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS riders_catalog (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                birth_year      INTEGER NOT NULL,
                weight_kg       REAL,
                gender          TEXT NOT NULL DEFAULT 'M',
                max_hr_override INTEGER
            )
        """)
        # Migrácia: pridaj birth_date do riders_catalog ak ešte neexistuje
        cols = [r[1] for r in db.execute("PRAGMA table_info(riders_catalog)").fetchall()]
        if "birth_date" not in cols:
            db.execute("ALTER TABLE riders_catalog ADD COLUMN birth_date TEXT")
        # Migrácia: pridaj birth_date do riders_cache ak ešte neexistuje
        cols = [r[1] for r in db.execute("PRAGMA table_info(riders_cache)").fetchall()]
        if "birth_date" not in cols:
            db.execute("ALTER TABLE riders_cache ADD COLUMN birth_date TEXT")
        # Migrácia: pridaj ant_device_id do straps ak ešte neexistuje
        cols = [r[1] for r in db.execute("PRAGMA table_info(straps)").fetchall()]
        if "ant_device_id" not in cols:
            db.execute("ALTER TABLE straps ADD COLUMN ant_device_id INTEGER")
        # Migrácia: pridaj ant_device_id do strap_catalog ak ešte neexistuje
        cols = [r[1] for r in db.execute("PRAGMA table_info(strap_catalog)").fetchall()]
        if "ant_device_id" not in cols:
            db.execute("ALTER TABLE strap_catalog ADD COLUMN ant_device_id INTEGER")
        db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('hr_formula', 'hunt')")
        db.commit()
        db.close()

    def _db(self):
        return sqlite3.connect(self.cache_db)

    def _load_hidden_positions(self):
        try:
            db = self._db()
            row = db.execute("SELECT value FROM settings WHERE key='hidden_positions'").fetchone()
            db.close()
            if row:
                self._hidden_positions = set(json.loads(row[0]))
        except Exception:
            pass

    def _save_hidden_positions(self):
        db = self._db()
        db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('hidden_positions', ?)",
                   (json.dumps(list(self._hidden_positions)),))
        db.commit()
        db.close()

    def get_hidden_positions(self) -> set:
        return self._hidden_positions

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

        if method == "GET" and path == "/network":
            return 200, self._network_status()

        if method == "GET" and path == "/bikes":
            return 200, self._get_bikes()

        if method == "POST" and path == "/bikes":
            return self._add_bike(data)

        if method == "POST" and path == "/reload":
            return await self._reload()

        if method == "POST" and path == "/sync":
            return await self._reload()   # sync = reload pre teraz

        if method == "POST" and path == "/dashboard-reload":
            await self.broadcast_fn({"type": "riders_updated"})
            return 200, {"ok": True}

        if method == "POST" and path == "/fix-hdmi":
            return await self._fix_hdmi()

        if method == "POST" and path == "/restart-bt":
            return await self._restart_bt()

        if method == "POST" and path == "/restart-ant":
            return await self._restart_ant()

        if method == "POST" and path == "/poweroff":
            return await self._poweroff()

        if method == "GET" and path == "/scan":
            return await self._scan()

        if method == "GET" and path == "/scan-ant":
            return await self._scan_ant()

        if method == "GET" and path == "/scan-ant-all":
            return await self._scan_ant_all()

        # Riders
        if method == "GET" and path == "/straps/status":
            return 200, self._straps_status()

        if method == "GET" and path == "/riders":
            return 200, self._get_riders()

        if path.startswith("/riders/"):
            parts = path.split("/")
            try:
                position = int(parts[2])
            except (IndexError, ValueError):
                return 400, {"error": "invalid position"}
            if method == "PUT" and len(parts) == 3:
                return await self._upsert_rider(position, data)
            if method == "DELETE" and len(parts) == 3:
                return await self._delete_rider(position)
            if method == "POST" and len(parts) == 4 and parts[3] == "visibility":
                return await self._set_rider_visibility(position, data)

        # Session
        if method == "GET" and path == "/session":
            cur = self.session_mgr.get_current() if self.session_mgr else None
            return 200, cur or {"active": False}

        if method == "POST" and path == "/session/start":
            if not self.session_mgr:
                return 503, {"error": "session manager not ready"}
            label    = data.get("label")
            duration = data.get("planned_duration_min")
            if duration is not None:
                try:
                    duration = int(duration)
                except (ValueError, TypeError):
                    duration = None
            return 200, self.session_mgr.start(label, duration)

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

        if path.startswith("/sessions/"):
            parts = path.split("/")
            try:
                sid = int(parts[2])
            except (IndexError, ValueError):
                return 400, {"error": "invalid session id"}
            if method == "GET" and len(parts) == 4 and parts[3] == "summary":
                if not self.session_mgr:
                    return 503, {"error": "session manager not ready"}
                return 200, self.session_mgr.get_summary(sid)
            if method == "GET" and len(parts) == 4 and parts[3] == "export.csv":
                if not self.session_mgr:
                    return 503, {"error": "session manager not ready"}
                return 200, {"__csv__": self.session_mgr.export_csv(sid), "session_id": sid}
            if (method == "GET" and len(parts) == 6
                    and parts[3] == "riders" and parts[5] == "export.fit"):
                try:
                    pos = int(parts[4])
                except ValueError:
                    return 400, {"error": "invalid position"}
                if not self.session_mgr:
                    return 503, {"error": "session manager not ready"}
                filename, fit_bytes = self.session_mgr.export_fit(sid, pos)
                if fit_bytes is None:
                    return 404, {"error": "no data for this rider/session"}
                return 200, {"__fit__": fit_bytes, "filename": filename}

        # Strap catalog
        if method == "GET" and path == "/strap-catalog":
            return 200, self._get_catalog()

        if method == "POST" and path == "/strap-catalog":
            return self._add_catalog(data)

        if path.startswith("/strap-catalog/"):
            try:
                catalog_id = int(path.split("/")[2])
            except (IndexError, ValueError):
                return 400, {"error": "invalid id"}
            if method == "PUT":
                return self._update_catalog(catalog_id, data)
            if method == "DELETE":
                return self._delete_catalog(catalog_id)

        # Zone history (posledných 10 min aktívnej session, pre obnovu po refreshi)
        if method == "GET" and path == "/zone-history":
            return 200, self._get_zone_history()

        # Interval timer
        if method == "GET" and path == "/timer":
            return 200, (self.timer.get_ws_state() if self.timer else {"configured": False})

        if method == "PUT" and path == "/timer":
            return await self._config_timer(data)

        if method == "POST" and path == "/timer/start":
            return await self._ctrl_timer("start")

        if method == "POST" and path == "/timer/pause":
            return await self._ctrl_timer("pause")

        if method == "POST" and path == "/timer/resume":
            return await self._ctrl_timer("resume")

        if method == "POST" and path == "/timer/stop":
            return await self._ctrl_timer("stop")

        # Settings
        if method == "GET" and path == "/settings":
            return 200, self._get_settings()

        if method == "PUT" and path == "/settings":
            return self._update_settings(data)

        # Rider catalog
        if method == "GET" and path == "/rider-catalog":
            return 200, self._get_rider_catalog()

        if method == "POST" and path == "/rider-catalog":
            return self._add_rider_catalog(data)

        if path.startswith("/rider-catalog/"):
            try:
                rc_id = int(path.split("/")[2])
            except (IndexError, ValueError):
                return 400, {"error": "invalid id"}
            if method == "PUT":
                return self._update_rider_catalog(rc_id, data)
            if method == "DELETE":
                return self._delete_rider_catalog(rc_id)

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

    def _get_catalog(self):
        db = self._db()
        rows = db.execute(
            "SELECT id, label, ble_name, ble_address, ant_device_id FROM strap_catalog ORDER BY id"
        ).fetchall()
        db.close()
        return [
            {"id": r[0], "label": r[1], "ble_name": r[2], "ble_address": r[3], "ant_device_id": r[4]}
            for r in rows
        ]

    def _add_catalog(self, data):
        label       = (data.get("label") or "").strip()
        ble_name    = (data.get("ble_name") or "").strip() or None
        ble_address = (data.get("ble_address") or "").strip().upper() or None
        ant_id      = data.get("ant_device_id")
        if ant_id is not None:
            try: ant_id = int(ant_id)
            except (TypeError, ValueError): ant_id = None
        if not label:
            return 400, {"error": "label required"}
        try:
            db = self._db()
            db.execute(
                "INSERT INTO strap_catalog(label, ble_name, ble_address, ant_device_id) VALUES(?,?,?,?)",
                (label, ble_name, ble_address, ant_id),
            )
            db.commit()
            db.close()
            return 201, {"ok": True}
        except sqlite3.IntegrityError:
            return 409, {"error": "ble_address already exists in catalog"}

    def _update_catalog(self, catalog_id, data):
        label       = (data.get("label") or "").strip()
        ble_name    = (data.get("ble_name") or "").strip() or None
        ble_address = (data.get("ble_address") or "").strip().upper() or None
        ant_id      = data.get("ant_device_id")
        if ant_id is not None:
            try: ant_id = int(ant_id)
            except (TypeError, ValueError): ant_id = None
        if not label:
            return 400, {"error": "label required"}
        try:
            db = self._db()
            db.execute(
                "UPDATE strap_catalog SET label=?, ble_name=?, ble_address=?, ant_device_id=? WHERE id=?",
                (label, ble_name, ble_address, ant_id, catalog_id),
            )
            db.commit()
            db.close()
            return 200, {"ok": True}
        except sqlite3.IntegrityError:
            return 409, {"error": "ble_address already exists"}

    def _delete_catalog(self, catalog_id):
        db = self._db()
        db.execute("DELETE FROM strap_catalog WHERE id=?", (catalog_id,))
        db.commit()
        db.close()
        return 200, {"ok": True}

    # ── Settings ───────────────────────────────────────────────────────────────

    def _get_settings(self):
        db = self._db()
        rows = db.execute("SELECT key, value FROM settings").fetchall()
        db.close()
        return {r[0]: r[1] for r in rows}

    def _update_settings(self, data):
        allowed = {"hr_formula"}
        db = self._db()
        for key, value in data.items():
            if key not in allowed:
                continue
            if key == "hr_formula" and value not in ("hunt", "tanaka", "classic"):
                return 400, {"error": "hr_formula must be 'hunt', 'tanaka' or 'classic'"}
            db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (key, value))
        db.commit()
        db.close()
        return 200, {"ok": True}

    def _get_formula(self):
        db = self._db()
        row = db.execute("SELECT value FROM settings WHERE key='hr_formula'").fetchone()
        db.close()
        return row[0] if row else "hunt"

    # ── Rider catalog ──────────────────────────────────────────────────────────

    def _get_rider_catalog(self):
        db = self._db()
        rows = db.execute(
            "SELECT id, name, birth_year, weight_kg, gender, max_hr_override, birth_date "
            "FROM riders_catalog ORDER BY name"
        ).fetchall()
        db.close()
        return [
            {"id": r[0], "name": r[1], "birth_year": r[2],
             "weight_kg": r[3], "gender": r[4], "max_hr_override": r[5],
             "birth_date": r[6]}
            for r in rows
        ]

    def _add_rider_catalog(self, data):
        name       = (data.get("name") or "").strip()
        birth_date = (data.get("birth_date") or "").strip() or None
        birth_year = data.get("birth_year")
        weight_kg  = data.get("weight_kg") or None
        gender     = (data.get("gender") or "M").upper()
        max_hr_override = data.get("max_hr_override") or None
        if not name or (not birth_date and not birth_year):
            return 400, {"error": "name and birth_date (or birth_year) required"}
        if birth_date and not birth_year:
            birth_year = int(birth_date[:4])
        db = self._db()
        db.execute(
            "INSERT INTO riders_catalog(name, birth_year, birth_date, weight_kg, gender, max_hr_override) "
            "VALUES(?,?,?,?,?,?)",
            (name, int(birth_year), birth_date, weight_kg, gender, max_hr_override),
        )
        db.commit()
        db.close()
        return 201, {"ok": True}

    def _update_rider_catalog(self, rc_id, data):
        name       = (data.get("name") or "").strip()
        birth_date = (data.get("birth_date") or "").strip() or None
        birth_year = data.get("birth_year")
        weight_kg  = data.get("weight_kg") or None
        gender     = (data.get("gender") or "M").upper()
        max_hr_override = data.get("max_hr_override") or None
        if not name or (not birth_date and not birth_year):
            return 400, {"error": "name and birth_date (or birth_year) required"}
        if birth_date and not birth_year:
            birth_year = int(birth_date[:4])
        db = self._db()
        db.execute(
            "UPDATE riders_catalog SET name=?, birth_year=?, birth_date=?, weight_kg=?, "
            "gender=?, max_hr_override=? WHERE id=?",
            (name, int(birth_year), birth_date, weight_kg, gender, max_hr_override, rc_id),
        )
        db.commit()
        db.close()
        return 200, {"ok": True}

    def _delete_rider_catalog(self, rc_id):
        db = self._db()
        db.execute("DELETE FROM riders_catalog WHERE id=?", (rc_id,))
        db.commit()
        db.close()
        return 200, {"ok": True}

    def _network_status(self):
        import os
        wireless = set()
        try:
            with open("/proc/net/wireless") as f:
                for line in f.readlines()[2:]:
                    iface = line.split(":")[0].strip()
                    if iface:
                        wireless.add(iface)
        except Exception:
            pass
        result = []
        try:
            for iface in sorted(os.listdir("/sys/class/net")):
                if iface == "lo":
                    continue
                try:
                    with open(f"/sys/class/net/{iface}/operstate") as f:
                        if f.read().strip() == "up":
                            result.append({
                                "iface": iface,
                                "type": "wifi" if iface in wireless else "eth",
                            })
                except Exception:
                    pass
        except Exception:
            pass
        return result

    def _status(self):
        db = self._db()
        count = db.execute("SELECT COUNT(*) FROM riders_cache").fetchone()[0]
        db.close()
        return {"last_sync_ok": 0, "cache_count": count}

    def _straps_status(self):
        result = []
        if self.manager:
            for s in self.manager.straps.values():
                result.append({
                    "ble_address":   s.ble_address,
                    "ant_device_id": None,
                    "position":      s.bike_position,
                    "name":          s.rider_name,
                    "connected":     s.connected,
                    "last_hr":       s.last_hr,
                    "battery":       s.battery,
                    "transport":     "ble",
                })
        if self.ant_manager:
            for ch in self.ant_manager.get_status():
                result.append({
                    "ble_address":   None,
                    "ant_device_id": ch["ant_device_id"],
                    "position":      ch["position"],
                    "name":          ch["name"],
                    "connected":     ch["connected"],
                    "last_hr":       ch["hr"],
                    "battery":       None,
                    "transport":     "ant",
                })
        return result

    def _get_riders(self):
        db   = self._db()
        rows = db.execute(
            "SELECT bike_position, name, max_hr, weight_kg, birth_year, gender, birth_date "
            "FROM riders_cache ORDER BY bike_position"
        ).fetchall()
        db.close()
        return [
            {"bike_position": r[0], "name": r[1], "max_hr": r[2],
             "weight_kg": r[3], "birth_year": r[4], "gender": r[5], "birth_date": r[6]}
            for r in rows
        ]

    async def _upsert_rider(self, position: int, data: dict):
        catalog_id = data.get("catalog_id")
        if catalog_id is not None:
            db = self._db()
            row = db.execute(
                "SELECT name, birth_year, weight_kg, gender, max_hr_override, birth_date "
                "FROM riders_catalog WHERE id=?", (int(catalog_id),)
            ).fetchone()
            db.close()
            if not row:
                return 404, {"error": "catalog entry not found"}
            name, birth_year, weight_kg, gender, max_hr_override, birth_date = row
            formula = self._get_formula()
            max_hr = max_hr_override or calc_max_hr(birth_year=birth_year, gender=gender, birth_date=birth_date, formula=formula)
        else:
            name       = (data.get("name") or "").strip()
            birth_year = data.get("birth_year")
            birth_date = (data.get("birth_date") or "").strip() or None
            weight_kg  = data.get("weight_kg")
            gender     = (data.get("gender") or "M").upper()
            if not name:
                return 400, {"error": "name required"}
            if not birth_year:
                return 400, {"error": "birth_year required"}
            formula = self._get_formula()
            max_hr = data.get("max_hr") or calc_max_hr(int(birth_year), gender, birth_date=birth_date, formula=formula)
        db = self._db()
        db.execute("""
            INSERT INTO riders_cache(bike_position, name, max_hr, weight_kg, birth_year, gender, birth_date)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(bike_position) DO UPDATE SET
                name=excluded.name, max_hr=excluded.max_hr,
                weight_kg=excluded.weight_kg, birth_year=excluded.birth_year,
                gender=excluded.gender, birth_date=excluded.birth_date,
                synced_at=CURRENT_TIMESTAMP
        """, (position, name, max_hr, weight_kg, int(birth_year), gender, birth_date))
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

    async def _set_rider_visibility(self, position: int, data: dict):
        hidden = bool(data.get("hidden", False))
        if hidden:
            self._hidden_positions.add(position)
        else:
            self._hidden_positions.discard(position)
        self._save_hidden_positions()
        await self.broadcast_fn({
            "type":     "rider_visibility",
            "position": position,
            "hidden":   hidden,
        })
        return 200, {"ok": True, "position": position, "hidden": hidden}

    def _get_bikes(self):
        db = self._db()
        bikes = db.execute(
            "SELECT id, position, label FROM bikes ORDER BY position"
        ).fetchall()
        result = []
        for bike_id, position, label in bikes:
            strap = db.execute(
                "SELECT id, ble_address, ant_device_id, label FROM straps "
                "WHERE bike_id=?",
                (bike_id,),
            ).fetchone()
            rider = db.execute(
                "SELECT name, max_hr, weight_kg, birth_year, gender, birth_date "
                "FROM riders_cache WHERE bike_position=?",
                (position,),
            ).fetchone()
            result.append({
                "id":       bike_id,
                "position": position,
                "label":    label,
                "hidden":   position in self._hidden_positions,
                "strap":    {
                                "id":            strap[0],
                                "ble_address":   strap[1],
                                "ant_device_id": strap[2],
                                "label":         strap[3],
                            } if strap else None,
                "rider":    {"name": rider[0], "max_hr": rider[1], "weight_kg": rider[2],
                             "birth_year": rider[3], "gender": rider[4], "birth_date": rider[5]}
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
        addr      = (data.get("ble_address") or "").strip().upper() or None
        ant_id    = data.get("ant_device_id")
        label     = (data.get("label") or "").strip()

        # If catalog_id provided, look up address and ant_device_id from catalog
        catalog_id = data.get("catalog_id")
        if catalog_id is not None:
            db = self._db()
            row = db.execute(
                "SELECT ble_address, label, ant_device_id FROM strap_catalog WHERE id=?",
                (int(catalog_id),),
            ).fetchone()
            db.close()
            if not row:
                return 404, {"error": "catalog entry not found"}
            if not row[0] and row[2] is None:
                return 400, {"error": "catalog entry has no ble_address or ant_device_id"}
            addr   = row[0].upper() if row[0] else None
            label  = label or row[1]
            if ant_id is None and row[2] is not None:
                ant_id = int(row[2])

        if not addr and ant_id is None:
            return 400, {"error": "ble_address or ant_device_id required"}

        if ant_id is not None:
            try:
                ant_id = int(ant_id)
            except (TypeError, ValueError):
                return 400, {"error": "ant_device_id must be integer"}

        db = self._db()
        # Zachovaj ant_device_id z existujúceho záznamu ak nie je v požiadavke
        if ant_id is None:
            existing = db.execute(
                "SELECT ant_device_id FROM straps WHERE bike_id=?", (bike_id,)
            ).fetchone()
            if existing and existing[0] is not None:
                ant_id = existing[0]
        db.execute("DELETE FROM straps WHERE bike_id=?", (bike_id,))
        db.execute(
            "INSERT INTO straps(ble_address, ant_device_id, bike_id, label) VALUES(?,?,?,?)",
            (addr, ant_id, bike_id, label),
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

    async def _restart_bt(self):
        import urllib.request
        try:
            urllib.request.urlopen("http://127.0.0.1:8768/restart-bt", data=b"", timeout=30)
            await asyncio.sleep(3)   # počkaj kým BlueZ nahodí hci0
            if self.manager:
                await self.manager.reload()
            return 200, {"ok": True}
        except Exception as e:
            return 500, {"error": str(e)}

    async def _fix_hdmi(self):
        import urllib.request
        try:
            urllib.request.urlopen("http://127.0.0.1:8767/fix-hdmi", data=b"", timeout=5)
            return 200, {"ok": True}
        except Exception as e:
            return 500, {"error": str(e)}

    async def _poweroff(self):
        import urllib.request
        try:
            urllib.request.urlopen("http://127.0.0.1:8769/poweroff", data=b"", timeout=5)
            return 200, {"ok": True}
        except Exception as e:
            return 500, {"error": str(e)}

    async def _restart_ant(self):
        if self.ant_manager:
            self.ant_manager.reload()
            return 200, {"ok": True}
        return 200, {"ok": True, "note": "ant_manager not running"}

    # ── Zone history ──────────────────────────────────────────────────────────

    def _get_zone_history(self):
        db = self._db()
        session = db.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not session:
            db.close()
            return {"history": {}}
        rows = db.execute("""
            SELECT bike_position,
                   zone,
                   CAST((julianday(ts) - julianday('1970-01-01')) * 86400000 AS INTEGER) AS ts_ms
            FROM   session_data
            WHERE  session_id = ?
              AND  ts >= datetime('now', '-10 minutes')
            ORDER  BY ts ASC
        """, (session[0],)).fetchall()
        db.close()
        history = {}
        for pos, zone, ts_ms in rows:
            if pos not in history:
                history[pos] = []
            history[pos].append({"zone": zone, "ts": ts_ms})
        return {"history": history}

    # ── Interval timer ────────────────────────────────────────────────────────

    async def _config_timer(self, data):
        if not self.timer:
            return 503, {"error": "timer not available"}
        try:
            rounds   = int(data.get("rounds",   3))
            work_min = float(data.get("work_min", 25))
            rest_min = float(data.get("rest_min",  5))
        except (TypeError, ValueError):
            return 400, {"error": "rounds/work_min/rest_min must be numeric"}
        self.timer.set_config(rounds, work_min, rest_min)
        db = self._db()
        self.timer.save_to_db(db)
        db.commit()
        db.close()
        await self.broadcast_fn(self.timer.get_ws_state())
        return 200, self.timer.get_ws_state()

    async def _ctrl_timer(self, action: str):
        if not self.timer:
            return 503, {"error": "timer not available"}
        ok = False
        if action == "start":
            ok = self.timer.start()
        elif action == "pause":
            ok = self.timer.pause()
        elif action == "resume":
            ok = self.timer.resume()
        elif action == "stop":
            ok = self.timer.stop()
        if not ok:
            return 400, {"error": f"cannot {action} in state '{self.timer.state}'"}
        db = self._db()
        self.timer.save_to_db(db)
        db.commit()
        db.close()
        await self.broadcast_fn(self.timer.get_ws_state())
        return 200, self.timer.get_ws_state()

    async def _reload(self):
        if self.manager:
            await self.manager.reload()
        if self.ant_manager:
            self.ant_manager.reload()
        await self.broadcast_fn({"type": "riders_updated"})
        return 200, {"ok": True}

    async def _scan_ant(self):
        """
        ANT+ multi-device wildcard scan — otvorí až 8 kanálov naraz, každý zachytí
        iný HR pás v dosahu. Počas skenu pozastaví StudioManager (uvoľní dongle).
        Parametre: ?channels=N (default 8, max 8), ?timeout=N sekúnd (default 20).
        """
        import threading

        ANT_SCAN_CHANNELS = min(int(8), self.ant_manager._node is None and 8 or 8)
        ANT_SCAN_TIMEOUT  = 20.0
        ANTPLUS_NETWORK_KEY = [0xB9, 0xA5, 0x21, 0xFB, 0xBD, 0x72, 0xC3, 0x45]

        logger.info(f"ANT+ multi-scan — {ANT_SCAN_CHANNELS} kanálov, {ANT_SCAN_TIMEOUT}s...")

        # Zastav StudioManager aby uvoľnil dongle
        if self.ant_manager:
            if self.ant_manager._node:
                try:
                    self.ant_manager._node.stop()
                except Exception:
                    pass
                self.ant_manager._node = None
            with self.ant_manager._lock:
                self.ant_manager._channels.clear()

        found    = {}    # channel_num → device_id
        timeouts = set() # channel_nums ktoré timed out
        lock     = threading.Lock()
        # Všetky kanály skončili keď found + timeouts == ANT_SCAN_CHANNELS
        done     = threading.Event()

        def _check_done():
            with lock:
                if len(found) + len(timeouts) >= ANT_SCAN_CHANNELS:
                    done.set()

        def _do_scan():
            try:
                from openant.easy.node import Node
                from openant.easy.channel import Channel
                from openant.base.message import Message

                node = Node()
                node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)

                channels = []
                for ch_num in range(ANT_SCAN_CHANNELS):
                    ch = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)
                    ch.set_id(0, 0x78, 0)   # wildcard
                    ch.set_period(8070)
                    ch.set_rf_freq(57)
                    ch.set_search_timeout(int(ANT_SCAN_TIMEOUT / 2.5))

                    def make_on_data(n):
                        def on_data(data):
                            with lock:
                                if n not in found:
                                    found[n] = None   # reserved — device ID TBD
                            _check_done()
                        return on_data

                    def make_on_timeout(n):
                        def on_timeout():
                            with lock:
                                timeouts.add(n)
                            _check_done()
                        return on_timeout

                    ch.on_broadcast_data = make_on_data(ch_num)
                    ch.on_search_timeout = make_on_timeout(ch_num)
                    ch.open()
                    channels.append(ch)

                t = threading.Thread(target=node.start, daemon=True)
                t.start()

                # Čakaj kým všetky kanály nájdu zariadenie alebo vyprší čas
                done.wait(timeout=ANT_SCAN_TIMEOUT + 5)

                # Získaj device ID pre každý nájdený kanál
                for ch_num in list(found.keys()):
                    # request_message je hardcoded na channel 0 v openant —
                    # funguje správne len pre kanál 0; ostatné kanály extrahujeme
                    # cez priamy prístup k ant.request_message
                    try:
                        node.ant.request_message(ch_num, Message.ID.RESPONSE_CHANNEL_ID)
                        _, event, data = node.wait_for_special(Message.ID.RESPONSE_CHANNEL_ID)
                        dev_num = data[1] | (data[2] << 8)
                        with lock:
                            found[ch_num] = dev_num
                        logger.info(f"ANT+ kanál {ch_num}: device {dev_num}")
                    except Exception as e:
                        logger.warning(f"ANT+ kanál {ch_num}: nepodarilo sa získať device ID: {e}")

                node.stop()
            except Exception as e:
                logger.error(f"ANT+ scan chyba: {e}")
            finally:
                if self.ant_manager:
                    self.ant_manager.load_and_start()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _do_scan)

        devices = [v for v in found.values() if v is not None]
        logger.info(f"ANT+ scan hotový — {len(devices)} zariadení nájdených")

        if not devices:
            return 404, {"error": "Žiadne ANT+ zariadenia nenájdené"}
        return 200, {"devices": devices, "count": len(devices)}

    async def _scan_ant_all(self):
        """
        ANT+ wildcard scan pre VŠETKY typy zariadení (device_type=0).
        Vráti device_id + device_type pre každé nájdené zariadenie — užitočné
        na zistenie či spinning bike vysiela kadanciu (FE-C, SPD+CAD, CAD).
        """
        import threading

        ANT_SCAN_CHANNELS   = 8
        ANT_SCAN_TIMEOUT    = 20.0
        ANTPLUS_NETWORK_KEY = [0xB9, 0xA5, 0x21, 0xFB, 0xBD, 0x72, 0xC3, 0x45]
        DEVICE_TYPE_NAMES   = {
            0x78: ("HR",       "Hrudný pás"),
            0x11: ("FE-C",     "Fitness zariadenie (FE-C) — kadencia/výkon"),
            0x0B: ("PWR",      "Merač výkonu"),
            0x79: ("SPD+CAD",  "Rýchlosť + kadencia"),
            0x7A: ("CAD",      "Kadencia"),
            0x7B: ("SPD",      "Rýchlosť"),
            0x7C: ("SDM",      "Footpod / krokomér"),
        }

        logger.info(f"ANT+ all-device scan — {ANT_SCAN_CHANNELS} kanálov, {ANT_SCAN_TIMEOUT}s...")

        if self.ant_manager:
            if self.ant_manager._node:
                try:
                    self.ant_manager._node.stop()
                except Exception:
                    pass
                self.ant_manager._node = None
            with self.ant_manager._lock:
                self.ant_manager._channels.clear()

        found    = {}   # channel_num → {"device_id": int, "device_type": int} | None
        timeouts = set()
        lock     = threading.Lock()
        done     = threading.Event()

        def _check_done():
            with lock:
                if len(found) + len(timeouts) >= ANT_SCAN_CHANNELS:
                    done.set()

        def _do_scan():
            try:
                from openant.easy.node import Node
                from openant.easy.channel import Channel
                from openant.base.message import Message

                node = Node()
                node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)

                for ch_num in range(ANT_SCAN_CHANNELS):
                    ch = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)
                    ch.set_id(0, 0, 0)   # wildcard — všetky device typy
                    ch.set_period(8070)
                    ch.set_rf_freq(57)
                    ch.set_search_timeout(int(ANT_SCAN_TIMEOUT / 2.5))

                    def make_on_data(n):
                        def on_data(data):
                            with lock:
                                if n not in found:
                                    found[n] = None
                            _check_done()
                        return on_data

                    def make_on_timeout(n):
                        def on_timeout():
                            with lock:
                                timeouts.add(n)
                            _check_done()
                        return on_timeout

                    ch.on_broadcast_data = make_on_data(ch_num)
                    ch.on_search_timeout = make_on_timeout(ch_num)
                    ch.open()

                t = threading.Thread(target=node.start, daemon=True)
                t.start()

                done.wait(timeout=ANT_SCAN_TIMEOUT + 5)

                for ch_num in list(found.keys()):
                    try:
                        node.ant.request_message(ch_num, Message.ID.RESPONSE_CHANNEL_ID)
                        _, event, data = node.wait_for_special(Message.ID.RESPONSE_CHANNEL_ID)
                        dev_id   = data[1] | (data[2] << 8)
                        dev_type = data[3]
                        with lock:
                            found[ch_num] = {"device_id": dev_id, "device_type": dev_type}
                        logger.info(f"ANT+ kanál {ch_num}: device {dev_id} type 0x{dev_type:02X}")
                    except Exception as e:
                        logger.warning(f"ANT+ kanál {ch_num}: nepodarilo sa získať info: {e}")

                node.stop()
            except Exception as e:
                logger.error(f"ANT+ all-scan chyba: {e}")
            finally:
                if self.ant_manager:
                    self.ant_manager.load_and_start()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _do_scan)

        seen_ids = set()
        devices  = []
        for v in found.values():
            if v and v["device_id"] and v["device_id"] not in seen_ids:
                seen_ids.add(v["device_id"])
                type_code = v["device_type"]
                short, desc = DEVICE_TYPE_NAMES.get(type_code, (f"0x{type_code:02X}", "Neznámy typ"))
                devices.append({
                    "device_id":        v["device_id"],
                    "device_type":      type_code,
                    "device_type_short": short,
                    "device_type_desc":  desc,
                })

        logger.info(f"ANT+ all-scan hotový — {len(devices)} unikátnych zariadení")
        if not devices:
            return 404, {"error": "Žiadne ANT+ zariadenia nenájdené"}
        return 200, {"devices": devices, "count": len(devices)}

    async def _scan(self):
        logger.info(f"BLE scan — hľadám HR zariadenia ({SCAN_TIMEOUT}s)...")
        # BlueZ zvláda len jednu BLE operáciu naraz — počkáme na connect_lock
        lock       = self.manager._connect_lock if self.manager else None
        scan_event = self.manager._scan_event   if self.manager else None
        async def _do_scan():
            return await BleakScanner.discover(
                timeout=SCAN_TIMEOUT,
                service_uuids=[HEART_RATE_SERVICE_UUID],
            )
        # Nastav scan_event → straps prestanú súperiť o lock
        if scan_event:
            scan_event.set()
        try:
            if lock:
                async with lock:
                    devices = await _do_scan()
            else:
                devices = await _do_scan()
        finally:
            if scan_event:
                scan_event.clear()

        # Load catalog for enrichment
        db = self._db()
        catalog_rows = db.execute(
            "SELECT label, ble_name, ble_address FROM strap_catalog"
        ).fetchall()
        catalog_by_addr = {r[2].upper(): r[0] for r in catalog_rows if r[2]}
        catalog_by_name = {r[1]: r[0] for r in catalog_rows if r[1]}

        auto_added = 0
        result = []
        for d in sorted(devices, key=lambda x: -(x.rssi or -999)):
            dev_name = d.name or "Unknown"
            dev_addr = d.address.upper()
            label = catalog_by_addr.get(dev_addr) or catalog_by_name.get(dev_name)
            entry = {"name": dev_name, "address": dev_addr, "rssi": d.rssi,
                     "auto_added": False}
            if label:
                entry["catalog_label"] = label
                # Ak je to MYZONE pás, doplň ble_name na addr-matched zázname (ak chýba)
                # a tiež doplň MAC na ble_name-matched zázname (ak existuje separátne)
                if dev_name.upper().startswith("MYZONE-"):
                    db.execute(
                        "UPDATE strap_catalog SET ble_name=? WHERE ble_address=? AND (ble_name IS NULL OR ble_name='')",
                        (dev_name, dev_addr),
                    )
                    dup = db.execute(
                        "SELECT id FROM strap_catalog WHERE ble_name=? AND (ble_address IS NULL OR ble_address='')",
                        (dev_name,),
                    ).fetchone()
                    if dup:
                        db.execute("UPDATE strap_catalog SET ble_address=? WHERE id=?", (dev_addr, dup[0]))
                        logger.info(f"Zlúčené duplikáty pre {dev_name} ({dev_addr})")
                    db.commit()
            elif dev_name.upper().startswith("MYZONE-"):
                # Skontroluj či existuje záznam s rovnakým ble_name (ale bez MAC)
                existing = db.execute(
                    "SELECT id, label FROM strap_catalog WHERE ble_name=?",
                    (dev_name,),
                ).fetchone()
                if existing:
                    # Doplní iba MAC adresu
                    db.execute(
                        "UPDATE strap_catalog SET ble_address=? WHERE id=?",
                        (dev_addr, existing[0]),
                    )
                    db.commit()
                    entry["catalog_label"] = existing[1]
                    entry["auto_added"] = False
                    entry["mac_filled"] = True
                    logger.info(f"Doplnená MAC pre {dev_name} ({dev_addr}) → label {existing[1]}")
                else:
                    # Nový pás — pridaj s krátkym labelom ako placeholder
                    short = dev_name.split("-")[-1][-6:]
                    try:
                        db.execute(
                            "INSERT INTO strap_catalog(label, ble_name, ble_address) "
                            "VALUES(?,?,?)",
                            (short, dev_name, dev_addr),
                        )
                        db.commit()
                        entry["catalog_label"] = short
                        entry["auto_added"] = True
                        auto_added += 1
                        logger.info(f"Auto-pridaný nový pás: {dev_name} ({dev_addr}) → label {short}")
                    except Exception:
                        pass  # už existuje alebo iná chyba
            result.append(entry)

        db.close()
        logger.info(f"Scan hotový — {len(result)} HR zariadení, {auto_added} nových v katalógu")
        return 200, result
