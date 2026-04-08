import json
import time


class IntervalTimer:
    """
    Interval timer — uchováva konfig a stav (idle/running/paused).
    Brodkast udalostí (timer_update) robí volajúci po každej zmene stavu.
    """

    def __init__(self):
        self.rounds    = 0
        self.work_min  = 0
        self.rest_min  = 0
        self.intervals = []   # [{duration, type, label}, ...]
        self.total_sec = 0

        self.state            = "idle"   # idle | running | paused
        self.start_epoch      = None     # float — kedy naposledy start/resume
        self.elapsed_at_pause = 0.0     # sekundy pred poslednou pauzou

    # ── Config ────────────────────────────────────────────────────────────────

    def set_config(self, rounds: int, work_min: int, rest_min: int):
        self.rounds   = max(1, rounds)
        self.work_min = max(1, work_min)
        self.rest_min = max(0, rest_min)
        self.intervals = []
        for _ in range(self.rounds):
            self.intervals.append({
                "duration": self.work_min * 60,
                "type":     "work",
                "label":    "Záťaž",
            })
            if self.rest_min > 0:
                self.intervals.append({
                    "duration": self.rest_min * 60,
                    "type":     "rest",
                    "label":    "Voľno",
                })
        self.total_sec = sum(i["duration"] for i in self.intervals)

    def load_from_db(self, db):
        row = db.execute(
            "SELECT value FROM settings WHERE key='interval_timer'"
        ).fetchone()
        if not row:
            return
        try:
            cfg = json.loads(row[0])
            self.set_config(
                cfg.get("rounds",   3),
                cfg.get("work_min", 25),
                cfg.get("rest_min", 5),
            )
            saved_state = cfg.get("state", "idle")
            if saved_state in ("running", "paused"):
                self.elapsed_at_pause = float(cfg.get("elapsed_at_pause", 0.0))
                if saved_state == "running":
                    # Koriguj čas od posledného uloženia (reštart gateway)
                    saved_at = float(cfg.get("saved_at", time.time()))
                    self.elapsed_at_pause += time.time() - saved_at
                    self.start_epoch = time.time()
                    self.state = "running"
                else:
                    self.state = "paused"
        except Exception:
            pass

    def save_to_db(self, db):
        if not self.intervals:
            return
        data = {
            "rounds":           self.rounds,
            "work_min":         self.work_min,
            "rest_min":         self.rest_min,
            "state":            self.state,
            "elapsed_at_pause": self.elapsed_at_pause,
            "saved_at":         time.time(),
        }
        db.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES('interval_timer', ?)",
            (json.dumps(data),),
        )

    # ── Controls ──────────────────────────────────────────────────────────────

    def start(self) -> bool:
        if not self.intervals:
            return False
        self.state            = "running"
        self.start_epoch      = time.time()
        self.elapsed_at_pause = 0.0
        return True

    def pause(self) -> bool:
        if self.state != "running":
            return False
        self.elapsed_at_pause += time.time() - self.start_epoch
        self.start_epoch       = None
        self.state             = "paused"
        return True

    def resume(self) -> bool:
        if self.state != "paused":
            return False
        self.start_epoch = time.time()
        self.state       = "running"
        return True

    def stop(self) -> bool:
        self.state            = "idle"
        self.start_epoch      = None
        self.elapsed_at_pause = 0.0
        return True

    # ── State ─────────────────────────────────────────────────────────────────

    def get_ws_state(self) -> dict:
        if not self.intervals:
            return {"type": "timer_update", "configured": False}
        return {
            "type":             "timer_update",
            "configured":       True,
            "state":            self.state,
            "is_running":       self.state == "running",
            "is_paused":        self.state == "paused",
            "start_epoch":      self.start_epoch,
            "elapsed_at_pause": self.elapsed_at_pause,
            "intervals":        self.intervals,
            "rounds":           self.rounds,
            "work_min":         self.work_min,
            "rest_min":         self.rest_min,
            "total_sec":        self.total_sec,
        }
