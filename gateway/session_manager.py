import asyncio
import csv
import io
import logging
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

FLUSH_INTERVAL = 5   # sekúnd medzi batch insertmi do DB


class SessionManager:
    """
    Spravuje tréningové session — štart/stop, ukladanie HR dát do SQLite.
    HR dáta sa bufferujú v pamäti a flushujú po FLUSH_INTERVAL sekundách,
    aby sme minimalizovali počet DB operácií pri 20 pásoch × 1 packet/s.
    """

    def __init__(self, cache_db: str, broadcast_fn=None):
        self.cache_db          = cache_db
        self.broadcast_fn      = broadcast_fn
        self.current_session_id: int | None = None
        self._planned_end: float | None = None   # unix timestamp
        self._buffer: list     = []
        self._ensure_tables()

    def _db(self):
        return sqlite3.connect(self.cache_db)

    def _ensure_tables(self):
        db = self._db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id                  INTEGER PRIMARY KEY,
                started_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
                ended_at            DATETIME,
                label               TEXT,
                planned_duration_min INTEGER
            );
            CREATE TABLE IF NOT EXISTS session_data (
                session_id    INTEGER REFERENCES sessions(id),
                bike_position INTEGER,
                ts            DATETIME DEFAULT CURRENT_TIMESTAMP,
                hr            INTEGER,
                zone          INTEGER,
                cal_inc       REAL,
                meps_inc      REAL,
                watts         INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_sd_session
                ON session_data(session_id, bike_position);
        """)
        # Migrácie — pridaj stĺpce ak chýbajú (existujúce DB)
        existing = {r[1] for r in db.execute("PRAGMA table_info(session_data)")}
        if "watts" not in existing:
            db.execute("ALTER TABLE session_data ADD COLUMN watts INTEGER DEFAULT 0")
        existing_s = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
        if "planned_duration_min" not in existing_s:
            db.execute("ALTER TABLE sessions ADD COLUMN planned_duration_min INTEGER")
        db.commit()
        db.close()

    # ── Štart / Stop ──────────────────────────────────────────────────────────

    def start(self, label: str | None = None,
              planned_duration_min: int | None = None) -> dict:
        if self.current_session_id:
            return {"error": "session already running",
                    "session_id": self.current_session_id}
        label = label or f"Hodina {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        now   = datetime.now().isoformat(timespec="seconds")
        db    = self._db()
        cur   = db.execute(
            "INSERT INTO sessions(started_at, label, planned_duration_min) VALUES(?,?,?)",
            (now, label, planned_duration_min)
        )
        self.current_session_id = cur.lastrowid
        import time
        self._planned_end = (time.time() + planned_duration_min * 60
                             if planned_duration_min else None)
        db.commit()
        db.close()
        logger.info(f"Session #{self.current_session_id} štart — {label}"
                    + (f" (plán {planned_duration_min} min)" if planned_duration_min else ""))
        return {"session_id": self.current_session_id,
                "label": label, "started_at": now,
                "planned_duration_min": planned_duration_min}

    def stop(self) -> dict:
        if not self.current_session_id:
            return {"error": "no active session"}
        # flush zvyšok buffera
        self._flush_sync()
        now = datetime.now().isoformat(timespec="seconds")
        db  = self._db()
        db.execute("UPDATE sessions SET ended_at=? WHERE id=?",
                   (now, self.current_session_id))
        summary = self._build_summary(db, self.current_session_id)
        db.commit()
        db.close()
        sid = self.current_session_id
        self.current_session_id = None
        logger.info(f"Session #{sid} ukončená")
        return {"session_id": sid, "ended_at": now, "summary": summary}

    def get_current(self) -> dict | None:
        if not self.current_session_id:
            return None
        db  = self._db()
        row = db.execute(
            "SELECT id, label, started_at FROM sessions WHERE id=?",
            (self.current_session_id,)
        ).fetchone()
        db.close()
        return {"session_id": row[0], "label": row[1], "started_at": row[2]} if row else None

    def list_sessions(self, limit: int = 20) -> list:
        db   = self._db()
        rows = db.execute(
            "SELECT id, label, started_at, ended_at FROM sessions "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        db.close()
        return [{"session_id": r[0], "label": r[1],
                 "started_at": r[2], "ended_at": r[3]} for r in rows]

    # ── Záznam HR dát ─────────────────────────────────────────────────────────

    def record(self, bike_position: int, hr: int, zone: int,
               cal_inc: float, meps_inc: float, watts: int = 0):
        """Volaná synchrónne z BleStrap._on_hr_data — len appenduje do buffera."""
        if not self.current_session_id:
            return
        self._buffer.append((
            self.current_session_id, bike_position,
            datetime.now().isoformat(timespec="seconds"),
            hr, zone,
            round(cal_inc, 3), round(meps_inc, 4), watts,
        ))

    # ── Flush loop ────────────────────────────────────────────────────────────

    async def flush_loop(self):
        """Asyncio task — každých FLUSH_INTERVAL sekúnd zapíše buffer do DB + auto-stop."""
        import time
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            if self._buffer:
                self._flush_sync()
            # Auto-stop po uplynutí plánovaného času
            if self._planned_end and self.current_session_id:
                if time.time() >= self._planned_end:
                    logger.info("Auto-stop — plánovaný čas vypršal")
                    result = self.stop()
                    self._planned_end = None
                    if self.broadcast_fn:
                        asyncio.create_task(self.broadcast_fn({
                            "type":    "session_stopped",
                            "summary": result.get("summary", []),
                            "auto":    True,
                        }))

    def _flush_sync(self):
        if not self._buffer:
            return
        batch = self._buffer[:]
        self._buffer.clear()
        try:
            db = self._db()
            db.executemany(
                "INSERT INTO session_data"
                "(session_id, bike_position, ts, hr, zone, cal_inc, meps_inc, watts)"
                " VALUES(?,?,?,?,?,?,?,?)",
                batch,
            )
            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"Session flush chyba: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────

    def _build_summary(self, db, session_id: int) -> list:
        rows = db.execute("""
            SELECT
                d.bike_position,
                r.name,
                COUNT(*)                                    AS packets,
                MIN(d.hr)                                   AS min_hr,
                MAX(d.hr)                                   AS max_hr,
                ROUND(AVG(d.hr))                            AS avg_hr,
                ROUND(SUM(d.meps_inc))                      AS total_meps,
                ROUND(SUM(d.cal_inc))                       AS total_calories,
                SUM(CASE WHEN d.zone=1 THEN 1 ELSE 0 END)  AS z1_sec,
                SUM(CASE WHEN d.zone=2 THEN 1 ELSE 0 END)  AS z2_sec,
                SUM(CASE WHEN d.zone=3 THEN 1 ELSE 0 END)  AS z3_sec,
                SUM(CASE WHEN d.zone=4 THEN 1 ELSE 0 END)  AS z4_sec,
                SUM(CASE WHEN d.zone=5 THEN 1 ELSE 0 END)  AS z5_sec,
                ROUND(AVG(CASE WHEN d.watts > 0 THEN d.watts END)) AS avg_watts,
                MAX(d.watts)                                AS max_watts
            FROM session_data d
            LEFT JOIN riders_cache r ON r.bike_position = d.bike_position
            WHERE d.session_id = ?
            GROUP BY d.bike_position
        """, (session_id,)).fetchall()

        return [
            {
                "bike_position":  r[0],
                "name":           r[1] or f"Bike {r[0]}",
                "duration_sec":   r[2],
                "min_hr":         r[3],
                "max_hr":         r[4],
                "avg_hr":         r[5],
                "total_meps":     r[6],
                "total_calories": r[7],
                "zones_sec":      {"1": r[8], "2": r[9], "3": r[10],
                                   "4": r[11], "5": r[12]},
                "avg_watts":      r[13] or 0,
                "max_watts":      r[14] or 0,
            }
            for r in rows
        ]

    def get_summary(self, session_id: int) -> list:
        db = self._db()
        result = self._build_summary(db, session_id)
        db.close()
        return result

    def export_fit(self, session_id: int, bike_position: int) -> tuple:
        """Vráti (filename, bytes) FIT súboru pre jedného jazdca."""
        db = self._db()
        sess_row = db.execute(
            "SELECT label, started_at, ended_at FROM sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        if not sess_row:
            db.close()
            return None, None

        rider_row = db.execute(
            "SELECT name, birth_year, weight_kg, gender, max_hr "
            "FROM riders_cache WHERE bike_position=?",
            (bike_position,),
        ).fetchone()

        records = db.execute(
            "SELECT ts, hr, cal_inc, watts FROM session_data "
            "WHERE session_id=? AND bike_position=? ORDER BY ts",
            (session_id, bike_position),
        ).fetchall()
        db.close()

        if not records:
            return None, None

        from fit_export import build_fit
        session_info = {
            "started_at": sess_row[1],
            "ended_at":   sess_row[2],
            "label":      sess_row[0],
        }
        rider_info = {
            "name":       rider_row[0] if rider_row else f"Bike {bike_position}",
            "birth_year": rider_row[1] if rider_row else None,
            "weight_kg":  rider_row[2] if rider_row else None,
            "gender":     rider_row[3] if rider_row else "M",
            "max_hr":     rider_row[4] if rider_row else None,
        }
        rec_dicts = [
            {"ts": r[0], "hr": r[1], "cal_inc": r[2] or 0, "watts": r[3] or 0}
            for r in records
        ]

        fit_bytes = build_fit(session_info, rider_info, rec_dicts)
        name = rider_info["name"].replace(" ", "_")
        filename = f"session_{session_id}_{name}.fit"
        return filename, fit_bytes

    def export_csv(self, session_id: int) -> str:
        db  = self._db()
        row = db.execute(
            "SELECT label, started_at FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        rows = db.execute("""
            SELECT d.ts, d.bike_position, r.name, d.hr, d.zone,
                   ROUND(d.cal_inc,2), ROUND(d.meps_inc,4), d.watts
            FROM session_data d
            LEFT JOIN riders_cache r ON r.bike_position = d.bike_position
            WHERE d.session_id = ?
            ORDER BY d.ts, d.bike_position
        """, (session_id,)).fetchall()
        db.close()

        out = io.StringIO()
        w   = csv.writer(out)
        w.writerow(["session_id", "label", "started_at"])
        w.writerow([session_id, row[0] if row else "", row[1] if row else ""])
        w.writerow([])
        w.writerow(["ts", "bike_position", "name", "hr", "zone",
                    "cal_inc", "meps_inc", "watts"])
        w.writerows(rows)
        return out.getvalue()
